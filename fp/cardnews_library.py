"""파트너 카드뉴스방: 10장 묶음 ZIP 을 전부 공개하고 파트너가 골라 하루 2개씩 받아간다.

영상방(video_library)과 같은 배정 원칙을 쓴다 — 한 묶음은 한 사람에게만, 받은 건 다시 받을 수 있다.
(2026-09-29: 파트너별 템플릿 고정은 사용자 결정으로 폐지. partner_card_templates 는 기록용으로만 남는다.)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import core, db
from . import video_library as vl

DAILY_CARDNEWS_LIMIT = 2
MAX_DECK_BYTES = 64 * 1024 * 1024
COOKIE = 'fp_cardnews_partner'
TEMPLATES = {
    '01-gradient': '그라데이션 · 그레인',
    '02-shortcut': '형광 숫자 · 키캡',
    '03-negative': '여백 · 비교',
    '04-photojournal': '사진 · 작은 캡션',
    '05-hero': '큰 피사체 · 하단 제목',
    '06-landscape': '풍경 · 타이포',
    '07-typeplay': '민트 · 타이포 연출',
}
AVAILABLE = 'published=1 AND claimed_at IS NULL AND claimed_by IS NULL'
# 영상 썸네일은 세로(9:12) 틀이라 정사각형 카드 표지는 양옆이 잘린다 — 카드뉴스는 1:1 로 보인다.
DECK_STYLE = '<style>.video-card img.deck-cover{aspect-ratio:1/1;object-fit:contain;background:#111}</style>'


def storage_dir():
    return vl.storage_dir() / 'cardnews'


def deck_path(row):
    key = row['file_key']
    if not re.fullmatch(r'[a-f0-9]{32}\.zip', key):
        raise ValueError('invalid storage key')
    root = storage_dir().resolve()
    path = root / key
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError('invalid storage path')
    if not path.is_file() or path.stat().st_size != row['size']:
        raise FileNotFoundError('deck unavailable')
    return path


def thumb_path(row):
    return storage_dir() / (row['file_key'][:-4] + '.jpg')


def get_deck(conn, did):
    return conn.execute('SELECT * FROM cardnews_decks WHERE id=?', (did,)).fetchone()


def template_of(conn, pid, assign=True):
    """파트너의 고정 템플릿. 처음이면 가장 적게 쓰인 템플릿을 준다."""
    row = conn.execute('SELECT template FROM partner_card_templates WHERE partner_id=?', (pid,)).fetchone()
    if row or not assign:
        return row['template'] if row else None
    used = {r['template']: r['n'] for r in conn.execute(
        'SELECT template, COUNT(*) AS n FROM partner_card_templates GROUP BY template').fetchall()}
    pick = min(TEMPLATES, key=lambda t: (used.get(t, 0), t))
    conn.execute('INSERT INTO partner_card_templates(partner_id,template,assigned_at) VALUES (?,?,?) '
                 'ON CONFLICT(partner_id) DO NOTHING', (pid, pick, core.now_iso()))
    conn.commit()
    return conn.execute('SELECT template FROM partner_card_templates WHERE partner_id=?', (pid,)).fetchone()['template']


def daily_count(conn, pid, day):
    start = day + 'T00:00:00'
    end = (date.fromisoformat(day) + timedelta(days=1)).isoformat() + 'T00:00:00'
    return conn.execute('SELECT COUNT(*) AS n FROM cardnews_decks WHERE claimed_by=? AND claimed_at>=? AND claimed_at<?',
                        (pid, start, end)).fetchone()['n']


def claim(conn, did, token, name):
    if not db.is_postgres():
        conn.execute('BEGIN IMMEDIATE')
    try:
        p = vl.partner(conn, token)
        if not p or name.strip() != p['name'].strip():
            raise PermissionError('등록한 이름을 확인해 주세요.')
        row = get_deck(conn, did)
        if not row:
            raise LookupError('카드뉴스를 찾을 수 없어요.')
        if row['claimed_by'] == p['id']:
            deck_path(row)
            conn.commit()
            return row
        if not row['published'] or row['claimed_at'] or row['claimed_by']:
            raise LookupError('다른 파트너가 먼저 받았거나 받을 수 없는 카드뉴스예요.')
        stamp = core.now_iso()
        if daily_count(conn, p['id'], stamp[:10]) >= DAILY_CARDNEWS_LIMIT:
            raise LookupError('오늘 받을 수 있는 카드뉴스를 이미 받았어요. 한국 시간 자정 이후에 새로 받을 수 있어요.')
        deck_path(row)
        won = conn.execute('UPDATE cardnews_decks SET claimed_by=?,claimed_name=?,claimed_at=? '
                           f'WHERE id=? AND {AVAILABLE} RETURNING id', (p['id'], p['name'], stamp, did)).fetchone()
        if not won:
            raise LookupError('다른 파트너가 먼저 받은 카드뉴스예요.')
        got = get_deck(conn, did)
        conn.commit()
        return got
    except Exception:
        conn.execute('ROLLBACK')
        raise


def upload(h, conn):
    vl.checked_csrf(h, {}, 'admin')
    n = int(h.headers.get('Content-Length', '0'))
    title = unquote(h.headers.get('X-Deck-Title', '')).strip()
    template = h.headers.get('X-Template', '').strip()
    if not 22 <= n <= MAX_DECK_BYTES:
        return vl.json_response(h, {'error': '파일 크기를 확인해 주세요.'}, 413)
    if not title or len(title) > 120 or template not in TEMPLATES:
        return vl.json_response(h, {'error': '제목과 템플릿을 확인해 주세요.'}, 400)
    root = storage_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = secrets.token_hex(16) + '.zip'
    tmp, final = root / (key + '.part'), root / key
    digest = hashlib.sha256()
    committed = False
    try:
        with tmp.open('xb') as target:
            os.chmod(tmp, 0o600)
            first = h.rfile.read(min(4, n))
            if first != b'PK\x03\x04':
                raise ValueError('ZIP 파일을 확인해 주세요.')
            target.write(first); digest.update(first); remaining = n - len(first)
            while remaining:
                block = h.rfile.read(min(vl.CHUNK, remaining))
                if not block:
                    raise ValueError('업로드가 끊겼어요. 다시 올려 주세요.')
                target.write(block); digest.update(block); remaining -= len(block)
            target.flush(); os.fsync(target.fileno())
        os.replace(tmp, final)
        result = conn.execute('INSERT INTO cardnews_decks(title,template,file_key,size,sha256,created_at) '
                              'VALUES (?,?,?,?,?,?) ON CONFLICT(sha256) DO NOTHING RETURNING id',
                              (title, template, key, n, digest.hexdigest(), core.now_iso())).fetchone()
        conn.commit()
        committed = True
        if result:
            did = result['id']
        else:
            final.unlink()
            did = conn.execute('SELECT id FROM cardnews_decks WHERE sha256=?', (digest.hexdigest(),)).fetchone()['id']
        return vl.json_response(h, {'ok': True, 'id': did, 'sha256': digest.hexdigest()}, 201)
    finally:
        if not committed and final.exists():
            final.unlink()
        if tmp.exists():
            tmp.unlink()


def upload_thumb(h, conn, did):
    vl.checked_csrf(h, {}, 'admin')
    row = get_deck(conn, did)
    n = int(h.headers.get('Content-Length', '0'))
    if not row or not 4 <= n <= 2 * 1024 * 1024:
        raise ValueError('thumbnail')
    data = h.rfile.read(n)
    if not data.startswith(b'\xff\xd8\xff'):
        raise ValueError('JPEG')
    path = thumb_path(row)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    part = path.with_suffix('.part')
    part.write_bytes(data); os.replace(part, path)
    vl.json_response(h, {'ok': True, 'id': did, 'sha256': hashlib.sha256(data).hexdigest()})


def card(row, token=None, claimable=False):
    from .server import esc
    did = row['id']
    cover = (f"<img class=deck-cover src='/cardnews/thumb/{did}' alt='{esc(row['title'])}' loading=lazy width=360 height=360>"
             f"<div class=video-info><h3>{esc(row['title'])}</h3><small>카드 10장 · {esc(TEMPLATES.get(row['template'], ''))}</small>"
             + ("<span class=video-select>선택하고 받기 →</span>" if claimable else '') + '</div>')
    body = f"<article class=video-card data-deck='{did}'>" + vl.source_link(row)
    if claimable and token:
        body += (f"<details><summary>{cover}</summary><form method=post action=/cardnews/claim>"
                 f"<input type=hidden name=id value='{did}'><input type=hidden name=t value='{esc(token)}'>"
                 f"<input type=hidden name=csrf value='{vl.csrf(token)}'>"
                 "<label>등록한 이름<input name=name required maxlength=100 autocomplete=name placeholder='이름 입력'></label>"
                 "<button>이름 확인하고 카드뉴스 받기</button></form></details>")
    else:
        body += cover
    return body + '</article>'


def listing(h, conn, p, got=None):
    from .server import esc
    rows = conn.execute(f'SELECT * FROM cardnews_decks WHERE {AVAILABLE} ORDER BY id DESC').fetchall()
    owned = conn.execute('SELECT * FROM cardnews_decks WHERE claimed_by=? ORDER BY claimed_at DESC', (p['id'],)).fetchall()
    fresh = next((r for r in owned if r['id'] == got), None) if got else None
    top = ''
    if fresh:
        # 받자마자 캡션과 원본 링크를 같이 보여주고 ZIP 은 자동으로 내려받게 한다.
        top = ("<section class=card style='border:2px solid var(--acc)'><h2>✅ 카드뉴스를 받았어요</h2>"
               f"<p><b>{esc(fresh['title'])}</b></p><p><a id=fresh-deck href='/cardnews/file/{fresh['id']}' download>"
               "카드 10장 ZIP 받기</a> — 자동으로 안 받아지면 눌러 주세요.</p>"
               "<p>아래 캡션을 복사해서 카드뉴스와 같이 올리면 됩니다.</p>"
               + vl.caption_box(fresh['caption'] or '') + vl.source_link(fresh)
               + "<script>document.getElementById('fresh-deck').click()</script></section>")
    body = (vl.GALLERY_STYLE + DECK_STYLE + top + "<section class=card><h2>🗂 받을 수 있는 카드뉴스 "
            f"<span>{len(rows)}개</span></h2><p>마음에 드는 카드뉴스를 골라 받아 가세요. 먼저 받은 사람에게 배정되고, "
            f"계정당 하루 {DAILY_CARDNEWS_LIMIT}개, 한국 시간 자정에 초기화됩니다.</p>"
            "<div class=video-grid>" + ''.join(card(r, p['portal_token'], True) for r in rows) + '</div>')
    if not rows:
        body += '<p>지금 받을 수 있는 카드뉴스가 없어요. 매일 새로 채워집니다.</p>'
    body += '</section><div class=card><h2>내가 받은 카드뉴스</h2>'
    for row in owned:
        again = ("<p>보관 기간이 지나 원본을 정리했어요.</p>" if row['purged_at']
                 else f"<a href='/cardnews/file/{row['id']}'>카드 10장 다시 받기</a>")
        body += (f"<div class=card><h3>{esc(row['title'])}</h3>{again}" + vl.source_link(row)
                 + vl.caption_box(row['caption'] or '') + '</div>')
    if not owned:
        body += '<p>아직 받은 카드뉴스가 없어요.</p>'
    vl.page(h, '카드뉴스방', body + '</div>' + vl.CAPTION_SCRIPT, p['portal_token'])


def dashboard_card(token=None, admin=False, limit=8):
    """작업실·관리자 대시보드에 붙는 카드뉴스 미리보기(최신 몇 개)."""
    conn = db.connect()
    try:
        rows = conn.execute(f'SELECT * FROM cardnews_decks WHERE {AVAILABLE} ORDER BY id DESC').fetchall()
        active = vl.partner(conn, token)
    finally:
        conn.close()
    body = (vl.GALLERY_STYLE + DECK_STYLE + "<section class=card id=partner-cardnews style='border:2px solid var(--acc)'>"
            f"<h2>🗂 받을 수 있는 카드뉴스 <span>{len(rows)}개</span></h2>"
            f"<p>카드 10장 묶음, 계정당 하루 {DAILY_CARDNEWS_LIMIT}개. 먼저 받은 사람에게 배정돼요.</p>"
            "<div class=video-grid>" + ''.join(card(r) for r in rows[:limit]) + '</div>')
    if not rows:
        body += '<p>지금 받을 수 있는 카드뉴스가 없어요.</p>'
    if admin:
        body += "<a class=lk href='/op/cardnews'>카드뉴스 전체 보기·관리 →</a>"
    else:
        href = '/cardnews?t=' + quote(token, safe='') if active else '/cardnews'
        body += f"<a class=lk href='{href}'>카드뉴스방에서 골라 받기 →</a>"
    return body + '</section>'


def admin_listing(h, conn):
    from .server import esc
    rows = conn.execute('SELECT * FROM cardnews_decks ORDER BY id DESC LIMIT 300').fetchall()
    assigned = conn.execute('SELECT t.template, p.name FROM partner_card_templates t JOIN partners p ON p.id=t.partner_id '
                            'ORDER BY t.template').fetchall()
    body = "<div class=card><h2>파트너별 카드뉴스 디자인</h2><ul>"
    for t, label in TEMPLATES.items():
        names = ', '.join(esc(r['name']) for r in assigned if r['template'] == t) or '—'
        left = sum(1 for r in rows if r['template'] == t and not r['claimed_at'] and r['published'])
        body += f"<li><b>{t}</b> {esc(label)} · 남은 묶음 {left}개 · {names}</li>"
    live = [r for r in rows if r['published'] and not r['claimed_at']]
    body += ("</ul></div>" + vl.GALLERY_STYLE + DECK_STYLE + f"<section class=card><h2>지금 파트너에게 보이는 카드뉴스 {len(live)}개</h2>"
             "<div class=video-grid>" + ''.join(card(r) for r in live) + "</div></section>")
    body += "<div class=card><h2>카드뉴스 배포 내역</h2>"
    for r in rows:
        state = f"{esc(r['claimed_name'])} · {esc(r['claimed_at'])} 수령" if r['claimed_at'] else ('배포 중' if r['published'] else '비공개')
        body += f"<div class=card><h3>{esc(r['title'])}</h3><p>{r['template']} · {state}</p>{vl.source_link(r, '유튜브 원본')}</div>"
    vl.page(h, '카드뉴스 배포 관리', body + '</div>', admin=True)


def storage_stats(conn):
    return {'available': conn.execute(f'SELECT COUNT(*) AS n FROM cardnews_decks WHERE {AVAILABLE}').fetchone()['n'],
            'by_template': {r['template']: r['n'] for r in conn.execute(
                f'SELECT template, COUNT(*) AS n FROM cardnews_decks WHERE {AVAILABLE} GROUP BY template').fetchall()}}


def handle(h):
    u = urlparse(h.path)
    if not (u.path == '/cardnews' or u.path.startswith('/cardnews/') or u.path.startswith('/op/cardnews')):
        return False
    conn = None
    try:
        conn = db.connect()
        admin = h._admin_ok()
        sync = vl.sync_authorized(h)
        h._video_sync_ok = sync
        if u.path.startswith('/op/cardnews'):
            sync_route = u.path in ('/op/cardnews/sync-status', '/op/cardnews/upload', '/op/cardnews/publish') or bool(
                re.fullmatch(r'/op/cardnews/(thumbnail|caption|source)/\d+', u.path))
            if not (admin or (sync and sync_route)):
                vl.response(h, '관리자 로그인이 필요해요.', 403); return True
            if h.command == 'GET' and u.path == '/op/cardnews/sync-status':
                rows = conn.execute('SELECT id,sha256,template,published,claimed_at,caption,source_url,file_key FROM cardnews_decks ORDER BY id').fetchall()
                vl.json_response(h, {'stock': storage_stats(conn), 'decks': [
                    {'id': r['id'], 'sha256': r['sha256'], 'template': r['template'], 'published': bool(r['published']),
                     'claimed': bool(r['claimed_at']), 'has_caption': bool(r['caption']), 'source_url': r['source_url'],
                     'has_thumbnail': thumb_path(r).is_file()} for r in rows]})
            elif h.command == 'GET' and u.path == '/op/cardnews':
                admin_listing(h, conn)
            elif h.command == 'POST' and u.path == '/op/cardnews/publish':
                f = vl.fields(h); vl.checked_csrf(h, f, 'admin')
                value = int(f['published'])
                if value not in (0, 1):
                    raise ValueError('published')
                # 받아간 묶음은 그대로 둔다 — 파트너가 다시 받을 수 있어야 한다.
                conn.execute('UPDATE cardnews_decks SET published=? WHERE id=? AND claimed_at IS NULL', (value, int(f['id'])))
                conn.commit()
                vl.json_response(h, {'ok': True})
            elif h.command == 'POST' and u.path == '/op/cardnews/upload':
                upload(h, conn)
            elif h.command == 'POST' and re.fullmatch(r'/op/cardnews/thumbnail/\d+', u.path):
                upload_thumb(h, conn, int(u.path.rsplit('/', 1)[1]))
            elif h.command == 'POST' and re.fullmatch(r'/op/cardnews/(caption|source)/\d+', u.path):
                f = vl.fields(h, 65536); vl.checked_csrf(h, f, 'admin')
                did = int(u.path.rsplit('/', 1)[1]); row = get_deck(conn, did)
                if not row or not hmac.compare_digest(f.get('sha256', ''), row['sha256']):
                    raise ValueError('deck mismatch')
                if '/caption/' in u.path:
                    text = f.get('caption', '').strip()
                    if not text or len(text) > 4000:
                        raise ValueError('caption')
                    conn.execute('UPDATE cardnews_decks SET caption=? WHERE id=?', (text, did))
                else:
                    url = f.get('source_url', '').strip()
                    if url and not vl.valid_source_url(url):
                        raise ValueError('source url')
                    conn.execute('UPDATE cardnews_decks SET source_url=? WHERE id=?', (url or None, did))
                conn.commit()
                vl.json_response(h, {'ok': True, 'id': did})
            else:
                vl.response(h, '없는 페이지예요.', 404)
            return True
        submitted = vl.fields(h) if u.path == '/cardnews/claim' and h.command == 'POST' else {}
        qs = parse_qs(u.query)
        token = submitted.get('t') or h._cookies().get(COOKIE, '') or h._cookies().get(vl.PARTNER_COOKIE, '')
        secure = '; Secure' if h.headers.get('X-Forwarded-Proto') == 'https' else ''
        cookie = ('Set-Cookie', f'{COOKIE}={{}}; HttpOnly; SameSite=Lax; Path=/cardnews; Max-Age=604800{secure}')
        if u.path == '/cardnews' and h.command == 'GET' and 't' in qs:
            token = qs['t'][0]
            if not vl.partner(conn, token):
                raise PermissionError('내 작업실에서 다시 들어와 주세요.')
            vl.redirect(h, '/cardnews', [(cookie[0], cookie[1].format(token))]); return True
        p = vl.partner(conn, token)
        m = re.fullmatch(r'/cardnews/(thumb|file)/(\d+)', u.path)
        if m and h.command in ('GET', 'HEAD'):
            row = get_deck(conn, int(m.group(2)))
            owner = bool(p and row and row['claimed_by'] == p['id'])
            if m.group(1) == 'thumb':
                visible = row and row['published'] and not row['claimed_at'] and p
                if not row or not (admin or sync or owner or visible):
                    raise PermissionError('볼 수 없는 카드뉴스예요.')
                vl.response(h, thumb_path(row).read_bytes(), kind='image/jpeg'); return True
            if not row or not (admin or sync or owner):
                raise PermissionError('배정받은 파트너만 받을 수 있어요.')
            if row['purged_at']:
                vl.response(h, '보관 기간이 지나 원본을 정리했어요.', 410); return True
            path = deck_path(row)
            name = quote(f"cardnews-{row['id']}.zip")
            vl.response(h, path.read_bytes(), kind='application/zip',
                        headers=[('Content-Disposition', f"attachment; filename*=UTF-8''{name}")]); return True
        if not p:
            vl.page(h, '카드뉴스방', '<div class=card><h2>내 작업실에서 들어와 주세요</h2><p>파트너 확인 후 카드뉴스를 받을 수 있어요.</p>'
                    '<a href=/find>내 작업실 찾기</a></div>', status=403); return True
        if u.path == '/cardnews' and h.command == 'GET':
            got = qs.get('got', [''])[0]
            listing(h, conn, p, int(got) if got.isdigit() else None)
        elif u.path == '/cardnews/claim' and h.command == 'POST':
            vl.checked_csrf(h, submitted, token)
            row = claim(conn, int(submitted['id']), token, submitted.get('name', ''))
            vl.redirect(h, f"/cardnews?got={row['id']}", [(cookie[0], cookie[1].format(token))])
        else:
            vl.response(h, '없는 페이지예요.', 404)
    except PermissionError as e:
        vl.response(h, str(e), 403)
    except LookupError as e:
        vl.response(h, str(e), 409)
    except (ValueError, KeyError, UnicodeError):
        vl.json_response(h, {'error': '입력한 내용과 파일을 확인해 주세요.'}, 400)
    except FileNotFoundError:
        vl.response(h, '원본 파일을 확인 중이에요. 잠시 후 다시 시도해 주세요.', 503)
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        if conn is not None:
            conn.close()
    return True
