"""Private MP4 distribution: atomic allocation, partner-bound retries, streamed files.

No original is stored in the public library or repository. Registration is draft-only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import core, db

MAX_VIDEO_BYTES = 512 * 1024 * 1024
CHUNK = 1024 * 1024
PARTNER_COOKIE = 'fp_video_partner'


def storage_dir():
    return Path(os.environ.get('FP_VIDEO_DIR', str(db.ROOT / 'data' / 'private_videos')))


def file_path(row):
    key = row['file_key']
    if not re.fullmatch(r'[a-f0-9]{32}\.mp4', key):
        raise ValueError('invalid storage key')
    root = storage_dir().resolve()
    path = root / key
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError('invalid storage path')
    if not path.is_file() or path.stat().st_size != row['size']:
        raise FileNotFoundError('original unavailable')
    return path


def partner(conn, token):
    if not token or len(token) > 256:
        return None
    return conn.execute("SELECT id,name,portal_token FROM partners WHERE portal_token=? AND status='active'", (token,)).fetchone()


def get_video(conn, vid):
    return conn.execute('SELECT * FROM exclusive_videos WHERE id=?', (vid,)).fetchone()


def claim(conn, vid, token, name):
    """UPDATE predicate is the lock: independent concurrent requests have one owner."""
    p = partner(conn, token)
    if not p or name.strip() != p['name'].strip():
        raise PermissionError('등록한 이름을 확인해 주세요.')
    row = get_video(conn, vid)
    if not row:
        raise LookupError('영상을 찾을 수 없어요.')
    file_path(row)  # Do not consume an allocation when storage is unavailable.
    won = conn.execute(
        "UPDATE exclusive_videos SET claimed_by=?,claimed_name=?,claimed_at=? "
        "WHERE id=? AND published=1 AND claimed_at IS NULL AND claimed_by IS NULL "
        "AND EXISTS (SELECT 1 FROM partners WHERE id=? AND portal_token=? AND status='active') "
        "RETURNING id", (p['id'], p['name'], core.now_iso(), vid, p['id'], token)).fetchone()
    conn.commit()
    row = get_video(conn, vid)
    if won or row['claimed_by'] == p['id']:
        return row
    raise LookupError('다른 파트너가 먼저 받은 영상이에요.')


def csrf(actor):
    from .server import _secret
    return hmac.new(_secret(), ('private-video-v1:'+actor).encode(), hashlib.sha256).hexdigest()


def response(h, body, status=200, kind='text/html; charset=utf-8', headers=()):
    if isinstance(body, str):
        body = body.encode()
    h.send_response(status)
    h.send_header('Content-Type', kind)
    h.send_header('Content-Length', str(len(body)))
    h.send_header('Cache-Control', 'private, no-store')
    h.send_header('Referrer-Policy', 'same-origin')
    h.send_header('X-Content-Type-Options', 'nosniff')
    for k, v in headers:
        h.send_header(k, v)
    h.end_headers()
    if h.command != 'HEAD':
        h.wfile.write(body)


def redirect(h, path, headers=()):
    response(h, b'', 303, headers=[('Location', path), *headers])


def json_response(h, obj, status=200):
    response(h, json.dumps(obj, ensure_ascii=False), status, 'application/json; charset=utf-8')


def page(h, title, body, token=None, admin=False, status=200):
    from .server import shell, shell_portal
    response(h, shell(title, body) if admin else shell_portal(title, '파트너 전용 영상', body, token), status)


def checked_csrf(h, fields, actor):
    origin = h.headers.get('Origin')
    if origin and urlparse(origin).netloc != h.headers.get('Host'):
        raise PermissionError('페이지를 새로 열고 다시 시도해 주세요.')
    received = fields.get('csrf') or h.headers.get('X-CSRF-Token') or ''
    if not hmac.compare_digest(received, csrf(actor)):
        raise PermissionError('페이지를 새로 열고 다시 시도해 주세요.')


def fields(h):
    n = int(h.headers.get('Content-Length', '0'))
    if not 0 < n <= 16384:
        raise ValueError('invalid request size')
    return {k:v[0] for k,v in parse_qs(h.rfile.read(n).decode('utf-8')).items()}


GALLERY_STYLE = """<style>
.video-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:18px 0}
.video-card{min-width:0;background:white;border:1px solid var(--ln);border-radius:14px;overflow:hidden}
.video-card summary{display:block;cursor:pointer;list-style:none}.video-card summary::-webkit-details-marker{display:none}
.video-card img{display:block;width:100%;height:auto;aspect-ratio:9/12;object-fit:cover;object-position:center;background:#e6ecf5}
.video-card h3{font-size:16px;line-height:1.5;margin:0 0 10px;word-break:keep-all;overflow-wrap:anywhere}
.video-info{padding:14px}.video-info small{color:var(--mut)}.video-select{display:block;color:var(--acc);font-weight:700;margin-top:10px}
.video-card form{display:block;padding:0 14px 16px}.video-card label{display:block;font-size:14px}
.video-card input{width:100%;min-width:0;margin:6px 0 10px}.video-card button{width:100%;font-size:14px}
.video-card a{text-decoration:none;color:inherit}.video-result{padding:16px}.video-result a{color:var(--acc);text-decoration:underline}
@media(max-width:640px){.video-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.video-info{padding:12px}.video-card h3{font-size:15px}}
@media(max-width:359px){.video-grid{grid-template-columns:1fr}}
</style>"""
GALLERY_SCRIPT = """<script>
(()=>{const section=document.querySelector('#partner-videos');if(!section)return;
const update=async()=>{try{const r=await fetch('/videos/catalog',{cache:'no-store'});if(!r.ok)return;const a=await r.json();section.querySelectorAll('[data-video]').forEach(e=>{if(!a.ids.includes(Number(e.dataset.video))&&!e.hasAttribute('data-claim-pending'))e.remove()});section.querySelectorAll('[data-available-count]').forEach(e=>e.textContent=a.ids.length+'편')}catch{}};
section.querySelectorAll('form[data-claim]').forEach(f=>f.addEventListener('submit',async e=>{e.preventDefault();const b=f.querySelector('button'),msg=f.querySelector('[role=status]');b.disabled=true;f.closest('[data-video]').setAttribute('data-claim-pending','');msg.textContent='이름을 확인하고 있어요.';try{const r=await fetch('/videos/claim',{method:'POST',headers:{Accept:'application/json'},body:new URLSearchParams(new FormData(f))});if(!r.ok){const t=await r.text();throw Error(t.startsWith('{')?'입력한 내용을 확인해 주세요.':t)}const a=await r.json();const card=f.closest('[data-video]');card.removeAttribute('data-video');const result=document.createElement('div');result.className='video-result';const note=document.createElement('p');note.textContent='내 영상으로 배정됐어요.';const link=document.createElement('a');link.href=a.download;link.textContent='원본 다시 받기';link.download='';result.append(note,link);card.replaceChildren(result);link.click();update()}catch(err){msg.textContent=err.message;b.disabled=false;f.closest('[data-video]')?.removeAttribute('data-claim-pending');update()}}));
setInterval(update,2500);document.addEventListener('visibilitychange',()=>{if(!document.hidden)update()});})();
</script>"""


def gallery(rows, token=None, admin=False):
    from .server import esc
    body = '<div class=video-grid>'
    for row in rows:
        vid = row['id']
        cover = (f"<img src='/videos/thumb/{vid}' alt='{esc(row['title'])}' loading=lazy width=360 height=480>"
                 f"<div class=video-info><h3>{esc(row['title'])}</h3><small>MP4 · {core.human_size(row['size'])}</small>"
                 "<span class=video-select>선택하고 받기 →</span></div>")
        body += f"<article class=video-card data-video='{vid}' id='video-{vid}'>"
        if token and not admin:
            body += (f"<details><summary>{cover}</summary><form data-claim method=post action=/videos/claim>"
                     f"<input type=hidden name=id value='{vid}'><input type=hidden name=t value='{esc(token)}'>"
                     f"<input type=hidden name=csrf value='{csrf(token)}'>"
                     "<label>등록한 이름<input name=name required maxlength=100 autocomplete=name placeholder='이름 입력'></label>"
                     "<button>이름 확인하고 원본 받기</button><p role=status aria-live=polite></p></form></details>")
        else:
            href = '/op/videos' if admin else '/videos'
            body += f"<a href='{href}'>{cover}</a>"
        body += '</article>'
    return body + '</div>'


def listing(h, conn, p):
    from .server import esc
    available = conn.execute('SELECT * FROM exclusive_videos WHERE published=1 AND claimed_at IS NULL AND claimed_by IS NULL ORDER BY id DESC').fetchall()
    owned = conn.execute('SELECT * FROM exclusive_videos WHERE claimed_by=? ORDER BY claimed_at DESC', (p['id'],)).fetchall()
    body = (GALLERY_STYLE + "<section id=partner-videos><div class=card><h2>받을 수 있는 영상 "
            f"<span data-available-count>{len(available)}편</span></h2><p>카드를 선택하고 등록한 이름을 입력하세요. 영상마다 한 명만 받을 수 있어요.</p></div>"
            + gallery(available, p['portal_token']) + '</section>' + GALLERY_SCRIPT)
    body += '<div class=card><h2>내가 받은 영상</h2>'
    for row in owned:
        body += f"<p>{esc(row['title'])} · <a href='/videos/file/{row['id']}'>원본 다시 받기</a></p>"
    if not owned:
        body += '<p>아직 받은 영상이 없어요.</p>'
    page(h, '파트너 전용 영상', body + '</div>', p['portal_token'])


def dashboard_card(token=None, admin=False):
    conn = db.connect()
    try:
        rows = conn.execute('SELECT * FROM exclusive_videos WHERE published=1 AND claimed_at IS NULL AND claimed_by IS NULL ORDER BY id DESC').fetchall()
        active = partner(conn, token)
    finally:
        conn.close()
    body = (GALLERY_STYLE + "<section class=card id=partner-videos style='border:2px solid var(--acc)'>"
            f"<h2>받을 수 있는 영상 <span data-available-count>{len(rows)}편</span></h2>"
            "<p>카드를 선택하고 이름을 입력하면 원본을 받을 수 있어요. 먼저 받은 사람에게만 배정됩니다.</p>"
            + gallery(rows, token if active else None, admin))
    if not rows:
        body += '<p>지금 받을 수 있는 영상이 없어요. 이미 받은 영상은 다시 받을 수 있어요.</p>'
    if admin:
        body += "<a class=lk href='/op/videos'>영상 확인·관리 →</a>"
    else:
        href = '/videos?t=' + quote(token, safe='') if active else '/videos'
        body += f"<a class=lk href='{href}'>내가 받은 영상 · 전체 목록 →</a>"
    return body + '</section>' + GALLERY_SCRIPT


def thumbnail_path(row):
    return storage_dir() / (row['file_key'] + '.jpg')


def upload_thumbnail(h, conn, vid):
    checked_csrf(h, {}, 'admin')
    row = get_video(conn, vid)
    if not row:
        raise LookupError('영상을 찾을 수 없어요.')
    file_path(row)
    n = int(h.headers.get('Content-Length', '0'))
    if not 4 <= n <= 2*1024*1024:
        raise ValueError('invalid thumbnail size')
    data = h.rfile.read(n)
    if len(data) != n or not data.startswith(b'\xff\xd8\xff') or not data.endswith(b'\xff\xd9'):
        raise ValueError('invalid JPEG')
    path = thumbnail_path(row)
    tmp = path.with_name(path.name + '.' + secrets.token_hex(6) + '.part')
    try:
        with tmp.open('xb') as f:
            os.chmod(tmp, 0o600);f.write(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    json_response(h, {'ok': True, 'id': vid, 'sha256': hashlib.sha256(data).hexdigest()})


def admin_listing(h, conn):
    from .server import esc
    rows = conn.execute('SELECT * FROM exclusive_videos ORDER BY id DESC').fetchall()
    body = ("<div class=card><h2>영상 원본 올리기</h2><p>MP4 파일을 올려 주세요. "
            f"최대 {MAX_VIDEO_BYTES // (1024*1024)}MB까지 받을 수 있어요. 올린 뒤 공개 버튼을 눌러야 파트너에게 보여요.</p>"
            "<form id=video-upload><label>영상 제목 <input name=title required maxlength=120></label>"
            "<label>원본 파일 <input name=file type=file accept='.mp4,video/mp4' required></label>"
            "<button>비공개로 올리기</button><p id=upload-status role=status></p></form></div>")
    body += '<div class=card><h2>영상 배포 내역</h2>'
    for row in rows:
        state = f"{esc(row['claimed_name'])} · {esc(row['claimed_at'])} 수령" if row['claimed_at'] else ('배포 중' if row['published'] else '비공개')
        body += (f"<div class=card><h3>{esc(row['title'])}</h3><p>{state}</p>"
                 f"<a href='/videos/file/{row['id']}'>원본 확인</a>")
        if not row['claimed_at']:
            value = 0 if row['published'] else 1
            label = '숨기기' if row['published'] else '공개하기'
            body += (f"<form method=post action=/op/videos/publish><input type=hidden name=id value='{row['id']}'>"
                     f"<input type=hidden name=csrf value='{csrf('admin')}'><input type=hidden name=published value='{value}'><button>{label}</button></form>")
        body += '</div>'
    body += '</div>'
    body += """<script>async function videoCover(file){const v=document.createElement('video');v.muted=true;v.preload='auto';const u=URL.createObjectURL(file);try{return await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('썸네일을 만들지 못했어요. 파일을 다시 올려 주세요.')),15000);v.onerror=()=>{clearTimeout(timer);reject(Error('영상을 확인해 주세요.'))};v.onloadedmetadata=()=>{v.currentTime=Math.min(2,v.duration/2)};v.onseeked=()=>{const c=document.createElement('canvas');c.width=540;c.height=Math.round(540*v.videoHeight/v.videoWidth);c.getContext('2d').drawImage(v,0,0,c.width,c.height);c.toBlob(b=>{clearTimeout(timer);b?resolve(b):reject(Error('썸네일 생성 실패'))},'image/jpeg',0.85)};v.src=u})}finally{v.removeAttribute('src');v.load();URL.revokeObjectURL(u)}}</script>"""
    body += '''<script>document.querySelector('#video-upload').onsubmit=async e=>{e.preventDefault();const f=e.target,b=f.querySelector('button'),s=document.querySelector('#upload-status');b.disabled=true;s.textContent='올리는 중이에요. 이 페이지를 닫지 마세요.';try{const file=f.elements.file.files[0];const r=await fetch('/op/videos/upload',{method:'POST',headers:{'Content-Type':'video/mp4','X-CSRF-Token':__VIDEO_CSRF__,'X-Video-Title':encodeURIComponent(f.elements.title.value),'X-File-Name':encodeURIComponent(file.name)},body:file});const data=await r.json();if(!r.ok)throw Error(data.error||'업로드에 실패했어요.');s.textContent='썸네일을 만드는 중이에요.';const cover=await videoCover(file);const thumb=await fetch('/op/videos/thumbnail/'+data.id,{method:'POST',headers:{'Content-Type':'image/jpeg','X-CSRF-Token':__VIDEO_CSRF__},body:cover});if(!thumb.ok)throw Error('원본은 저장됐지만 썸네일을 올리지 못했어요. 다시 올려 주세요.');location.reload()}catch(err){s.textContent=err.message;b.disabled=false}};</script>'''.replace('__VIDEO_CSRF__',json.dumps(csrf('admin')))
    page(h, '영상 배포 관리', body, admin=True)


def upload(h, conn):
    checked_csrf(h, {}, 'admin')
    if db.is_postgres() or os.environ.get('VERCEL'):
        return json_response(h, {'error':'영상 원본은 전용 서버에서 올려 주세요.'}, 503)
    n = int(h.headers.get('Content-Length', '0'))
    title = unquote(h.headers.get('X-Video-Title','')).strip()
    name = Path(unquote(h.headers.get('X-File-Name',''))).name
    if not 12 <= n <= MAX_VIDEO_BYTES:
        return json_response(h, {'error':'파일 크기를 확인해 주세요.'}, 413)
    if not title or len(title)>120 or len(name)>200 or not name.lower().endswith('.mp4'):
        return json_response(h, {'error':'영상 제목과 MP4 파일을 확인해 주세요.'}, 400)
    root = storage_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = secrets.token_hex(16)+'.mp4'
    tmp, final = root/(key+'.part'), root/key
    digest = hashlib.sha256()
    committed = False
    try:
        with tmp.open('xb') as target:
            os.chmod(tmp, 0o600)
            first = h.rfile.read(min(32,n))
            if first[4:8] != b'ftyp':
                raise ValueError('MP4 파일을 확인해 주세요.')
            target.write(first);digest.update(first);remaining=n-len(first)
            while remaining:
                block=h.rfile.read(min(CHUNK,remaining))
                if not block:
                    raise ValueError('업로드가 끊겼어요. 다시 올려 주세요.')
                target.write(block);digest.update(block);remaining-=len(block)
            target.flush();os.fsync(target.fileno())
        os.replace(tmp,final)
        result=conn.execute('INSERT INTO exclusive_videos(title,file_key,orig_name,size,sha256,created_at) VALUES (?,?,?,?,?,?) ON CONFLICT(sha256) DO NOTHING RETURNING id',
                            (title,key,name,n,digest.hexdigest(),core.now_iso())).fetchone()
        conn.commit()
        committed = True
        if result:
            vid=result['id']
        else:
            final.unlink()
            vid=conn.execute('SELECT id FROM exclusive_videos WHERE sha256=?',(digest.hexdigest(),)).fetchone()['id']
        return json_response(h, {'ok':True,'id':vid,'sha256':digest.hexdigest()}, 201)
    finally:
        if not committed and final.exists():
            final.unlink()
        if tmp.exists():
            tmp.unlink()


def stream(h, row):
    path = file_path(row)
    size=row['size'];start,end=0,size-1;status=200
    value=h.headers.get('Range')
    if value:
        m=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
        if not m or not any(m.groups()):
            return response(h,b'',416,headers=[('Content-Range',f'bytes */{size}')])
        a,b=m.groups()
        if a:
            start=int(a);end=min(int(b),size-1) if b else size-1
        else:
            suffix=int(b)
            start=max(0,size-suffix)
        if start>=size or start>end:
            return response(h,b'',416,headers=[('Content-Range',f'bytes */{size}')])
        status=206
    length=end-start+1
    h.send_response(status)
    for k,v in [('Content-Type','video/mp4'),('Content-Length',str(length)),('Content-Disposition',"attachment; filename*=UTF-8''"+quote(row['orig_name'],safe='')),('Accept-Ranges','bytes'),('Cache-Control','private, no-store'),('Referrer-Policy','no-referrer'),('X-Content-Type-Options','nosniff')]:
        h.send_header(k,v)
    if status==206:
        h.send_header('Content-Range',f'bytes {start}-{end}/{size}')
    h.end_headers()
    if h.command=='HEAD':
        return
    with path.open('rb') as source:
        source.seek(start);remaining=length
        while remaining:
            block=source.read(min(CHUNK,remaining))
            if not block:
                break
            h.wfile.write(block);remaining-=len(block)


def handle(h):
    u=urlparse(h.path)
    if not (u.path=='/videos' or u.path.startswith('/videos/') or u.path=='/op/videos' or u.path.startswith('/op/videos/')):
        return False
    conn=None
    try:
        conn=db.connect()
        admin=h._admin_ok()
        if u.path.startswith('/op/videos'):
            if not admin:
                response(h,'관리자 로그인이 필요해요.',403);return True
            if h.command=='GET' and u.path=='/op/videos':
                admin_listing(h,conn)
            elif h.command=='POST' and re.fullmatch(r'/op/videos/thumbnail/\d+',u.path):
                upload_thumbnail(h,conn,int(u.path.rsplit('/',1)[1]))
            elif h.command=='POST' and u.path=='/op/videos/upload':
                upload(h,conn)
            elif h.command=='POST' and u.path=='/op/videos/publish':
                f=fields(h);checked_csrf(h,f,'admin');row=get_video(conn,int(f['id']))
                if not row:
                    raise LookupError('영상을 찾을 수 없어요.')
                file_path(row)
                published=int(f['published'])
                if published not in (0,1):
                    raise ValueError('invalid visibility')
                conn.execute('UPDATE exclusive_videos SET published=? WHERE id=? AND claimed_at IS NULL AND claimed_by IS NULL',(published,row['id']));conn.commit()
                redirect(h,'/op/videos')
            else:
                response(h,'없는 페이지예요.',404)
            return True
        qs=parse_qs(u.query)
        submitted=fields(h) if u.path=='/videos/claim' and h.command=='POST' else {}
        token=submitted.get('t') or h._cookies().get(PARTNER_COOKIE,'')
        if u.path=='/videos' and h.command=='GET' and 't' in qs:
            token=qs['t'][0]
            if not partner(conn,token):
                raise PermissionError('내 작업실에서 다시 들어와 주세요.')
            secure='; Secure' if h.headers.get('X-Forwarded-Proto')=='https' else ''
            redirect(h,'/videos',[('Set-Cookie',f'{PARTNER_COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/videos; Max-Age=604800{secure}')]);return True
        p=partner(conn,token)
        if u.path=='/videos/catalog' and h.command=='GET':
            ids=[r['id'] for r in conn.execute('SELECT id FROM exclusive_videos WHERE published=1 AND claimed_at IS NULL AND claimed_by IS NULL').fetchall()]
            json_response(h,{'ids':ids});return True
        if re.fullmatch(r'/videos/thumb/\d+',u.path) and h.command in ('GET','HEAD'):
            row=get_video(conn,int(u.path.rsplit('/',1)[1]))
            if not row or not (admin or (row['published'] and not row['claimed_at']) or (p and row['claimed_by']==p['id'])):
                raise PermissionError('현재 받을 수 없는 영상이에요.')
            path=thumbnail_path(row)
            if path.is_symlink():
                raise PermissionError('잘못된 파일이에요.')
            response(h,path.read_bytes(),kind='image/jpeg');return True
        if re.fullmatch(r'/videos/file/\d+',u.path) and h.command in ('GET','HEAD'):
            row=get_video(conn,int(u.path.rsplit('/',1)[1]))
            if not row or not (admin or (p and row['claimed_by']==p['id'])):
                raise PermissionError('배정받은 파트너만 원본을 받을 수 있어요.')
            stream(h,row);return True
        if not p:
            page(h,'파트너 전용 영상','<div class=card><h2>내 작업실에서 들어와 주세요</h2><p>파트너 확인 후 영상을 받을 수 있어요.</p><a href=/find>내 작업실 찾기</a></div>',status=403);return True
        if u.path=='/videos' and h.command=='GET':
            listing(h,conn,p)
        elif u.path=='/videos/available' and h.command=='GET':
            ids=[r['id'] for r in conn.execute('SELECT id FROM exclusive_videos WHERE published=1 AND claimed_at IS NULL AND claimed_by IS NULL').fetchall()]
            json_response(h,{'ids':ids})
        elif u.path=='/videos/claim' and h.command=='POST':
            f=submitted;checked_csrf(h,f,token)
            row=claim(conn,int(f['id']),token,f.get('name',''))
            secure='; Secure' if h.headers.get('X-Forwarded-Proto')=='https' else ''
            cookie=('Set-Cookie',f'{PARTNER_COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/videos; Max-Age=604800{secure}')
            if 'application/json' in h.headers.get('Accept',''):
                response(h,json.dumps({'download':f"/videos/file/{row['id']}"}),kind='application/json',headers=[cookie])
            else:
                redirect(h,f"/videos/file/{row['id']}",[cookie])
        else:
            response(h,'없는 페이지예요.',404)
    except PermissionError as e:
        response(h,str(e),403)
    except LookupError as e:
        response(h,str(e),409)
    except (ValueError,KeyError,UnicodeError):
        json_response(h,{'error':'입력한 내용과 파일을 확인해 주세요.'},400)
    except FileNotFoundError:
        response(h,'원본 파일을 확인 중이에요. 잠시 후 다시 시도해 주세요.',503)
    except (BrokenPipeError,ConnectionResetError):
        pass  # The allocation remains attached to the same partner for retry.
    except (OSError, sqlite3.Error):
        json_response(h, {'error':'저장소에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.'}, 503)
    finally:
        if conn is not None:
            conn.close()
    return True
