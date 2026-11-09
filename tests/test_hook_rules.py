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


if __name__ == "__main__":
    unittest.main()
