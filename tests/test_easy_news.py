"""easy_news mode: day completeness, schema post -> queue row, link comment shape."""
import datetime, importlib.util, json, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('drop_maintenance', ROOT / 'deploy/drop_maintenance.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from deploy import easy_news_pipeline as easy
from deploy.lint_drops import check_easy_news_text, check_one
from deploy.check_drop_authoring import digest

D = datetime.date(2026, 11, 13)


def post(**kw):
    p = {'source_key': 'x-1', 'category': 'a', 'type': 'ai', 'target': '혼자 가게 하는 사장님', 'fmt': '깃허브 스킬 소개',
         'body': '긴 글 맡길 때\n써줘 한 번이면 손해임\n\n클로드 스킬 하나로\n자료 찾기부터\n검토까지 순서대로 함\n\n쓰는 법은 댓글에 적어둠',
         'replies': ['1. 설치\n\n클로드에서\n스킬을 추가하면 됨'] * 4, 'link_intro': '깃허브 원문',
         'before_body': '', 'before_replies': [], 'evidence_anchor': 'x001', 'link_evidence_anchor': 'e001',
         'card_title': '', 'card_sub': '', 'card_rows': [], 'original_read': True, 'six_checks_passed': True,
         'humanize_summary': 'none', 'editorial_note': 'n'}
    p.update(kw)
    return p


SRC = {'key': 'x-1', 'url': 'https://x.com/a/status/1', 'handle': 'a', 'category_hint': 'a', 'has_prompts': False, 'published_at': '2026-11-08T01:02:03+00:00',
       'date_quote': '2026-11-08T01:02:03.000Z', 'file': 'discovery/snap/x-1.txt',
       'evidence_anchors': {'x001': '클로드 스킬로 긴 글을 순서대로'},
       'external': {'url': 'https://github.com/o/r', 'kind': 'github', 'title': 'GitHub - o/r: desc',
                    'file': 'discovery/snap/ext.txt', 'evidence_anchors': {'e001': 'A Claude skill for research'}}}
IMG = {'src': '/thread-img/fp-easy-20261114-1.png', 'alt': 'a', 'credit': 'c', 'source_url': 'https://github.com/o/r',
       'sha256': '0' * 64, 'path': '/tmp/x.png'}


class DayCompleteness(unittest.TestCase):
    def test_six_easy_news_rows_complete_the_day(self):
        q = {'drops': [{'date': '2026-11-13', 'editorial_kind': 'easy_news'} for _ in range(6)]}
        self.assertEqual(m.gaps(q, D, mode='easy_news')[0]['day'], '2026-11-14')

    def test_old_four_plus_two_day_is_complete_in_easy_mode(self):
        q = {'drops': [{'date': '2026-11-13'} for _ in range(4)] +
             [{'date': '2026-11-13', 'supplemental': True, 'editorial_kind': 'original_guidance'} for _ in range(2)]}
        self.assertEqual(m.gaps(q, D, mode='easy_news')[0]['day'], '2026-11-14')

    def test_partial_day_stops(self):
        for n in (1, 5):
            q = {'drops': [{'date': '2026-11-13'} for _ in range(n)]}
            g = m.gaps(q, D, mode='easy_news')[0]
            self.assertEqual((g['day'], g['partial'], g['count']), ('2026-11-13', True, n))

    def test_empty_day_is_not_partial(self):
        g = m.gaps({'drops': []}, D, mode='easy_news')[0]
        self.assertFalse(g['partial'])

    def test_real_queue_is_complete_through_1113(self):
        q = json.loads((ROOT / 'config/drops_queue.json').read_text())
        days = {g['day'] for g in m.gaps(q, datetime.date(2026, 11, 10), mode='easy_news')}
        self.assertFalse(days & {'2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13'})


class RowShape(unittest.TestCase):
    def test_schema_post_becomes_easy_news_row(self):
        row = easy.make_row('2026-11-14', 345, post(), SRC, IMG, '2026-11-10T09:00:00+09:00', '2026-11-10T09:01:00+09:00', False)
        ref = next(r for r in json.loads((ROOT / 'config/drops_queue.json').read_text())['drops']
                   if r.get('editorial_kind') == 'easy_news' and not r.get('supplemental'))
        self.assertEqual(set(ref) - {'previous_title'}, set(row))
        self.assertEqual(row['title'], '컨텐츠 345 · 긴 글 맡길 때 써줘 한 번이면 손해임')
        self.assertEqual((row['format'], row['editorial_kind'], row['type']), ('case_thread', 'easy_news', 'ai'))
        self.assertNotIn('path', row['main_images'][0])
        self.assertEqual([s['url'] for s in row['sources']], ['https://x.com/a/status/1', 'https://github.com/o/r'])
        self.assertEqual(row['sources'][0]['published_at'], '2026-11-08T01:02:03+00:00')
        self.assertIsNone(row['sources'][1]['published_at'])
        self.assertEqual(check_easy_news_text(row), [])

    def test_supplemental_flag_for_posts_five_and_six(self):
        row = easy.make_row('2026-11-14', 349, post(type='evergreen'), SRC, IMG, 'a', 'b', True)
        self.assertIs(row['supplemental'], True)
        self.assertEqual(list(row)[:8], ['date', 'title', 'type', 'target', 'fmt', 'format', 'editorial_kind', 'supplemental'])

    def test_review_quotes_come_from_anchors(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as t:
            w = pathlib.Path(t)
            (w / 'discovery/snap').mkdir(parents=True)
            (w / 'discovery/snap/x-1.txt').write_text('URL: x\ndatetime: 2026-11-08T01:02:03.000Z\n\n클로드 스킬로 긴 글을 순서대로\n')
            (w / 'discovery/snap/ext.txt').write_text('A Claude skill for research\n')
            (w / 'b.txt').write_text('b'); (w / 'a.md').write_text('a')
            row = easy.make_row('2026-11-14', 345, post(), SRC, IMG, 'a', 'b', False)
            rev = easy.review_for(w, row, post(), SRC, w / 'b.txt', w / 'a.md')
            self.assertEqual(rev['copy_sha256'], digest(row))
            self.assertEqual(rev['sources'][0]['quote'], '클로드 스킬로 긴 글을 순서대로')
            self.assertEqual(rev['sources'][0]['date_quote'], '2026-11-08T01:02:03.000Z')
            self.assertEqual(rev['sources'][1]['quote'], 'A Claude skill for research')


class LinkComment(unittest.TestCase):
    def test_intro_blank_line_then_single_url(self):
        row = easy.make_row('2026-11-14', 345, post(), SRC, IMG, 'a', 'b', False)
        intro, blank, url = row['link_comment'].split('\n')
        self.assertEqual((intro, blank, url), ('깃허브 원문', '', 'https://github.com/o/r'))

    def test_x_post_is_linked_when_no_outside_original(self):
        src = dict(SRC, external=None)
        row = easy.make_row('2026-11-14', 345, post(link_intro='원문 타래'), src, IMG, 'a', 'b', False)
        self.assertEqual(row['link_comment'], '원문 타래\n\nhttps://x.com/a/status/1')
        self.assertEqual(len(row['sources']), 1)


class Collector(unittest.TestCase):
    def test_status_id_time_and_internal_links(self):
        from deploy import easy_news_collect as c
        self.assertEqual(c.snowflake_time('2119115745510715713').isoformat()[:19], '2026-11-07T17:12:19')
        self.assertTrue(c.is_internal('https://help.x.com/rules'))
        self.assertFalse(c.is_internal('https://github.com/o/r'))
        self.assertEqual(c.clean_url('https://a.com/p?utm_source=x&id=3'), 'https://a.com/p?id=3')
        self.assertEqual(c.clean_url('https://youtu.be/abc?si=zz'), 'https://youtu.be/abc')
        self.assertEqual(list(easy.anchor_map('[#Figma](https://y/h)[#Use](https://y/u)\n### 피그마 파일 종류 6가지', 'e').values()),
                         ['### 피그마 파일 종류 6가지'])
        q = {'drops': [{'sources': [{'url': 'https://x.com/a/status/1'}], 'link_comment': '원문\n\nhttps://github.com/o/r'}]}
        self.assertEqual(c.used_urls(q), {'https://x.com/a/status/1', 'https://github.com/o/r'})


class Precheck(unittest.TestCase):
    def pack(self):
        return {'sources': {f'x-{i}': dict(SRC, key=f'x-{i}', handle=f'h{i}') for i in range(6)}}

    def posts(self):
        types = ['ai', 'marketing', 'evergreen', 'evergreen', 'evergreen', 'evergreen']
        return [post(source_key=f'x-{i}', type=t, target=f't{i}', fmt=f'f{i}') for i, t in enumerate(types)]

    def test_valid_day_passes(self):
        self.assertEqual(easy.precheck(self.pack(), self.posts()), [])

    def test_banned_marks_question_and_bracket_are_caught(self):
        ps = self.posts()
        ps[0]['body'] = ps[0]['body'].replace('손해임', '손해임 → 이득')
        ps[1]['body'] = '이거 알고 있음?\n\n내용\n\n댓글에 있음'
        ps[2]['body'] = '이거 써봤음?\n\n내용\n\n댓글에 있음'
        ps[3]['replies'] = ['[필수] 이거'] * 4
        errs = '\n'.join(easy.precheck(self.pack(), ps))
        for needle in ('금지 문자', '질문형 첫 줄 2개', '"["'):
            self.assertIn(needle, errs)

    def test_duplicate_source_and_wrong_types(self):
        ps = self.posts(); ps[1]['source_key'] = 'x-0'; ps[0]['type'] = 'evergreen'
        errs = '\n'.join(easy.precheck(self.pack(), ps))
        self.assertIn('source_key 가 겹침', errs); self.assertIn('type 구성', errs)


class HookAndSourceMix(unittest.TestCase):
    def test_imperative_hooks_limited_and_handles_capped(self):
        pk = Precheck().pack(); ps = Precheck().posts()
        for k in ('x-0', 'x-1', 'x-2'):
            pk['sources'][k]['handle'] = 'same'
        ps[0]['body'] = '잘된 글 찾느라\n계정마다 넘기지 마셈\n\n내용\n\n댓글에 있음'
        ps[1]['body'] = '화면 시안 하나만\n받고 고르지 마셈\n\n내용\n\n댓글에 있음'
        errs = '\n'.join(easy.precheck(pk, ps))
        self.assertIn('명령형 첫 줄 2개', errs); self.assertIn('같은 계정 출처', errs)

    def test_advice_post_cannot_claim_prompt_collection(self):
        pk = Precheck().pack(); ps = Precheck().posts()
        pk['sources']['x-0'].update(external=None, category_hint='c', has_prompts=False)
        ps[0].update(category='c', card_rows=[{'label': 'a', 'text': 'b'}])
        self.assertIn('조언 글', '\n'.join(easy.precheck(pk, ps)))

    def test_promo_filter_and_prompt_detection(self):
        from deploy import easy_news_collect as c
        cfg = json.loads((ROOT / 'config/drop_maintenance.json').read_text())['easy_news']
        self.assertTrue(c.promo_reason('', 'https://goo.gle/hanbat', cfg))
        self.assertTrue(c.promo_reason('', 'https://gemini.google/students/', cfg))
        self.assertTrue(c.promo_reason('학생 요금제 1년 무료', '', cfg))
        self.assertIsNone(c.promo_reason('스킬 공유함', 'https://github.com/o/r', cfg))
        self.assertTrue(c.has_prompts('프롬프트\n상품 설명을 써 줘.\n후기 답글을 정리해줘'))
        self.assertFalse(c.has_prompts('엑셀은 버리지 마라. 기록이 중요함'))


if __name__ == '__main__':
    unittest.main()
