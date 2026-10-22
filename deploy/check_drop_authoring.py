"""Read-only authoring preflight and review gate; never publishes or starts agents.

python3 deploy/check_drop_authoring.py
python3 deploy/check_drop_authoring.py --baseline origin/main --reviews /path/reviews.json
Reviews are required only for new/changed copy, not untouched reservations.
"""
from __future__ import annotations
import argparse
import re
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
HUMANIZE = Path.home() / '.local/share/im-not-ai'
COPY_FIELDS = ('date', 'title', 'body', 'replies', 'link_comment', 'format', 'sources', 'main_images')


def digest(row):
    return hashlib.sha256(json.dumps({k: row.get(k) for k in COPY_FIELDS},
                                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def copy_text(row):
    return '\n\n'.join([row.get('body', ''), *(row.get('replies') or []),
                         row.get('link_comment', '')]).strip()


def environment(root=ROOT):
    rules = HUMANIZE / 'skills/humanize-korean/references/quick-rules.md'
    gate = HUMANIZE / 'scripts/verify_change_rate.py'
    return {'project': str(root),
            'humanize': {'installed': rules.is_file() and gate.is_file(),
                         'rules': str(rules), 'gate': str(gate),
                         'review_completed': False},
            'commands': {k: shutil.which(k) for k in ('agent-reach', 'mcporter', 'fp-hycli')},
            'capability': {'exa': 'requires_live_probe', 'x': 'requires_original_probe',
                           'reddit': 'requires_original_probe',
                           'hycli': 'lounge_catalog_only'},
            'collector_present': (root / 'deploy/collect_drop_sources.py').is_file()}


def artifact(base, record):
    p = (base / record['file']).resolve()
    if not p.is_relative_to(base.resolve()):
        raise ValueError('Artifact must stay within the review directory')
    raw = p.read_bytes()
    if hashlib.sha256(raw).hexdigest() != record['sha256']:
        raise ValueError('Artifact hash mismatch')
    return p, raw.decode('utf-8')


def review_one(row, review, base, now, structure_only=False):
    """Deterministic evidence checks; human reading/meaning review stays explicit."""
    errors = []
    if review.get('copy_sha256') != digest(row):
        return ['copy_changed_after_review']
    try:
        before, _ = artifact(base, review['humanize']['before'])
        after, text = artifact(base, review['humanize']['after'])
        if text.split('<!-- HUMANIZE-SUMMARY', 1)[0].strip() != copy_text(row):
            errors.append('reviewed_copy_does_not_match_queue')
        if '<!-- HUMANIZE-SUMMARY' not in text or review['humanize'].get('six_checks_passed') is not True:
            errors.append('humanize_manual_review_missing')
        gate = HUMANIZE / 'scripts/verify_change_rate.py'
        result = subprocess.run([sys.executable, str(gate), '--before', str(before), '--after', str(after)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            errors.append('humanize_change_rate_requires_review_or_runtime_missing')
        if structure_only:
            return errors
        reviewed_sources = review['sources']
        for source in row.get('sources') or []:
            record = next((r for r in reviewed_sources if r.get('url') == source.get('url')), None)
            if not record or record.get('original_read') is not True:
                errors.append('original_review_missing')
                continue
            _, snapshot = artifact(base, record['snapshot'])
            if not record.get('quote') or record['quote'] not in snapshot:
                errors.append('literal_evidence_missing')
            if any(s in snapshot.lower() for s in ('target url returned error', 'blocked by network security')):
                errors.append('blocked_page_is_not_original')
            # Dates come from the saved original, never from search hints or collection time.
            host = (urlsplit(source['url']).hostname or '').removeprefix('www.')
            if host in ('reddit.com', 'x.com', 'twitter.com'):
                date = datetime.fromisoformat(str(source.get('published_at') or '').replace('Z', '+00:00'))
                if date.tzinfo is None or not now - timedelta(days=json.loads((ROOT / 'config/assets.json').read_text())['_research']['discovery_days']) <= date <= now:
                    errors.append('community_source_not_recent')
                if not record.get('date_quote') or record['date_quote'] not in snapshot:
                    errors.append('publication_date_evidence_missing')
                if record.get('published_at') != source.get('published_at'):
                    errors.append('publication_date_review_mismatch')
        if not row.get('sources'):
            errors.append('sources_missing')
    except (KeyError, ValueError, TypeError, OSError, subprocess.SubprocessError):
        errors.append('missing_or_invalid_review_artifact')
    return errors


def structure_preserved(before, after):
    allowed = {'body', 'replies', 'format'}
    metadata = lambda row: {k: v for k, v in row.items() if k not in allowed}
    return (metadata(before) == metadata(after)
            and re.sub(r'\s', '', copy_text(before)) == re.sub(r'\s', '', copy_text(after))
            and after.get('format') == 'case_thread')


def same_source_revision(before, after):
    # Rewriting an existing case still needs its saved original evidence; it is not new discovery.
    allowed = {'body', 'replies', 'link_comment'}
    return bool(before) and {k:v for k,v in before.items() if k not in allowed} == {k:v for k,v in after.items() if k not in allowed}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline', help='Git ref containing the queue before edits')
    p.add_argument('--reviews', type=Path)
    p.add_argument('--structure-only', action='store_true', help='Prove unchanged ordered text and sources; still require humanize review')
    p.add_argument('--existing-sources', action='store_true', help='Rewrite existing cases with unchanged source records; original and humanize evidence still required')
    a = p.parse_args()
    env = environment()
    print(json.dumps(env, ensure_ascii=False, indent=2))
    if not a.baseline:
        return 0 if env['humanize']['installed'] and env['collector_present'] else 1
    proc = subprocess.run(['git', 'show', f'{a.baseline}:config/drops_queue.json'],
                          cwd=ROOT, capture_output=True, text=True)
    if proc.returncode:
        print('Cannot read baseline; refusing to assume all rows reviewed')
        return 1
    originals = json.loads(proc.stdout)['drops']
    old_rows = {(r['date'], r['title']): r for r in originals}
    old = {digest(r) for r in originals}
    rows = json.loads((ROOT / 'config/drops_queue.json').read_text())['drops']
    changed = [r for r in rows if digest(r) not in old]
    if not changed:
        print('No changed copy; no new editorial review claimed')
        return 0
    if not a.reviews:
        print(f'BLOCKED: {len(changed)} changed posts require original + humanize review artifacts')
        return 1
    reviews = json.loads(a.reviews.read_text())
    failed = []
    now = datetime.now(timezone.utc)
    for row in changed:
        errors = review_one(row, reviews.get(digest(row), {}), a.reviews.parent, now, a.structure_only)
        if a.existing_sources and not same_source_revision(old_rows.get((row['date'], row['title']), {}), row):
            errors.append('existing_source_revision_changed_identity_or_sources')
        if a.structure_only and not structure_preserved(old_rows.get((row['date'], row['title']), {}), row):
            errors.append('structure_only_changed_content_or_source')
        if errors:
            failed.append({'title': row['title'], 'errors': errors})
    # Every changed day's full batch must contain a recent, reviewed community source.
    for day in ([] if a.structure_only or a.existing_sources else sorted({r['date'] for r in changed})):
        group = [r for r in rows if r['date'] == day]
        community = [r for r in group if any((urlsplit(s.get('url', '')).hostname or '').removeprefix('www.')
                     in ('reddit.com', 'x.com', 'twitter.com') for s in r.get('sources', []))]
        if not any(not review_one(r, reviews.get(digest(r), {}), a.reviews.parent, now) for r in community):
            failed.append({'date': day, 'errors': ['no_verified_recent_community_source_in_daily_batch']})
    print(json.dumps({'changed': len(changed), 'failures': failed}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
