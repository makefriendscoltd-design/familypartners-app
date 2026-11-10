"""쉬운 AI 소식(editorial_kind: easy_news) 하루 6개 작성 경로.

drop_maintenance.py 가 config `"mode": "easy_news"` 일 때 부른다.
수집(easy_news_collect) -> 모델 초안(agent-run, 읽기 전용) -> 컨트롤러가 row·이미지·리뷰를
만들고 lint_drops + check_drop_authoring 을 통과해야만 candidate 를 돌려준다.
모델은 증거 문장을 다시 치지 않고 스냅샷 앵커 ID만 고른다.
"""
from __future__ import annotations
import datetime as dt, hashlib, json, os, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy.check_drop_authoring import copy_text, digest, review_one  # noqa: E402
from deploy.easy_news_collect import collect  # noqa: E402

CFG = json.loads((ROOT / 'config/drop_maintenance.json').read_text())
EASY = CFG.get('easy_news', {})
KST = dt.timezone(dt.timedelta(hours=9))
# 브리프 금지 문자: 이모지, 화살표, 가운뎃점, ※, 체크 표시
BANNED = re.compile('[←-⇿➡⬅-⬇·‧・※✅❌✔⭐️'
                    '\U0001f000-\U0001faff☀-➿]')
CREDIT_TAIL = ' · 원본 참고 화면, 파트너의 직접 실행 결과 아님'


class BuildError(RuntimeError):
    def __init__(self, errors):
        super().__init__('; '.join(map(str, errors))[:2000])
        self.errors = list(errors)


def stamp():
    return dt.datetime.now(KST).isoformat(timespec='seconds')


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def anchor_map(text, prefix, limit=400, skip=('URL:', 'datetime:', 'URL Source:')):
    seen, out = set(), {}
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 8 or line in seen or line.startswith(('http://', 'https://', *skip)):
            continue
        # Markdown link/image/hashtag-only lines are navigation, not evidence.
        if len(re.sub(r'!?\[[^\]]*\]\([^)]*\)|[#*|>\s]', '', line)) < 8:
            continue
        seen.add(line)
        out[f'{prefix}{len(out) + 1:03d}'] = line
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- source pack
def source_pack(w: Path, day: str, queue: dict, discovery=None) -> dict:
    target = w / 'discovery'
    if discovery and Path(discovery).resolve() != target.resolve():
        shutil.copytree(discovery, target)  # snapshots must live under the review folder
    if not (target / 'discovery.json').exists():
        collect(target, queue, EASY)  # CollectError (Aside down etc.) stops the run
    data = json.loads((target / 'discovery.json').read_text())
    cands = data['candidates']
    if len(cands) < EASY.get('min_candidates', 6):
        raise RuntimeError(f'not_enough_easy_news_candidates ({len(cands)}); see {target / "discovery.json"}')
    sources = {}
    for c in cands:
        xfile = target / c['file']
        xtext = xfile.read_text()
        src = {'key': c['key'], 'url': c['url'], 'published_at': c['published_at'], 'date_quote': c['datetime'],
               'file': str(xfile.relative_to(w)), 'reactions': c.get('reactions', {}),
               'handle': c.get('handle') or c['url'].split('/')[3].lower(),
               'category_hint': c.get('category_hint'), 'has_prompts': c.get('has_prompts', False),
               'evidence_anchors': anchor_map(xtext, 'x'), 'external': None}
        if c.get('external'):
            efile = target / c['external']['file']
            etext = efile.read_text()
            src['external'] = {'url': c['external']['url'], 'kind': c['external']['kind'],
                               'title': c['external']['title'][:200], 'file': str(efile.relative_to(w)),
                               'evidence_anchors': anchor_map(etext, 'e')}
        sources[c['key']] = src
    easy = [r for r in queue['drops'] if r.get('editorial_kind') == 'easy_news']
    examples = [{k: r[k] for k in ('type', 'target', 'fmt', 'body', 'replies', 'link_comment')} for r in easy[-6:-3]]
    pack = {'day': day, 'collected_at': data['finished_at'], 'sources': sources, 'examples': examples,
            'recent_titles': [r['title'] for r in queue['drops'][-40:]]}
    save(w / 'source-pack.json', pack)
    return pack


# ---------------------------------------------------------------- authoring
def output_schema():
    schema = json.loads((ROOT / 'deploy/drop_maintenance_easy_schema.json').read_text())
    lim = EASY['reply_count']
    item = schema['properties']['posts']['items']['properties']
    for k in ('replies', 'before_replies'):
        item[k].update(minItems=lim['min'], maxItems=lim['max'])
    schema['properties']['posts'].update(minItems=EASY['per_day'], maxItems=EASY['per_day'])
    return schema


