"""패밀리 파트너스 운영 대시보드 (로컬 웹 서버).

표준 라이브러리 http.server 만 사용. 외부 의존성 0.
읽기 전용 대시보드 — 오늘 챌린지 보드 / 리마인더 / 월 정산 / 온보딩 키트 / 오늘 글감.
실제 변경(강퇴 집행 등)은 안전하게 CLI(`python -m fp enforce --yes`)로 둔다.

실행:  python -m fp serve --port 8000  →  http://localhost:8000
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import io
import mimetypes
import os
import re
from http.client import HTTPMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from . import core, db, messages, onboard, ppurio, products

LIB_DIR = db.ROOT / "assets" / "library"
GUIDE_DIR = db.ROOT / "assets" / "guide"

# Vercel 서버리스 요청 본문 한도(4.5MB)보다 약간 아래로 — 초과 시 업로드 거부.
MAX_UPLOAD = 4_000_000


def save_upload(conn, title, category, fname, data) -> int:
    """업로드 파일을 DB(base64)에 저장하고 library id 반환 — 서버리스에서도 영구 보존.

    로컬에서도 동일하게 DB 저장(디스크 의존 X). 큰 파일은 호출 전에 거른다.
    """
    b64 = base64.b64encode(data).decode("ascii")
    return core.add_library_file(conn, title, category, None, fname, len(data), b64)

# 관리자 인증 — 서명 쿠키 (서버리스/멀티인스턴스 안전, 메모리 세션 불필요)
COOKIE = "fp_admin"


def _secret() -> bytes:
    return (os.environ.get("FP_SECRET") or "fp-local-dev-secret-change-me").encode()


def _admin_token() -> str:
    return hmac.new(_secret(), b"admin-v1", hashlib.sha256).hexdigest()
# 로그인 없이 접근 가능한 경로
PUBLIC_GET = {"/login", "/logout", "/join", "/me", "/wall", "/files", "/feed",
              "/guide", "/find", "/favicon.ico"}
PUBLIC_POST = {"/login", "/join", "/submit", "/find", "/me/save", "/me/links"}


def guide_img(name: str, caption: str) -> str:
    """가이드 캡처 슬롯 — assets/guide/<name> 있으면 이미지, 없으면 안내 박스."""
    if (GUIDE_DIR / name).exists():
        return (f"<figure style='margin:12px 0'><img src='/guide-img/{name}' "
                f"style='max-width:100%;border:1px solid var(--ln);border-radius:8px'>"
                f"<figcaption class=empty>{esc(caption)}</figcaption></figure>")
    return (f"<div class=card style='border-style:dashed;margin:12px 0'>"
            f"<div class=empty>📷 캡처 자리 — {esc(caption)}<br>"
            f"<code>assets/guide/{esc(name)}</code> 에 이미지를 넣으면 여기 표시됩니다.</div></div>")


def _q(s: str) -> str:
    return quote(s, safe="")


def parse_multipart(raw: bytes, boundary: str):
    """multipart/form-data 최소 파서 (텍스트 필드 + 다중 파일). 표준 라이브러리만.

    files 는 {필드명: [(파일명, 데이터), ...]} — 같은 이름으로 여러 파일(multiple) 허용.
    """
    fields, files = {}, {}
    delim = b"--" + boundary.encode()
    for chunk in raw.split(delim):
        if not chunk or chunk[:2] == b"--":
            continue
        if chunk[:2] == b"\r\n":
            chunk = chunk[2:]
        if chunk[-2:] == b"\r\n":
            chunk = chunk[:-2]
        if b"\r\n\r\n" not in chunk:
            continue
        head, data = chunk.split(b"\r\n\r\n", 1)
        h = head.decode("utf-8", "replace")
        nm = re.search(r'name="([^"]*)"', h)
        if not nm:
            continue
        fn = re.search(r'filename="([^"]*)"', h)
        if fn:
            if fn.group(1):  # 빈 파일 입력(선택 안 함)은 건너뜀
                files.setdefault(nm.group(1), []).append((fn.group(1), data))
        else:
            fields[nm.group(1)] = data.decode("utf-8", "replace")
    return fields, files


CSS = """
:root{--bg:#eef3fb;--card:#ffffff;--mut:#5d6b80;--ln:#dce5f2;
--grn:#15a34a;--yel:#b45309;--red:#dc2626;--txt:#0f2440;--acc:#2563eb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Malgun Gothic",sans-serif}
header{padding:18px 24px;border-bottom:1px solid var(--ln);display:flex;
align-items:center;gap:18px;position:sticky;top:0;background:var(--card);z-index:2}
header h1{font-size:17px;margin:0;color:var(--acc)}nav a{color:var(--mut);text-decoration:none;
margin-right:16px;font-size:14px}nav a:hover{color:var(--acc)}
main{max-width:920px;margin:0 auto;padding:24px}
.card{background:var(--card);border:1px solid var(--ln);border-radius:12px;
padding:18px 20px;margin-bottom:18px;box-shadow:0 1px 3px rgba(15,36,64,.05)}
.card h2{margin:0 0 12px;font-size:15px;display:flex;justify-content:space-between}
.pill{font-size:12px;color:var(--mut)}
.row{display:flex;align-items:center;gap:10px;padding:7px 0;border-top:1px solid var(--ln)}
.row:first-of-type{border-top:none}.nm{font-weight:600;min-width:90px}
.hd{color:var(--mut);font-size:13px;min-width:120px}.meta{color:var(--mut);font-size:13px;margin-left:auto}
.b-grn{color:var(--grn)}.b-yel{color:var(--yel)}.b-red{color:var(--red)}
.empty{color:var(--mut);font-size:14px;padding:6px 0}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{text-align:left;padding:7px 8px;border-top:1px solid var(--ln)}
th{color:var(--mut);font-weight:500}.num{text-align:right;font-variant-numeric:tabular-nums}
pre{white-space:pre-wrap;background:#f3f7fd;border:1px solid var(--ln);border-radius:8px;
padding:14px;font:13px/1.5 ui-monospace,Consolas,monospace;color:var(--txt)}
.kpi{display:flex;gap:14px;margin-bottom:18px}.kpi .card{flex:1;text-align:center;margin:0}
.kpi .big{font-size:30px;font-weight:700;color:var(--acc)}.kpi .lb{color:var(--mut);font-size:13px}
input,button,select{font:14px inherit;background:#fff;color:var(--txt);
border:1px solid var(--ln);border-radius:7px;padding:7px 10px}
button{color:#fff;background:var(--acc);border-color:var(--acc);cursor:pointer;font-weight:600}
button:hover{filter:brightness(1.08)}
form{display:flex;gap:8px;margin-top:10px}a.lk{color:var(--acc);text-decoration:none}
a.lk:hover{text-decoration:underline}
textarea{font:14px inherit;background:#fff;color:var(--txt);border:1px solid var(--ln);
border-radius:7px;padding:8px 10px;width:100%;resize:vertical}
.media-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-top:12px}
.media-grid.solo{grid-template-columns:1fr}
img.media,video.media{width:100%;border-radius:10px;border:1px solid var(--ln);
background:#000;display:block;max-height:520px;object-fit:contain}
.video-wrap{position:relative;padding-top:56.25%;border-radius:10px;overflow:hidden;border:1px solid var(--ln)}
.video-wrap iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
.feed-date{font-size:13px;color:var(--acc);font-weight:600}
button.ghost{background:#fff;color:var(--acc);border:1px solid var(--ln);padding:5px 12px;font-size:13px}
.dl-cta{grid-column:1/-1;border:2px dashed var(--acc);border-radius:10px;padding:14px;text-align:center;background:#f3f7fd}
.dl-btn{display:inline-block;background:var(--acc);color:#fff;padding:12px 20px;border-radius:9px;
font-weight:700;text-decoration:none;font-size:15px}
.dl-btn:hover{filter:brightness(1.08)}
.dl-warn{margin:10px 0 0;color:var(--red);font-size:13px;font-weight:600}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(148px,1fr));gap:8px;margin-top:6px}
.tile{position:relative;aspect-ratio:1/1;border-radius:12px;overflow:hidden;
border:1px solid var(--ln);text-decoration:none;display:block;
background:#d7e2f3 center/cover no-repeat;transition:transform .08s}
.tile:hover{transform:scale(1.015)}
.tile.txt{background:linear-gradient(135deg,#eef4fc,#d8e6fb)}
.tile.txt.t-ai{background:linear-gradient(135deg,#eafaf0,#cdeedd)}
.tile.txt.t-eg{background:linear-gradient(135deg,#fdf3e3,#f7e4c4)}
.tile .ov{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:flex-end;
padding:10px;background:linear-gradient(to top,rgba(8,20,40,.72),rgba(8,20,40,0) 58%)}
.tile.txt .ov{background:none;justify-content:flex-start;padding:38px 13px 13px}
.tile .tt{color:#fff;font-weight:700;font-size:13px;line-height:1.35;
display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.tile.txt .tt{color:var(--txt);font-size:15px;-webkit-line-clamp:2}
.tile .bd{margin-top:8px;color:var(--mut);font-size:12px;line-height:1.45;white-space:normal;
display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.tile .dd{position:absolute;top:8px;left:8px;background:rgba(8,20,40,.6);color:#fff;
font-size:12px;font-weight:700;padding:3px 9px;border-radius:20px}
.tile.txt .dd{background:var(--acc)}
.tile .tag{position:absolute;top:7px;right:9px;font-size:15px;filter:drop-shadow(0 1px 2px rgba(0,0,0,.4))}
.lightbox{position:fixed;inset:0;background:rgba(8,20,40,.72);display:none;z-index:50;
overflow:auto;padding:28px 14px}
.lightbox:target{display:block}
.lb-inner{background:var(--card);border-radius:14px;max-width:580px;margin:0 auto;
padding:18px 20px 24px;position:relative}
.lb-close{position:absolute;top:6px;right:14px;font-size:26px;line-height:1;
color:var(--mut);text-decoration:none;font-weight:700}
.lb-close:hover{color:var(--red)}
.lb-inner h2{display:block;margin:2px 40px 8px 0;font-size:16px}
"""


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def shell(title: str, body: str) -> bytes:
    nav = ('<a href="/">대시보드</a><a href="/#글감"><b>✍️글감쓰기</b></a>'
           '<a href="/people">인원</a>'
           '<a href="/review">검수</a><a href="/board">랭킹</a>'
           '<a href="/library">자료실</a><a href="/feed">글감피드</a>'
           '<a href="/onboard">온보딩</a><a href="/wall">인증보드</a>'
           '<a href="/logout" style="margin-left:auto">로그아웃</a>')
    doc = (f"<!doctype html><html lang=ko><head><meta charset=utf-8>"
           f"<meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>{esc(title)}</title><style>{CSS}</style></head><body>"
           f"<header><a href='/' style='text-decoration:none'><h1>🏠 패밀리 파트너스</h1></a>"
           f"<nav>{nav}</nav></header>"
           f"<main>{body}</main></body></html>")
    return doc.encode("utf-8")


def _no_db() -> str:
    return ("<div class=card><h2>DB가 없습니다</h2>"
            "<p class=empty>터미널에서 <code>python -m fp init</code> 실행 후 새로고침하세요.</p></div>")


def _ipt(name, ph, req=False, grow=False):
    return (f"<input name={name} placeholder='{esc(ph)}'"
            f"{' required' if req else ''}{' style=flex:1' if grow else ''}>")


def quick_actions() -> str:
    return (
        "<div class=card><h2>빠른 작업 <span class=pill>터미널 없이 여기서</span></h2>"
        f"<form method=post action=/op/partner>{_ipt('name','파트너 이름',True)}"
        f"{_ipt('handle','@핸들(비우면 자동발급)')}{_ipt('contact','연락처',False,True)}"
        f"<button>① 파트너 등록</button></form>"
        "<p class=empty>판매링크 3개는 등록 후 <b>인원→상세→정보 수정</b>에서 넣으면 키트에 자동 삽입됩니다.</p>"
        f"<form method=post action=/op/submit>{_ipt('who','이름/@핸들',True)}"
        f"{_ipt('url','게시물 링크',True,True)}<button>② 제출 입력(출석)</button></form>"
        "<p class=empty style='margin-top:10px'>"
        "<a class=lk href=/reminders>✉ 보낼 메시지(리마인더)</a> · "
        "<a class=lk href=/enforce>⛔ 강퇴 집행</a> · "
        "<a class=lk href=/onboard>📋 온보딩 키트</a> · "
        "<a class=lk href=/library>📁 자료실(사진·영상)</a></p></div>"
    )


def drop_form() -> str:
    """대시보드: 오늘 글감 등록(본문 + 사진·영상 직접 업로드 + 외부 링크)."""
    return (
        "<div class=card id=글감 style='border:2px solid var(--acc)'>"
        "<h2>📝 오늘 글감 올리기 <span class=pill>여기에 매일 콘텐츠 작성 → 피드에 게시</span></h2>"
        "<form method=post action=/op/drop enctype='multipart/form-data' "
        "style='flex-direction:column;align-items:stretch'>"
        "<input name=title placeholder='제목 (예: 6/8 가족여행 후기 글감)' required>"
        "<textarea name=body rows=6 placeholder='본문/캡션 — 파트너가 복사해서 베끼거나 변형해 씁니다'></textarea>"
        "<label class=empty style='margin-top:4px'>📷 사진·짧은 영상 직접 첨부(여러 개 · "
        "<b>개당 4MB 이하</b>)</label>"
        "<input type=file name=media accept='image/*,video/*' multiple>"
        "<textarea name=urls rows=2 placeholder='긴 영상은 여기에 링크 — 유튜브·드라이브 등 한 줄에 하나'></textarea>"
        "<div style='display:flex;gap:8px;align-items:center;margin-top:8px'>"
        "<select name=dtype><option value=marketing>마케팅 자료</option>"
        "<option value=ai>AI 콘텐츠</option><option value=evergreen>상시 자료</option></select>"
        "<input name=drop_date placeholder='날짜 YYYY-MM-DD (비우면 오늘)' style='flex:1'>"
        "<button>글감 게시</button></div></form>"
        "<p class=empty>게시 즉시 <a class=lk href='/feed'>글감 피드</a>에 올라가고, 늦게 들어온 분도 전부 봅니다. "
        "사진·영상은 자료실에도 자동 저장됩니다.</p></div>"
    )


def view_dashboard(qs) -> str:
    flash = ""
    if qs.get("msg"):
        flash = (f"<div class=card style='border-color:var(--grn)'>"
                 f"<b class=b-grn>{esc(qs['msg'][0])}</b></div>")
    conn = db.connect()
    as_of = core.today()
    b = core.daily_board(conn, as_of)

    def rows(items, cls, meta_fn):
        if not items:
            return "<div class=empty>없음</div>"
        out = []
        for s in items:
            out.append(f"<div class=row>"
                       f"<a class='nm lk {cls}' href='/partner?id={s.row['id']}'>{esc(s.name)}</a>"
                       f"<span class=hd>{esc(s.handle or '-')}</span>"
                       f"<span class=meta>{meta_fn(s)}</span></div>")
        return "".join(out)

    # 오늘(자정~다음날 자정, KST) 기준 단일 판정:
    #   완료 = 오늘 제출함(=미션완료) / 미이행 = 오늘 안 한 활성자 전체
    done_list = b["done"]
    undone_list = b["at_risk"] + b["kick"]   # 오늘 미제출 활성자 전부
    kick_n = len(b["kick"])                   # 그중 어제도 빵꾸(강퇴 대상)

    kpi = (f"<div class=kpi>"
           f"<div class=card><div class='big b-grn'>{len(done_list)}</div>"
           f"<div class=lb>오늘 완료(미션완료)</div></div>"
           f"<div class=card><div class='big b-yel'>{len(undone_list)}</div>"
           f"<div class=lb>미이행자</div></div>"
           f"<div class=card><div class=big>{b['active_count']}</div>"
           f"<div class=lb>활성 파트너</div></div>"
           f"</div>")

    # 오늘 제출한 콘텐츠 링크(파트너별, 유효 제출만) — 완료 명단에서 바로 열기
    url_map = {}
    for sub in core.submissions_on(conn, as_of):
        if sub["valid"]:
            url_map.setdefault(sub["partner_id"], []).append(sub["url"])

    def done_meta(s):
        urls = url_map.get(s.row["id"], [])
        if not urls:
            return f"🔥 {s.streak}일 연속"
        extra = f" (+{len(urls) - 1})" if len(urls) > 1 else ""
        link = (f"<a class=lk href='{esc(urls[-1])}' target=_blank rel=noopener>"
                f"🔗 콘텐츠 열기{extra}</a>")
        return f"🔥 {s.streak}일 연속 · {link}"

    def undone_meta(s):
        badge = " <span class=b-red>⚠️어제 빵꾸</span>" if s.missed_yesterday else ""
        tgt = esc(s.row["contact"] or s.handle or "-")
        return f"{s.streak}일 연속 · {tgt}{badge}"

    done = rows(done_list, "b-grn", done_meta)
    undone = rows(undone_list, "b-yel", undone_meta)
    scheduled = core.scheduled_drops(conn, as_of)
    conn.close()

    # 예약된 글감 큐 — 미래 날짜로 등록된 글감(날짜 되면 자동 공개)
    sched_card = ""
    if scheduled:
        items = []
        for r in scheduled:
            try:
                dd = (core.parse_date(r["drop_date"]) - as_of).days
            except Exception:
                dd = 0
            items.append(
                f"<div class=row><span class=hd>📅 {esc(r['drop_date'])} "
                f"<b class=b-yel>D-{dd}</b></span>"
                f"<span class=nm style='min-width:0;margin-left:6px'>{esc(r['title'])}</span>"
                f"<span class=pill style='margin-left:auto'>"
                f"{DROP_TYPE.get(r['dtype'], r['dtype'])}</span>"
                f"<form method=post action=/op/drop-del style='display:inline;margin:0 0 0 10px' "
                f"onsubmit=\"return confirm('이 예약 글감을 삭제할까요?')\">"
                f"<input type=hidden name=id value={r['id']}>"
                f"<input type=hidden name=back value='/'>"
                f"<button class=ghost style='color:var(--red)'>삭제</button></form></div>")
        sched_card = (
            "<div class=card style='border-color:var(--yel)'>"
            f"<h2 class=b-yel>🗓️ 예약된 글감 — {len(scheduled)}건 "
            "<span class=pill>날짜 되면 자동 공개</span></h2>"
            "<p class=empty style='margin:-4px 0 8px'>미래 날짜로 등록된 글감입니다. "
            "그 날짜 전까지는 피드·작업실에 <b>안 보이고</b>, 당일 자동으로 공개됩니다.</p>"
            + "".join(items) + "</div>")

    enforce_note = ("" if not kick_n else
                    f"<p class=empty>⚠️ 어제 빵꾸 {kick_n}명(강퇴 대상) — "
                    "<a class=lk href='/enforce'>강퇴 집행</a> 또는 터미널 "
                    "<code>python -m fp enforce --yes</code></p>")
    return (flash + drop_form() + sched_card + quick_actions() +
            f"<p class=pill>{b['date']} 기준 (자정~다음날 자정, KST)</p>{kpi}"
            f"<div class=card><h2>✅ 오늘 완료(미션완료) — {len(done_list)}명</h2>{done}</div>"
            f"<div class=card><h2>⏳ 미이행자 — 오늘 아직 미제출 {len(undone_list)}명</h2>{undone}"
            f"{enforce_note}"
            f"<p class=empty>리마인더 문구: <code>python -m fp reminders</code></p></div>")


def view_settle(qs) -> str:
    conn = db.connect()
    as_of = core.today()
    m = (qs.get("month", [None])[0]) or as_of.strftime("%Y-%m")
    y, mo = (int(x) for x in m.split("-"))
    rep = core.settle(conn, y, mo)
    conn.close()
    if not rep:
        return f"<div class=card><h2>{m} 월 정산</h2><div class=empty>해당 월 매출 없음.</div></div>"
    body = ["<table><tr><th>이름</th><th>상태</th><th class=num>건수</th>"
            "<th class=num>매출</th><th class=num>쉐어정산</th><th>비고</th></tr>"]
    tg = ts = 0
    for r in sorted(rep, key=lambda x: -x["gross"]):
        note = "<span class=b-red>몰수</span>" if r["forfeited"] else ""
        body.append(f"<tr><td>{esc(r['name'])}</td><td>{esc(r['status'])}</td>"
                    f"<td class=num>{r['count']}</td>"
                    f"<td class=num>{products.won(r['gross'])}</td>"
                    f"<td class=num>{products.won(r['share'])}</td><td>{note}</td></tr>")
        tg += r["gross"]; ts += r["share"]
    body.append(f"<tr><th>합계</th><th></th><th></th>"
                f"<th class=num>{products.won(tg)}</th>"
                f"<th class=num>{products.won(ts)}</th><th></th></tr></table>")
    return f"<div class=card><h2>{m} 월 정산</h2>{''.join(body)}</div>"


IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".avif"}
VID_EXT = {".mp4", ".mov", ".webm", ".m4v", ".ogv"}


def _youtube_id(url: str):
    m = re.search(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|shorts/|live/))([\w-]{6,})", url)
    return m.group(1) if m else None


def render_asset(conn, a: str) -> str:
    """글감 첨부 1줄 → HTML. `m:<libid>`=업로드 미디어, 그 외=외부 URL(유튜브/드라이브 등)."""
    a = (a or "").strip()
    if not a:
        return ""
    if a.startswith("m:") and a[2:].isdigit():
        row = core.get_library(conn, int(a[2:]))
        if not row or row["kind"] != "file":
            return ""
        ext = os.path.splitext((row["orig_name"] or "").lower())[1]
        src = f"/m/{row['id']}"
        if ext in IMG_EXT:
            return f"<img class=media src='{src}' loading=lazy alt='{esc(row['orig_name'])}'>"
        if ext in VID_EXT:
            return f"<video class=media src='{src}' controls preload=metadata></video>"
        return (f"<a class=lk href='/dl/{row['id']}'>⬇ {esc(row['orig_name'])} "
                f"({core.human_size(row['size'])})</a>")
    yt = _youtube_id(a)
    if yt:
        return (f"<div class=video-wrap><iframe src='https://www.youtube.com/embed/{esc(yt)}' "
                f"allowfullscreen allow='accelerometer;encrypted-media;picture-in-picture'></iframe></div>")
    ext = os.path.splitext(a.lower().split("?")[0])[1]
    if ext in IMG_EXT:
        return f"<img class=media src='{esc(a)}' loading=lazy>"
    if ext in VID_EXT:
        return f"<video class=media src='{esc(a)}' controls preload=metadata></video>"
    is_drive = "drive.google" in a or "docs.google" in a
    label = "드라이브 열어서 영상/사진 다운로드" if is_drive else "링크 열어서 다운로드"
    return ("<div class=dl-cta>"
            f"<a href='{esc(a)}' target=_blank rel=noopener class=dl-btn>⬇ {esc(label)}</a>"
            "<p class=dl-warn>⚠️ <b>여기 눌러 드라이브 열고 영상(사진)을 꼭 다운로드</b>하세요. "
            "텍스트만 베끼고 영상 안 올리면 안 됩니다!</p></div>")


COPY_JS = (
    "<script>function fpCopy(b){var t=b.parentNode.querySelector('pre');"
    "if(!t)return;var s=t.innerText;"
    "var o=b.getAttribute('data-o');if(o===null){o=b.textContent;b.setAttribute('data-o',o);}"
    "function ok(){b.textContent='✅ 복사됨';setTimeout(function(){b.textContent=o},1800);}"
    "(navigator.clipboard?navigator.clipboard.writeText(s):Promise.reject())"
    ".then(ok,function(){"
    "var r=document.createRange();r.selectNodeContents(t);"
    "var sel=getSelection();sel.removeAllRanges();sel.addRange(r);"
    "try{document.execCommand('copy');ok();}catch(e){}});}</script>"
)

DROP_TYPE = {"ai": "AI 콘텐츠", "marketing": "마케팅 자료", "evergreen": "상시 자료"}
TILE_BG = {"ai": "t-ai", "marketing": "", "evergreen": "t-eg"}


def _short_date(date_str: str) -> str:
    try:
        d = core.parse_date(date_str)
        return f"{d.month}/{d.day}"
    except Exception:
        return esc(date_str)


def _drop_thumb(conn, assets_text: str | None) -> str | None:
    """글감 첨부에서 갤러리 썸네일로 쓸 이미지 URL 1개 추출(없으면 None)."""
    for a in (assets_text or "").splitlines():
        a = a.strip()
        if not a:
            continue
        if a.startswith("m:") and a[2:].isdigit():
            row = core.get_library(conn, int(a[2:]))
            if row and row["kind"] == "file":
                ext = os.path.splitext((row["orig_name"] or "").lower())[1]
                if ext in IMG_EXT:
                    return f"/m/{row['id']}"
            continue
        yt = _youtube_id(a)
        if yt:
            return f"https://img.youtube.com/vi/{yt}/hqdefault.jpg"
        ext = os.path.splitext(a.lower().split("?")[0])[1]
        if ext in IMG_EXT:
            return a
    return None


def view_feed(qs) -> bytes:
    """공개 글감 피드 — 지난 콘텐츠 전체(최신순). 로그인 없이 누구나 열람."""
    conn = db.connect()
    rows = core.published_drops(conn, core.today(), 365)
    tiles, modals = [], []
    for r in rows:
        did = f"d{r['id']}"
        thumb = _drop_thumb(conn, r["assets"])
        has_assets = bool((r["assets"] or "").strip())
        if thumb:
            tcls = "tile"
            style = f" style=\"background-image:url('{esc(thumb)}')\""
            tag = ""
            inner = f"<span class=tt>{esc(r['title'])}</span>"
        else:
            tcls = f"tile txt {TILE_BG.get(r['dtype'], '')}".rstrip()
            style = ""
            tag = "<span class=tag>🎬</span>" if has_assets else "<span class=tag>📝</span>"
            prev = " ".join((r["body"] or "").split())
            prev = (prev[:80] + "…") if len(prev) > 80 else prev
            inner = (f"<span class=tt>{esc(r['title'])}</span>"
                     + (f"<span class=bd>{esc(prev)}</span>" if prev else ""))
        tiles.append(
            f"<a class='{tcls}' href='#{did}'{style}>"
            f"<span class=dd>{_short_date(r['drop_date'])}</span>{tag}"
            f"<span class=ov>{inner}</span></a>")

        media = ""
        if r["assets"]:
            parts = [render_asset(conn, a) for a in r["assets"].splitlines()]
            parts = [p for p in parts if p]
            if parts:
                solo = " solo" if len(parts) == 1 else ""
                media = f"<div class='media-grid{solo}'>{''.join(parts)}</div>"
        body = (f"<pre>{esc(r['body'])}</pre>"
                "<button type=button class=ghost onclick=fpCopy(this)>📋 본문 복사</button>"
                ) if (r["body"] or "").strip() else ""
        modals.append(
            f"<div id={did} class=lightbox><div class=lb-inner>"
            f"<a class=lb-close href='#' title='닫기'>✕</a>"
            f"<h2>{esc(r['title'])} "
            f"<span class=pill>{esc(DROP_TYPE.get(r['dtype'], r['dtype']))}</span></h2>"
            f"<div class=feed-date>📅 {esc(r['drop_date'])}</div>"
            f"{body}{media}</div></div>")
    conn.close()

    body_html = (f"<div class=gallery>{''.join(tiles)}</div>{''.join(modals)}" if tiles
                 else "<div class=card><div class=empty>아직 올라온 글감이 없습니다.</div></div>")
    intro = ("<div class=card style='border-color:var(--acc)'><h2>📚 글감 피드</h2>"
             "<p class=empty>매일 올라오는 콘텐츠 보관함입니다. <b>늦게 들어와도 1일차부터 전부</b> 볼 수 있어요. "
             "<b>썸네일을 누르면</b> 본문(복사)·사진·영상이 펼쳐집니다.</p></div>")
    token = (qs.get("t", [None])[0])
    return shell_portal("글감 피드", "매일 콘텐츠 보관함",
                        COPY_JS + intro + body_html, token)


def view_drop(qs) -> str:
    conn = db.connect()
    d = core.parse_date(qs.get("date", [None])[0])
    rows = core.drops_on(conn, d)
    if not rows:
        conn.close()
        return (f"<div class=card><h2>오늘의 글감 — {core.iso(d)}</h2>"
                f"<div class=empty>등록된 글감 없음 — 대시보드에서 ‘📝 오늘 글감 올리기’로 등록하세요.</div></div>")
    out = []
    for r in rows:
        media = ""
        if r["assets"]:
            parts = [render_asset(conn, a) for a in r["assets"].splitlines()]
            parts = [p for p in parts if p]
            if parts:
                solo = " solo" if len(parts) == 1 else ""
                media = f"<div class='media-grid{solo}'>{''.join(parts)}</div>"
        out.append(f"<div class=card><h2>{esc(r['title'])}"
                   f"<span class=pill>{r['drop_date']} · {DROP_TYPE.get(r['dtype'], r['dtype'])}</span></h2>"
                   f"<pre>{esc(r['body'] or '')}</pre>{media}</div>")
    conn.close()
    return "".join(out)


def view_onboard(qs) -> str:
    who = qs.get("who", [None])[0]
    conn = db.connect()
    plist = conn.execute("SELECT name, handle FROM partners ORDER BY id").fetchall()
    opts = "".join(f"<option value='{esc(p['name'])}'"
                   f"{' selected' if who == p['name'] else ''}>{esc(p['name'])}</option>"
                   for p in plist)
    form = (f"<div class=card><h2>온보딩 키트</h2>"
            f"<form method=get action=/onboard>"
            f"<select name=who><option value=''>— 파트너 선택 —</option>{opts}</select>"
            f"<button>키트 생성</button></form></div>")
    kit_html = ""
    if who:
        p = core.find_partner(conn, who)
        if p:
            kit = onboard.build_kit(p["name"], p["referral_code"], p["portal_token"],
                                    p["handle"], onboard.links_of(p), p["openchat_url"], p["partner_type"])
            kit_html = f"<div class=card><pre>{esc(kit)}</pre></div>"
        else:
            kit_html = "<div class=card><div class=empty>파트너를 찾을 수 없음</div></div>"
    conn.close()
    return form + kit_html


# =========================================================================== #
# 파트너 포털 (토큰 링크 · 비번 없음 · 포털에서 직접 제출)
# =========================================================================== #
def shell_portal(title: str, sub: str, body: str, token: str | None = None) -> bytes:
    # 토큰이 있으면(=내 작업실에서 온 경우) 작업실 링크가 토큰을 유지하도록 함
    t = ("?t=" + _q(token)) if token else ""
    home = f"/me{t}" if token else "/feed"
    me_link = (f"<a href='/me{t}'>🏠 내 작업실</a>" if token
               else "<a href='/find'>🏠 내 작업실 찾기</a>")
    nav = (me_link
           + f"<a href='/guide{t}'>📖 사용법</a>"
           + f"<a href='/feed{t}'>📚 글감 피드</a>"
           + f"<a href='/files{t}'>📁 자료실</a>"
           + f"<a href='/wall{t}'>🏆 인증보드</a>")
    doc = (f"<!doctype html><html lang=ko><head><meta charset=utf-8>"
           f"<meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>{esc(title)}</title><style>{CSS}</style></head><body>"
           f"<header><a href='{home}' style='text-decoration:none'>"
           f"<h1>🤝 패밀리 파트너스</h1></a>"
           f"<span class=pill>{esc(sub)}</span>"
           f"<nav style='margin-left:auto'>{nav}</nav></header>"
           f"<main>{body}</main></body></html>")
    return doc.encode("utf-8")


NOTION_URL = "https://makefriends.notion.site/372b31f1da558017943ce42c44793754"


def view_join(qs) -> bytes:
    err = ""
    if qs.get("e"):
        err = ("<div class=card style='border-color:var(--red)'>"
               "<b class=b-red>성함은 필수입니다.</b></div>")
    elif qs.get("blocked"):
        err = ("<div class=card style='border-color:var(--red)'>"
               "<b class=b-red>강퇴 이력이 있어 재참여가 불가합니다.</b></div>")
    elif qs.get("dup"):
        err = ("<div class=card style='border-color:var(--red)'>"
               "<b class=b-red>이미 등록된 성함입니다.</b> 본인이면 운영자에게 작업실 링크를 요청하세요. "
               "동명이인이면 성함 뒤에 구분을 붙여 등록하세요(예: 박상철A).</div>")
    rules = (
        "<div class=card style='border-color:var(--yel)'>"
        "<h2 class=b-yel>📢 버는 만큼 빡셉니다</h2>"
        "<p class=empty>참여비 없는 자율 프로그램입니다. 시작 전에 꼭 읽어주세요.</p></div>"

        "<div class=card><h2>1. 매일 업로드 해야 합니다</h2>"
        "<p>· 누락 시 <b class=b-red>즉시 강퇴</b>됩니다.</p>"
        "<p>· 개인 사정 안 봐드립니다.</p>"
        "<p>· 강퇴되면 다시 참여 불가합니다.</p></div>"

        "<div class=card><h2>2. 할 일은 단순합니다</h2>"
        "<p>· 스레드 아이디를 만든다</p>"
        "<p>· 올리라는 콘텐츠를 올린다 <span class=empty>(콘텐츠 소스 다 드립니다)</span></p>"
        "<p>· 본인 카톡방으로 유입시킨다 <span class=empty>(공지 설정 다 해드립니다)</span></p>"
        "<p>· 질의에 답변만 해준다 <span class=empty>(모르면 대신 답변해드립니다)</span></p>"
        "<p>· 판매되면 정산받는다</p></div>"

        "<div class=card><h2>3. 주마다 피드백합니다</h2>"
        "<p>· 라이브 콘텐츠 피드백 · 자세한 일정은 추후 공지</p></div>"

        "<div class=card><h2>4. 매월 정산합니다</h2>"
        "<p>· 월초마다 판매된 만큼 정산</p>"
        f"<p>· 정산 비율은 <a class=lk href='{NOTION_URL}' target=_blank>여기(노션 페이지)</a>에 기재</p></div>"
    )
    form = (
        f"{err}<div class=card style='border-color:var(--acc)'><h2>참여 등록</h2>"
        "<p class=empty>카톡방에 입장하셨다면 아래로 등록하세요. 등록하면 "
        "<b>나만의 작업실 링크</b>(비번 없는 개인 페이지)가 생깁니다.</p>"
        "<p class=empty><b>내 유형</b>을 골라주세요 — 수강한 프로그램에 따라 파는 상품이 달라요:</p>"
        "<form method=post action=/join style='flex-wrap:wrap'>"
        "<select name=ptype style=flex:1>"
        "<option value=family>메이크패밀리 파트너스 — 기존 패밀리회원(강의 3종)</option>"
        "<option value=aimax>AIMAX 파트너스 — 창업프로그램 수강생(AI 직원)</option>"
        "<option value=both>둘 다 수강 — 통합(전부 판매)</option>"
        "</select>"
        "<input name=name placeholder='성함 (필수)' required>"
        "<input name=contact placeholder='연락처 (필수)' required style=flex:1>"
        "<button>등록하고 내 작업실 링크 받기</button></form>"
        "<p class=empty>※ 스레드 아이디는 등록 후 작업실 STEP 1에서 직접 만들어 입력합니다.<br>"
        "※ 운영 단톡방 닉네임은 <b>성함+연락처 뒤 4자리</b>로 변경해주세요. (예: 홍길동5678)</p></div>"
    )
    closing = ("<div class=card><p class=empty>안 하실 분은 나가셔도 됩니다. 감사합니다.<br>"
               "이미 등록했는데 작업실 링크를 잃어버렸다면 → "
               "<a class=lk href='/find'>내 작업실 찾기</a></p></div>")
    return shell_portal("합류", "참여 안내", rules + form + closing)


def view_find(qs) -> bytes:
    nf = ("<div class=card style='border-color:var(--red)'>"
          "<b class=b-red>일치하는 정보가 없습니다.</b> 등록 시 넣은 성함·연락처를 확인하거나 "
          "운영자에게 문의하세요.</div>") if qs.get("nf") else ""
    body = (nf + "<div class=card><h2>내 작업실 찾기</h2>"
            "<p class=empty>작업실 링크를 잃어버렸나요? 등록한 <b>성함과 연락처</b>로 다시 들어갈 수 있습니다. "
            "(비밀번호 없음)</p>"
            "<form method=post action=/find>"
            "<input name=name placeholder='성함' required>"
            "<input name=contact placeholder='연락처(등록 시 입력한 것)' required style=flex:1>"
            "<button>내 작업실 열기</button></form></div>")
    return shell_portal("내 작업실 찾기", "재로그인", body)


def view_me(qs) -> bytes | None:
    token = qs.get("t", [None])[0]
    conn = db.connect()
    p = core.find_by_token(conn, token)
    if not p:
        conn.close()
        return None
    as_of = core.today()
    name = p["name"]
    saved = qs.get("ok")  # 방금 제출/등록 완료 플래그

    if p["status"] == "kicked":
        conn.close()
        body = (f"<div class=card><h2 class=b-red>강퇴 처리됨</h2>"
                f"<p class=empty>{esc(name)}님은 현재 강퇴 상태입니다. 재입장은 불가합니다.</p></div>")
        return shell_portal(name, "강퇴", body)

    streak = core.streak_for(conn, p, as_of)
    posted = core.posted_today(conn, p["id"], as_of)

    # 오늘 상태 카드
    if posted:
        status_card = (f"<div class=card><h2>오늘 발행 ✅</h2>"
                       f"<div class='big b-grn' style='font-size:34px'>🔥 {streak}일 연속</div>"
                       f"<p class=empty>오늘 몫 완료! 내일도 이어가세요.</p></div>")
    else:
        status_card = (f"<div class=card style='border-color:var(--yel)'>"
                       f"<h2 class=b-yel>오늘 아직 미발행 ⏳</h2>"
                       f"<div class=empty>현재 {streak}일 연속 · <b>자정 전 1건 발행</b> 안 하면 강퇴됩니다.</div></div>")

    ok_banner = ("<div class=card style='border-color:var(--grn)'>"
                 "<b class=b-grn>제출 완료! 오늘 출석 처리됐습니다.</b></div>") if saved else ""

    # 제출 폼
    submit = (f"<div class=card><h2>오늘 글 제출</h2>"
              f"<form method=post action=/submit>"
              f"<input type=hidden name=t value='{esc(token)}'>"
              f"<input name=url placeholder='발행한 게시물 링크 붙여넣기' required style='flex:1'>"
              f"<select name=channel><option value=threads>스레드</option>"
              f"<option value=instagram>인스타</option><option value=blog>블로그</option>"
              f"<option value=etc>기타</option></select>"
              f"<button>제출</button></form>"
              f"<p class=empty>제출이 곧 출석입니다.</p></div>")

    # 오늘 올릴 글감 — 공개된 것 중 최신 1건만(미래 예약분은 그 날짜 전까지 숨김).
    recent = core.published_drops(conn, core.today(), 30)
    feed_link = (f"<a class=lk href='/feed?t={esc(token)}'>"
                 "📚 지난 글감 전체보기 →</a>")
    if recent:
        r = recent[0]   # 가장 최근 등록한 글감
        media = ""
        if r["assets"]:
            parts = [render_asset(conn, a) for a in r["assets"].splitlines()]
            parts = [p for p in parts if p]
            if parts:
                solo = " solo" if len(parts) == 1 else ""
                media = f"<div class='media-grid{solo}'>{''.join(parts)}</div>"
        body_html = (f"<pre>{esc(r['body'])}</pre>"
                     "<button type=button class=ghost onclick=fpCopy(this)>📋 본문 복사</button>"
                     ) if (r["body"] or "").strip() else ""
        more = (f"<p class=empty style='margin-top:10px'>"
                f"📅 {esc(r['drop_date'])} 등록 · 이전 글감은 여기서 → {feed_link}</p>"
                if len(recent) > 1 else
                f"<p class=empty style='margin-top:10px'>{feed_link}</p>")
        drop_card = ("<div class=card style='border:2px solid var(--acc)'>"
                     "<h2>📌 오늘 올릴 글감 <span class=pill>최신 1건만 — 이걸 올리세요</span></h2>"
                     "<p class=empty style='margin:-4px 0 10px'>"
                     "⚠️ <b>맨 위 이 글감을 올리세요.</b> 어제 거 올리면 안 됩니다. "
                     "예전 글감을 보려면 아래 ‘지난 글감 전체보기’로만 들어가세요.</p>"
                     f"<h2>{esc(r['title'])}"
                     f"<span class=pill>{DROP_TYPE.get(r['dtype'], r['dtype'])}</span></h2>"
                     f"{body_html}{media}{more}</div>")
    else:
        drop_card = ("<div class=card><h2>📌 오늘 올릴 글감</h2>"
                     "<div class=empty>아직 등록 전입니다.</div>"
                     f"<p class=empty>{feed_link}</p></div>")

    # 내 판매 링크 (유형별 상품 — 운영진이 발급, 공지에 자동삽입)
    plinks = onboard.links_of(p)
    pslots = onboard.type_slots(p["partner_type"])
    lk_items = "".join(
        f"<div class=row><span class=hd>{esc(label)}</span>"
        + (f"<a class='lk meta' style='margin-left:0' href='{esc(plinks.get(key))}' target=_blank>{esc(plinks.get(key))}</a>"
           if (plinks.get(key) or '').strip() else "<span class=meta>발급 대기</span>")
        + "</div>"
        for key, label in pslots)
    link_card = (f"<div class=card><h2>내 판매 링크 — {esc(onboard.type_label(p['partner_type']))}</h2>{lk_items}"
                 "<p class=empty>이 링크들이 단톡방 공지에 자동으로 들어갑니다. 이 링크로 들어온 결제가 내 실적입니다.</p></div>")

    # 운영 공지 배너
    notices = core.active_notices(conn)
    notice_card = ""
    if notices:
        items = "".join(f"<div class=row><span class=hd>{n['notice_date']}</span>"
                        f"<span class=meta style='margin-left:0'><b>{esc(n['title'])}</b>"
                        f"{('<br>' + esc(n['body'])) if n['body'] else ''}</span></div>"
                        for n in notices)
        notice_card = f"<div class=card style='border-color:var(--acc)'><h2>📢 운영 공지</h2>{items}</div>"

    # 내 제출 이력
    subs = core.recent_submissions(conn, p["id"], 10)
    hist = "".join(
        f"<div class=row><span class=hd>{s['post_date']}</span>"
        f"<a class='lk meta' style='margin-left:0' href='{esc(s['post_url'])}' target=_blank>{esc(s['post_url'])}</a>"
        f"{'' if s['valid'] else ' <span class=b-red>✗무효</span>'}</div>"
        for s in subs)
    hist_card = (f"<div class=card><h2>내 제출 이력</h2>"
                 f"{hist or '<div class=empty>아직 없음</div>'}</div>")

    files_link = (f"<div class=card><a class=lk href='/files?t={esc(token)}'>📁 자료실 — "
                  "글감에 쓸 사진·영상 다운로드 →</a></div>")
    wall_link = (f"<div class=card><a class=lk href='/wall?t={esc(token)}'>🏆 전체 인증 보드 보기 — "
                 "동료들이 지금 얼마나 달리고 있는지 →</a></div>")

    # 처음 세팅 가이드 — STEP 1~4 단계별 카드 (각 단계에서 본인이 만든 정보 입력)
    links = onboard.links_of(p)
    profile = onboard.profile_text(p["name"], p["handle"])
    notice = onboard.notice_text(p["partner_type"], links)
    slots = onboard.type_slots(p["partner_type"])
    has_handle = bool((p["handle"] or "").strip())
    has_oc = bool((p["openchat_url"] or "").strip())
    n_links = sum(1 for k, _ in slots if (links.get(k) or "").strip())
    need = len(slots)
    def ck(done):
        return "✅" if done else "⬜"
    saved2_flag = (qs.get("saved2") or [None])[0]
    if saved2_flag == "links":
        saved2 = ("<div class=card style='border:2px solid var(--red)'>"
                  "<b class=b-red>✅ 링크 저장됨 — 아직 안 끝났어요!</b>"
                  "<p style='margin:6px 0'>저장하면 <b>아래 STEP 3의 ‘카톡방 공지’ 내용이 바뀝니다.</b> "
                  "<span class=b-red>바뀐 공지를 [📋 복사]해서 <b>내 카톡방 공지를 직접 교체</b>하세요.</span> "
                  "(카톡방 공지는 저절로 안 바뀝니다.)</p></div>")
    elif saved2_flag:
        saved2 = ("<div class=card style='border-color:var(--grn)'>"
                  "<b class=b-grn>저장됐습니다. 운영자 화면에 바로 반영됩니다.</b></div>")
    else:
        saved2 = ""

    intro = (f"<div class=card style='border-color:var(--acc)'><h2>📋 처음 세팅 (STEP 1~4 · 한 번만)</h2>"
             f"<p class=empty>유형: <b>{esc(onboard.type_label(p['partner_type']))}</b> · "
             f"위에서부터 <b>순서대로</b> 따라하세요. ✅ = 완료된 단계입니다.</p></div>")
    step1 = (
        f"<div class=card style='border-color:{'var(--grn)' if has_handle else 'var(--yel)'}'>"
        f"<h2>{ck(has_handle)} STEP 1. 스레드 계정 만들기</h2>"
        "<p>① 스레드(Threads) 앱에서 <b>새 계정</b>을 만드세요. 아이디는 "
        "<b>자유롭게</b> 정하면 됩니다. <b>자동으로 안 만들어지니 직접 만들어 주세요.</b></p>"
        "<p style='background:#fff7e6;border-left:3px solid var(--yel);padding:8px 10px;border-radius:6px'>"
        "📷 <b>계정 생성 후 꼭 설정:</b> 모바일 우측 상단 <b>☰(짝대기 2~3개)</b> 클릭 → "
        "<b>계정</b> → <b>미디어</b> → <b>‘고화질로 업로드’ 체크 필수</b></p>"
        "<p>② 프로필 소개글은 아래 그대로 복붙:</p>"
        f"<pre>{esc(profile)}</pre>"
        "<p>③ <b>만든 스레드 아이디</b>를 입력하고 저장:</p>"
        "<form method=post action=/me/save>"
        f"<input type=hidden name=t value='{esc(token)}'>"
        f"<input name=handle value='{esc(p['handle'] or '')}' "
        "placeholder='내가 만든 스레드 아이디' style=flex:1>"
        "<button>저장</button></form></div>")
    step2 = (
        f"<div class=card style='border-color:{'var(--grn)' if has_oc else 'var(--ln)'}'>"
        f"<h2>{ck(has_oc)} STEP 2. 오픈톡방 개설</h2>"
        "<p>① 카카오톡 <b>오픈채팅(그룹) 3,000명</b>으로 개설.</p>"
        "<p style='background:#fff7e6;border-left:3px solid var(--yel);padding:8px 10px;border-radius:6px'>"
        "👤 <b>내 방 프로필 닉네임을 아래로 변경하세요</b> (예전엔 ‘AIMAX 매니저’로 통일했는데, "
        "이제 <b>성함을 붙입니다</b>):</p>"
        f"<pre>AIMAX 매니저 ({esc(name)})</pre>"
        "<p>② 방 제목·소개(통일):</p>"
        f"<pre>[제목] {esc(onboard.OPENCHAT_TITLE)}\n\n[소개]\n{esc(onboard.OPENCHAT_INTRO)}</pre>"
        "<p>③ 아래 <b>공지를 복사</b>해서 <b>내 카톡방 ‘공지’에 직접 붙여넣으세요.</b></p>"
        "<p style='background:#fdecec;border-left:3px solid var(--red);padding:8px 10px;border-radius:6px'>"
        "⚠️ <b>카톡에 자동으로 안 올라갑니다.</b> 아래 [📋 공지 복사] 누르고 → 내 카톡방 공지에 붙여넣어야 해요. "
        "(STEP 3에서 판매 링크를 넣으면 이 공지에 자동으로 채워지니, <b>링크 넣은 뒤 다시 복사</b>하세요.)</p>"
        f"<div><pre>{esc(notice)}</pre>"
        "<button type=button class=ghost onclick=fpCopy(this)>📋 공지 복사</button></div>"
        "<p>④ <b>만든 오픈톡방 링크</b>를 입력하고 저장:</p>"
        "<form method=post action=/me/save>"
        f"<input type=hidden name=t value='{esc(token)}'>"
        f"<input name=openchat value='{esc(p['openchat_url'] or '')}' "
        "placeholder='내 오픈톡방 초대 링크' style=flex:1>"
        "<button>저장</button></form></div>")
    link_inputs = "".join(
        f"<input name='link_{esc(key)}' value='{esc(links.get(key) or '')}' "
        f"placeholder='{esc(label)}' style='flex:1 1 200px;min-width:0'>"
        for key, label in slots)
    step3 = (
        f"<div class=card id=notice style='border-color:{'var(--grn)' if n_links == need else 'var(--ln)'}'>"
        f"<h2>{ck(n_links == need)} STEP 3. 판매 링크 넣기 <span class=pill>{n_links}/{need}</span></h2>"
        "<p>운영자에게 카톡으로 <b>“판매 링크 발급 요청합니다”</b> → 받은 링크를 아래에 넣고 저장하세요. "
        "(이 링크로 들어온 결제가 내 실적)</p>"
        "<form method=post action=/me/links style='flex-wrap:wrap'>"
        f"<input type=hidden name=t value='{esc(token)}'>"
        f"{link_inputs}"
        "<button>저장</button></form>"
        # 저장하면 아래 공지가 바뀜 → 이걸 다시 복사해서 카톡방 공지 교체
        "<p style='margin-top:16px;background:#fdecec;border-left:3px solid var(--red);"
        "padding:8px 10px;border-radius:6px'>"
        "⚠️ <b>저장하면 아래 ‘내 카톡방 공지’ 내용이 바뀝니다.</b> "
        "<span class=b-red>바뀐 공지를 [📋 복사]해서 <b>내 카톡방 공지를 직접 교체</b>하세요. "
        "카톡방 공지는 저절로 안 바뀝니다!</span></p>"
        "<h2 style='margin-top:8px'>📢 지금 내 카톡방에 들어갈 공지 (링크 반영됨)</h2>"
        f"<div><pre>{esc(notice)}</pre>"
        "<button type=button class=ghost onclick=fpCopy(this)>📋 카톡방 공지 복사</button></div></div>")
    step4 = (
        "<div class=card><h2>⬜ STEP 4. 매일 콘텐츠 올리고 제출</h2>"
        "<p>① 아래 <b>‘오늘 올릴 글감’</b>에서 <b>본문은 복사</b>(그대로 또는 단어만 바꿔서), "
        "<b>영상·사진은 꼭 다운로드</b>해서 함께 스레드에 올리세요. "
        "<span class=b-red>텍스트만 올리고 영상 빼먹으면 반응 안 옵니다.</span></p>"
        "<p>② <b>댓글·반응이 오면 → 내 오픈톡방 링크로 유도</b>하세요 "
        "(대댓글로 방 초대링크 안내). 답변하기 어려우면 운영진이 대신 답변해드립니다.</p>"
        "<p>③ <b>매일 1건 발행 후 아래 ‘오늘 글 제출’에 링크 제출 = 출석</b>입니다. "
        "(카톡 말고 <b>여기 작업실에 제출</b>해야 출석 처리됩니다.)</p></div>")
    setup = intro + step1 + step2 + step3 + step4
    find_note = ("<div class=card><p class=empty>💡 이 작업실 링크는 북마크하세요. 잃어버려도 "
                 "<a class=lk href='/find'>내 작업실 찾기</a>(성함+연락처)로 다시 들어올 수 있습니다.</p></div>")

    gvid = _youtube_id(GUIDE_VIDEO)
    gembed = (f"<div class=video-wrap><iframe src='https://www.youtube.com/embed/{esc(gvid)}' "
              "allowfullscreen allow='accelerometer;encrypted-media;picture-in-picture'></iframe></div>"
              if gvid else
              f"<a class=lk href='{esc(GUIDE_VIDEO)}' target=_blank>▶ 가이드 영상 열기</a>")
    guide_banner = (
        "<div class=card style='border:2px solid var(--acc)'>"
        "<h2>📖 사용법 가이드 영상 <span class=pill>막히면 이거부터</span></h2>"
        "<p class=empty style='margin:0 0 10px'>가입부터 매일 올리는 것까지 이 영상에 다 있어요.</p>"
        f"{gembed}</div>")

    conn.close()
    body = (COPY_JS + notice_card + saved2 + ok_banner + status_card + guide_banner +
            setup + submit + drop_card + files_link + hist_card + link_card +
            wall_link + find_note)
    return shell_portal(name, f"{esc(name)}님의 작업실", body, token)


def view_wall(qs=None) -> bytes:
    conn = db.connect()
    s = core.wall_summary(conn, core.today())
    conn.close()
    kpi = (f"<div class=kpi>"
           f"<div class=card><div class='big b-grn'>{s['posted_today']}</div>"
           f"<div class=lb>오늘 발행</div></div>"
           f"<div class=card><div class=big>{s['total']}</div><div class=lb>전체 파트너</div></div>"
           f"<div class=card><div class='big b-yel'>{s['avg_streak']}</div>"
           f"<div class=lb>평균 연속일</div></div></div>")
    medals = ["🥇", "🥈", "🥉"]
    rows = []
    for i, r in enumerate(s["board"]):
        rank = medals[i] if i < 3 else f"{i+1}"
        today = "✅" if r["posted_today"] else "⏳"
        rows.append(f"<div class=row><span style='min-width:32px'>{rank}</span>"
                    f"<span class=nm>{esc(r['name'])}</span>"
                    f"<span class=hd>{esc(r['handle'] or '-')}</span>"
                    f"<span class=meta>🔥 {r['streak']}일 · 오늘 {today} · 누적 {r['total']}건</span></div>")
    body = (f"<p class=pill>{core.iso(core.today())} · 모두의 실행을 투명하게 공개합니다</p>{kpi}"
            f"<div class=card><h2>오늘의 실행 랭킹</h2>{''.join(rows) or '<div class=empty>없음</div>'}"
            f"<p class=empty>혼자 가면 빠를 수 있습니다. 함께 가면 훨씬 크게 갈 수 있습니다.</p></div>")
    token = (qs or {}).get("t", [None])[0]
    return shell_portal("인증 보드", "함께 성장하는 파트너", body, token)


def view_review(qs) -> str:
    conn = db.connect()
    d = core.parse_date(qs.get("date", [None])[0])
    subs = core.submissions_on(conn, d)
    conn.close()
    if not subs:
        return f"<div class=card><h2>제출 검수 — {core.iso(d)}</h2><div class=empty>제출 없음.</div></div>"
    rows = []
    for s in subs:
        flags = []
        if not s["valid"]:
            flags.append(f"<span class=b-red>✗무효</span>")
        if s["dup"]:
            flags.append(f"<span class=b-yel>⚠중복</span>")
        flag_html = " ".join(flags)
        if s["valid"]:
            action = (f"<form method=post action=/reject style='margin:0;gap:6px'>"
                      f"<input type=hidden name=id value='{s['id']}'>"
                      f"<input type=hidden name=date value='{core.iso(d)}'>"
                      f"<input name=reason placeholder='무효 사유' style='width:120px'>"
                      f"<button>무효</button></form>")
        else:
            action = f"<span class=pill>{esc(s['void_reason'] or '무효')}</span>"
        rows.append(
            f"<div class=row><span class=nm>{esc(s['name'])}</span>"
            f"<a class='lk hd' href='{esc(s['url'])}' style='min-width:240px' target=_blank>{esc(s['url'])}</a>"
            f"<span class=meta>{flag_html} {action}</span></div>")
    return (f"<div class=card><h2>제출 검수 — {core.iso(d)}"
            f"<span class=pill>총 {len(subs)}건</span></h2>"
            f"<p class=empty>가짜·스팸·재탕은 무효 처리하세요. 무효는 출석에서 빠집니다. "
            f"⚠중복=같은 링크가 2회 이상.</p>{''.join(rows)}</div>")


def view_board(qs) -> str:
    conn = db.connect()
    lb = core.leaderboard(conn, core.parse_date(qs.get("date", [None])[0]))
    conn.close()
    if not lb:
        return "<div class=card><h2>활동 랭킹</h2><div class=empty>활성 파트너 없음.</div></div>"
    medals = ["🥇", "🥈", "🥉"]
    rows = []
    for i, r in enumerate(lb):
        rank = medals[i] if i < 3 else f"{i+1}"
        today = "✅" if r["posted_today"] else "⏳"
        rows.append(
            f"<div class=row><span style='min-width:34px'>{rank}</span>"
            f"<a class='nm lk' href='/partner?id={r['id']}'>{esc(r['name'])}</a>"
            f"<span class=hd>{esc(r['handle'] or '-')}</span>"
            f"<span class=meta>🔥 {r['streak']}일 · 오늘 {today} · 누적 {r['total']}건</span></div>")
    return f"<div class=card><h2>활동 랭킹 <span class=pill>연속일 순</span></h2>{''.join(rows)}</div>"


def view_people(qs) -> str:
    flash = ""
    if qs.get("msg"):
        flash = (f"<div class=card style='border-color:var(--grn)'>"
                 f"<b class=b-grn>{esc(qs['msg'][0])}</b></div>")
    conn = db.connect()
    rows = core.all_partners(conn)
    conn.close()
    TYPE_BADGE = {
        "family": ("패밀리", "#e8f0fe", "#2563eb"),
        "aimax": ("AIMAX", "#e7f6ec", "#15803d"),
        "both": ("통합", "#fdf0e3", "#b45309"),
    }
    items = []
    for r in rows:
        st_cls = {"active": "b-grn", "kicked": "b-red", "paused": "b-yel"}.get(r["status"], "")
        handle = (r["handle"] or "").strip()
        oc = (r["openchat_url"] or "").strip()
        tlabel, tbg, tfg = TYPE_BADGE.get(r["partner_type"], TYPE_BADGE["family"])
        type_badge = (f"<span style='background:{tbg};color:{tfg};font-size:12px;font-weight:600;"
                      f"padding:2px 9px;border-radius:10px;min-width:54px;text-align:center'>{tlabel}</span>")
        handle_html = (
            f"<a class='hd lk' href='https://www.threads.net/@{esc(handle)}' target=_blank>"
            f"@{esc(handle)} ↗</a>" if handle else "<span class=hd>계정 미입력</span>")
        oc_html = (f"<a class=lk href='{esc(oc)}' target=_blank>💬 톡방</a> · "
                   if oc else "<span class=empty>톡방 미입력</span> · ")
        items.append(
            f"<div class=row>"
            f"<a class='nm lk' href='/partner?id={r['id']}'>{esc(r['name'])}</a>"
            f"{type_badge}"
            f"{handle_html}"
            f"<span class='pill {st_cls}' style='min-width:48px'>{r['status']}</span>"
            f"<span class=meta>"
            f"{oc_html}"
            f"<a class=lk href='/me?t={esc(r['portal_token'] or '')}' target=_blank>작업실</a> · "
            f"<form method=post action=/op/delete style='display:inline;margin:0' "
            f"onsubmit=\"return confirm('{esc(r['name'])} 삭제할까요? 되돌릴 수 없습니다.')\">"
            f"<input type=hidden name=id value='{r['id']}'>"
            f"<button style='padding:3px 9px;background:#fff;border-color:var(--red);color:var(--red)'>삭제</button>"
            f"</form></span></div>")
    listing = (f"<div class=card><h2>전체 인원 ({len(rows)})</h2>"
               f"<p class=empty>이름=상세 · <b>@아이디</b>=스레드 계정 열기 · "
               f"<b>💬톡방</b>=오픈톡방 열기 · <b>작업실</b>=참여자 화면. "
               f"강퇴는 상세에서, 삭제는 완전 제거.</p>"
               f"{''.join(items) or '<div class=empty>아직 없음</div>'}</div>")
    reset = ("<div class=card style='border-color:var(--red)'>"
             "<h2 class=b-red>전체 초기화 (테스트 정리)</h2>"
             "<p class=empty>모든 인원·기록·관리자 비번을 삭제합니다. 되돌릴 수 없습니다. "
             "확인란에 <b>초기화</b> 입력 후 실행하면, 다음 로그인 때 비번을 새로 설정합니다.</p>"
             "<form method=post action=/op/reset "
             "onsubmit=\"return confirm('정말 전체 초기화할까요? 되돌릴 수 없습니다.')\">"
             "<input name=confirm placeholder='초기화' required>"
             "<button style='background:#fff;border-color:var(--red);color:var(--red)'>전체 초기화</button></form></div>")
    return flash + listing + reset


def view_partner(qs) -> str:
    pid = qs.get("id", [None])[0]
    conn = db.connect()
    p = conn.execute("SELECT * FROM partners WHERE id=?", (pid,)).fetchone() if pid else None
    if not p:
        conn.close()
        return "<div class=card><h2>파트너를 찾을 수 없음</h2></div>"
    d = core.partner_detail(conn, p, core.today())
    conn.close()
    r = d["row"]
    st_cls = {"active": "b-grn", "kicked": "b-red", "paused": "b-yel"}.get(r["status"], "")
    pslots = onboard.type_slots(r["partner_type"])
    plinks = onboard.links_of(r)
    n_lk = sum(1 for k, _ in pslots if (plinks.get(k) or "").strip())
    head = (f"<div class=card><h2>{esc(r['name'])} "
            f"<span class='pill {st_cls}'>{r['status']}</span></h2>"
            f"<div class=row><span class=hd>유형</span><span class=meta>"
            f"{esc(onboard.type_label(r['partner_type']))}</span></div>"
            f"<div class=row><span class=hd>스레드 아이디</span><span class=meta>"
            + (f"<a class=lk href='https://www.threads.net/@{esc((r['handle'] or '').strip())}' target=_blank>@{esc(r['handle'])} ↗</a>"
               if (r['handle'] or '').strip() else '-') + "</span></div>"
            f"<div class=row><span class=hd>오픈톡방</span><span class=meta>"
            + (f"<a class=lk href='{esc((r['openchat_url'] or '').strip())}' target=_blank>💬 {esc(r['openchat_url'])} ↗</a>"
               if (r['openchat_url'] or '').strip() else '-') + "</span></div>"
            f"<div class=row><span class=hd>연락처</span><span class=meta>{esc(r['contact'] or '-')}</span></div>"
            f"<div class=row><span class=hd>가입일</span><span class=meta>{esc(r['joined_date'])}</span></div>"
            f"<div class=row><span class=hd>오늘</span><span class=meta>"
            f"{'발행 ✅' if d['posted_today'] else '미발행 ⏳'} · 🔥 {d['streak']}일 연속</span></div>"
            f"<div class=row><span class=hd>제출</span><span class=meta>"
            f"유효 {d['total_valid']}건 / 무효 {d['total_void']}건</span></div>"
            f"<div class=row><span class=hd>판매링크</span><span class=meta>"
            f"{n_lk}/{len(pslots)} 입력됨 "
            f"<span class=pill>파트너가 STEP 3에서 입력</span></span></div>"
            f"<div class=row><span class=hd>작업실</span><span class=meta>"
            f"<a class=lk href='/me?t={esc(r['portal_token'] or '')}' target=_blank>"
            f"🔗 참여자가 보는 화면 열기</a></span></div></div>")
    subs = []
    for s in d["submissions"][:30]:
        mark = "" if s["valid"] else f" <span class=b-red>✗{esc(s['void_reason'] or '무효')}</span>"
        subs.append(f"<div class=row><span class=hd>{s['post_date']}</span>"
                    f"<a class='lk meta' href='{esc(s['post_url'])}' target=_blank style='margin-left:0'>"
                    f"[{esc(s['channel'])}] {esc(s['post_url'])}</a>{mark}</div>")
    sub_card = f"<div class=card><h2>제출 이력</h2>{''.join(subs) or '<div class=empty>없음</div>'}</div>"
    evs = []
    for e in d["events"][:30]:
        evs.append(f"<div class=row><span class=hd>{e['date']}</span>"
                   f"<span class=meta>{esc(e['type'])}"
                   f"{' — ' + esc(e['reason']) if e['reason'] else ''}</span></div>")
    ev_card = f"<div class=card><h2>운영 이력</h2>{''.join(evs) or '<div class=empty>없음</div>'}</div>"
    edit = (f"<div class=card><h2>정보 수정</h2>"
            f"<form method=post action=/op/edit style='flex-wrap:wrap'>"
            f"<input type=hidden name=id value='{r['id']}'>"
            f"<select name=ptype style=flex:1>"
            f"<option value=family{' selected' if r['partner_type']=='family' else ''}>메이크패밀리 파트너스</option>"
            f"<option value=aimax{' selected' if r['partner_type']=='aimax' else ''}>AIMAX 파트너스</option>"
            f"<option value=both{' selected' if r['partner_type']=='both' else ''}>둘 다</option>"
            f"</select>"
            f"<input name=handle value='{esc(r['handle'] or '')}' placeholder='스레드 아이디'>"
            f"<input name=contact value='{esc(r['contact'] or '')}' placeholder='연락처'>"
            f"<input name=openchat value='{esc(r['openchat_url'] or '')}' placeholder='오픈톡방 링크' style=flex:1>"
            f"<button>저장</button></form>"
            f"<p class=empty>유형을 바꾸면 공지·판매링크 슬롯이 그 유형에 맞게 바뀝니다. "
            f"판매링크는 파트너가 작업실 STEP 3에서 입력(여기선 위에 현황만).</p></div>")
    return head + edit + sub_card + ev_card


def view_files(qs=None) -> bytes:
    """자료실 — 파트너/모두가 보는 다운로드 목록."""
    conn = db.connect()
    items = core.list_library(conn)
    conn.close()
    by_cat = {}
    for it in items:
        by_cat.setdefault(it["category"] or "기타", []).append(it)
    cards = []
    for cat, lst in by_cat.items():
        rows = []
        for it in lst:
            if it["kind"] == "file":
                rows.append(f"<div class=row><span class=nm>{esc(it['title'])}</span>"
                            f"<span class=hd>{esc(it['orig_name'] or '')}</span>"
                            f"<a class=meta href='/dl/{it['id']}'>⬇ 다운로드 "
                            f"{core.human_size(it['size'])}</a></div>")
            else:
                rows.append(f"<div class=row><span class=nm>{esc(it['title'])}</span>"
                            f"<a class=meta href='{esc(it['url'])}' target=_blank>🔗 열기</a></div>")
        cards.append(f"<div class=card><h2>{esc(cat)}</h2>{''.join(rows)}</div>")
    if not cards:
        cards = ["<div class=card><div class=empty>아직 올라온 자료가 없습니다.</div></div>"]
    body = ("<div class=card><h2>📁 자료실</h2>"
            "<p class=empty>글감에 쓸 사진·영상을 받아서 본인 콘텐츠에 활용하세요.</p></div>"
            + "".join(cards))
    token = (qs or {}).get("t", [None])[0]
    return shell_portal("자료실", "사진·영상 다운로드", body, token)


def view_library(qs) -> str:
    """자료실 운영자 화면 — 업로드/링크추가/삭제."""
    flash = ""
    if qs.get("msg"):
        flash = (f"<div class=card style='border-color:var(--grn)'>"
                 f"<b class=b-grn>{esc(qs['msg'][0])}</b></div>")
    conn = db.connect()
    items = core.list_library(conn)
    conn.close()
    upload = (
        "<div class=card><h2>자료 링크 추가</h2>"
        "<p class=empty>사진·영상은 <b>구글드라이브(또는 카톡방)</b>에 올리고, 그 공유 링크를 여기에 등록하세요.</p>"
        "<form method=post action=/op/library-link>"
        "<input name=title placeholder='제목(예: 6월 캠페인 썸네일)' required>"
        "<input name=category placeholder='분류(선택, 예: 사진/영상)'>"
        "<input name=url placeholder='https://drive.google.com/... 공유 링크' required style=flex:1>"
        "<button>링크 추가</button></form></div>"
    )
    rows = []
    for it in items:
        if it["kind"] == "file":
            tgt = f"<a class=lk href='/dl/{it['id']}'>{core.human_size(it['size'])} ⬇</a>"
        else:
            tgt = f"<a class=lk href='{esc(it['url'])}' target=_blank>링크</a>"
        rows.append(
            f"<div class=row><span class=nm>{esc(it['title'])}</span>"
            f"<span class=hd>{esc(it['category'] or '-')}</span>"
            f"<span class=meta>{tgt} · "
            f"<form method=post action=/op/library-del style='display:inline;margin:0'>"
            f"<input type=hidden name=id value='{it['id']}'>"
            f"<button style='padding:3px 9px'>삭제</button></form></span></div>")
    listing = (f"<div class=card><h2>올라온 자료 ({len(items)})</h2>"
               f"{''.join(rows) or '<div class=empty>없음</div>'}</div>")
    return flash + upload + listing


def _send_reminder_sms(jobs) -> tuple[int, int, int]:
    """jobs: [(PartnerStatus, 메시지본문)] → (성공수, 실패수, 번호없음수). 1명씩 개별 발송."""
    sent = fail = noaddr = 0
    for stat, msg in jobs:
        if not ppurio.normalize_phone(stat.row["contact"] or ""):
            noaddr += 1
            continue
        try:
            ppurio.send_sms([{"phone": stat.row["contact"], "name": stat.name}], msg)
            sent += 1
        except Exception:
            fail += 1
    return sent, fail, noaddr


def _sms_btn(pid: int, phone: str | None, kind: str) -> str:
    """미인증자 1명에게 문자 발송 버튼(뿌리오 설정 + 유효 번호 있을 때만)."""
    if not ppurio.is_configured():
        return ""
    if not ppurio.normalize_phone(phone or ""):
        return "<p class=empty style='margin-top:8px'>📵 연락처가 없거나 휴대폰 형식이 아니라 문자 발송 불가</p>"
    return (f"<form method=post action=/op/sms style='margin-top:8px'>"
            f"<input type=hidden name=pid value={pid}>"
            f"<input type=hidden name=kind value={kind}>"
            f"<button class=ghost>📨 이 분께 문자 발송</button></form>")


def view_reminders(qs=None) -> str:
    qs = qs or {}
    conn = db.connect()
    b = core.daily_board(conn, core.today())
    conn.close()
    targets = b["at_risk"] + b["kick"]

    head = "<div class=card><h2>보낼 메시지</h2>"
    if qs.get("sent") is not None:
        sent = qs.get("sent", ["0"])[0]
        fail = qs.get("fail", ["0"])[0]
        no = qs.get("no", ["0"])[0]
        head = ("<div class=card style='border-color:var(--grn)'>"
                f"<b class=b-grn>📨 문자 발송 완료 — 성공 {esc(sent)}건"
                f"{f', 실패 {esc(fail)}건' if fail != '0' else ''}"
                f"{f', 번호없음 {esc(no)}명' if no != '0' else ''}.</b></div>"
                "<div class=card><h2>보낼 메시지</h2>")
    if ppurio.is_configured():
        n_send = sum(1 for s in targets if ppurio.normalize_phone(s.row["contact"] or ""))
        bulk = (f"<form method=post action=/op/sms-all style='margin-top:10px'>"
                f"<button{' disabled' if not n_send else ''}>"
                f"📨 미인증자 전체에게 문자 발송 ({n_send}명)</button></form>"
                "<p class=empty>독려는 위험군에게, 경고는 어제 빵꾸난 분께 자동 분기됩니다. "
                "휴대폰 번호가 없는 분은 건너뜁니다.</p>") if targets else ""
        head += ("<p class=empty>아래 칸을 복사해 카톡으로 보내거나, 버튼으로 바로 문자(뿌리오) 발송하세요.</p>"
                 + bulk + "</div>")
    else:
        head += ("<p class=empty>아래 칸을 복사해 카톡으로 보내세요. "
                 "문자(뿌리오) 자동발송을 켜려면 프로젝트 루트 <code>.env</code> 에 "
                 "<code>PPURIO_ACCOUNT_ID</code>·<code>PPURIO_BASIC_AUTH</code>·"
                 "<code>PPURIO_CALLER_NUMBER</code> 를 넣으세요.</p></div>")

    blocks = []
    for s in b["at_risk"]:
        tgt = s.row["contact"] or (s.handle or s.name)
        blocks.append(f"<div class=card><h2>{esc(s.name)} <span class=pill>{esc(tgt)} · 독려</span></h2>"
                      f"<pre>{esc(messages.reminder(s.name, s.streak))}</pre>"
                      f"{_sms_btn(s.row['id'], s.row['contact'], 'remind')}</div>")
    for s in b["kick"]:
        tgt = s.row["contact"] or (s.handle or s.name)
        blocks.append(f"<div class=card style='border-color:var(--red)'>"
                      f"<h2>{esc(s.name)} <span class=pill>{esc(tgt)} · 경고</span></h2>"
                      f"<pre>{esc(messages.warning(s.name))}</pre>"
                      f"{_sms_btn(s.row['id'], s.row['contact'], 'warn')}</div>")
    if not blocks:
        blocks = ["<div class=card><div class=empty>보낼 메시지 없음 — 전원 오늘 완료 👍</div></div>"]
    return head + "".join(blocks)


def view_enforce(qs) -> str:
    conn = db.connect()
    targets = core.enforce(conn, core.today(), dry_run=True)
    conn.close()
    done = qs.get("done")
    head = ""
    if done:
        head = (f"<div class=card style='border-color:var(--grn)'>"
                f"<b class=b-grn>{esc(done[0])}명 강퇴·몰수 처리 완료.</b></div>")
    if not targets:
        return head + "<div class=card><h2>강퇴 집행</h2><div class=empty>어제 빵꾸난 사람 없음 👍</div></div>"
    rows = "".join(f"<div class=row><span class='nm b-red'>{esc(t['name'])}</span>"
                   f"<span class=hd>{esc(t['handle'] or '-')}</span>"
                   f"<span class=meta>{esc(t['contact'] or '-')} → {t['month']} 수익 몰수</span></div>"
                   for t in targets)
    return (head + f"<div class=card style='border-color:var(--red)'>"
            f"<h2 class=b-red>⛔ 강퇴 대상 ({len(targets)}명)</h2>{rows}"
            "<p class=empty>아래를 누르면 즉시 강퇴 + 그 달 수익 몰수됩니다. 되돌릴 수 없습니다.</p>"
            "<form method=post action=/op/enforce><button "
            "style='background:#fff;border-color:var(--red);color:var(--red)'>"
            "강퇴 집행</button></form></div>")


def view_landing() -> bytes:
    body = (
        "<div class=card><h2>패밀리 파트너스 운영 시스템</h2>"
        "<p class=empty>매일 콘텐츠 챌린지로 함께 성장하는 파트너 프로그램.</p>"
        "<p style='margin-top:14px;font-size:15px'>"
        "<a class=lk href='/guide'>📖 사용법 가이드</a>　·　"
        "<a class=lk href='/join'>🙌 파트너로 등록</a>　·　"
        "<a class=lk href='/login'>🔑 관리자 로그인</a></p></div>"
        "<div class=card><h2>처음이세요?</h2>"
        "<p class=empty>먼저 <a class=lk href='/guide'>사용법 가이드</a>를 보세요 — "
        "캡처와 함께 단계별로 안내합니다.</p></div>"
    )
    return shell_portal("패밀리 파트너스", "함께 성장하는 파트너", body)


GUIDE_VIDEO = "https://youtube.com/live/bqXinSUY9wo"


def view_guide(qs=None) -> bytes:
    token = (qs or {}).get("t", [None])[0]
    vid = _youtube_id(GUIDE_VIDEO)
    embed = (f"<div class=video-wrap><iframe src='https://www.youtube.com/embed/{esc(vid)}' "
             "allowfullscreen allow='accelerometer;encrypted-media;picture-in-picture'></iframe></div>"
             if vid else
             f"<p><a class=lk href='{esc(GUIDE_VIDEO)}' target=_blank>▶ 가이드 영상 열기</a></p>")
    body = ("<div class=card style='border:2px solid var(--acc)'>"
            "<h2>📖 패밀리 파트너스 사용법 영상</h2>"
            "<p class=empty style='margin-bottom:12px'>아래 영상 하나면 가입부터 매일 올리는 것까지 전부 나옵니다. "
            "막히면 이 영상부터 보세요.</p>"
            f"{embed}"
            "<p class=empty style='margin-top:12px'>"
            f"영상이 안 보이면 → <a class=lk href='{esc(GUIDE_VIDEO)}' target=_blank>유튜브에서 바로 보기</a></p></div>")
    return shell_portal("사용법", "가이드 영상", body, token)


def view_login(first_set: bool, err: str = "") -> bytes:
    title = "관리자 비밀번호 설정 (최초 1회)" if first_set else "관리자 로그인"
    note = ("이 비밀번호로 운영 화면에 들어갑니다. 잊지 마세요."
            if first_set else "운영 화면은 관리자(운영자)만 들어갑니다.")
    e = f"<p class='b-red'>{esc(err)}</p>" if err else ""
    pw2 = ("<input type=password name=pw2 placeholder='비밀번호 확인' required>"
           if first_set else "")
    btn = "설정하고 들어가기" if first_set else "들어가기"
    body = (f"<div class=card><h2>{title}</h2>{e}"
            f"<form method=post action=/login>"
            f"<input type=password name=pw placeholder='비밀번호' required>{pw2}"
            f"<button>{btn}</button></form>"
            f"<p class=empty>{note}</p></div>")
    return shell_portal("관리자", "운영자 전용", body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 조용히
        pass

    def _cookies(self) -> dict:
        out = {}
        for part in self.headers.get("Cookie", "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k] = v
        return out

    def _admin_ok(self) -> bool:
        return hmac.compare_digest(self._cookies().get(COOKIE, ""), _admin_token())

    def _send(self, body: bytes, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")  # 항상 최신(참여자 즉시 반영)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str):
        # Location 헤더는 latin-1만 허용 → 비ASCII(한글 등) 있으면 퍼센트 인코딩
        try:
            location.encode("latin-1")
        except UnicodeEncodeError:
            location = quote(location, safe="/?=&#%:")
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _guide_img(self, name: str):
        # 안전: 파일명만 허용(경로 이동 차단)
        safe = os.path.basename(name)
        path = GUIDE_DIR / safe
        if "/" in name or "\\" in name or not path.exists() or not path.is_file():
            self.send_response(404); self.end_headers(); return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(safe)[0] or "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _file_bytes(self, row):
        """library 행에서 실제 바이트를 얻는다 — DB(base64) 우선, 없으면 디스크."""
        if row["data_b64"]:
            try:
                return base64.b64decode(row["data_b64"])
            except Exception:
                return None
        if row["stored_name"]:
            path = LIB_DIR / row["stored_name"]
            if path.exists():
                return path.read_bytes()
        return None

    def _download(self, lid: str):
        conn = db.connect()
        row = core.get_library(conn, int(lid)) if lid.isdigit() else None
        conn.close()
        if not row or row["kind"] != "file":
            return self._send(shell_portal("없음", "", "<div class=card>파일을 찾을 수 없음</div>"), 404)
        data = self._file_bytes(row)
        if data is None:
            return self._send(shell_portal("없음", "", "<div class=card>파일 내용이 없습니다</div>"), 404)
        self.send_response(200)
        ctype = mimetypes.guess_type(row["orig_name"] or "")[0] or "application/octet-stream"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition",
                         "attachment; filename*=UTF-8''" + _q(row["orig_name"] or "file"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _media(self, lid: str):
        """업로드 미디어를 인라인(다운로드 강제 X)으로 — 이미지·영상 임베드용. 공개."""
        conn = db.connect()
        row = core.get_library(conn, int(lid)) if lid.isdigit() else None
        conn.close()
        if not row or row["kind"] != "file":
            return self._send(shell_portal("없음", "", "<div class=card>파일 없음</div>"), 404)
        data = self._file_bytes(row)
        if data is None:
            return self._send(shell_portal("없음", "", "<div class=card>파일 내용이 없습니다</div>"), 404)
        self.send_response(200)
        ctype = mimetypes.guess_type(row["orig_name"] or "")[0] or "application/octet-stream"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition",
                         "inline; filename*=UTF-8''" + _q(row["orig_name"] or "file"))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        u = urlparse(self.path)
        ctype = self.headers.get("Content-Type", "")
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        files = {}
        if ctype.startswith("multipart/form-data") and "boundary=" in ctype:
            boundary = ctype.split("boundary=", 1)[1].strip().strip('"')
            f, files = parse_multipart(raw, boundary)
        else:
            f = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
        try:
            if u.path == "/login":
                pw = f.get("pw") or ""
                conn = db.connect()
                if not core.admin_is_set(conn):
                    if len(pw) < 4 or pw != (f.get("pw2") or ""):
                        conn.close()
                        return self._send(view_login(True, "비밀번호 불일치 또는 4자 미만입니다."))
                    core.set_admin_password(conn, pw)
                    conn.close()
                else:
                    ok = core.check_admin_password(conn, pw)
                    conn.close()
                    if not ok:
                        return self._send(view_login(False, "비밀번호가 틀렸습니다."))
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie",
                                 f"{COOKIE}={_admin_token()}; HttpOnly; Path=/; SameSite=Lax")
                self.end_headers()
                return
            # 관리자 게이트 — 공개 POST 외 전부 로그인 필요
            if u.path not in PUBLIC_POST and not self._admin_ok():
                return self._redirect("/login")
            if u.path == "/join":
                name = (f.get("name") or "").strip()
                contact = (f.get("contact") or "").strip()
                if not name:
                    return self._redirect("/join?e=1")
                conn = db.connect()
                if core.is_rejoin_blocked(conn, name, contact):
                    conn.close()
                    return self._redirect("/join?blocked=1")
                if core.find_partner(conn, name):
                    conn.close()
                    return self._redirect("/join?dup=1")
                try:
                    row = core.self_register(conn, name, None, contact or None,
                                             partner_type=(f.get("ptype") or "family"))
                    token = row["portal_token"]
                except Exception:
                    conn.close()
                    return self._redirect("/join?dup=1")
                conn.close()
                return self._redirect(f"/me?t={token}&ok=1")
            if u.path == "/submit":
                token = (f.get("t") or "").strip()
                url = (f.get("url") or "").strip()
                conn = db.connect()
                p = core.find_by_token(conn, token)
                if p and url and p["status"] != "kicked":
                    core.add_submission(conn, p["id"], url,
                                        (f.get("channel") or "threads"))
                conn.close()
                return self._redirect(f"/me?t={token}&ok=1")
            if u.path == "/find":  # 파트너 재로그인(성함+연락처)
                conn = db.connect()
                p = core.find_for_login(conn, f.get("name"), f.get("contact"))
                conn.close()
                if p:
                    return self._redirect(f"/me?t={p['portal_token']}")
                return self._redirect("/find?nf=1")
            if u.path == "/me/save":  # 파트너 세팅 입력(스레드 아이디·오픈톡방)
                token = (f.get("t") or "").strip()
                conn = db.connect()
                core.update_self(conn, token, f.get("handle"), f.get("openchat"))
                conn.close()
                return self._redirect(f"/me?t={token}&saved2=1")
            if u.path == "/me/links":  # 파트너 STEP3 판매링크(유형별 슬롯) 입력
                token = (f.get("t") or "").strip()
                links = {k[5:]: v for k, v in f.items() if k.startswith("link_")}
                conn = db.connect()
                core.update_links(conn, token, links)
                conn.close()
                # 링크 저장 → 공지가 바뀌었으니 '다시 복사' 안내 + STEP3 공지블록으로 이동
                # (앵커는 ASCII만 — Location 헤더는 latin-1만 허용, 한글이면 인코딩 에러)
                return self._redirect(f"/me?t={token}&saved2=links#notice")
            if u.path == "/reject":  # 운영자 제출 무효 처리
                conn = db.connect()
                core.reject_submission(conn, int(f.get("id", 0)), (f.get("reason") or "").strip() or None)
                conn.close()
                back = (f.get("date") or "").strip()
                return self._redirect(f"/review?date={back}" if back else "/review")
            if u.path == "/op/partner":  # 운영자: 파트너 등록
                name = (f.get("name") or "").strip()
                if not name:
                    return self._redirect("/?msg=" + _q("이름을 입력하세요"))
                conn = db.connect()
                try:
                    core.add_partner(conn, name, (f.get("handle") or "").strip() or None,
                                     (f.get("contact") or "").strip() or None, core.gen_code(),
                                     sales_url=(f.get("sales_url") or "").strip() or None)
                    m = f"파트너 등록 완료: {name}"
                except Exception:
                    m = f"이미 있는 이름입니다: {name}"
                conn.close()
                return self._redirect("/?msg=" + _q(m))
            if u.path == "/op/submit":  # 운영자: 제출 입력
                who = (f.get("who") or "").strip(); url = (f.get("url") or "").strip()
                conn = db.connect()
                p = core.find_partner(conn, who) if who else None
                if p and url:
                    core.add_submission(conn, p["id"], url)
                    m = f"제출 입력: {p['name']}"
                else:
                    m = f"파트너를 찾을 수 없음: {who}"
                conn.close()
                return self._redirect("/?msg=" + _q(m))
            if u.path == "/op/drop":  # 운영자: 글감 등록(본문 + 사진·영상 업로드 + 외부링크)
                title = (f.get("title") or "").strip()
                if not title:
                    return self._redirect("/?msg=" + _q("글감 제목을 입력하세요"))
                assets = []
                skipped = 0
                conn = db.connect()
                for fname, data in (files.get("media") or []):
                    if not fname or not data:
                        continue
                    if len(data) > MAX_UPLOAD:
                        skipped += 1
                        continue
                    lid = save_upload(conn, title, "글감", fname, data)
                    assets.append(f"m:{lid}")
                for line in (f.get("urls") or "").splitlines():
                    line = line.strip()
                    if line:
                        assets.append(line)
                dtype = (f.get("dtype") or "marketing").strip() or "marketing"
                dd = (f.get("drop_date") or "").strip() or None
                core.add_drop(conn, title, (f.get("body") or "").strip() or None,
                              assets, dtype, dd)
                conn.close()
                msg = f"글감 등록: {title} (첨부 {len(assets)}개)"
                if skipped:
                    msg += f" · {skipped}개는 4MB 초과로 제외 — 유튜브/드라이브 링크로 올려주세요"
                return self._redirect("/?msg=" + _q(msg))
            if u.path == "/op/drop-del":  # 운영자: 글감 삭제
                conn = db.connect()
                core.delete_drop(conn, int(f.get("id", 0)))
                conn.close()
                return self._redirect(f.get("back") or "/feed")
            if u.path == "/op/delete":  # 운영자: 파트너 완전 삭제
                conn = db.connect()
                row = conn.execute("SELECT name FROM partners WHERE id=?",
                                   (int(f.get("id", 0)),)).fetchone()
                core.delete_partner(conn, int(f.get("id", 0)))
                conn.close()
                nm = row["name"] if row else ""
                return self._redirect("/people?msg=" + _q(f"삭제됨: {nm}"))
            if u.path == "/op/reset":  # 운영자: 전체 초기화(확인어구 필요)
                if (f.get("confirm") or "").strip() == "초기화":
                    conn = db.connect()
                    core.reset_all(conn)
                    conn.close()
                    return self._redirect("/login")
                return self._redirect("/people?msg=" + _q("초기화하려면 확인어구를 정확히 입력하세요"))
            if u.path == "/op/edit":  # 운영자: 파트너 핸들/연락처 수정
                pid = (f.get("id") or "").strip()
                conn = db.connect()
                ptype = (f.get("ptype") or "family")
                if ptype not in ("family", "aimax", "both"):
                    ptype = "family"
                conn.execute(
                    "UPDATE partners SET handle=?, contact=?, openchat_url=?, partner_type=? WHERE id=?",
                    ((f.get("handle") or "").strip() or None,
                     (f.get("contact") or "").strip() or None,
                     (f.get("openchat") or "").strip() or None, ptype, int(pid)))
                conn.commit(); conn.close()
                return self._redirect(f"/partner?id={pid}")
            if u.path == "/op/enforce":  # 운영자: 강퇴 집행
                conn = db.connect()
                done = core.enforce(conn, core.today(), dry_run=False)
                conn.close()
                return self._redirect(f"/enforce?done={len(done)}")
            if u.path == "/op/sms":  # 운영자: 미인증자 1명에게 문자 발송
                conn = db.connect()
                b = core.daily_board(conn, core.today())
                conn.close()
                pid = (f.get("pid") or "").strip()
                kind = (f.get("kind") or "remind").strip()
                stat = next((s for s in (b["at_risk"] + b["kick"])
                             if str(s.row["id"]) == pid), None)
                if not stat:
                    return self._redirect("/reminders")
                msg = (messages.warning(stat.name) if kind == "warn"
                       else messages.reminder(stat.name, stat.streak))
                sent, fail, no = _send_reminder_sms([(stat, msg)])
                return self._redirect(f"/reminders?sent={sent}&fail={fail}&no={no}")
            if u.path == "/op/sms-all":  # 운영자: 미인증자 전체에게 문자 발송
                conn = db.connect()
                b = core.daily_board(conn, core.today())
                conn.close()
                jobs = [(s, messages.reminder(s.name, s.streak)) for s in b["at_risk"]]
                jobs += [(s, messages.warning(s.name)) for s in b["kick"]]
                sent, fail, no = _send_reminder_sms(jobs)
                return self._redirect(f"/reminders?sent={sent}&fail={fail}&no={no}")
            if u.path == "/op/library-upload":  # 자료실: 파일 업로드
                title = (f.get("title") or "").strip()
                flist = files.get("file") or []
                fname, data = flist[0] if flist else (None, None)
                if not fname or not data:
                    return self._redirect("/library?msg=" + _q("파일을 선택하세요"))
                if len(data) > MAX_UPLOAD:
                    return self._redirect("/library?msg=" + _q(
                        "파일이 너무 큽니다(4MB 초과). 영상 등 큰 파일은 유튜브/드라이브 링크로 올려주세요."))
                conn = db.connect()
                save_upload(conn, title or fname,
                            (f.get("category") or "").strip() or None, fname, data)
                conn.close()
                return self._redirect("/library?msg=" + _q(f"업로드 완료: {title or fname}"))
            if u.path == "/op/library-link":  # 자료실: 외부 링크 추가
                title = (f.get("title") or "").strip(); url = (f.get("url") or "").strip()
                if not title or not url:
                    return self._redirect("/library?msg=" + _q("제목과 링크가 필요합니다"))
                conn = db.connect()
                core.add_library_link(conn, title, (f.get("category") or "").strip() or None, url)
                conn.close()
                return self._redirect("/library?msg=" + _q(f"링크 추가: {title}"))
            if u.path == "/op/library-del":  # 자료실: 삭제
                conn = db.connect()
                row = core.delete_library(conn, int(f.get("id", 0)))
                conn.close()
                if row and row["kind"] == "file" and row["stored_name"]:
                    p = LIB_DIR / row["stored_name"]
                    if p.exists():
                        p.unlink()
                return self._redirect("/library?msg=" + _q("삭제됨"))
            self._send(shell("404", "<div class=card>없는 페이지</div>"), 404)
        except Exception as e:
            self._send(shell("오류", f"<div class=card><pre>{esc(e)}</pre></div>"), 500)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        try:
            if u.path == "/login":
                conn = db.connect(); first = not core.admin_is_set(conn); conn.close()
                return self._send(view_login(first))
            if u.path == "/logout":
                self.send_response(303)
                self.send_header("Location", "/login")
                self.send_header("Set-Cookie", f"{COOKIE}=; Path=/; Max-Age=0")
                self.end_headers()
                return
            if u.path == "/guide":
                return self._send(view_guide(qs))
            if u.path.startswith("/guide-img/"):
                return self._guide_img(u.path[len("/guide-img/"):])
            if u.path == "/" and not self._admin_ok():
                return self._send(view_landing())   # 비로그인 메인 = 랜딩
            # 관리자 영역 게이트 — 공개 경로 외에는 로그인 필요
            if (u.path not in PUBLIC_GET and not u.path.startswith("/dl/")
                    and not u.path.startswith("/m/") and not self._admin_ok()):
                return self._redirect("/login")
            # 파트너 포털(공개) — DB 유무와 무관하게 동작
            if u.path == "/join":
                return self._send(view_join(qs))
            if u.path == "/find":
                return self._send(view_find(qs))
            if u.path == "/me":
                page = view_me(qs)
                if page is None:
                    return self._send(shell_portal("링크 오류", "",
                        "<div class=card><h2>유효하지 않은 링크</h2>"
                        "<p class=empty>링크를 다시 확인하거나 <a class=lk href=/join>여기서 등록</a>하세요.</p></div>"), 404)
                return self._send(page)
            if u.path == "/wall":
                if not db.is_postgres() and not db.db_path().exists():
                    return self._send(shell_portal("인증 보드", "", _no_db()))
                return self._send(view_wall(qs))
            if u.path == "/files":
                if not db.is_postgres() and not db.db_path().exists():
                    return self._send(shell_portal("자료실", "", _no_db()))
                return self._send(view_files(qs))
            if u.path.startswith("/dl/"):
                return self._download(u.path[4:])
            if u.path.startswith("/m/"):
                return self._media(u.path[3:])
            if u.path == "/feed":
                if not db.is_postgres() and not db.db_path().exists():
                    return self._send(shell_portal("글감 피드", "", _no_db()))
                return self._send(view_feed(qs))

            # 운영자 대시보드
            if not db.is_postgres() and not db.db_path().exists() and u.path != "/favicon.ico":
                body = shell("패밀리 파트너스", _no_db())
            elif u.path == "/":
                body = shell("대시보드", view_dashboard(qs))
            elif u.path == "/reminders":
                body = shell("보낼 메시지", view_reminders(qs))
            elif u.path == "/enforce":
                body = shell("강퇴 집행", view_enforce(qs))
            elif u.path == "/review":
                body = shell("제출 검수", view_review(qs))
            elif u.path == "/board":
                body = shell("활동 랭킹", view_board(qs))
            elif u.path == "/people":
                body = shell("인원", view_people(qs))
            elif u.path == "/library":
                body = shell("자료실", view_library(qs))
            elif u.path == "/partner":
                body = shell("파트너 상세", view_partner(qs))
            elif u.path == "/settle":
                body = shell("월 정산", view_settle(qs))
            elif u.path == "/drop":
                body = shell("오늘 글감", view_drop(qs))
            elif u.path == "/onboard":
                body = shell("온보딩", view_onboard(qs))
            elif u.path == "/favicon.ico":
                self.send_response(204); self.end_headers(); return
            else:
                return self._send(shell("404", "<div class=card>없는 페이지</div>"), 404)
        except Exception as e:  # 화면 깨지지 않게
            body = shell("오류", f"<div class=card><h2>오류</h2><pre>{esc(e)}</pre></div>")
        self._send(body)


# =========================================================================== #
# WSGI 진입점 (Vercel/서버리스) — 기존 Handler 라우팅을 소켓 없이 재사용
# =========================================================================== #
_SKIP_HEADERS = {"date", "server", "connection", "transfer-encoding"}


def _emit_via_handler(method, full_path, headers, body):
    h = Handler.__new__(Handler)
    h.command = method
    h.path = full_path
    h.request_version = "HTTP/1.1"
    h.requestline = f"{method} {full_path} HTTP/1.1"
    h.client_address = ("127.0.0.1", 0)
    h.rfile = io.BytesIO(body or b"")
    h.wfile = io.BytesIO()
    h._headers_buffer = []
    msg = HTTPMessage()
    for k, v in headers.items():
        msg[k] = v
    h.headers = msg
    (h.do_POST if method == "POST" else h.do_GET)()
    raw = h.wfile.getvalue()
    head, _, resp_body = raw.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status = "200 OK"
    if lines and b" " in lines[0]:
        status = lines[0].decode("latin1").split(" ", 1)[1]
    out_headers = []
    for line in lines[1:]:
        if b":" in line:
            k, v = line.split(b":", 1)
            k = k.decode("latin1").strip()
            if k.lower() not in _SKIP_HEADERS:
                out_headers.append((k, v.decode("latin1").strip()))
    return status, out_headers, resp_body


def wsgi_app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/") or "/"
    qs = environ.get("QUERY_STRING", "")
    full = path + (("?" + qs) if qs else "")
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    body = environ["wsgi.input"].read(length) if length else b""
    headers = {}
    if environ.get("CONTENT_TYPE"):
        headers["Content-Type"] = environ["CONTENT_TYPE"]
    if length:
        headers["Content-Length"] = str(length)
    if environ.get("HTTP_COOKIE"):
        headers["Cookie"] = environ["HTTP_COOKIE"]
    status, out_headers, resp_body = _emit_via_handler(method, full, headers, body)
    start_response(status, out_headers)
    return [resp_body]


app = wsgi_app  # WSGI/Vercel 진입점


def serve(port: int = 8000) -> None:
    # 배포 시: 환경변수 HOST=0.0.0.0 (외부 공개), PORT=8080 등으로 지정.
    # 로컬은 기본 127.0.0.1 (이 PC에서만).
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", port))
    srv = ThreadingHTTPServer((host, port), Handler)
    where = "0.0.0.0 (외부 공개)" if host == "0.0.0.0" else "localhost"
    print(f"운영 대시보드: http://{where}:{port}  (Ctrl+C 로 종료)")
    srv.serve_forever()
