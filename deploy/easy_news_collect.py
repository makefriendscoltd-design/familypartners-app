"""쉬운 AI 소식 소재 수집기 — X 검색(Aside 브라우저, 읽기 전용) + 원문 스냅샷.

    python3 deploy/easy_news_collect.py --out _workspace/easy-discovery-<시각>

- X 검색·글 열람은 Aside CLI `aside repl` 한 번에 한 페이지만 연다. 좋아요·게시·팔로우
  같은 쓰기 동작은 코드에 없다. Aside가 안 돌면 다른 경로로 대체하지 않고 실패한다.
- 이미 큐(sources·link_comment)에 있는 URL과 겹치는 글·바깥 링크는 뺀다.
- 바깥 링크: github는 `gh api`(저장소 정보+README), 그 밖의 웹은 r.jina.ai 스냅샷.
  로그인·에러 페이지면 그 후보는 버린다(사실 확인 불가).
- 결과: <out>/discovery.json, <out>/snap/*.txt. 수집기는 후보만 찾는다. 작성·검증·발행은 하지 않는다.
"""
from __future__ import annotations
import argparse, base64, datetime as dt, json, re, subprocess, sys, time
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl, urlencode

ROOT = Path(__file__).resolve().parents[1]
KST = dt.timezone(dt.timedelta(hours=9))
STATUS = re.compile(r'^https://(?:x|twitter)\.com/([A-Za-z0-9_]+)/status/(\d+)')
FAIL_MARKERS = ('target url returned error', 'blocked by network security', 'sign in to continue',
                'log in to continue', 'please log in', '로그인이 필요', 'access denied', '403 forbidden',
                'just a moment...', 'enable javascript and cookies', 'page not found', '404 not found')


class CollectError(RuntimeError):
    pass


