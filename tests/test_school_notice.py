"""School notice must never reuse a legacy product's referral link."""
import unittest
import test_video_library as fixture
from fp import onboard, core, db


class SchoolNoticeTest(unittest.TestCase):
    setUp = fixture.VideoHTTPTest.setUp
    tearDown = fixture.VideoHTTPTest.tearDown
    request = fixture.VideoHTTPTest.request

    def test_all_partner_types_use_school_only_and_preserve_legacy_storage(self):
        conn = db.connect()
        try:
            core.update_links(conn, 'token-a', {'LINK_AIMAX': 'https://example.org/old-course'})
            core.update_links(conn, 'token-a', {'LINK_SCHOOL': 'https://example.org/my-school'})
            links = onboard.links_of(core.find_by_token(conn, 'token-a'))
            self.assertEqual(links['LINK_AIMAX'], 'https://example.org/old-course')
            for kind in ('family', 'aimax', 'both'):
                notice = onboard.notice_text(kind, links)
                self.assertIn('https://aixschool.kr/admission/', notice)
                self.assertIn('https://example.org/my-school', notice)
                self.assertNotIn('old-course', notice)
                self.assertNotIn('{{', notice)
                self.assertEqual(onboard.type_slots(kind)[0][0], 'LINK_SCHOOL')
        finally:
            conn.close()

    def test_public_copy_page_and_partner_workspace(self):
        for path in ('/notice', '/me?t=token-a'):
            code, _, body = self.request('GET', path)
            self.assertEqual(code, 200)
            html = body.decode()
            self.assertIn('공통공지 전체 복사', html)
            self.assertIn('https://aixschool.kr/admission/', html)
            self.assertNotIn('{{LINK_SCHOOL}}', html)
            if path == '/notice':
                self.assertNotIn('token-a', html)
            else:
                self.assertLess(html.index('id=school-notice'), html.index('id=setup'))
                self.assertIn('name=\'link_LINK_SCHOOL\'', html)
