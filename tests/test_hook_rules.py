import unittest

from deploy.lint_drops import HOOK_RULES_FROM, check_day_hooks, check_hook


def post(body, date=HOOK_RULES_FROM):
    return {"format": "case_thread", "date": date, "body": body}


class HookRulesTest(unittest.TestCase):
    def test_list_hook_passes(self):
        self.assertEqual(check_hook(post("블로그를 지금 당장 해야 하는 이유\n\n1. 아직 블루오션\n2. 자동화하면 됨")), [])

    def test_disclaimer_in_body_fails(self):
        msgs = check_hook(post("결과 표부터 그리세요.\n\n이건 외부 사례가 아니라\n내가 직접 제안하는 방식임."))
        self.assertTrue(any("방어 문장" in m for m in msgs))

    def test_source_intro_hook_fails(self):
        self.assertTrue(check_hook(post("레딧의 atmja_560은 CSV 등록 흐름을 소개함\n\n본문")))
        self.assertTrue(check_hook(post("SyedAnasAhmadShah가 n8n 영상 흐름을 공개했음\n\n본문")))

    def test_two_question_hooks_per_day_fail(self):
        day = [post("메일 정리만 시키고 있음?\n\nx"), post("모르는 질문도 답하게 함?\n\nx"), post("결과부터\n\nx")]
        self.assertTrue(check_day_hooks(day))
        self.assertFalse(check_day_hooks(day[1:]))


class EasyNewsLinkTest(unittest.TestCase):
    def test_easy_news_accepts_source_link(self):
        from deploy.lint_drops import check_one
        cfg = {"by_code": {}, "proper": [], "used_kw_before": set()}
        d = {"format": "case_thread", "date": "2026-11-14", "type": "ai", "editorial_kind": "easy_news",
             "body": "한글 파일 AI한테 못 읽혀서 복붙하고 있었으면 이거 보셈\n\n깃허브 도구임",
             "replies": ["1. 뭘 해주는지\n\n변환해줌", "2. 쓰는 법\n\n한 줄이면 됨"],
             "link_comment": "깃허브 주소\n\nhttps://github.com/chrisryugj/kordoc",
             "sources": [{"url": "https://github.com/chrisryugj/kordoc"}]}
        self.assertNotIn("마지막 댓글에 라운지 활용노트 링크 필요", check_one(d, cfg))
        self.assertNotIn("마지막 댓글에 원문 링크 필요", check_one(d, cfg))
        d.pop("editorial_kind")
        self.assertIn("마지막 댓글에 라운지 활용노트 링크 필요", check_one(d, cfg))

    def test_easy_news_line_and_source_person(self):
        from deploy.lint_drops import check_easy_news_text
        ok = {"body": "한글 파일 못 읽히면\n이거 보셈", "replies": ["1. 쓰는 법\n\nnpx -y kordoc setup 한 줄이면 됨"],
              "link_comment": "깃허브 주소\n\nhttps://github.com/chrisryugj/kordoc"}
        self.assertEqual(check_easy_news_text(ok), [])
        long = {**ok, "body": "한글 파일 AI한테 못 읽혀서 복붙하고 있었으면 이거 보셈"}
        self.assertTrue(any("20자" in m for m in check_easy_news_text(long)))
        who = {**ok, "replies": ["X에서 도파민이라는\n분이 올린 스킬임"]}
        self.assertTrue(any("출처 인물" in m for m in check_easy_news_text(who)))


if __name__ == "__main__":
    unittest.main()
