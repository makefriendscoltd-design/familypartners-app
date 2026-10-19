import json
import unittest
from unittest.mock import patch
import test_video_library as fixture
from fp import db, core, thread_copy, dropqueue

class ThreadCopyTest(unittest.TestCase):
    setUp = fixture.VideoHTTPTest.setUp
    tearDown = fixture.VideoHTTPTest.tearDown
    request = fixture.VideoHTTPTest.request

    def test_feed_and_workspace_show_separate_blocks(self):
        entries=json.loads(dropqueue.QUEUE_PATH.read_text())['drops'][-2:]
        c=db.connect()
        for d in entries:
            core.add_drop(c,d['title'],d['body'],drop_date=d['date'])
        c.close()
        for path in ['/feed', '/me?t=token-a']:
            status,_,body=self.request('GET',path)
            self.assertEqual(status,200)
            html=body.decode()
            for label in ['본문 복사','댓글 1 복사','마지막 댓글 · 자료 링크 복사']:
                self.assertIn(label,html)
            self.assertIn('https://lounge.aimax.ai.kr/worldclass/zuA9204Dbb8',html)

    def test_admin_body_edit_cannot_keep_stale_replies_and_html_is_escaped(self):
        d=json.loads(dropqueue.QUEUE_PATH.read_text())['drops'][-1]
        row=dict(d,drop_date=d['date'],body='<script>alert(1)</script>')
        rendered=thread_copy.render(row)
        self.assertNotIn('댓글 1',rendered)
        self.assertNotIn('<script>',rendered)
        self.assertIn('&lt;script&gt;',rendered)

    def test_copy_lengths(self):
        for d in json.loads(dropqueue.QUEUE_PATH.read_text())['drops']:
            for text in d.get('replies',[])+([d['link_comment']] if d.get('link_comment') else []):
                self.assertLessEqual(len(text),500)
