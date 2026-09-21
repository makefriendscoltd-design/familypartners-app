#!/usr/bin/env python3
"""Poll explicitly authorized outputs; verify draft artifacts before publishing.
No producer execution, AI calls, browser, partner claims, or outbound messages
(only a once-a-day low-stock Telegram alert to the operator).
"""
import argparse
import fcntl
import hashlib
import http.client
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import quote, urlencode, urlsplit


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def save(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def bindings(value):
    if isinstance(value, dict):
        if 'path' in value and 'sha256' in value:
            yield value
        for child in value.values():
            yield from bindings(child)
    elif isinstance(value, list):
        for child in value:
            yield from bindings(child)


# Editorial limit for readable copy, not a claim about platform API limits.
CAPTION_MAX_CHARS = 300


def short_caption(script, keyword, title):
    """Keep source headings and the video's exact comment keyword; CTA cannot truncate."""
    if not re.fullmatch(r'[가-힣A-Za-z0-9_]{1,20}', keyword):
        raise ValueError('invalid_comment_keyword')
    if len(script) <= CAPTION_MAX_CHARS and script.endswith(f'댓글에 {keyword} 남기면\n이 영상 정리본 드릴게요.'):
        return script
    title = title.strip()
    if not title or len(title) > 100:
        raise ValueError('invalid_caption_title')
    cta = f'댓글에 {keyword} 남기면\n이 영상 정리본 드릴게요.'
    title = re.sub(r'([?!]) +', r'\1\n', title, count=1)
    headings = re.findall(r'(?:^|\n)\s*(?:첫째|둘째|셋째|넷째|다섯째)[,.]\s*([^\n]+)', script)
    points = []
    for heading in headings:
        point = re.split(r'(?<=[.!?])\s+', heading.strip())[0].rstrip('.')
        point = re.sub(r'입니다$', '', point).strip()
        # Omit overlong sentences as a whole; never cut a word or drop its caveat.
        if point and len(point) <= 60:
            candidate_points = points + ['• ' + point]
            result = title + '\n\n' + '\n'.join(candidate_points) + '\n\n' + cta
            if len(result) <= CAPTION_MAX_CHARS:
                points = candidate_points
        if len(points) == 3:
            break
    result = title + ('\n\n' + '\n'.join(points) if points else '') + '\n\n' + cta
    if len(result) > CAPTION_MAX_CHARS:
        raise ValueError('caption_too_long')
    return result


def candidate(root, key, min_age=60, input_snapshots=None):
    root = Path(root)
    video = root / 'final.mp4'
    if not video.is_file() or time.time() - video.stat().st_mtime < min_age:
        raise ValueError('incomplete_or_still_writing')
    gate = read_json(root / 'render_gate.json')
    machine = read_json(root / 'machine_validation.json')
    visual = read_json(root / 'visual_validation.json')
    manifest = read_json(root / 'production_manifest.json')
    if manifest['source_id'] != key:
        raise ValueError('source_id_mismatch')
    if any(x['status'] != 'pass' for x in (gate, machine, visual)):
        raise ValueError('quality_gate_not_pass')
    if Path(gate['video']).resolve() != video.resolve():
        raise ValueError('gate_video_path_mismatch')
    sha = digest(video)
    if not all(x == sha for x in (gate['video_sha256'], machine['final_sha256'], visual['video_sha256'])):
        raise ValueError('video_hash_mismatch')
    for field in ('render_inputs', 'content_lineage'):
        for bound in bindings(manifest[field]):
            path = Path(bound['path'])
            # Render evidence may bind a protected Downloads asset. A previously
            # hash-verified immutable snapshot is sufficient for that render;
            # scripts/CTA and item-local files must always be checked in place.
            external = root.resolve() not in path.resolve().parents
            if field == 'render_inputs' and external and input_snapshots:
                frozen = Path(input_snapshots) / bound['sha256']
                if frozen.is_file():
                    path = frozen
            if digest(path) != bound['sha256']:
                raise ValueError('input_hash_mismatch')
    lineage = manifest['content_lineage']
    for field, relative in [('script', '07_script_final.txt'), ('cta_transform', 'notebooklm/cta-transform.json')]:
        if Path(lineage[field]['path']).resolve() != (root / relative).resolve():
            raise ValueError('content_path_mismatch')
    caption = (root / '07_script_final.txt').read_text().strip()
    keyword = read_json(root / 'notebooklm/cta-transform.json')['comment_keyword'].strip()
    if not keyword or f'댓글에 {keyword}' not in caption or not 1 <= len(caption) <= 4000:
        raise ValueError('caption_or_keyword_invalid')
    title = manifest['title_candidate'].strip()
    if not title:
        raise ValueError('missing_title')
    return dict(key=key, video=str(video), sha256=sha, title=title, caption=short_caption(caption, keyword, title), keyword=keyword)


class API:
    def __init__(self, base, token):
        self.url = urlsplit(base)
        if self.url.scheme != 'https' and self.url.hostname not in ('localhost', '127.0.0.1'):
            raise ValueError('https_required')
        self.token = token

    def request(self, method, path, body=None, headers=None):
        cls = http.client.HTTPSConnection if self.url.scheme == 'https' else http.client.HTTPConnection
        conn = cls(self.url.hostname, self.url.port, timeout=180)
        hs = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json'}
        hs.update(headers or {})
        try:
            conn.request(method, path, body, hs)
            response = conn.getresponse()
            data = response.read()
            if response.status not in (200, 201, 303):
                raise RuntimeError(f'http_{response.status}')
            return data
        finally:
            conn.close()

    def status(self):
        return self.sync_status()['videos']

    def sync_status(self):
        return json.loads(self.request('GET', '/op/videos/sync-status'))

    def form(self, path, data):
        return self.request('POST', path, urlencode(data).encode(), {'Content-Type': 'application/x-www-form-urlencoded'})

    def upload(self, path, file, headers=None):
        hs = dict(headers or {}, **{'Content-Length': str(Path(file).stat().st_size)})
        with Path(file).open('rb') as body:
            return json.loads(self.request('POST', path, body, hs))


def emit(config, record):
    args = [config['crm_cli'], 'emit', '--source-system', 'familypartners', '--channel', 'web',
            '--campaign', 'partners-short-videos', '--stage', 'sent', '--status', 'success',
            '--count', '1', '--dedupe-key', 'partner-video-published-' + record['sha256']]
    result = subprocess.run(args, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('crm_emit_failed')
    record['crm_emitted'] = True


def import_one(api, item, record, persist, config):
    # A withdrawn file must never be republished by replaying an old batch.
    if item['sha256'] in config.get('excluded_sha256', {}):
        record.update(sha256=item['sha256'], status='withdrawn_by_review')
        persist()
        return 'excluded_by_review'
    item = dict(item, caption=short_caption(item['caption'], item['keyword'], item['title']))
    # Discover prior success even if an upload/publish response was lost.
    existing = next((r for r in api.status() if r['sha256'] == item['sha256']), None)
    if existing and existing.get('queued'):
        # The server publishes queued originals at KST midnight; CRM is emitted once it is live.
        record.update(id=existing['id'], sha256=item['sha256'], status='queued')
        persist()
        return 'queued'
    if existing and (existing['claimed'] or existing['published']):
        record.update(id=existing['id'], sha256=item['sha256'], status='published_or_claimed')
        persist()
        if record.get('publish_attempted') and not record.get('crm_emitted'):
            emit(config, record); persist()
        return 'already_available_or_claimed'
    if not existing:
        existing = api.upload('/op/videos/upload', item['video'],
                              {'X-Video-Title': quote(item['title']), 'X-File-Name': quote(item['key'] + '.mp4')})
    vid = existing['id']
    record.update(id=vid, sha256=item['sha256'], status='draft')
    persist()
    # Full original roundtrip catches truncated upload/storage before public listing.
    downloaded = api.request('GET', f'/videos/file/{vid}')
    if hashlib.sha256(downloaded).hexdigest() != item['sha256']:
        raise ValueError('remote_original_hash_mismatch')
    del downloaded
    with tempfile.TemporaryDirectory() as tmp:
        thumb = Path(tmp) / 'thumbnail.jpg'
        result = subprocess.run([config['ffmpeg'], '-nostdin', '-hide_banner', '-loglevel', 'error',
                                 '-ss', '1', '-i', item['video'], '-frames:v', '1', '-vf', 'scale=540:-2',
                                 '-q:v', '3', str(thumb)], capture_output=True, timeout=90)
        if result.returncode or not thumb.is_file():
            raise RuntimeError('thumbnail_failed')
        api.upload(f'/op/videos/thumbnail/{vid}', thumb, {'Content-Type': 'image/jpeg'})
        if hashlib.sha256(api.request('GET', f'/videos/thumb/{vid}')).hexdigest() != digest(thumb):
            raise ValueError('remote_thumbnail_hash_mismatch')
    api.form(f'/op/videos/caption/{vid}', {'sha256': item['sha256'], 'caption': item['caption']})
    if json.loads(api.request('GET', f'/videos/caption/{vid}'))['caption'] != item['caption']:
        raise ValueError('remote_caption_mismatch')
    record.update(status='verified', publish_attempted=True)
    persist()
    api.form('/op/videos/queue', {'id': vid, 'queued': 1})
    status = next(r for r in api.status() if r['id'] == vid)
    if not (status.get('queued') or status['published'] or status['claimed']):
        raise ValueError('queue_not_confirmed')
    record.update(status='queued', verified_at=time.time())
    persist()
    return 'queued'


LOW_STOCK_DAYS = 2


def check_stock(api, state, persist, notify=None):
    """Alert once per day when the queue covers fewer than LOW_STOCK_DAYS full refills."""
    st = api.sync_status().get('stock')
    if not st:
        return None
    days = st['queued'] // st['target']
    state['stock'] = dict(st, days=days, checked_at=time.time())
    today = time.strftime('%Y-%m-%d')
    if days < LOW_STOCK_DAYS and state.get('low_stock_alerted_day') != today:
        text = (f"[파트너스 영상] 대기열 {st['queued']}편 남음 (하루 최대 {st['target']}편 기준 {days}일치). "
                f"지금 받을 수 있는 영상 {st['available']}편. 새 숏폼을 대기열에 올려야 함.")
        if (notify or telegram)(text):
            state['low_stock_alerted_day'] = today
    persist()
    return days


def telegram(text):
    import sys
    sys.path.insert(0, str(Path.home() / 'orca/projects/loopguard/src'))
    try:
        from loopguard import notify
        return notify.send({}, text)[0]
    except Exception:
        return False


def discover(config):
    """Find newly produced shorts so a fresh render needs no config edit to ship."""
    auto = config.get('auto_discover')
    if not auto:
        return []
    root = Path(auto['root'])
    found = []
    for directory in sorted(root.glob(auto.get('pattern', '*/shorts'))):
        video = directory / 'final.mp4'
        if not video.is_file():
            continue
        # An old render that never shipped stays out; it needs a human look, not a surprise upload.
        if (time.time() - video.stat().st_mtime) / 86400 > auto.get('max_age_days', 14):
            continue
        stat = video.stat()
        found.append({'key': re.sub(r'-\d{8}$', '', directory.parent.name),
                      'directory': str(directory), 'auto': True,
                      'fingerprint': f'{stat.st_mtime_ns}:{stat.st_size}'})
    return found


HEADCOPY_MAX_PX = 920


def discover_cutback(config):
    """Clips cut from a longform by the cutback 롱폼숏폼화 tool: jobs/<id>/out/cNN.mp4 + plan.json."""
    auto = config.get('cutback_jobs')
    if not auto:
        return []
    found = []
    for out in sorted(Path(auto['root']).glob('*/out')):
        job = out.parent
        for video in sorted(out.glob('c*.mp4')):
            stat = video.stat()
            if (time.time() - stat.st_mtime) / 86400 > auto.get('max_age_days', 14):
                continue
            found.append({'key': f'cutback-{job.name}-{video.stem}', 'directory': str(job), 'video': str(video),
                          'kind': 'cutback', 'auto': True, 'fingerprint': f'{stat.st_mtime_ns}:{stat.st_size}'})
    return found


def headcopy_widths(video, ffmpeg):
    """Pixel width of each cyan/green headcopy line on a 1080-wide frame at 3s."""
    from io import BytesIO
    from PIL import Image
    frame = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-ss', '3', '-i', str(video), '-frames:v', '1',
                            '-vf', 'scale=1080:-2', '-f', 'image2pipe', '-vcodec', 'png', 'pipe:1'],
                           capture_output=True, timeout=60).stdout
    im = Image.open(BytesIO(frame)).convert('RGB').crop((0, 200, 1080, 700))
    width, height = im.size
    px = im.load()
    def ink(x, y):
        r, g, b = px[x, y]
        return (b > 140 and g > 120 and r < 140 and b - r > 60) or (g > 170 and r > 120 and b < 110 and g - b > 90)
    rows = [y for y in range(height) if sum(ink(x, y) for x in range(0, width, 2)) > 2]
    lines, start, prev = [], None, None
    for y in rows:
        if start is None:
            start = y
        elif y - prev > 12:
            lines.append((start, prev)); start = y
        prev = y
    if start is not None:
        lines.append((start, prev))
    widths = []
    for y0, y1 in (l for l in lines if l[1] - l[0] > 40):
        xs = [x for x in range(width) if any(ink(x, y) for y in range(y0, y1 + 1, 2))]
        widths.append(xs[-1] - xs[0] + 1)
    return widths


