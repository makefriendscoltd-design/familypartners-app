"""Collect dated editorial candidates and the live lounge catalog; never publish.

Usage: python3 deploy/collect_drop_sources.py --out /tmp/drop-research
Feed entries are discovery only. Read the original and fact-check before drafting.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent


def lounge_notes(raw: bytes, base_url: str) -> list[dict]:
    """Read public Next.js page data without executing scripts or bypassing login."""
    chunks = []
    for match in re.finditer(r'self\.__next_f\.push\((.*?)\)</script>', raw.decode()):
        try:
            value = json.loads(match.group(1))
            if len(value) > 1 and isinstance(value[1], str):
                chunks.append(value[1])
        except (ValueError, TypeError):
            continue

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get('articles'), list) and isinstance(value.get('resources'), list):
                yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    for line in ''.join(chunks).splitlines():
        try:
            value = json.loads(line.split(':', 1)[1])
        except (ValueError, IndexError):
            continue
        for catalog in walk(value):
            rows = []
            for key, kind in [('articles', 'overseas_case'), ('resources', 'free_material')]:
                for row in catalog[key]:
                    slug = row.get('slug')
                    if not isinstance(slug, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', slug):
                        raise ValueError('Invalid lounge slug')
                    rows.append({'slug': slug, 'title': row['title'],
                                 'description': row.get('description'), 'kind': kind,
                                 'channel': row.get('channel'), 'topic': row.get('topic'),
                                 'url': f'{base_url}/{slug}', 'published_at': None,
                                 'body_verified': False, 'requires_auth': True})
            return rows
    raise ValueError('Current lounge catalog not found; do not substitute legacy resources')


def candidates(raw: bytes, feed_url: str, now: datetime, days: int) -> list[dict]:
    rows = []
    for item in ET.fromstring(raw).findall('.//item'):
        try:
            date = parsedate_to_datetime(item.findtext('pubDate') or '')
            if date.tzinfo is None:
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        url = (item.findtext('link') or '').strip()
        if not url.startswith('https://') or not now - timedelta(days=days) <= date <= now:
            continue
        rows.append({'title': item.findtext('title'), 'url': url,
                     'published_at': date.isoformat(), 'collected_at': now.isoformat(),
                     'discovery_feed': feed_url, 'original_read': False,
                     'status': 'candidate_not_verified'})
    return sorted(rows, key=lambda r: r['published_at'], reverse=True)


def collect(out: Path) -> dict:
    policy = json.loads((ROOT / 'config/assets.json').read_text())['_research']
    now = datetime.now(timezone.utc)
    out.mkdir(parents=True, exist_ok=False)
    result = {'collected_at': now.isoformat(), 'candidates': [], 'resources': [],
              'errors': [], 'snapshots': [], 'publication_allowed': False}

    def fetch(url: str, filename: str) -> bytes:
        req = urllib.request.Request(url, headers={'User-Agent': 'FamilyPartners-source-review/1.0'})
        with urllib.request.urlopen(req, timeout=25) as response:
            raw = response.read()
        (out / filename).write_bytes(raw)
        result['snapshots'].append({'url': url, 'file': filename,
                                    'sha256': hashlib.sha256(raw).hexdigest()})
        return raw

    for i, url in enumerate(policy['official_feeds']):
        try:
            raw = fetch(url, f'feed-{i}.xml')
            result['candidates'].extend(candidates(raw, url, now, policy['discovery_days']))
        except Exception as exc:
            result['errors'].append({'source': url, 'error': type(exc).__name__})
    try:
        result['resources'] = lounge_notes(
            fetch(policy['lounge_catalog_url'], 'lounge-notes.html'), policy['lounge_url'])
    except Exception as exc:
        result['errors'].append({'source': policy['lounge_catalog_url'], 'error': type(exc).__name__})
    result['candidates'].sort(key=lambda r: r['published_at'], reverse=True)
    (out / 'discovery.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    r = collect(args.out)
    print(json.dumps({'candidates': len(r['candidates']), 'lounge_resources': len(r['resources']),
                      'errors': r['errors'], 'output': str(args.out / 'discovery.json')}, ensure_ascii=False))
    raise SystemExit(1 if r['errors'] or not r['candidates'] or not r['resources'] else 0)
