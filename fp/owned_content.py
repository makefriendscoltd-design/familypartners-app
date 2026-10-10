"""Private, read-only library of content already assigned to a partner."""
from urllib.parse import urlencode
from . import db, video_library as vl, cardnews_library as cn

COOKIE = 'fp_owned_partner'
STYLE = '''<style>
.owned-tools{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:20px 0}.owned-tabs{display:flex;flex-wrap:wrap;gap:8px}.owned-tabs a{margin:0;padding:10px 15px;border:1px solid var(--ln);border-radius:8px;color:var(--txt);text-decoration:none;background:white}.owned-tabs a[aria-current=page]{background:var(--acc);color:white;border-color:var(--acc)}
.owned-search{display:flex;gap:8px;margin:0 0 24px}.owned-search input[type=search]{flex:1;min-width:0}.owned-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}.owned-item{background:white;border:1px solid var(--ln);border-radius:12px;overflow:hidden;min-width:0}.owned-cover{width:100%;height:190px;object-fit:contain;background:#edf1f7;display:block}.owned-info{padding:18px}.owned-info h3{font-size:17px;line-height:1.5;margin:8px 0 16px;overflow-wrap:anywhere}.owned-meta{font-size:12px;color:var(--mut)}.owned-info details{margin-top:16px}.owned-info summary{cursor:pointer;font-weight:600}.owned-info .action-primary{box-sizing:border-box;width:100%}.owned-info textarea{box-sizing:border-box;max-width:100%}.owned-empty{padding:32px;background:white;border:1px solid var(--ln);border-radius:12px}.owned-info a{overflow-wrap:anywhere}.owned-pager{display:flex;justify-content:center;gap:24px;margin:28px 0}
@media(max-width:800px){.owned-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:480px){.owned-tabs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));width:100%}.owned-tabs a{text-align:center;padding:10px 4px;font-size:13px}.owned-grid{grid-template-columns:1fr}.owned-cover{height:200px}}
</style>'''


def handle(h, qs):
    from .server import esc, shell_portal
    token = qs.get('t', [h._cookies().get(COOKIE, '')])[0]
    if not token:
        vl.redirect(h, '/find'); return
    conn = db.connect()
    try:
        partner = vl.partner(conn, token)
        if not partner:
            vl.page(h, '내가 받은 콘텐츠', '<div class=card><h2>작업실 링크를 다시 확인해 주세요</h2><a href=/find>내 작업실 찾기</a></div>', status=403)
            return
        videos = conn.execute('SELECT * FROM exclusive_videos WHERE claimed_by=? ORDER BY claimed_at DESC,id DESC', (partner['id'],)).fetchall()
        decks = conn.execute('SELECT * FROM cardnews_decks WHERE claimed_by=? ORDER BY claimed_at DESC,id DESC', (partner['id'],)).fetchall()
        kind = qs.get('kind', ['all'])[0]
        if kind not in ('all', 'video', 'cardnews'): kind = 'all'
        query = qs.get('q', [''])[0].strip()[:200]
        def link(**kwargs):
            return '/my-content?' + urlencode(dict(t=token, kind=kind, q=query, **kwargs))
        entries = [('video', r) for r in videos] + [('cardnews', r) for r in decks]
        entries.sort(key=lambda entry: (entry[1]['claimed_at'] or '', entry[1]['id']), reverse=True)
        entries = [(typ, r) for typ, r in entries if (kind == 'all' or typ == kind) and query.casefold() in r['title'].casefold()]
        pages = max(1, (len(entries) + 23) // 24)
        try: page = min(pages, max(1, int(qs.get('page', ['1'])[0])))
        except ValueError: page = 1
        body = (STYLE + vl.GALLERY_STYLE + '<section class=workflow-hero><div><p class=section-kicker>내 콘텐츠 보관함</p>'
                '<h2>내가 받은 콘텐츠</h2><p>받은 영상과 카드뉴스를 다시 내려받고, 캡션을 복사하세요.</p>'
                '</div></section><nav class="owned-tools owned-tabs" aria-label="콘텐츠 종류">')
        for value, label, count in [('all', '전체', len(videos)+len(decks)), ('video', '영상', len(videos)), ('cardnews', '카드뉴스', len(decks))]:
            href = '/my-content?' + urlencode(dict(t=token, kind=value, q=query))
            body += f"<a href='{esc(href)}'" + (' aria-current=page' if kind == value else '') + f'>{label} {count}</a>'
        body += (f"</nav><form class=owned-search method=get action=/my-content><input type=hidden name=t value='{esc(token)}'>"
                 f"<input type=hidden name=kind value='{esc(kind)}'><input type=search name=q aria-label='받은 콘텐츠 제목 검색' placeholder='제목으로 찾기' value='{esc(query)}' maxlength=200><button>검색</button></form>")
        if not entries:
            body += ('<div class=owned-empty><h3>검색 결과가 없어요</h3><p>다른 제목으로 검색해 보세요.</p></div>' if query else
                     '<div class=owned-empty><h3>아직 받은 콘텐츠가 없어요</h3><p>영상방이나 카드뉴스방에서 콘텐츠를 받으면 여기에 모여요.</p>'
                     f"<a class=lk href='/videos?t={esc(token)}'>영상 받기</a> · <a class=lk href='/cardnews?t={esc(token)}'>카드뉴스 받기</a></div>")
        body += '<div class=owned-grid>'
        for typ, row in entries[(page-1)*24:page*24]:
            prefix, label = ('videos', '영상') if typ == 'video' else ('cardnews', '카드뉴스')
            caption = vl.caption_text(conn, row['id']) if typ == 'video' else row['caption'] or ''
            body += f"<article class=owned-item data-owned='{typ}-{row['id']}'>"
            if not row['purged_at']:
                body += f"<img class=owned-cover src='/{prefix}/thumb/{row['id']}' alt='' loading=lazy>"
            body += f"<div class=owned-info><span class=owned-meta>{label} · {esc((row['claimed_at'] or '')[:10])} 받음</span><h3>{esc(row['title'])}</h3>"
            if row['purged_at']:
                body += '<p class=setup-hint>보관 기간이 지나 원본이 정리됐어요. 캡션과 기록은 확인할 수 있어요.</p>'
            else:
                body += f"<a class=action-primary href='/{prefix}/file/{row['id']}' download>{'영상' if typ == 'video' else '카드뉴스 ZIP'} 다시 받기</a>"
            body += vl.source_link(row)
            if caption:
                body += '<details><summary>캡션 보기 · 복사</summary>' + vl.caption_box(caption) + '</details>'
            body += '</div></article>'
        body += '</div>'
        if pages > 1:
            body += '<nav class=owned-pager aria-label="목록 페이지">'
            if page > 1: body += f"<a href='{esc(link(page=page-1))}'>← 이전</a>"
            body += f'<span>{page} / {pages}</span>'
            if page < pages: body += f"<a href='{esc(link(page=page+1))}'>다음 →</a>"
            body += '</nav>'
        secure = '; Secure' if h.headers.get('X-Forwarded-Proto') == 'https' else ''
        cookies = [('Set-Cookie', f'{name}={token}; HttpOnly; SameSite=Lax; Path={path}; Max-Age=604800{secure}') for name,path in [(COOKIE,'/my-content'),(vl.PARTNER_COOKIE,'/videos'),(cn.COOKIE,'/cardnews')]]
        vl.response(h, shell_portal('내가 받은 콘텐츠', '', body + vl.CAPTION_SCRIPT, token), headers=cookies)
    finally:
        conn.close()
