"""HTTP contracts for the daily workspace and independent attendance flags."""
import json
import re
import unittest
from html.parser import HTMLParser

import test_video_library as fixture
from fp import core, db, onboard


class PageElements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.details = {}
        self.links = []
        self.ids = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'details' and 'id' in attrs:
            self.details[attrs['id']] = 'open' in attrs
        if tag == 'a':
            self.links.append(attrs.get('href'))


class WorkspaceLayoutTest(unittest.TestCase):
    setUp = fixture.VideoHTTPTest.setUp
    tearDown = fixture.VideoHTTPTest.tearDown
    request = fixture.VideoHTTPTest.request

    def page(self, path='/me?t=token-a', cookie=None):
        code, _, body = self.request('GET', path, cookie=cookie)
        self.assertEqual(code, 200)
        return body.decode()

    def complete_setup(self):
        conn = db.connect()
        try:
            links = {key: 'https://example.org/product/' + key.lower()
                     for key, _ in onboard.type_slots('family')}
            conn.execute("UPDATE partners SET handle=?, openchat_url=?, links_json=? "
                         "WHERE portal_token='token-a'",
                         ('test_partner', 'https://open.kakao.com/o/example', json.dumps(links)))
            conn.commit()
        finally:
            conn.close()

    def test_daily_submit_precedes_setup_and_beginner_setup_is_open(self):
        html = self.page()
        elements = PageElements(html)
        self.assertLess(html.index('id=submit'), html.index('id=setup'))
        self.assertTrue(elements.details['setup'])
        self.assertEqual(len(elements.ids), len(set(elements.ids)))

    def test_complete_setup_collapses_but_link_save_opens_notice(self):
        self.complete_setup()
        self.assertFalse(PageElements(self.page()).details['setup'])
        saved = self.page('/me?t=token-a&saved2=links')
        self.assertTrue(PageElements(saved).details['setup'])
        self.assertIn('notice', PageElements(saved).ids)
        self.assertIn('공지', saved)
        self.assertIn('직접 교체', saved)

    def test_partner_navigation_preserves_identity_without_admin_links(self):
        elements = PageElements(self.page())
        for path in ('/me?t=token-a', '/me?t=token-a#submit', '/feed?t=token-a',
                     '/videos?t=token-a', '/cardnews?t=token-a', '/files?t=token-a',
                     '/guide?t=token-a'):
            self.assertIn(path, elements.links)
        self.assertFalse(any(link and link.startswith('/op/') for link in elements.links))

    def test_today_submission_counts_even_when_yesterday_missed(self):
        conn = db.connect()
        try:
            pid = conn.execute("SELECT id FROM partners WHERE portal_token='token-a'").fetchone()['id']
            core.add_submission(conn, pid, 'https://threads.com/@test/post/today')
            board = core.daily_board(conn, core.today())
            self.assertIn(pid, [status.row['id'] for status in board['kick']])
        finally:
            conn.close()
        html = self.page('/', cookie=self.admin)
        numbers = re.findall(r"<div class=['\"]big[^'\"]*['\"]>(\d+)</div>"
                             r"<div class=lb>([^<]+)</div>", html)
        kpis = {label: int(number) for number, label in numbers}
        self.assertEqual(kpis['오늘 완료(미션완료)'], 1)
        self.assertEqual(kpis['오늘 미제출'], 1)
        self.assertIn('오늘 제출 완료 1명', html)
        self.assertIn('강퇴 대상', html)
        self.assertIn(f"/partner?id={pid}", html)
        self.assertLess(html.index('오늘 운영 현황'), html.index('id=content-management'))
        self.assertEqual(len(PageElements(html).ids), len(set(PageElements(html).ids)))
