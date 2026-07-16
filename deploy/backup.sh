#!/usr/bin/env bash
# Family Partners SQLite 일일 백업 (7일 보관)
# systemd timer(familypartners-backup.timer)가 매일 03:20 KST 실행.
# SQLite 온라인 백업(.backup)으로 무결성 있는 스냅샷을 뜬다(락/쓰기 중에도 안전).
set -uo pipefail

APP=/home/ubuntu/familypartners
DB="$APP/data/challenge.db"
DEST=/home/ubuntu/familypartners-backups
KEEP=7

mkdir -p "$DEST"
[ -f "$DB" ] || { echo "[backup] DB 없음: $DB"; exit 1; }

TS="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/challenge-${TS}.db"

# .backup 은 일관된 스냅샷을 보장(단순 cp 와 달리 쓰기 중에도 안전)
if sqlite3 "$DB" ".backup '$OUT'"; then
    gzip -f "$OUT"
    echo "[backup] 완료 -> ${OUT}.gz ($(du -h "${OUT}.gz" | cut -f1))"
else
    echo "[backup] 실패"; exit 1
fi

# 7일 초과분 정리
ls -1t "$DEST"/challenge-*.db.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
echo "[backup] 보관 개수: $(ls -1 "$DEST"/challenge-*.db.gz 2>/dev/null | wc -l)"
