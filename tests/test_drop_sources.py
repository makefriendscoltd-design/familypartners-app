import unittest
import json
from datetime import datetime, timezone

from deploy.collect_drop_sources import candidates, lounge_notes, community_candidates, catalog_rows
from deploy.lint_drops import check_one


class SourceCollectionTests(unittest.TestCase):
    def test_hycli_rejects_rendered_first_page_and_login_payloads(self):
        for raw in ({'items': [{'title': 'Only the visible first page'}]},
                    {'text': 'Log in'}, {'articles': [], 'resources': []}):
            with self.assertRaises(ValueError):
                catalog_rows(raw, 'https://example.org/worldclass')
        payload = {'articles': [{'slug': 'case', 'title': 'Case'}],
                   'resources': [{'slug': 'case', 'title': 'Duplicate'}]}
        with self.assertRaises(ValueError):
            catalog_rows(payload, 'https://example.org/worldclass')

    def test_community_search_rejects_offsite_removed_and_duplicates(self):
        raw = '''Title: Blog fallback
URL: https://example.com/post
Title: Actual post
URL: https://www.reddit.com/r/n8n/comments/abc123/demo/
Title: Duplicate
URL: https://www.reddit.com/r/n8n/comments/abc123/demo/?utm_source=search
Title: Removed
URL: https://www.reddit.com/r/n8n/comments/abc124/demo/
Text: [removed]
Title: X post
URL: https://x.com/creator/status/12345
'''
        now = datetime(2026, 10, 19, tzinfo=timezone.utc)
        reddit = community_candidates(raw, 'reddit', now)
        self.assertEqual(len(reddit), 1)
        self.assertIsNone(reddit[0]['published_at'])
        self.assertFalse(reddit[0]['original_read'])
        self.assertEqual(len(community_candidates(raw, 'x', now)), 1)
        with self.assertRaises(ValueError):
            community_candidates("You've hit Exa's free MCP rate limit.", 'x', now)

    def test_current_notes_distinguish_free_material_from_overseas_case(self):
        props = {'articles': [{'slug': 'case-1', 'title': 'Case'}],
                 'resources': [{'slug': 'free-1', 'title': 'Material'}]}
        flight = '22:' + json.dumps(['$', 'component', None, props]) + '\n'
        html = '<script>self.__next_f.push(' + json.dumps([1, flight]) + ')</script>'
        rows = lounge_notes(html.encode(), 'https://example.org/worldclass')
        self.assertEqual([r['kind'] for r in rows], ['overseas_case', 'free_material'])
        self.assertTrue(all(not r['body_verified'] and r['published_at'] is None for r in rows))
        with self.assertRaises(ValueError):
            lounge_notes(b'<html>Login</html>', 'https://example.org/worldclass')

    def test_excludes_old_future_and_undated_discovery(self):
        raw = b'''<rss><channel>
        <item><title>recent</title><link>https://example.org/recent</link><pubDate>Sun, 18 Oct 2026 00:00:00 GMT</pubDate></item>
        <item><title>old</title><link>https://example.org/old</link><pubDate>Thu, 01 Oct 2026 00:00:00 GMT</pubDate></item>
        <item><title>future</title><link>https://example.org/future</link><pubDate>Tue, 20 Oct 2026 00:00:00 GMT</pubDate></item>
        <item><title>unknown</title><link>https://example.org/unknown</link></item>
        </channel></rss>'''
        rows = candidates(raw, 'https://example.org/feed', datetime(2026, 10, 19, tzinfo=timezone.utc), 7)
        self.assertEqual([r['title'] for r in rows], ['recent'])
        self.assertFalse(rows[0]['original_read'])

    def test_new_copy_requires_real_source_record_but_preserves_reservations(self):
        cfg = {'sources_required_after': '2026-10-24'}
        d = {'date': '2026-10-25', 'type': 'marketing', 'body': '오늘 할 일부터 적어봐'}
        self.assertTrue(any('sources' in x for x in check_one(d, cfg)))
        d['date'] = '2026-10-24'
        self.assertEqual(check_one(d, cfg), [])

    def test_source_timestamp_is_not_substituted_for_publication_date(self):
        cfg = {'sources_required_after': '2026-10-24'}
        source = {'url': 'https://example.org/post', 'title': 'Original',
                  'published_at': None, 'collected_at': '2026-10-19T00:00:00Z',
                  'verified_at': '2026-10-19T00:01:00Z'}
        d = {'date': '2026-10-25', 'type': 'marketing', 'body': '오늘 할 일부터 적어봐', 'sources': [source]}
        self.assertEqual(check_one(d, cfg), [])
        source['verified_at'] = ''
        self.assertTrue(any('verified_at' in x for x in check_one(d, cfg)))
