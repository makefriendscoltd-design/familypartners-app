"""Real HTTP tests for post performance collection; isolated SQLite."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from fp import core, db, server, video_library as v


class PerfHTTPTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = dict(os.environ)
        for key in ('DATABASE_URL_UNPOOLED', 'POSTGRES_URL_NON_POOLING', 'DATABASE_URL', 'POSTGRES_URL', 'VERCEL'):
            env.pop(key, None)
        env.update(FP_DB=self.tmp.name + '/test.db', FP_VIDEO_DIR=self.tmp.name + '/videos', FP_SECRET='isolated-perf-test')
        self.env = patch.dict(os.environ, env, clear=True); self.env.start()
        db.init_db()
        c = db.connect()
        c.execute("INSERT INTO partners(name,portal_token,joined_date) VALUES ('가','t-a','2026-09-14')")
        pid = c.execute("SELECT id FROM partners").fetchone()["id"]
        today = core.iso(core.today())
        c.execute("INSERT INTO drops(drop_date,dtype,title,fmt,created_at) VALUES (?,?,?,?,?)",
                  (today, 'ai', '글감A', '숫자형', today))
        did = c.execute("SELECT id FROM drops").fetchone()["id"]
        rows = [('https://www.threads.com/@a/post/AAA', today, 1, did),
                ('https://www.threads.net/@a/post/BBB', today, 1, did),
                ('https://instagram.com/p/CCC', today, 1, did),
                ('https://www.threads.com/@a/post/OLD', '2026-01-01', 1, did),
                ('https://www.threads.com/@a/post/VOID', today, 0, did)]
        for url, d, valid, drop in rows:
            c.execute("INSERT INTO submissions(partner_id,post_url,channel,post_date,submitted_at,valid,drop_id) "
                      "VALUES (?,?,?,?,?,?,?)", (pid, url, 'threads', d, today, valid, drop))
        c.commit(); c.close()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.thread.start()
        self.bearer = {'Authorization': 'Bearer ' + v.sync_token()}
        self.admin = 'fp_admin=' + server._admin_token()

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join(); self.env.stop(); self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=15)
        c.request(method, path, body, headers or {}); r = c.getresponse()
        out = (r.status, r.read()); c.close(); return out

    def test_targets_requires_token_and_filters(self):
        self.assertEqual(self.req('GET', '/op/perf/targets')[0], 403)
        st, body = self.req('GET', '/op/perf/targets', headers=self.bearer)
        self.assertEqual(st, 200)
        urls = sorted(t['url'] for t in json.loads(body)['targets'])
        self.assertEqual(urls, ['https://www.threads.com/@a/post/AAA', 'https://www.threads.net/@a/post/BBB'])

    def test_report_saves_and_error_keeps_old_numbers(self):
        ts = json.loads(self.req('GET', '/op/perf/targets', headers=self.bearer)[1])['targets']
        a, b = sorted(ts, key=lambda t: t['url'])
        payload = {'results': [{'id': a['id'], 'likes': 10, 'replies': 2, 'reposts': 1, 'quotes': 0, 'shares': 3},
                               {'id': b['id'], 'likes': 4, 'replies': 0, 'reposts': 0, 'quotes': 0, 'shares': 0}]}
        hs = dict(self.bearer, **{'Content-Type': 'application/json'})
        st, body = self.req('POST', '/op/perf/report', json.dumps(payload), hs)
        self.assertEqual((st, json.loads(body)), (200, {'saved': 2, 'failed': 0}))
        st, body = self.req('POST', '/op/perf/report', json.dumps({'results': [{'id': a['id'], 'error': 'NOHIT'}]}), hs)
        self.assertEqual(json.loads(body), {'saved': 0, 'failed': 1})
        c = db.connect(); r = c.execute("SELECT likes,comments,shares,perf_error FROM submissions WHERE id=?", (a['id'],)).fetchone(); c.close()
        self.assertEqual((r['likes'], r['comments'], r['shares'], r['perf_error']), (10, 2, 3, 'NOHIT'))
        st, page = self.req('GET', '/op/perf', headers={'Cookie': self.admin})
        self.assertEqual(st, 200)
        html = page.decode()
        self.assertIn('글감A', html)
        self.assertIn('<b>10</b>', html)  # median of totals 16 and 4

    def test_admin_page_requires_admin_and_bad_input_rejected(self):
        self.assertEqual(self.req('GET', '/op/perf')[0], 403)
        hs = dict(self.bearer, **{'Content-Type': 'application/json'})
        st, _ = self.req('POST', '/op/perf/report', json.dumps({'results': [{'id': 1, 'likes': -5}]}), hs)
        self.assertEqual(st, 400)


if __name__ == '__main__':
    unittest.main()
