#!/usr/bin/env python3
"""카드뉴스 묶음(build_deck.py 결과)을 파트너 카드뉴스방에 올린다.

  sync_cardnews.py --config config/partner_video_sync.json --state-dir ~/.local/share/familypartners-video-sync

config 의 cardnews.decks_root 아래 <원본키>/BUNDLES.json 이 있는 폴더만 본다.
템플릿 ZIP 하나 = 서버 묶음 하나. 올린 뒤 원본·썸네일·캡션·원본 링크를 되읽어 확인한다.
"""
import argparse
import fcntl
import hashlib
import io
import json
import time
from pathlib import Path
from urllib.parse import quote

import sync_partner_videos as sv

CTA = '댓글에 AIMAX 남기면\n관련 정보 보내드릴게요.'


def caption(content):
    cover = content['slides'][0]['f']
    title = cover.get('title', '').replace('\n', ' ').strip()
    sub = cover.get('sub', '').replace('\n', ' ').strip()
    return f'{title}\n{sub}\n\n{CTA}' if sub else f'{title}\n\n{CTA}'


def thumbnail(png):
    from PIL import Image
    buf = io.BytesIO()
    Image.open(png).convert('RGB').resize((540, 540)).save(buf, 'JPEG', quality=85)
    return buf.getvalue()


def upload_deck(api, deck_dir, template, content, record):
    bundle = deck_dir / f'{template}.zip'
    sha = sv.digest(bundle)
    existing = next((d for d in api.cardnews_status()['decks'] if d['sha256'] == sha), None)
    if existing and existing['has_thumbnail'] and existing['has_caption']:
        record.update(id=existing['id'], sha256=sha, status='uploaded')
        return 'already_uploaded'
    if not existing:
        existing = api.upload('/op/cardnews/upload', bundle, {
            'Content-Type': 'application/zip', 'X-Template': template,
            'X-Deck-Title': quote(content['slides'][0]['f']['title'].replace('\n', ' '))})
    did = existing['id']
    thumb = thumbnail(deck_dir / template / 'png' / '01.png')
    api.request('POST', f'/op/cardnews/thumbnail/{did}', thumb, {'Content-Type': 'image/jpeg'})
    text = caption(content)
    api.form(f'/op/cardnews/caption/{did}', {'sha256': sha, 'caption': text})
    if content.get('source_url'):
        api.form(f'/op/cardnews/source/{did}', {'sha256': sha, 'source_url': content['source_url']})
    if hashlib.sha256(api.request('GET', f'/cardnews/file/{did}')).hexdigest() != sha:
        raise ValueError('remote_deck_hash_mismatch')
    if hashlib.sha256(api.request('GET', f'/cardnews/thumb/{did}')).hexdigest() != hashlib.sha256(thumb).hexdigest():
        raise ValueError('remote_thumbnail_hash_mismatch')
    row = next(d for d in api.cardnews_status()['decks'] if d['id'] == did)
    if not row['has_caption'] or row.get('source_url') != content.get('source_url'):
        raise ValueError('remote_metadata_mismatch')
    record.update(id=did, sha256=sha, status='uploaded', verified_at=time.time())
    return 'uploaded'


def run(config, state_path):
    root = Path(config['cardnews']['decks_root'])
    state = sv.read_json(state_path) if state_path.exists() else {'items': {}}
    api = sv.API(config['base_url'], Path(config['token_file']).read_text().strip())
    results = []
    for deck_dir in sorted(p for p in root.iterdir() if (p / 'BUNDLES.json').is_file()):
        content = json.loads((deck_dir / 'content.json').read_text())
        for template in json.loads((deck_dir / 'BUNDLES.json').read_text())['made']:
            template = template.removesuffix('.zip')
            key = f'{deck_dir.name}/{template}'
            record = state['items'].setdefault(key, {})
            if record.get('status') == 'uploaded':
                continue
            try:
                status = upload_deck(api, deck_dir, template, content, record)
                record.pop('error', None)
            except Exception as exc:  # noqa: BLE001 — 한 묶음 실패가 나머지를 막지 않게
                status = 'blocked'
                record['error'] = (str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__)[:100]
            results.append({'key': key, 'status': status})
            sv.save(state_path, state)
    state['last_finished_at'] = time.time()
    sv.save(state_path, state)
    print(json.dumps({'results': results}, ensure_ascii=False))
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--state-dir', required=True)
    args = parser.parse_args()
    directory = Path(args.state_dir)
    with (directory / 'cardnews.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"status":"already_running"}'); return
        run(sv.read_json(args.config), directory / 'cardnews-state.json')


if __name__ == '__main__':
    main()
