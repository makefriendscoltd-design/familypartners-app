"""무료자료형은 숫자 과장 없이 활용 목록과 실재 자료 CTA를 허용한다."""
import unittest
from deploy.lint_drops import check_one


class DropCopyRules(unittest.TestCase):
    def setUp(self):
        self.cfg = {'by_code': {'guide': {}}, 'proper': ['챗GPT'], 'used_kw_before': set()}
        self.article = {
            'type': 'evergreen', 'asset': 'guide', 'cta_keyword': '빛',
            'body': '상품 사진 소품부터 사지 마세요\n\n조명부터 바꿔보세요\n빛의 방향을 비교해보는 거임\n\n조명 가이드 필요하면\n댓글에 빛 남겨주세요',
        }

    def test_free_copy_without_arbitrary_number_or_tool(self):
        self.assertEqual(check_one(self.article, self.cfg), [])

    def test_unknown_material_rejected(self):
        self.article['asset'] = 'missing'
        self.assertTrue(any('없는 자료' in x for x in check_one(self.article, self.cfg)))

    def test_missing_cta_rejected(self):
        self.article['body'] = self.article['body'].replace('댓글에 빛 남겨주세요', '읽어보세요')
        self.assertTrue(any('댓글 유도 없음' in x for x in check_one(self.article, self.cfg)))

    def test_unbroken_paragraph_rejected(self):
        self.article['body'] = '후킹\n\n가\n나\n다\n라\n\n댓글에 빛 남겨주세요'
        self.assertTrue(any('문단' in x for x in check_one(self.article, self.cfg)))

    def test_long_mobile_line_rejected(self):
        self.article['body'] = '가' * 31 + '\n\n댓글에 빛 남겨주세요'
        self.assertTrue(any('한 줄' in x for x in check_one(self.article, self.cfg)))

    def test_ai_rules_unchanged(self):
        self.article['type'] = 'ai'
        self.article['body'] = '사진 조명 비교\n\n댓글 빛'
        failures = check_one(self.article, self.cfg)
        self.assertIn('구체적 숫자 없음', failures)
        self.assertIn('도구·서비스 이름이 본문에 없음(직관성)', failures)


if __name__ == '__main__':
    unittest.main()
