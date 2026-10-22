import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from deploy.check_drop_authoring import digest, copy_text, environment, review_one, structure_preserved, same_source_revision
from deploy.collect_drop_sources import original_snapshot


class AuthoringEvidenceTests(unittest.TestCase):
    def test_reddit_200_wrapper_with_upstream_403_is_not_original(self):
        url = 'https://www.reddit.com/r/ClaudeAI/comments/abc/demo/'
        blocked = f'Title: \nURL Source: {url}\nWarning: Target URL returned error 403: Forbidden\nMarkdown Content:\n' + 'Blocked content. ' * 40
        with self.assertRaises(ValueError):
            original_snapshot(blocked.encode(), url)

    def test_installation_is_never_reported_as_review(self):
        self.assertFalse(environment()['humanize']['review_completed'])

    def test_real_humanize_gate_and_review_tampering(self):
        now = datetime.now(timezone.utc)
        source = {'url': 'https://www.reddit.com/r/n8n/comments/abc/demo/',
                  'published_at': now.isoformat()}
        row = {'body': '사례 작성자는 입력과 결과를 나눠서 확인했다고 설명함.',
               'sources': [source], 'format': 'standalone'}
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            def save(name, text):
                p = base / name
                p.write_text(text)
                return {'file': name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            review = {'copy_sha256': digest(row), 'humanize': {
                'before': save('before.txt', copy_text(row)),
                'after': save('after.md', copy_text(row) + '\n\n<!-- HUMANIZE-SUMMARY fixture -->'),
                'six_checks_passed': True}, 'sources': [{
                    'url': source['url'], 'original_read': True,
                    'snapshot': save('source.txt', 'I checked inputs and outputs. ' + now.isoformat()),
                    'quote': 'I checked inputs and outputs.', 'date_quote': now.isoformat(),
                    'published_at': now.isoformat()}]}
            self.assertEqual(review_one(row, review, base, now), [])
            row['body'] += ' 검증되지 않은 성과를 추가함.'
            self.assertEqual(review_one(row, review, base, now), ['copy_changed_after_review'])
            row['body'] = '사례 작성자는 입력과 결과를 나눠서 확인했다고 설명함.'
            (base / 'source.txt').write_text('Changed evidence')
            self.assertIn('missing_or_invalid_review_artifact', review_one(row, review, base, now))

    def test_structure_only_cannot_bypass_content_or_source_review(self):
        before = {'date': '2026-10-22', 'title': 'Test', 'body': 'Hook\n\nOriginal detail.',
                  'sources': [{'url': 'https://example.org/original'}], 'format': 'standalone'}
        after = dict(before, body='Hook', replies=['Original detail.'], format='case_thread')
        self.assertTrue(structure_preserved(before, after))
        after['replies'] = ['Invented result.']
        self.assertFalse(structure_preserved(before, after))
        after['replies'] = ['Original detail.']
        after['sources'] = [{'url': 'https://example.org/different'}]
        self.assertFalse(structure_preserved(before, after))

    def test_existing_source_revision_does_not_allow_new_topics_or_sources(self):
        before = {'date': '2026-10-22', 'title': 'Existing case', 'sources': [{'url': 'https://example.org/a'}], 'body': 'Old copy'}
        after = dict(before, body='Rewritten copy with details', replies=['Details'])
        self.assertTrue(same_source_revision(before, after))
        after['sources'] = [{'url': 'https://example.org/b'}]
        self.assertFalse(same_source_revision(before, after))
        self.assertFalse(same_source_revision({}, after))