def author(w: Path, pack: dict, out: Path, feedback=None):
    prompt = (ROOT / 'deploy/drop_maintenance_easy_prompt.md').read_text()
    prompt += f'\nRun request: {w}/source-pack.json\nThe only day to author: {pack["day"]}.\n'
    prompt += f'Each post needs {EASY["reply_count"]["min"]} to {EASY["reply_count"]["max"]} replies.\n'
    proper = json.loads((ROOT / 'config/assets.json').read_text())['_고유명사']
    prompt += '\nThe ai-type post must name its actual tool in body or replies, matching one of: ' + json.dumps(proper, ensure_ascii=False) + '\n'
    if feedback:
        prompt += ('\nREWRITE: your previous output (' + feedback['previous_output'] + ') failed the controller checks below. '
                   'Fix every listed problem, keep everything else that was valid, and return all six posts again.\n'
                   + json.dumps(feedback['errors'], ensure_ascii=False, indent=1) + '\n')
    name = out.stem
    (w / f'{name}.prompt.txt').write_text(prompt)
    save(w / 'easy-output-schema.json', output_schema())
    env = dict(os.environ, LOOPGUARD_CODEX_MODEL=CFG['model'])
    cmd = [CFG['agent_runner'], '--only', 'codex', '--request-scoped-timeout', '--timeout', str(CFG['model_timeout_seconds']),
           '--cwd', str(ROOT), '--output-schema', str(w / 'easy-output-schema.json'), '-o', str(out), '--json', '-']
    p = subprocess.run(cmd, input=prompt, cwd=ROOT, text=True, capture_output=True, env=env,
                       timeout=CFG['model_timeout_seconds'] + 90)
    (w / f'{name}.agent.log').write_text(p.stdout + '\n' + p.stderr)
    if p.returncode:
        raise RuntimeError('author_engine_failed; see ' + str(w / f'{name}.agent.log'))
    return json.loads(out.read_text())['posts']


# ---------------------------------------------------------------- checks
def first_paragraph(body):
    return body.strip().split('\n\n', 1)[0].strip()


def is_question(body):
    return first_paragraph(body).rstrip().endswith('?')


IMPERATIVE_NOT = re.compile(r'(마셈|마세요|마라|지 마|지마|말 것|말기)[.!]?$')


def is_imperative_hook(body):
    """'~하지 마셈' style hooks read as advice, not news (user feedback 2026-11-10)."""
    return bool(IMPERATIVE_NOT.search(' '.join(first_paragraph(body).split('\n')).rstrip()))


def text_errors(i, p):
    """Mechanical brief rules that lint_drops does not cover."""
    errs = []
    label = f'post {i + 1}'
    parts = [p['body'], *p['replies'], p['link_intro']]
    for part in parts:
        if BANNED.search(part):
            errs.append(f'{label}: 금지 문자(이모지·화살표·가운뎃점·※) "{BANNED.search(part).group()}"')
            break
    for part in parts:
        bad = [l for l in part.split('\n') if l.lstrip().startswith('[')]
        if bad:
            errs.append(f'{label}: 줄 맨 앞이 "[" 로 시작함: {bad[0][:20]}')
            break
    if re.search(r'https?://|www\.', '\n'.join(parts)):
        errs.append(f'{label}: 본문·댓글·link_intro 에 URL 금지')
    intro = p['link_intro'].strip()
    if not intro or '\n' in intro or len(intro) > EASY['max_line_chars']:
        errs.append(f'{label}: link_intro 는 {EASY["max_line_chars"]}자 이하 한 줄')
    if len(first_paragraph(p['body']).split('\n')) > 2:
        errs.append(f'{label}: 첫 줄 후킹은 한 줄 또는 20자 이하 두 줄, 그 뒤 빈 줄')
    for row in p.get('card_rows') or []:
        for line in [row['label'], *row['text'].split('|')]:
            if len(line) > 30 or BANNED.search(line):
                errs.append(f'{label}: card_rows 한 줄 30자 이하·금지 문자 없음: {line[:20]}')
                break
    return errs