def aside_eval(url: str, extract_js: str, wait_s: int = 10, scrolls: int = 0, timeout: int = 120):
    """Open exactly one page in Aside, run a read-only extractor, close the tab."""
    code = (f"const p = await openTab({json.dumps(url)});"
            f"await new Promise(r=>setTimeout(r,{wait_s * 1000}));"
            f"for (let i=0;i<{scrolls};i++){{ await p.evaluate(()=>window.scrollBy(0,2500)); await new Promise(r=>setTimeout(r,2000)); }}"
            f"const r = await p.evaluate({extract_js});"
            "console.log('R '+JSON.stringify(r));"
            "try{ await p.close(); }catch(e){}")
    try:
        p = subprocess.run(['aside', 'repl', code], text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CollectError(f'aside_unavailable: {e}') from e
    for line in p.stdout.splitlines():
        if line.startswith('R '):
            return json.loads(line[2:])
    raise CollectError(f'aside_no_result (exit {p.returncode}): {(p.stdout + p.stderr)[-400:]}')


SEARCH_JS = r"""()=>[...document.querySelectorAll('article')].map(a=>{
 const t=a.querySelector('time');const l=t&&t.closest('a');
 const g=a.querySelector('[role=group]');
 return {url:l?l.href:null,dt:t?t.getAttribute('datetime'):null,
  text:(a.querySelector('[data-testid=tweetText]')||{}).innerText||'',
  aria:g?g.getAttribute('aria-label'):''}})"""

POST_JS = r"""()=>[...document.querySelectorAll('article')].slice(0,14).map(a=>{
 const t=a.querySelector('time');const l=t&&t.closest('a');
 const u=a.querySelector('[data-testid="User-Name"]');
 const times=[...a.querySelectorAll('time')].map(x=>{const y=x.closest('a');return {dt:x.getAttribute('datetime'),href:y?y.href:''}});
 return {url:l?l.href:null,dt:t?t.getAttribute('datetime'):null,user:u?u.innerText:'',times,
  text:a.innerText.slice(0,5000),
  links:[...a.querySelectorAll('a[href]')].map(x=>x.href).filter(h=>!/^https:\/\/(x|twitter)\.com/.test(h))}})"""


def parse_dt(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def snowflake_time(status_id: str) -> dt.datetime:
    return dt.datetime.fromtimestamp(((int(status_id) >> 22) + 1288834974657) / 1000, dt.timezone.utc)


def canonical_status(url: str | None):
    m = STATUS.match(url or '')
    return f'https://x.com/{m.group(1)}/status/{m.group(2)}' if m else None


def reactions(aria: str) -> dict:
    out = {}
    for n, k in re.findall(r'([\d,]+)\s+(repl|repost|like|bookmark|view)', aria or ''):
        out[k] = int(n.replace(',', ''))
    return out


def clean_url(url: str) -> str:
    s = urlsplit(url)
    yt = (s.hostname or '').removeprefix('www.') in ('youtu.be', 'youtube.com', 'm.youtube.com')
    q = [(k, v) for k, v in parse_qsl(s.query)
         if not k.lower().startswith(('utm_', 'fbclid', 'ref_src')) and not (yt and k == 'si')]
    return urlunsplit((s.scheme, s.netloc, s.path, urlencode(q), ''))


def resolve(url: str) -> str:
    """Follow every redirect (t.co, then goo.gle/bit.ly ...) so filters see the real original."""
    host = urlsplit(url).hostname or ''
    if host != 't.co' and host not in SHORTENERS:
        return clean_url(url)
    p = subprocess.run(['curl', '-sL', '-o', '/dev/null', '-w', '%{url_effective}', '--max-redirs', '8',
                        '--max-time', '25', url], text=True, capture_output=True)
    final = p.stdout.strip()
    return clean_url(final) if final.startswith('https://') and (urlsplit(final).hostname or '') != 't.co' else ''


SHORTENERS = {'goo.gle', 'bit.ly', 'buff.ly', 'han.gl', 'me2.do', 'tinyurl.com', 'lnkd.in', 'ow.ly', 'url.kr', 'vo.la'}
PROMPT_LINE = re.compile(r'(해\s?줘|해주세요|해 주세요|줘)[.!]?\s*$|^\s*(prompt|프롬프트)\s*[:：]?', re.I | re.M)


def has_prompts(text: str) -> bool:
    """Copy-paste prompt collection: says 프롬프트/prompt and contains request sentences."""
    if re.search(r'프롬프트\s*(모음|공유|정리)|프롬프트\s*[:：]|prompt\s*[:：]', text, re.I):
        return True
    return bool(re.search(r'프롬프트|prompt', text, re.I)) and len(PROMPT_LINE.findall(text)) >= 2


def promo_reason(text: str, url: str, cfg: dict) -> str | None:
    h = (urlsplit(url).hostname or '').removeprefix('www.')
    if any(h == x or h.endswith('.' + x) for x in cfg.get('exclude_hosts', [])):
        return 'promo_host_' + h
    if any(pat in url for pat in cfg.get('exclude_url_patterns', [])):
        return 'promo_url'
    for word in cfg.get('exclude_text_patterns', []):
        if word in text:
            return 'promo_text_' + word
    return None


def is_internal(url: str) -> bool:
    h = (urlsplit(url).hostname or '').removeprefix('www.')
    return (h in ('x.com', 'twitter.com', 't.co') or h.endswith(('.x.com', '.twitter.com'))
            or not url.startswith('https://'))


def used_urls(queue: dict) -> set[str]:
    used = set()
    for r in queue.get('drops', []):
        for s in r.get('sources') or []:
            used.add(clean_url(s.get('url', '')).rstrip('/'))
        for u in re.findall(r'https://\S+', r.get('link_comment') or ''):
            used.add(clean_url(u).rstrip('/'))
        for im in r.get('main_images') or []:
            if im.get('source_url'):
                used.add(clean_url(im['source_url']).rstrip('/'))
    return used


def search(keyword: str, cfg: dict, since: str) -> list[dict]:
    q = f"{keyword} lang:ko since:{since} min_faves:{cfg['min_faves']} -filter:replies"
    url = 'https://x.com/search?q=' + quote(q) + '&src=typed_query&f=top'
    rows = aside_eval(url, SEARCH_JS, wait_s=cfg.get('wait_seconds', 10), scrolls=cfg.get('search_scrolls', 2))
    out = []
    for r in rows:
        u = canonical_status(r.get('url'))
        if u and r.get('dt'):
            out.append({'url': u, 'search_dt': r['dt'], 'search_text': r.get('text', ''),
                        'reactions': reactions(r.get('aria', '')), 'keyword': keyword})
    return out


def snapshot_post(url: str, cfg: dict) -> dict:
    """Main post + the author's own thread replies, in the review snapshot format."""
    arts = aside_eval(url, POST_JS, wait_s=cfg.get('wait_seconds', 10), scrolls=1)
    if not arts:
        raise CollectError('x_post_empty')
    handle = STATUS.match(url).group(1).lower()
    main = arts[0]
    if f'@{handle}' not in (main.get('user') or '').lower():
        raise CollectError('x_post_not_loaded')
    # The first <time> can belong to a quoted post; take the one linking to this status id
    # and require it to agree with the timestamp encoded in the id itself.
    sid = STATUS.match(url).group(2)
    own = [t['dt'] for t in main.get('times') or [] if t.get('dt') and f'/status/{sid}' in (t.get('href') or '')]
    if not own or abs((parse_dt(own[0]) - snowflake_time(sid)).total_seconds()) > 120:
        raise CollectError('x_post_datetime_unverified')
    main['dt'] = own[0]
    thread = [a for a in arts[1:] if f'@{handle}' in (a.get('user') or '').lower()][:8]
    text = f"URL: {url}\ndatetime: {main['dt']}\n\n{main['text'].strip()}\n"
    if thread:
        text += '\n===== THREAD/REPLIES =====\n\n' + '\n\n'.join(a['text'].strip() for a in thread) + '\n'
    links = []
    for a in [main, *thread]:
        for h in a.get('links') or []:
            if h not in links:
                links.append(h)
    return {'datetime': main['dt'], 'text': text.replace('\r\n', '\n'), 'raw_links': links}


def fetch_github(owner: str, repo: str) -> tuple[str, str]:
    info = json.loads(subprocess.run(['gh', 'api', f'repos/{owner}/{repo}'], text=True, capture_output=True,
                                     check=True, timeout=40).stdout)
    readme = ''
    p = subprocess.run(['gh', 'api', f'repos/{owner}/{repo}/readme'], text=True, capture_output=True, timeout=40)
    if p.returncode == 0:
        readme = base64.b64decode(json.loads(p.stdout).get('content', '')).decode('utf-8', 'replace')
    title = f"GitHub - {info['full_name']}: {info.get('description') or ''}".strip()
    text = (f"Title: {title}\n\nURL Source: {info['html_url']}\n\nstars: {info.get('stargazers_count')}\n"
            f"language: {info.get('language')}\nlicense: {(info.get('license') or {}).get('spdx_id')}\n"
            f"updated: {info.get('pushed_at')}\n\nREADME:\n{readme[:30000]}\n")
    return title, text


def fetch_web(url: str) -> tuple[str, str]:
    p = subprocess.run(['curl', '-s', '--max-time', '60', 'https://r.jina.ai/' + url], text=True,
                       capture_output=True, timeout=90)
    text = p.stdout
    low = text[:4000].lower()
    if p.returncode or len(text) < 400 or any(m in low for m in FAIL_MARKERS) or 'warning: target url returned error' in low:
        raise CollectError('web_snapshot_failed')
    m = re.search(r'^Title:\s*(.+)$', text, re.M)
    return (m.group(1).strip() if m else urlsplit(url).hostname), text[:40000]


def fetch_external(url: str) -> tuple[str, str, str]:
    s = urlsplit(url)
    parts = [x for x in s.path.split('/') if x]
    if (s.hostname or '').removeprefix('www.') == 'github.com' and len(parts) >= 2:
        title, text = fetch_github(parts[0], parts[1])
        return 'github', title, text
    title, text = fetch_web(url)
    return 'web', title, text


def slug(url: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', url.lower().split('://', 1)[-1])[:60].strip('-')


def collect(out: Path, queue: dict, cfg: dict, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(KST)
    since = (now - dt.timedelta(days=cfg['days'])).date().isoformat()
    out.mkdir(parents=True, exist_ok=True)
    snap = out / 'snap'
    snap.mkdir(exist_ok=True)
    used = used_urls(queue)
    log = {'started_at': now.isoformat(), 'since': since, 'searches': [], 'excluded': []}
    found: dict[str, dict] = {}
    for kw in cfg['keywords'][:cfg.get('max_queries', len(cfg['keywords']))]:
        rows = search(kw, cfg, since)  # CollectError propagates: no silent fallback
        log['searches'].append({'keyword': kw, 'results': len(rows)})
        for r in rows:
            if r['url'].rstrip('/') in used:
                log['excluded'].append({'url': r['url'], 'reason': 'already_in_queue'})
                continue
            if snowflake_time(r['url'].rsplit('/', 1)[1]) < now - dt.timedelta(days=cfg['days']):
                continue
            found.setdefault(r['url'], r)
    if not log['searches'] or not any(s['results'] for s in log['searches']):
        raise CollectError('x_search_returned_nothing')
    ranked = sorted(found.values(), key=lambda r: -r['reactions'].get('like', 0))
    candidates = []
    per_handle: dict[str, int] = {}
    attempts = 0
    for r in ranked:
        if len(candidates) >= cfg.get('max_candidates', 20) or attempts >= cfg['snapshot_limit']:
            break
        key = 'x-' + r['url'].rsplit('/', 1)[1]
        handle = STATUS.match(r['url']).group(1).lower()
        why = promo_reason(r['search_text'], '', cfg)
        if why:  # cheap pre-filter on the search card before opening the post
            log['excluded'].append({'url': r['url'], 'reason': why})
            continue
        if per_handle.get(handle, 0) >= cfg.get('max_per_handle', 3):
            log['excluded'].append({'url': r['url'], 'reason': 'handle_limit'})
            continue
        attempts += 1
        try:
            post = snapshot_post(r['url'], cfg)
        except CollectError as e:
            log['excluded'].append({'url': r['url'], 'reason': str(e)})
            continue
        (snap / f'{key}.txt').write_text(post['text'], encoding='utf-8')
        external = None
        for raw in post['raw_links']:
            # t.co hides the real short link, so check the first hop and the final URL.
            hop = raw if urlsplit(raw).hostname != 't.co' else subprocess.run(
                ['curl', '-s', '-o', '/dev/null', '-w', '%{redirect_url}', '--max-time', '15', raw],
                text=True, capture_output=True).stdout.strip()
            why = promo_reason('', hop, cfg)
            if why:
                external = {'url': hop, 'error': why}
                break
            link = resolve(hop or raw)
            if not link or is_internal(link):
                continue
            why = promo_reason('', link, cfg)
            if why:
                external = {'url': link, 'error': why}
                break
            if link.rstrip('/') in used:
                external = {'url': link, 'error': 'already_in_queue'}
                break
            if any((c['external'] or {}).get('url') == link for c in candidates):
                external = {'url': link, 'error': 'same_original_as_other_candidate'}
                break
            try:
                kind, title, text = fetch_external(link)
            except (CollectError, subprocess.SubprocessError, ValueError, KeyError) as e:
                external = {'url': link, 'error': str(e) or type(e).__name__}
                break
            name = 'ext-' + slug(link) + '.txt'
            (snap / name).write_text(text.replace('\r\n', '\n'), encoding='utf-8')
            external = {'url': link, 'kind': kind, 'title': title, 'file': 'snap/' + name}
            break
        if external and external.get('error'):
            log['excluded'].append({'url': r['url'], 'reason': 'external_' + external['error'], 'external': external['url']})
            continue
        why = promo_reason(post['text'], '', cfg)
        if why:
            log['excluded'].append({'url': r['url'], 'reason': why})
            continue
        prompts = has_prompts(post['text'])
        if not external and not prompts:
            # Opinion/advice posts without an original or copy-paste prompts are not easy news.
            log['excluded'].append({'url': r['url'], 'reason': 'no_original_link_or_prompts'})
            continue
        category = ('a' if external['kind'] == 'github' else 'b') if external else 'c'
        per_handle[handle] = per_handle.get(handle, 0) + 1
        candidates.append({'key': key, 'url': r['url'], 'handle': handle, 'category_hint': category,
                           'has_prompts': prompts, 'datetime': post['datetime'],
                           'published_at': post['datetime'].replace('.000Z', '+00:00').replace('Z', '+00:00'),
                           'reactions': r['reactions'], 'keyword': r['keyword'],
                           'file': f'snap/{key}.txt', 'external': external})
    result = {**log, 'finished_at': dt.datetime.now(KST).isoformat(), 'candidates': candidates}
    (out / 'discovery.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--queue', type=Path, default=ROOT / 'config/drops_queue.json')
    a = p.parse_args()
    cfg = json.loads((ROOT / 'config/drop_maintenance.json').read_text())['easy_news']
    try:
        r = collect(a.out, json.loads(a.queue.read_text()), cfg)
    except CollectError as e:
        print(json.dumps({'error': str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({'candidates': len(r['candidates']), 'excluded': len(r['excluded'])}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
