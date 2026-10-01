"""Count actual submitted SNS posts without URL aliases or duplicate ownership."""
from datetime import date
import unittest

import test_video_library as fixture
from fp import activity, core, db


class PostIdentityTest(unittest.TestCase):
    def test_tracking_and_route_aliases_identify_the_same_post(self):
        self.assertEqual(activity.post_identity('https://www.instagram.com/reel/ABC_123/?utm_source=x#top'),
                         activity.post_identity('https://instagram.com/p/ABC_123/'))
        self.assertEqual(activity.post_identity('https://threads.net/@old/post/ABC_123?x=1'),
                         activity.post_identity('https://www.threads.com/@new/post/ABC_123'))
        self.assertEqual(activity.post_identity('https://m.threads.com/t/ABC_123'),
                         activity.post_identity('https://threads.net/@old/post/ABC_123'))
        self.assertNotEqual(activity.post_identity('https://instagram.com/p/ABC_123'),
                            activity.post_identity('https://threads.com/t/ABC_123'))
        self.assertEqual(activity.post_identity('https://www.threads.com/share/SHARED?x=1'),
                         ('threads', 'share:SHARED'))
        self.assertEqual(activity.post_identity('https://threads.net/share/SHARED#top'),
                         ('threads', 'share:SHARED'))

    def test_profiles_non_post_urls_and_spoof_hosts_are_not_posts(self):
        for url in ('https://instagram.com/example/', 'https://threads.com/@example',
                    'https://instagram.com.example.org/p/A', 'https://example.org/instagram.com/p/A',
                    'https://threads.com@example.org/t/A', 'https://instagram.com/p/',
                    '(봐주기)', 'not a url'):
            with self.subTest(url=url):
                self.assertIsNone(activity.post_identity(url))