def precheck(pack, posts):
    errs = []
    n = EASY['per_day']
    if len(posts) != n:
        return [f'posts must be exactly {n}']
    lim = EASY['reply_count']
    keys = [p['source_key'] for p in posts]
    if len(set(keys)) != len(keys):
        errs.append('source_key 가 겹침 — 글마다 다른 소재')
    for i, p in enumerate(posts):
        if p['source_key'] not in pack['sources']:
            errs.append(f'post {i + 1}: unknown source_key {p["source_key"]}')
            continue
        s = pack['sources'][p['source_key']]
        if not lim['min'] <= len(p['replies']) <= lim['max']:
            errs.append(f'post {i + 1}: replies {len(p["replies"])}개 (허용 {lim["min"]}~{lim["max"]})')
        if p['evidence_anchor'] not in s['evidence_anchors']:
            errs.append(f'post {i + 1}: evidence_anchor {p["evidence_anchor"]} 가 해당 X 스냅샷 앵커에 없음')
        ext = s.get('external')
        if ext and p['link_evidence_anchor'] not in ext['evidence_anchors']:
            errs.append(f'post {i + 1}: 바깥 링크가 있는 소재는 link_evidence_anchor 를 그 원문 앵커(e...)에서 골라야 함')
        want = s.get('category_hint')
        if p['category'] != want:
            errs.append(f'post {i + 1}: category {p["category"]} 가 소재 유형 {want} 와 다름 (a 깃허브·도구, b 공식 기능·무료 강의, c 복붙 프롬프트 모음)')
        if p['category'] == 'c' and not s.get('has_prompts'):
            errs.append(f'post {i + 1}: 프롬프트 모음(c)인데 원문에 복붙 프롬프트가 없음 — 조언 글은 쓰지 말 것')
        if not ext and not p.get('card_rows'):
            errs.append(f'post {i + 1}: 바깥 링크 없는 소재는 이미지 카드(card_title/card_sub/card_rows 3~6개) 필요')
        if p['original_read'] is not True or p['six_checks_passed'] is not True:
            errs.append(f'post {i + 1}: original_read/six_checks_passed 가 true 가 아님')
        errs += text_errors(i, p)
    types = [p['type'] for p in posts]
    reg = EASY['regular_types']
    if sorted(types[:len(reg)]) != sorted(reg) or any(t != 'evergreen' for t in types[len(reg):]):
        errs.append(f'type 구성: 앞 {len(reg)}개는 {reg} 를 하나씩, 나머지는 evergreen')
    for field in ('target', 'fmt'):
        vals = [p[field] for p in posts[:len(reg)]]
        if len(set(vals)) != len(vals):
            errs.append(f'앞 {len(reg)}개 글의 {field} 가 겹침: {vals}')
    if sum(p['fmt'] == '리스트형' for p in posts) > 0:
        errs.append("fmt 에 '리스트형' 쓰지 말 것")
    handles = {}
    for p in posts:
        h = pack['sources'].get(p['source_key'], {}).get('handle')
        handles[h] = handles.get(h, 0) + 1
    over = [h for h, n in handles.items() if n > EASY.get('max_per_handle_per_day', 2)]
    if over:
        errs.append(f'같은 계정 출처 하루 {EASY.get("max_per_handle_per_day", 2)}개 초과: {over}')
    imp = sum(is_imperative_hook(p['body']) for p in posts)
    if imp > EASY.get('max_imperative_hooks', 1):
        errs.append(f'"~마셈/~지 마" 명령형 첫 줄 {imp}개 (하루 최대 {EASY.get("max_imperative_hooks", 1)}개) — 결과·손해·대상 지정·"~하는 법"·"이거 보셈"으로')
    q = sum(is_question(p['body']) for p in posts)
    if q > 1:
        errs.append(f'질문형 첫 줄 {q}개 (하루 최대 1개)')
    return errs


# ---------------------------------------------------------------- images
def render_images(w, jobs):
    if not jobs:
        return {}
    spec = w / 'image-jobs.json'
    save(spec, {'playwright': EASY['playwright_module'], 'jobs': jobs})
    p = subprocess.run(['node', str(ROOT / 'deploy/easy_news_images.mjs'), str(spec)], text=True,
                       capture_output=True, timeout=120 + 60 * len(jobs))
    with (w / 'images.log').open('a') as log:
        log.write(p.stdout + '\n' + p.stderr)
    results = {}
    for line in p.stdout.splitlines():
        if line.startswith('IMG '):
            r = json.loads(line[4:])
            results[r['out']] = r
    return results