def cutback_candidate(source, config, min_age=60):
    job = Path(source['directory'])
    video = Path(source['video'])
    if not video.is_file() or time.time() - video.stat().st_mtime < min_age:
        raise ValueError('incomplete_or_still_writing')
    channel = read_json(job / 'meta.json').get('channel', '')
    # Only our own channels ship automatically; anything else needs a human decision first.
    if channel not in config['cutback_jobs'].get('channels', []):
        raise ValueError('channel_not_approved')
    clip = next((c for c in read_json(job / 'plan.json')['clips'] if c['id'] == video.stem), None)
    if not clip or not clip.get('head1', '').strip() or not clip.get('head2', '').strip():
        raise ValueError('missing_headcopy')
    probe = json.loads(subprocess.run([str(Path(config['ffmpeg']).with_name('ffprobe')), '-v', 'error', '-show_streams',
                                       '-show_format', '-of', 'json', str(video)], capture_output=True, timeout=60).stdout)
    streams = probe['streams']
    v = next(x for x in streams if x['codec_type'] == 'video')
    if v['height'] * 9 != v['width'] * 16 or v['width'] < 1080 or not any(x['codec_type'] == 'audio' for x in streams) \
            or float(probe['format']['duration']) > 180:
        raise ValueError('format_not_partner_short')
    widths = headcopy_widths(video, config['ffmpeg'])
    if len(widths) < 2 or max(widths[:2]) > HEADCOPY_MAX_PX:
        raise ValueError('headcopy_width_over_920px')
    head1, head2 = clip['head1'].strip(), clip['head2'].strip()
    caption = f"{head1}\n{head2}\n\n{clip['title'].strip()}\n\n댓글에 정리 남기면\n이 영상 정리본 드릴게요."
    if len(caption) > CAPTION_MAX_CHARS:
        raise ValueError('caption_too_long')
    return dict(key=source['key'], video=str(video), sha256=digest(video), title=f'{head1} {head2}',
                caption=caption, keyword='정리')


