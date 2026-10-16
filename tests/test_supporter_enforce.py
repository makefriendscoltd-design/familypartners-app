"""서포터즈 경고/자격 종료 판정 — 격리된 임시 SQLite 로만 돈다(data/ 미접촉)."""
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


class SupporterEnforceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch_env({"FP_DB": str(Path(self.tmp.name) / "t.db")})
        from fp import core, db
        self.core, self.db = core, db
        db.init_db()
        self.conn = db.connect()
        self.as_of = date(2026, 10, 20)
        self.set_start(self.as_of - timedelta(days=1))

    def tearDown(self):
        self.conn.close()
        self.env()
        self.tmp.cleanup()

    def set_start(self, d):
        self.core.set_setting(self.conn, "warn_start_date", self.core.iso(d))

    def add(self, name, joined_days_ago=10):
        j = self.core.iso(self.as_of - timedelta(days=joined_days_ago))
        return self.core.add_partner(self.conn, name, contact="010-0000-0000", joined=j)

    def post(self, pid, days_ago):
        self.core.add_submission(self.conn, pid, f"https://x/{pid}/{days_ago}",
                                 post_date=self.core.iso(self.as_of - timedelta(days=days_ago)))

    def warns(self, pid):
        return self.conn.execute("SELECT COUNT(*) c FROM events WHERE partner_id=? AND type='warn'",
                                 (pid,)).fetchone()["c"]

    def status(self, pid):
        return self.conn.execute("SELECT status FROM partners WHERE id=?", (pid,)).fetchone()["status"]

    def test_misses_before_start_are_ignored(self):
        pid = self.add("가")            # 10일 내내 누락, 시행일은 어제
        out = self.core.enforce(self.conn, self.as_of, dry_run=False)
        self.assertEqual([t["dates"] for t in out], [["2026-10-19"]])
        self.assertEqual(self.warns(pid), 1)
        self.assertEqual(self.status(pid), "active")

    def test_same_day_is_idempotent(self):
        pid = self.add("나")
        self.core.enforce(self.conn, self.as_of, dry_run=False)
        again = self.core.enforce(self.conn, self.as_of, dry_run=False)
        self.assertEqual(again, [])
        self.assertEqual(self.warns(pid), 1)

    def test_two_warns_end_without_forfeit(self):
        pid = self.add("다")
        self.core.enforce(self.conn, self.as_of, dry_run=False)            # 10/19 경고 1
        nxt = self.as_of + timedelta(days=1)
        out = self.core.enforce(self.conn, nxt, dry_run=False)             # 10/20 경고 2
        self.assertTrue(out[0]["kicked"])
        self.assertEqual(self.status(pid), "kicked")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) c FROM events WHERE type='forfeit'").fetchone()["c"], 0)
        self.assertTrue(self.core.is_rejoin_blocked(self.conn, "다", None))

    def test_backlog_dates_each_warned(self):
        self.set_start(self.as_of - timedelta(days=4))
        a = self.add("라")
        self.post(a, 4); self.post(a, 2)          # 3일 전·1일 전 누락 → 경고 2 → 종료
        b = self.add("마")
        self.post(b, 4); self.post(b, 3); self.post(b, 1)   # 2일 전만 누락 → 경고 1
        out = {t["name"]: t for t in self.core.enforce(self.conn, self.as_of, dry_run=False)}
        self.assertEqual(out["라"]["dates"], ["2026-10-17", "2026-10-19"])
        self.assertEqual(self.status(a), "kicked")
        self.assertEqual(out["마"]["dates"], ["2026-10-18"])
        self.assertEqual(self.status(b), "active")

    def test_dry_run_changes_nothing(self):
        pid = self.add("바")
        out = self.core.enforce(self.conn, self.as_of, dry_run=True)
        self.assertEqual(len(out), 1)
        self.assertEqual(self.warns(pid), 0)

    def test_paused_skipped_and_resume_not_backdated(self):
        self.set_start(self.as_of - timedelta(days=5))
        pid = self.add("사")
        self.core.set_status(self.conn, pid, "paused", self.core.iso(self.as_of - timedelta(days=6)))
        self.assertEqual(self.core.enforce(self.conn, self.as_of, dry_run=False), [])
        self.core.set_status(self.conn, pid, "active", self.core.iso(self.as_of))
        self.assertEqual(self.core.enforce(self.conn, self.as_of, dry_run=False), [])
        self.assertEqual(self.warns(pid), 0)

    def test_excuse_voids_auto_warn_and_restore_resets(self):
        pid = self.add("아")
        self.core.enforce(self.conn, self.as_of, dry_run=False)
        self.assertEqual(self.core.warn_count(self.conn, pid), 1)
        self.post(pid, 1)                          # 봐주기 = 어제 출석 인정
        self.assertEqual(self.core.warn_count(self.conn, pid), 0)
        # 두 번 누락해 종료 → 복구하면 경고 0
        nxt = self.as_of + timedelta(days=2)
        self.core.enforce(self.conn, nxt, dry_run=False)
        self.assertEqual(self.status(pid), "kicked")
        self.core.restore_partner(self.conn, pid)
        self.assertEqual(self.core.warn_count(self.conn, pid), 0)


class SupporterMigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch_env({"FP_DB": str(Path(self.tmp.name) / "m.db")})

    def tearDown(self):
        self.env()
        self.tmp.cleanup()

    def test_migrates_once(self):
        from fp import core, db
        db.init_db()
        conn = db.connect()
        self.assertIsNotNone(core.get_setting(conn, "warn_start_date"))
        conn.execute("INSERT INTO partners(name, joined_date, status, partner_type, links_json) "
                     "VALUES ('옛사람','2026-09-01','kicked','family','{\"LINK_AIMAX\":\"u\"}')")
        conn.execute("DELETE FROM settings WHERE key='migrated_supporter_v1'")
        conn.commit(); conn.close()
        db.init_db()
        conn = db.connect()
        r = conn.execute("SELECT partner_type, links_json FROM partners").fetchone()
        self.assertEqual(r["partner_type"], "supporter")
        self.assertIn("LINK_AIMAX", r["links_json"])       # 데이터는 그대로
        conn.execute("UPDATE partners SET partner_type='family'"); conn.commit(); conn.close()
        db.init_db()                                        # 마커가 있으니 다시 안 바꾼다
        conn = db.connect()
        self.assertEqual(conn.execute("SELECT partner_type FROM partners").fetchone()[0], "family")
        conn.close()

    def test_join_page_has_no_type_select(self):
        from fp import db, server
        db.init_db()
        html = server.view_join({}).decode("utf-8")
        self.assertNotIn("<select", html)
        self.assertIn("name=ptype value=supporter", html)
        for bad in ("패밀리 파트너스", "즉시 강퇴", "박상철"):
            self.assertNotIn(bad, html)


def patch_env(values):
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)

    def restore():
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return restore


if __name__ == "__main__":
    unittest.main()