def image_for(w, pack, posts, tag):
    """Screenshot the outside original when there is one, else a prompt card."""
    dest = ROOT / 'assets/thread-images'
    dest.mkdir(parents=True, exist_ok=True)
    shots, cards, plan = [], [], []
    for i, p in enumerate(posts):
        s = pack['sources'][p['source_key']]
        out = str(dest / f'fp-easy-{tag}-{i + 1}.png')
        card = {'title': p['card_title'], 'sub': p['card_sub'], 'rows': [[r['label'], r['text']] for r in p['card_rows']]}
        if s.get('external'):
            shots.append({'kind': 'shot', 'url': s['external']['url'], 'out': out})
        else:
            cards.append({'kind': 'card', 'card': card, 'out': out})
        plan.append((out, s, card))
    res = render_images(w, shots + cards)
    retry = [{'kind': 'card', 'card': c, 'out': o} for o, s, c in plan
             if s.get('external') and not res.get(o, {}).get('ok') and c['rows']]
    if retry:
        res.update(render_images(w, retry))
    images, errs = [], []
    for i, (out, s, card) in enumerate(plan):
        r = res.get(out, {})
        if not r.get('ok') or not Path(out).is_file() or Path(out).stat().st_size < 20000:
            errs.append(f'post {i + 1}: 이미지 생성 실패 ({r.get("error", "no output")})' +
                        (' — 원문 캡처가 막히면 card_rows 를 채워야 함' if s.get('external') else ''))
            images.append(None)
            continue
        if r['kind'] == 'shot':
            ext = s['external']
            where = ext['title'].split(':', 1)[0].strip() if ext['kind'] == 'github' else re.sub(r'\s*\([^)]*$', '', (ext['title'] or ext['url'])[:60]).strip()
            credit = f'{where} 원문 화면'
            source_url = ext['url']
        else:
            credit = '메이크패밀리 제작 프롬프트 정리 이미지'
            source_url = (s.get('external') or {}).get('url') or s['url']
        images.append({'src': '/thread-img/' + Path(out).name, 'alt': credit + CREDIT_TAIL, 'credit': credit,
                       'source_url': source_url, 'sha256': sha(out), 'path': out})
    return images, errs


# ---------------------------------------------------------------- rows
def link_comment(intro, url):
    return intro.strip() + '\n\n' + url


def make_row(day, number, p, s, image, collected_at, verified_at, supplemental):
    """Schema post -> queue row, same shape as the 2026-11-10~13 easy_news rows."""
    hook = ' '.join(first_paragraph(p['body']).split('\n'))
    ext = s.get('external')
    sources = [{'url': s['url'], 'title': 'X 글: ' + hook[:60], 'published_at': s['published_at'],
                'collected_at': collected_at, 'verified_at': verified_at}]
    if ext:
        sources.append({'url': ext['url'], 'title': ext['title'][:120] or ext['url'], 'published_at': None,
                        'collected_at': collected_at, 'verified_at': verified_at})
    row = {'date': day, 'title': f'컨텐츠 {number} · {hook}', 'type': p['type'], 'target': p['target'], 'fmt': p['fmt'],
           'format': 'case_thread', 'editorial_kind': 'easy_news'}
    if supplemental:
        row['supplemental'] = True
    row.update(body=p['body'].strip(), replies=[r.strip() for r in p['replies']],
               link_comment=link_comment(p['link_intro'], ext['url'] if ext else s['url']),
               sources=sources, main_images=[{k: v for k, v in image.items() if k != 'path'}] if image else [])
    return row


def review_for(w, row, p, s, before_file, after_file):
    ext = s.get('external')
    recs = [{'url': s['url'], 'original_read': True,
             'snapshot': {'file': s['file'], 'sha256': sha(w / s['file'])},
             'quote': s['evidence_anchors'][p['evidence_anchor']],
             'date_quote': s['date_quote'], 'published_at': s['published_at']}]
    if ext:
        recs.append({'url': ext['url'], 'original_read': True,
                     'snapshot': {'file': ext['file'], 'sha256': sha(w / ext['file'])},
                     'quote': ext['evidence_anchors'][p['link_evidence_anchor']],
                     'date_quote': None, 'published_at': None})
    return {'copy_sha256': digest(row),
            'humanize': {'before': {'file': before_file.name, 'sha256': sha(before_file)},
                         'after': {'file': after_file.name, 'sha256': sha(after_file)}, 'six_checks_passed': True},
            'sources': recs}


def parse_failures(text):
    return [l.strip() for l in text.splitlines() if l.strip().startswith(('✗', '↳'))][:40]


