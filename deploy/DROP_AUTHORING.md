# 글감 작성 검증

운영 큐는 `config/drops_queue.json`, 수집 정책은 `config/assets.json`이다.
현재 Mac 작업 폴더는 `/Users/apple/orca/projects/familypartners`다. 다른 checkout에서 수정했다면 원래 폴더에도 작성 도구·지침을 반영하고, Git 차이와 남은 변경을 기록한다.

1. `python3 deploy/check_drop_authoring.py`: humanize 실제 설치 위치, CLI 경로, 미검증 역량을 구분한다.
2. `python3 deploy/collect_drop_sources.py --out /tmp/fp-research-고유이름`: 검색 + 원문 스냅샷. `--read-originals 0`은 후보 탐색 전용이다. `ready_to_draft=false`는 자동 확인이 끝나지 않았다는 뜻이다. 스냅샷도 직접 읽어 제목·본문·발행일을 대조한다.
3. 콘텐츠 리서치 게이트에 맞춰 주장 근거를 검증하고 작성한다. 커뮤니티 글은 작성자의 사례로 소개하며 독립된 성과 사실로 승격하지 않는다.
4. humanize-korean 정본 룰북을 읽고 초안을 윤문한다. 윤문 전후 파일, HUMANIZE-SUMMARY와 6항 검토 결과를 저장한다. 변경률 검사만으로 의미 보존이나 자연스러움을 입증할 수 없다.
5. 아래 리뷰를 저장하고 `python3 deploy/lint_drops.py --include-published --reviews 작업폴더/reviews.json` 실행. 기본 비교 대상은 origin/main이다. 해당 ref를 먼저 fetch하고 발행본인지 확인한다. 새로 쓴 뒤 baseline을 새 커밋으로 바꿔 검사를 건너뛰지 않는다.
6. 반영 후 운영 DB 및 실제 피드의 본문·댓글·이미지를 확인한다.

`reviews.json`은 `deploy.check_drop_authoring.digest(queue_row)`를 키로 하는 객체다. 본문·댓글·출처·이미지 등이 바뀌면 digest가 바뀌어 재검토가 필요하다. 실제 복사 텍스트는 `copy_text(queue_row)`다. 증거 파일은 reviews.json과 같은 폴더 또는 하위 폴더에 둔다.

```json
{
  "<copy digest>": {
    "copy_sha256": "<same digest>",
    "humanize": {
      "before": {"file": "draft.txt", "sha256": "<actual file hash>"},
      "after": {"file": "final.md", "sha256": "<actual file hash>"},
      "six_checks_passed": true
    },
    "sources": [{
      "url": "<same original URL as queue sources>",
      "original_read": true,
      "snapshot": {"file": "original.txt", "sha256": "<actual file hash>"},
      "quote": "<literal passage supporting the case>",
      "date_quote": "<literal publication date from the original>",
      "published_at": "<same ISO timestamp as queue sources>"
    }]
  }
}
```

`true`는 실제 검토 후에만 기입한다. 설치 성공, 검색 요약, 라운지 목록, 인증 안내 화면은 원문 검증 증거가 아니다. 원문 날짜가 불명확한 자료를 최근 커뮤니티 소재로 계산하지 않는다. 출처 접근이 막히면 기존 운영 예약을 지우거나 자동화를 재실행하지 않고 미완료로 보고한다.

2026-10-22: 모든 신규 글감은 한 줄 본문 + 대댓글 + 링크 댓글 구조다. 기존 원고를 그대로 재배치할 때는 `--structure-only --reviews ...`로 공백 외 원고·출처 보존을 검증한다. 이 모드는 새 리서치 성공을 의미하지 않으며 휴머나이즈 검토는 생략하지 않는다.
