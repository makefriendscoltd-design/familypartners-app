"""Exercise partner submissions over HTTP without unreliable performance inputs."""
import json
import unittest
from html.parser import HTMLParser
from urllib.parse import urlencode

import test_video_library as fixture
from fp import core, db


class FormFields(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('input', 'select') and attrs.get('name'):
            self.fields[attrs['name']] = attrs


class SubmissionWithoutPerformanceTest(unittest.TestCase):
    setUp = fixture.VideoHTTPTest.setUp
    tearDown = fixture.VideoHTTPTest.tearDown
    request = fixture.VideoHTTPTest.request

    def submit(self, url, **extra):
        return self.request('POST', '/submit', urlencode(dict(t='token-a', url=url, **extra)),
                            headers={'Content-Type': 'application/x-www-form-urlencoded'})

    def submissions(self):
        conn = db.connect()
        try:
            return [dict(row) for row in conn.execute('SELECT * FROM submissions ORDER BY id')]
        finally:
            conn.close()

    def test_form_omits_metrics_and_does_not_require_a_drop(self):
        conn = db.connect()
        try:
            core.add_drop(conn, '테스트 글감', '테스트 본문', drop_date=core.iso(core.today()))
        finally:
            conn.close()
        code, _, body = self.request('GET', '/me?t=token-a')
        self.assertEqual(code, 200)
        fields = FormFields(body.decode()).fields
        self.assertNotIn('room', fields)
        self.assertNotIn('replies', fields)
        if 'drop_id' in fields:
            self.assertNotIn('required', fields['drop_id'])

    def test_submission_without_metrics_or_drop_records_attendance(self):
        self.assertEqual(self.submit('https://www.instagram.com/reel/example/')[0], 303)
        rows = self.submissions()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['valid'], 1)
        self.assertEqual(row['channel'], 'instagram')
        self.assertIsNone(row['drop_id'])
        self.assertIsNone(row['room_members'])
        self.assertIsNone(row['comments'])
        conn = db.connect()
        try:
            self.assertTrue(core.posted_today(conn, row['partner_id'], core.today()))
        finally:
            conn.close()
        self.assertIn('오늘 발행', self.request('GET', '/me?t=token-a')[2].decode())

    def test_stale_form_cannot_write_metrics_or_overwrite_history(self):
        conn = db.connect()
        try:
            pid = conn.execute("SELECT id FROM partners WHERE portal_token='token-a'").fetchone()['id']
            sid = core.add_submission(conn, pid, 'https://www.threads.net/@example/post/old',
                                      post_date='2026-09-01', room_members=42)
            conn.execute('UPDATE submissions SET comments=7, views=123, leads=3, perf_at=? WHERE id=?',
                         ('2026-09-02T12:00:00', sid))
            conn.commit()
        finally:
            conn.close()
        historical = self.submissions()[0]
        self.assertEqual(self.submit('https://www.instagram.com/p/new/', channel='threads',
                                     room='999', replies='888', views='777', leads='666')[0], 303)
        rows = self.submissions()
        self.assertEqual(rows[0], historical)
        for field in ('room_members', 'comments', 'views', 'leads', 'perf_at'):
            self.assertIsNone(rows[1][field], field)

    def test_channel_uses_real_host_and_overrides_wrong_selection(self):
        cases = [
            ('https://instagram.com/p/a', 'threads', 'instagram'),
            ('https://www.instagram.com/reel/b', 'threads', 'instagram'),
            ('https://threads.net/@example/post/c', 'instagram', 'threads'),
            ('https://www.threads.com/@example/post/d', 'instagram', 'threads'),
            ('https://m.threads.net/@example/post/e', 'instagram', 'threads'),
            ('https://instagram.com.example.org/p/f', 'blog', 'blog'),
            ('https://example.org/threads.com/post/g', 'blog', 'blog'),
            ('https://threads.net@example.org/post/h', 'blog', 'blog'),
        ]
        for url, selected, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(self.submit(url, channel=selected)[0], 303)
                self.assertEqual(self.submissions()[-1]['channel'], expected)

    def test_perf_redirects_to_activity_board(self):
        code, headers, _ = self.request('GET', '/perf', cookie=self.admin)
        self.assertIn(code, (302, 303, 307, 308))
        self.assertEqual(headers['Location'], '/board')
        code, _, body = self.request('GET', '/', cookie=self.admin)
        self.assertEqual(code, 200)
        self.assertNotIn('href="/perf"', body.decode())
        self.assertNotIn("href='/perf'", body.decode())

    def test_public_status_explicitly_disables_old_performance_rankings(self):
        code, _, body = self.request('GET', '/status')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['perf'],
                         {'enabled': False, 'reason': 'unreliable_post_attribution'})