def build(w: Path, pack: dict, posts: list, queue: dict):
    errs = precheck(pack, posts)
    if errs:
        raise BuildError(errs)
    day = pack['day']
    tag = day.replace('-', '')
    start = max(int(m.group(1)) for a in queue['drops'] if (m := re.search(r'컨텐츠\s*(\d+)', a['title']))) + 1
    images, errs = image_for(w, pack, posts, tag)
    if errs:
        raise BuildError(errs)
    collected = pack['collected_at']
    verified = stamp()
    nreg = len(EASY['regular_types'])
    rows, reviews, review_errors = [], {}, []
    for i, p in enumerate(posts):
        s = pack['sources'][p['source_key']]
        row = make_row(day, start + i, p, s, images[i], collected, verified, i >= nreg)
        before = w / f'{start + i}-draft.txt'
        after = w / f'{start + i}-final.md'
        before.write_text(copy_text(dict(row, body=p['before_body'], replies=p['before_replies'])) + '\n')
        after.write_text(copy_text(row) + '\n\n<!-- HUMANIZE-SUMMARY\n' + p['humanize_summary'] + '\nSix checks passed: true\n-->\n')
        rev = review_for(w, row, p, s, before, after)
        e = review_one(row, rev, w, dt.datetime.now(dt.timezone.utc))
        if e:
            review_errors.append(f'post {i + 1}: {e}' + (' (윤문 전후 변경률 30% 미만이어야 함 — before 는 실제 초안)' if any('change_rate' in x for x in e) else ''))
        reviews[digest(row)] = rev
        rows.append(row)
    if review_errors:
        raise BuildError(review_errors)
    candidate = json.loads(json.dumps(queue))
    candidate['drops'].extend(rows)
    save(w / 'candidate.json', candidate)
    save(w / 'reviews.json', reviews)
    save(w / 'new-posts.json', rows)
    lint = subprocess.run([sys.executable, 'deploy/lint_drops.py', str(w / 'candidate.json'), '--include-published',
                           '--baseline', 'origin/main', '--reviews', str(w / 'reviews.json')],
                          cwd=ROOT, text=True, capture_output=True)
    (w / 'lint.log').write_text(lint.stdout + '\n' + lint.stderr)
    auth = subprocess.run([sys.executable, 'deploy/check_drop_authoring.py', '--baseline', 'origin/main',
                           '--reviews', str(w / 'reviews.json'), '--queue', str(w / 'candidate.json')],
                          cwd=ROOT, text=True, capture_output=True)
    (w / 'authoring.log').write_text(auth.stdout + '\n' + auth.stderr)
    errs = []
    if lint.returncode:
        errs += ['lint: ' + x for x in parse_failures(lint.stdout)] or ['lint_failed; see ' + str(w / 'lint.log')]
    if auth.returncode:
        try:
            errs += ['authoring: ' + json.dumps(f, ensure_ascii=False) for f in
                     json.loads(auth.stdout[auth.stdout.rindex('{\n  "changed"'):])['failures']]
        except ValueError:
            errs.append('authoring_failed; see ' + str(w / 'authoring.log'))
    if errs:
        raise BuildError(errs)
    save(w / 'editorial-review.json', [{'title': r['title'], 'review': p['editorial_note'], 'humanize': p['humanize_summary']}
                                       for r, p in zip(rows, posts)])
    return candidate, rows, [Path(im['path']) for im in images]


def write_preview(w: Path, rows: list) -> Path:
    out = [f'# 미리보기 {rows[0]["date"]} (dry-run, 배포 안 함)\n']
    for r in rows:
        out.append(f'\n## {r["title"]}\n')
        out.append(f'- type: {r["type"]}{" (supplemental)" if r.get("supplemental") else ""} / target: {r["target"]} / fmt: {r["fmt"]}')
        for s in r['sources']:
            out.append(f'- source: {s["url"]} (published_at: {s["published_at"]})')
        for im in r['main_images']:
            out.append(f'- image: {ROOT / "assets/thread-images" / Path(im["src"]).name}  (credit: {im["credit"]})')
        out.append('\n### 본문\n\n```\n' + r['body'] + '\n```')
        for n, reply in enumerate(r['replies'], 1):
            out.append(f'\n### 댓글 {n}\n\n```\n{reply}\n```')
        out.append('\n### 링크 댓글\n\n```\n' + r['link_comment'] + '\n```')
    path = w / 'preview.md'
    path.write_text('\n'.join(out) + '\n')
    return path
