#!/usr/bin/env python3
"""Poll explicitly authorized outputs; verify draft artifacts before publishing.
No producer execution, AI calls, browser, partner claims, or outbound messages.
"""
import argparse
import fcntl
import hashlib
import http.client
import json
import os
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
    return dict(key=key, video=str(video), sha256=sha, title=title, caption=caption, keyword=keyword)


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
        return json.loads(self.request('GET', '/op/videos/sync-status'))['videos']

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
    # Discover prior success even if an upload/publish response was lost.
    existing = next((r for r in api.status() if r['sha256'] == item['sha256']), None)
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
    api.form('/op/videos/publish', {'id': vid, 'published': 1})
    status = next(r for r in api.status() if r['id'] == vid)
    if not (status['published'] or status['claimed']):
        raise ValueError('publication_not_confirmed')
    record.update(status='published', verified_at=time.time())
    persist()
    emit(config, record); persist()
    return 'published'


def run(config, state_path, dry_run=False):
    state = read_json(state_path) if state_path.exists() else {'items': {}}
    state['last_run_at'] = time.time()
    def persist():
        save(state_path, state)
    api = None if dry_run else API(config['base_url'], Path(config['token_file']).read_text().strip())
    results = []
    for source in config['sources']:
        key = source['key']
        record = state['items'].setdefault(key, {})
        try:
            item = candidate(source['directory'], key, input_snapshots=config.get('input_snapshots'))
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
                checked = candidate(source['directory'], key, input_snapshots=config.get('input_snapshots'))
                if checked != item or digest(frozen) != item['sha256']:
                    raise ValueError('source_changed_during_check')
                item['video'] = str(frozen)
                status = import_one(api, item, record, persist, config)
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
