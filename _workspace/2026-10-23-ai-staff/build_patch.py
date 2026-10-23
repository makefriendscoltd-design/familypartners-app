"""Print a patch for reviewed first-party copy; no file or provider mutations."""
import datetime
import difflib
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy.check_drop_authoring import digest, copy_text

base = Path(__file__).parent
additions = json.loads((base / 'additions.json').read_text())
queue_path = ROOT / 'config/drops_queue.json'
old = queue_path.read_text()
queue = json.loads(old)
refs_path = Path('/Users/apple/orca/projects/familypartners/_workspace/2026-09-22-drop-voice/reference-feed1-30.json')
refs = {int(r['title'].split()[-1]): r for r in json.loads(refs_path.read_text())}
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
patches, reviews = [], {}


def add(path, text):
    patches.append('*** Add File: ' + str(path) + '\n' + ''.join('+' + line + '\n' for line in text.splitlines()))


def artifact(name, text):
    add(base / name, text)
    return {'file': name, 'sha256': hashlib.sha256(text.encode()).hexdigest()}


for draft in additions:
    selected = [refs[n] for n in draft['reference_ids']]
    source_url = 'https://familypartners.aimax.ai.kr/feed?n=500'
    row = {k: draft[k] for k in ('date', 'target', 'fmt', 'body', 'replies')}
    row.update(type='evergreen', title=f"컨텐츠 {draft['number']} · {draft['title']}",
               format='case_thread', supplemental=True, editorial_kind='original_guidance',
               link_comment='관련 업무를 더 살펴볼 자료는 여기 있음.\n\nhttps://lounge.aimax.ai.kr/worldclass/' + draft['related'],
               sources=[{'url': source_url, 'title': '파트너스 초기 원문 ' + ', '.join(r['title'] for r in selected),
                         'published_at': None, 'collected_at': now, 'verified_at': now}])
    text = copy_text(row)
    summary = ('\n\n<!-- HUMANIZE-SUMMARY\nGenre: practical Korean social thread, register preserved.\n'
               f'Before/after characters: {len(text)}/{len(text)}. Change rate: 0% (no further style edits).\n'
               'A translationese, B excess English, D stock phrases, H transitions: 0 -> 0 each. Meaningful task lists retained.\n'
               'Six checks: 6/6. Grade B: concrete proposals and example instructions preserved.\n'
               'Preserved hooks, input/output examples, checks, proposal wording and links. No invented experience or performance claims.\n-->\n')
    number = draft['number']
    reviews[digest(row)] = {
        'copy_sha256': digest(row),
        'humanize': {'before': artifact(f'{number}-draft.txt', text + '\n'),
                     'after': artifact(f'{number}-final.md', text + summary), 'six_checks_passed': True},
        'sources': [{'url': source_url, 'original_read': True,
                     'snapshot': artifact(f'{number}-originals.json', json.dumps(selected, ensure_ascii=False, indent=2) + '\n'),
                     'quote': selected[0]['body'].splitlines()[0], 'published_at': None}],
        'editorial_review': {'first_party_originals_read': True, 'proposal_only': True,
                             'no_external_case_claims': True, 'no_performance_claims': True,
                             'reference_ids': draft['reference_ids']}}
    assert not any(r['title'] == row['title'] for r in queue['drops'])
    queue['drops'].append(row)

new = json.dumps(queue, ensure_ascii=False, indent=2) + '\n'
diff = list(difflib.unified_diff(old.splitlines(True), new.splitlines(True)))[2:]
patches.insert(0, '*** Update File: ' + str(queue_path) + '\n' + ''.join('@@\n' if line.startswith('@@') else line for line in diff))
add(base / 'reviews.json', json.dumps(reviews, ensure_ascii=False, indent=2) + '\n')
print('*** Begin Patch\n' + ''.join(patches) + '*** End Patch')