def check(source, config, input_snapshots=None):
    if source.get('kind') == 'cutback':
        return cutback_candidate(source, config)
    return candidate(source['directory'], source['key'], input_snapshots=input_snapshots)


def sources(config):
    listed = config['sources']
    known = {s['key'] for s in listed}
    return listed + [s for s in discover(config) + discover_cutback(config) if s['key'] not in known]


def run(config, state_path, dry_run=False):
    state = read_json(state_path) if state_path.exists() else {'items': {}}
    state['last_run_at'] = time.time()
    def persist():
        save(state_path, state)
    api = None if dry_run else API(config['base_url'], Path(config['token_file']).read_text().strip())
    results = []
    limit = config.get('auto_discover', {}).get('max_per_run', 5)
    started = 0
    for source in sources(config):
        key = source['key']
        record = state['items'].setdefault(key, {})
        # An unchanged original that already shipped is not re-hashed every five minutes.
        if source.get('auto') and record.get('done_fingerprint') == source['fingerprint']:
            results.append({'key': key, 'status': 'already_done'})
            continue
        # Cap first-time auto uploads per run so a backlog cannot flood one pass.
        first_time = bool(source.get('auto')) and not record.get('id')
        if first_time and started >= limit:
            results.append({'key': key, 'status': 'deferred_to_next_run'})
            continue
        try:
            item = check(source, config, input_snapshots=config.get('input_snapshots'))
            if record.get('sha256') and record['sha256'] != item['sha256']:
                raise ValueError('published_source_revision_requires_review')
            if dry_run:
                results.append({'key': key, 'status': 'ready', 'keyword': item['keyword']})
                continue
            # Snapshot upload input; a producer rewrite cannot mutate bytes in flight.
            with tempfile.TemporaryDirectory() as tmp:
                import shutil
                frozen = Path(tmp) / 'final.mp4'
                shutil.copyfile(item['video'], frozen)
                checked = check(source, config, input_snapshots=config.get('input_snapshots'))
                if checked != item or digest(frozen) != item['sha256']:
                    raise ValueError('source_changed_during_check')
                item['video'] = str(frozen)
                status = import_one(api, item, record, persist, config)
            if first_time and status != 'excluded_by_review':
                started += 1
            if source.get('auto') and status != 'draft':
                record['done_fingerprint'] = source['fingerprint']
            results.append({'key': key, 'status': status})
            record.pop('error', None)
        except FileNotFoundError:
            record['error'] = 'waiting_for_artifacts'
            results.append({'key': key, 'status': 'waiting_for_artifacts'})
        except Exception as exc:
            # Never include HTTP bodies, credentials, names, or script text in logs.
            error = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
            record['error'] = error[:100]
            results.append({'key': key, 'status': 'blocked', 'reason': error[:100]})
        if not dry_run:
            persist()
    if not dry_run:
        try:
            check_stock(api, state, persist)
        except Exception as exc:
            state['stock_error'] = type(exc).__name__
        state['last_finished_at'] = time.time(); persist()
    print(json.dumps({'dry_run': dry_run, 'results': results}, ensure_ascii=False))
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    directory = Path(args.state_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'sync.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"status":"already_running"}'); return
        run(read_json(args.config), directory / 'state.json', args.dry_run)


if __name__ == '__main__':
    main()
