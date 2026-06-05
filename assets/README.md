# 에셋 공간 (글감 첨부용 영상·사진)

파트너가 **다운로드해서 배포에 쓰는** 영상/사진을 여기에 둡니다.

## 구조
```
assets/
  2026-06-04/        ← 그날 배포할 파일(날짜 폴더)
    video1.mp4
    photo1.jpg
  library/           ← 상시 재사용 마케팅 자료(하나씩 풀 용도)
    intro_reel.mp4
```

## 쓰는 법
1. 오늘 배포할 파일을 `assets/오늘날짜/` 에 넣는다.
2. 글감 등록 시 첨부로 연결:
   ```
   python -m fp drop add --type marketing --title "..." --body "..." \
     --assets "assets/2026-06-04/video1.mp4,assets/2026-06-04/photo1.jpg"
   ```
3. `python -m fp drop today` → 파트너에게 방송할 형태로 출력(경로/링크 포함).

## 대용량 영상은?
로컬 경로 대신 **공유 링크(Google Drive 등)** 를 `--assets` 에 그대로 넣어도 됩니다.
파트너 수가 많으면 Drive 폴더 공유가 현실적입니다. (이 저장소엔 링크만 기록)
