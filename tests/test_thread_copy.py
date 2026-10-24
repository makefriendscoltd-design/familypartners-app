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
        entries=[d for d in json.loads(dropqueue.QUEUE_PATH.read_text())['drops']
                 if d.get('supplemental') and d.get('replies') and d.get('editorial_kind') != 'original_guidance']
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

    def test_standalone_copy_has_one_post_with_sources_outside_copy(self):
        from html.parser import HTMLParser
        class Blocks(HTMLParser):
            def __init__(self):
                super().__init__(); self.in_pre=False; self.texts=[]
            def handle_starttag(self,tag,attrs):
                if tag=='pre':self.in_pre=True;self.texts.append('')
            def handle_endtag(self,tag):
                if tag=='pre':self.in_pre=False
            def handle_data(self,text):
                if self.in_pre:self.texts[-1]+=text
        entries=[d for d in json.loads(dropqueue.QUEUE_PATH.read_text())['drops'] if d.get('format')=='standalone' and d['date']=='2026-10-19']
        self.assertEqual(len(entries),3)
        for d in entries:
            html=thread_copy.render(dict(d,drop_date=d['date']))
            parser=Blocks();parser.feed(html)
            self.assertEqual(parser.texts,[d['body']])
            self.assertIn('원문 출처 확인',html)
            self.assertIn(d['sources'][0]['url'],html)
            self.assertNotIn('댓글 1 복사',html)

    def test_case_images_are_downloadable_and_paths_cannot_escape(self):
        import hashlib
        from urllib.parse import urlsplit
        cases=[d for d in json.loads(dropqueue.QUEUE_PATH.read_text())['drops'] if d.get('format')=='case_thread' and d.get('main_images') and d.get('editorial_kind')!='original_guidance']
        self.assertEqual(len(cases),14)  # All Oct22–24 copy now uses reply threads; existing images preserved.
        for d in cases:
            self.assertEqual(len(d['main_images']),2)
            row=dict(d,drop_date=d['date'])
            html=thread_copy.render(row)
            self.assertIn('이미지 1 저장',html)
            self.assertIn('이미지 2 저장',html)
            self.assertNotIn('로그인 후',html)
            for image in d['main_images']:
                status,headers,body=self.request('GET',image['src'])
                self.assertEqual(status,200)
                self.assertEqual(headers['Content-Type'],'image/jpeg')
                self.assertEqual(hashlib.sha256(body).hexdigest(),image['sha256'])
        for path in ['/thread-img/../../config/assets.json','/thread-img/%2e%2e%2f.env','/thread-img/missing.jpg']:
            self.assertEqual(self.request('GET',path)[0],404)

    def test_supplemental_guidance_images_render_and_download(self):
        import hashlib
        entries=[d for d in json.loads(dropqueue.QUEUE_PATH.read_text())['drops'] if d.get('editorial_kind')=='original_guidance']
        self.assertEqual(len(entries),8)
        for d in entries:
            self.assertGreaterEqual(len(d.get('main_images',[])),1)
            row=dict(d,drop_date=d['date'])
            html=thread_copy.render(row)
            self.assertIn('이미지 1 저장',html)
            self.assertIn('관련 원문 참고 화면',html)
            self.assertEqual(thread_copy.thumbnail(row),d['main_images'][0]['src'])
            for image in d['main_images']:
                status,headers,body=self.request('GET',image['src'])
                self.assertEqual(status,200)
                self.assertEqual(headers['Content-Type'],'image/jpeg')
                self.assertEqual(hashlib.sha256(body).hexdigest(),image['sha256'])

    def test_today_example_pngs_render_and_download_unchanged(self):
        import hashlib
        entries=json.loads(dropqueue.QUEUE_PATH.read_text())['drops']
        d=next(d for d in entries if d['title']=='사진 한 장 · 봉제인형과 픽셀 그림 만들기')
        self.assertEqual(len(d['main_images']),4)
        html=thread_copy.render(dict(d,drop_date=d['date']))
        self.assertIn('이미지 4 저장',html)
        for image in d['main_images']:
            self.assertIn(image['src'],html)
            status,headers,body=self.request('GET',image['src'])
            self.assertEqual(status,200)
            self.assertEqual(headers['Content-Type'],'image/png')
            self.assertEqual(hashlib.sha256(body).hexdigest(),image['sha256'])

    def test_all_current_and_scheduled_posts_use_reply_threads(self):
        from collections import defaultdict
        from deploy.lint_drops import check_day
        days=defaultdict(list)
        for d in json.loads(dropqueue.QUEUE_PATH.read_text())['drops']:
            if '2026-10-22' <= d['date'] <= '2026-10-26': days[d['date']].append(d)
        self.assertEqual(len(days),5)
        for date, entries in days.items():
            self.assertEqual(len(entries),4 if date == '2026-10-22' else 6)
            self.assertEqual(len({d['fmt'] for d in entries if not d.get('supplemental')}),4)
            for d in entries:
                self.assertEqual(d['format'],'case_thread')
                self.assertGreaterEqual(len(d['body'].split('\n\n')),2)
                self.assertNotIn('\n',d['body'].split('\n\n')[0])
                self.assertGreaterEqual(len(d['replies']),2)
                html=thread_copy.render(dict(d,drop_date=date))
                self.assertEqual(html.count('<pre '),len(d['replies'])+2)
                self.assertIn('댓글 1 복사',html)
                self.assertNotIn('본문 하나로 완결',html)
                if d.get('editorial_kind') != 'original_guidance':
                    self.assertIn(d['sources'][0]['url'],d['link_comment'])
            self.assertEqual(check_day(date,entries)[0],[])
            import copy
            bad=copy.deepcopy(entries)
            bad[0]['format']='standalone'
            self.assertIn('모든 글감은 본문 + 대댓글 구조여야 함',check_day(date,bad)[0])