class ActivityRankingTest(unittest.TestCase):
    setUp = fixture.VideoHTTPTest.setUp
    tearDown = fixture.VideoHTTPTest.tearDown
    request = fixture.VideoHTTPTest.request
    AS_OF = date(2026, 10, 1)

    def add(self, conn, pid, url, day='2026-09-29', channel='threads', valid=1):
        sid = core.add_submission(conn, pid, url, channel=channel, post_date=day)
        conn.execute('UPDATE submissions SET valid=?, submitted_at=? WHERE id=?',
                     (valid, day + 'T12:00:00+09:00', sid))
        conn.commit()
        return sid

    def test_alias_duplicates_and_cross_partner_reposts_count_once_at_original_week(self):
        conn = db.connect()
        try:
            self.add(conn, 1, 'https://instagram.com/reel/SAME/?tracking=old', '2026-09-25')
            self.add(conn, 2, 'https://www.instagram.com/p/SAME/#new', '2026-09-29')
            self.add(conn, 1, 'https://threads.net/@old/post/T1', channel='instagram')
            self.add(conn, 2, 'https://threads.com/@new/post/T1?tracking=x', '2026-09-30')
            self.add(conn, 2, 'https://instagram.com/tv/T1', channel='threads')
            result = activity.summary(conn, 1, as_of=self.AS_OF)
            self.assertEqual(result['total_posts'], 3)
            self.assertEqual(result['channel_totals'], {'threads': 1, 'instagram': 2})
            self.assertEqual(result['weekly_posts'], 2)
            self.assertEqual(result['me']['count'], 1)
            self.assertEqual(result['me']['total'], 2)
            self.assertEqual(result['week_start'], '2026-09-28')
            self.assertEqual(result['week_end'], '2026-10-04')
        finally:
            conn.close()

    def test_invalid_future_and_non_post_records_cannot_claim_ownership(self):
        conn = db.connect()
        try:
            self.add(conn, 1, 'https://instagram.com/p/REAL', '2026-09-24', valid=0)
            self.add(conn, 2, 'https://instagram.com/reels/REAL', '2026-09-29')
            self.add(conn, 1, 'https://threads.com/t/FUTURE', '2026-10-02')
            self.add(conn, 1, 'https://instagram.com/example/')
            self.add(conn, 1, '(봐주기)', channel='manual')
            result = activity.summary(conn, 2, as_of=self.AS_OF)
            self.assertEqual(result['total_posts'], 1)
            self.assertEqual(result['weekly_posts'], 1)
            self.assertEqual(result['me']['count'], 1)
            self.assertEqual(result['me']['rank'], 1)
        finally:
            conn.close()

    def test_ties_competition_rank_and_own_rank_outside_first_five(self):
        conn = db.connect()
        try:
            for pid in range(3, 9):
                conn.execute('INSERT INTO partners(id,name,portal_token,joined_date) VALUES(?,?,?,?)',
                             (pid, f'합성{pid}', f'token-{pid}', '2026-09-01'))
            conn.commit()
            for pid, count in enumerate((7, 7, 6, 5, 4, 3, 2, 0), start=1):
                for n in range(count):
                    self.add(conn, pid, f'https://threads.com/t/P{pid}N{n}')
            result = activity.summary(conn, 7, as_of=self.AS_OF)
            self.assertEqual([r['rank'] for r in result['leaders'][:5]], [1, 1, 3, 4, 5])
            self.assertEqual(result['me'], {'count': 2, 'rank': 7, 'total': 2})
            self.assertEqual(result['participants'], 7)
            self.assertIsNone(activity.summary(conn, 8, as_of=self.AS_OF)['me']['rank'])
        finally:
            conn.close()

    def test_week_boundary_and_inactive_historical_totals(self):
        conn = db.connect()
        try:
            self.add(conn, 1, 'https://threads.com/t/SUNDAY', '2026-09-27')
            self.add(conn, 1, 'https://threads.com/t/MONDAY', '2026-09-28')
            self.add(conn, 2, 'https://instagram.com/p/PAUSED', '2026-09-28')
            conn.execute("UPDATE partners SET status='paused' WHERE id=2")
            conn.commit()
            sunday = activity.summary(conn, 1, as_of=date(2026, 9, 27))
            monday = activity.summary(conn, 1, as_of=date(2026, 9, 28))
            self.assertEqual(sunday['me']['count'], 1)
            self.assertEqual(monday['me']['count'], 1)
            self.assertEqual(monday['total_posts'], 3)
            self.assertEqual(monday['weekly_posts'], 1)
            self.assertEqual(monday['participants'], 1)
            self.assertEqual(monday['me']['total'], 2)
        finally:
            conn.close()

    def test_empty_summary_and_real_workspace_activity_position(self):
        conn = db.connect()
        try:
            result = activity.summary(conn, 1, as_of=self.AS_OF)
            self.assertEqual(result['total_posts'], 0)
            self.assertEqual(result['weekly_posts'], 0)
            self.assertEqual(result['participants'], 0)
            self.assertEqual(result['leaders'], [])
            self.assertEqual(result['me'], {'count': 0, 'rank': None, 'total': 0})
        finally:
            conn.close()
        code, _, body = self.request('GET', '/me?t=token-a')
        self.assertEqual(code, 200)
        html = body.decode()
        self.assertLess(html.index('id=submit'), html.index('id=activity'))
        self.assertLess(html.index('id=activity'), html.index('내 제출 이력'))
        panel = html.split('id=activity ', 1)[1].split('</section>', 1)[0]
        metrics = panel.split('<details', 1)[0]
        self.assertNotIn('조회수', metrics)
        self.assertIn('누적 등록 링크', metrics)

    def test_registration_time_controls_kst_week_and_ownership_not_post_date(self):
        conn = db.connect()
        try:
            old = self.add(conn, 1, 'https://threads.com/share/SAME', '2026-09-29')
            duplicate = self.add(conn, 2, 'https://threads.net/share/SAME?x=1', '2026-09-27')
            boundary = self.add(conn, 1, 'https://instagram.com/p/BOUNDARY', '2026-09-27')
            invalid = self.add(conn, 1, 'https://threads.com/t/INVALID', '2026-09-29')
            future = self.add(conn, 1, 'https://threads.com/t/FUTURETIME', '2026-09-29')
            for sid, timestamp in (
                (old, '2026-09-28T09:00:00+09:00'),
                (duplicate, '2026-09-27T14:00:00Z'),
                (boundary, '2026-09-27T15:00:00Z'),
                (invalid, 'invalid-date'),
                (future, '2026-10-01T15:00:00Z'),
            ):
                conn.execute('UPDATE submissions SET submitted_at=? WHERE id=?', (timestamp, sid))
            conn.commit()
            result = activity.summary(conn, 1, as_of=self.AS_OF)
            self.assertEqual(result['total_posts'], 2)
            self.assertEqual(result['weekly_posts'], 1)
            self.assertEqual(result['me']['total'], 1)
            self.assertEqual(result['me']['count'], 1)
            self.assertEqual(activity.summary(conn, 2, as_of=self.AS_OF)['me']['total'], 1)
        finally:
            conn.close()
