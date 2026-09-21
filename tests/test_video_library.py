"""Real HTTP tests with isolated SQLite/storage; never start production watchdog."""
import concurrent.futures
import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, quote
from http.server import ThreadingHTTPServer

from fp import db, server, video_library as v


class VideoHTTPTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = dict(os.environ)
        for key in ('DATABASE_URL_UNPOOLED','POSTGRES_URL_NON_POOLING','DATABASE_URL','POSTGRES_URL','VERCEL'):
            env.pop(key, None)
        env.update(FP_DB=self.tmp.name+'/test.db', FP_VIDEO_DIR=self.tmp.name+'/videos', FP_SECRET='isolated-video-test')
        self.env = patch.dict(os.environ, env, clear=True); self.env.start()
        db.init_db()
        c=db.connect()
        for name,token in [('테스트가','token-a'),('테스트나','token-b')]:
            c.execute('INSERT INTO partners(name,portal_token,joined_date) VALUES (?,?,?)',(name,token,'2026-09-14'))
        c.commit();c.close()
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.admin='fp_admin='+server._admin_token()
        self.a=v.PARTNER_COOKIE+'=token-a';self.b=v.PARTNER_COOKIE+'=token-b'
        self.blob=b'\x00\x00\x00\x18ftypisom'+bytes(range(256))*20000

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join();self.env.stop();self.tmp.cleanup()

    def request(self,method,path,body=None,cookie=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=15)
        hs=dict(headers or {})
        if cookie: hs['Cookie']=cookie
        c.request(method,path,body,hs);r=c.getresponse();out=(r.status,dict(r.getheaders()),r.read());c.close();return out

    def upload(self,blob=None):
        return self.request('POST','/op/videos/upload',self.blob if blob is None else blob,self.admin,
            {'X-CSRF-Token':v.csrf('admin'),'X-Video-Title':quote('테스트 영상'),'X-File-Name':quote('원본.mp4')})

    def publish(self,vid):
        return self.request('POST','/op/videos/publish',urlencode(dict(id=vid,published=1,csrf=v.csrf('admin'))),self.admin)

    def claim(self,vid,which):
        token,name,cookie=('token-a','테스트가',self.a) if which=='a' else ('token-b','테스트나',self.b)
        return self.request('POST','/videos/claim',urlencode(dict(id=vid,name=name,csrf=v.csrf(token))),cookie)

    def queue(self,vid,queued=1):
        return self.request('POST','/op/videos/queue',urlencode(dict(id=vid,queued=queued,csrf=v.csrf('admin'))),self.admin)

    def test_midnight_refill_tops_up_to_target_once_per_day_oldest_first(self):
        ids=[]
        for i in range(4):
            status,_,body=self.upload(self.blob+bytes([i]));self.assertEqual(status,201);ids.append(json.loads(body)['id'])
        withdrawn=ids[3]  # unpublished but never queued: must never be auto-published
        self.assertEqual(self.publish(ids[0])[0],303)
        for vid in ids[1:3]:self.assertEqual(self.queue(vid)[0],303)
        self.assertEqual(self.claim(ids[1],'a')[0],409)
        c=db.connect()
        try:
            # Today's refill already ran on the first request, with nothing queued yet.
            self.assertIsNotNone(c.execute('SELECT day FROM video_refills WHERE day=?',(v.core.now_iso()[:10],)).fetchone())
            with patch.object(v,'VISIBLE_TARGET',2):
                self.assertEqual(v.refill(c,'2999-01-01'),1)
                self.assertIsNone(v.refill(c,'2999-01-01'))
                self.assertEqual(v.stock(c),{'available':2,'queued':1,'target':2})
                rows={r['id']:r for r in c.execute('SELECT id,published,queued FROM exclusive_videos').fetchall()}
                self.assertEqual((rows[ids[1]]['published'],rows[ids[1]]['queued']),(1,0))
                self.assertEqual(rows[ids[2]]['queued'],1)
                self.assertEqual((rows[withdrawn]['published'],rows[withdrawn]['queued']),(0,0))
        finally:c.close()
        self.assertEqual(self.claim(ids[1],'a')[0],303)
        status=json.loads(self.request('GET','/op/videos/sync-status',cookie=self.admin)[2])
        self.assertEqual(status['stock']['queued'],1)
        self.assertIn('대기열',self.request('GET','/op/videos',cookie=self.admin)[2].decode())
        self.assertEqual(self.queue(ids[2],0)[0],303)
        c=db.connect()
        try:self.assertEqual(v.stock(c)['queued'],0)
        finally:c.close()

    def test_disk_guard_purges_oldest_old_claims_only(self):
        ids=[]
        for i in range(4):
            status,_,body=self.upload(self.blob+bytes([i]));self.assertEqual(status,201);vid=json.loads(body)['id'];ids.append(vid)
            self.assertEqual(self.publish(vid)[0],303)
        for vid,who in zip(ids[:3],'aab'):
            self.assertEqual(self.claim(vid,who)[0],303)
        c=db.connect()
        try:
            # ids[0] claimed 40 days ago, ids[1] 20 days ago, ids[2] yesterday, ids[3] unclaimed
            for vid,stamp in [(ids[0],'2026-08-01T10:00:00'),(ids[1],'2026-08-21T10:00:00'),(ids[2],'2026-09-09T10:00:00')]:
                c.execute('UPDATE exclusive_videos SET claimed_at=? WHERE id=?',(stamp,vid))
            c.commit()
            level=[85.0]
            def used():return level[0]
            real_unlink=Path.unlink
            def unlink(path,*a,**k):level[0]-=10;return real_unlink(path,*a,**k)
            with patch.object(Path,'unlink',unlink):
                self.assertEqual(v.purge(c,'2026-09-10',used),2)  # 85 -> 75 -> 65: two oldest old claims
            rows={r['id']:r for r in c.execute('SELECT id,purged_at,file_key FROM exclusive_videos').fetchall()}
        finally:c.close()
        self.assertIsNotNone(rows[ids[0]]['purged_at']);self.assertIsNotNone(rows[ids[1]]['purged_at'])
        self.assertIsNone(rows[ids[2]]['purged_at'])
        self.assertIsNone(rows[ids[3]]['purged_at'])
        self.assertFalse((v.storage_dir()/rows[ids[0]]['file_key']).exists())
        self.assertTrue((v.storage_dir()/rows[ids[3]]['file_key']).exists())
        self.assertEqual(self.request('GET',f'/videos/file/{ids[0]}',cookie=self.a)[0],410)
        self.assertIn('보관 기간이 지나',self.request('GET','/videos',cookie=self.a)[2].decode())
        c=db.connect()
        try:self.assertEqual(v.purge(c,'2026-09-10',lambda:60.0),0)  # under the start line: nothing happens
        finally:c.close()

    def test_upload_visibility_security_and_ranges(self):
        self.assertEqual(self.request('GET','/op/videos')[0],403)
        html=self.request('GET','/op/videos',cookie=self.admin)[2].decode()
        self.assertIn("'X-CSRF-Token':\"",html)
        self.assertEqual(self.request('POST','/op/videos/upload',b'no-csrf',self.admin)[0],403)
        self.assertEqual(self.upload(b'invalid file content')[0],400)
        code,_,payload=self.upload();self.assertEqual(code,201);vid=json.loads(payload)['id']
        self.assertEqual(json.loads(self.upload()[2])['id'],vid)
        self.assertEqual(len(list(Path(self.tmp.name+'/videos').glob('*.mp4'))),1)
        self.assertEqual(json.loads(self.request('GET','/videos/available',cookie=self.a)[2])['ids'],[])
        self.assertEqual(self.claim(vid,'a')[0],409)
        self.assertEqual(self.publish(vid)[0],303)
        for url in ('/me?t=token-a','/feed?t=token-a','/'):
            code,_,html=self.request('GET',url,cookie=self.admin if url=='/' else None)
            self.assertEqual(code,200)
            self.assertIn('id=partner-videos',html.decode())
            self.assertIn('테스트 영상',html.decode())
        self.assertEqual(self.request('GET',f'/videos/file/{vid}')[0],403)
        bad=urlencode(dict(id=vid,name='테스트나',csrf=v.csrf('token-a')))
        self.assertEqual(self.request('POST','/videos/claim',bad,self.a)[0],403)
        self.assertEqual(self.request('POST','/videos/claim',urlencode(dict(id=vid,name='테스트가')),self.a)[0],403)
        self.assertEqual(self.claim(vid,'a')[0],303)
        self.assertEqual(self.claim(vid,'a')[0],303)
        self.assertEqual(self.claim(vid,'b')[0],409)
        path=f'/videos/file/{vid}'
        for method in ('GET','HEAD'):
            self.assertEqual(self.request(method,path,cookie=self.b)[0],403)
        code,headers,data=self.request('GET',path,cookie=self.a)
        self.assertEqual(code,200);self.assertEqual(hashlib.sha256(data).digest(),hashlib.sha256(self.blob).digest())
        self.assertIn('no-store',headers['Cache-Control'])
        code,headers,data=self.request('HEAD',path,cookie=self.a)
        self.assertEqual(code,200);self.assertEqual(int(headers['Content-Length']),len(self.blob));self.assertEqual(data,b'')
        for rg,expected in [('bytes=3-19',self.blob[3:20]),('bytes=-11',self.blob[-11:]),('bytes=5120000-',self.blob[5120000:])]:
            code,_,data=self.request('GET',path,cookie=self.a,headers={'Range':rg})
            self.assertEqual(code,206);self.assertEqual(data,expected)
        self.assertEqual(self.request('GET',path,cookie=self.a,headers={'Range':'bytes=99999999-'})[0],416)
        self.assertEqual(json.loads(self.request('GET','/videos/available',cookie=self.b)[2])['ids'],[])
        self.assertNotIn('테스트가',self.request('GET','/videos',cookie=self.b)[2].decode())
        self.assertIn('테스트가',self.request('GET','/op/videos',cookie=self.admin)[2].decode())
        c=db.connect();c.execute("UPDATE partners SET status='paused' WHERE portal_token='token-a'");c.commit();c.close()
        self.assertEqual(self.request('GET',path,cookie=self.a)[0],403)
        self.assertEqual(self.request('GET',path,cookie=self.admin)[0],200)

    def test_thumbnail_and_dashboard_claim(self):
        vid=json.loads(self.upload()[2])['id']
        path=f'/op/videos/thumbnail/{vid}'
        jpeg=b'\xff\xd8\xff'+b'fixture'+b'\xff\xd9'
        self.assertEqual(self.request('POST',path,jpeg)[0],403)
        self.assertEqual(self.request('POST',path,b'bad',self.admin,{'X-CSRF-Token':v.csrf('admin')})[0],400)
        self.assertEqual(self.request('POST',path,jpeg,self.admin,{'X-CSRF-Token':v.csrf('admin')})[0],200)
        thumb=f'/videos/thumb/{vid}'
        self.assertEqual(self.request('GET',thumb)[0],403)
        self.publish(vid)
        code,headers,data=self.request('GET',thumb)
        self.assertEqual((code,data),(200,jpeg));self.assertIn('no-store',headers['Cache-Control'])
        form=urlencode(dict(id=vid,name='테스트가',t='token-a',csrf=v.csrf('token-a')))
        code,headers,data=self.request('POST','/videos/claim',form,headers={'Accept':'application/json'})
        self.assertEqual(code,200);self.assertIn('Set-Cookie',headers)
        self.assertEqual(json.loads(data)['download'],f'/videos/file/{vid}')
        self.assertEqual(self.request('GET',thumb)[0],403)
        self.assertEqual(self.request('GET',thumb,cookie=self.a)[0],200)
        self.assertEqual(json.loads(self.request('GET','/videos/catalog')[2])['ids'],[])

    def test_caption_ownership_update_and_existing_recipient(self):
        uploaded=json.loads(self.upload()[2]);vid=uploaded['id'];path=f'/op/videos/caption/{vid}'
        caption="원본 내용 <script> & 테스트\n\n댓글에 ‘정리’ 남겨주시면\n이 영상 정리본 드릴게요."
        form=urlencode(dict(sha256=uploaded['sha256'],caption=caption,csrf=v.csrf('admin')))
        self.assertEqual(self.request('POST',path,form)[0],403)
        wrong=urlencode(dict(sha256='wrong',caption=caption,csrf=v.csrf('admin')))
        self.assertEqual(self.request('POST',path,wrong,self.admin)[0],400)
        self.assertEqual(self.request('POST',path,form,self.admin)[0],303)
        self.publish(vid)
        endpoint=f'/videos/caption/{vid}'
        self.assertEqual(self.request('GET',endpoint,cookie=self.a)[0],403)
        f=urlencode(dict(id=vid,t='token-a',name='테스트가',csrf=v.csrf('token-a')))
        code,_,payload=self.request('POST','/videos/claim',f,headers={'Accept':'application/json'})
        self.assertEqual(code,200);self.assertEqual(json.loads(payload)['caption'],caption)
        self.assertEqual(self.request('GET',endpoint,cookie=self.b)[0],403)
        self.assertEqual(json.loads(self.request('GET',endpoint,cookie=self.a)[2])['caption'],caption)
        page=self.request('GET','/videos',cookie=self.a)[2].decode()
        self.assertIn('&lt;script&gt;',page);self.assertIn('data-copy-caption',page)
        updated=caption+'\n내용 보완'
        f=urlencode(dict(sha256=uploaded['sha256'],caption=updated,csrf=v.csrf('admin')))
        self.assertEqual(self.request('POST',path,f,self.admin)[0],303)
        db.init_db()
        self.assertEqual(json.loads(self.request('GET',endpoint,cookie=self.a)[2])['caption'],updated)

    def test_daily_limit_concurrent_different_videos_and_midnight(self):
        ids=[]
        for n in range(7):
            vid=json.loads(self.upload(self.blob+bytes([n]))[2])['id'];self.publish(vid);ids.append(vid)
        with patch('fp.core.now_iso',return_value='2026-09-15T23:59:59'):
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                results=list(pool.map(lambda vid:(vid,self.claim(vid,'a')[0]),ids))
            self.assertEqual(sorted(status for _,status in results),[303,303,409,409,409,409,409])
            owned_ids=[vid for vid,status in results if status==303]
            owned=owned_ids[0]
            remaining=[vid for vid in ids if vid not in owned_ids]
            self.assertEqual(self.claim(owned,'a')[0],303)
            self.assertEqual(self.claim(remaining[0],'b')[0],303)
            self.assertEqual(self.claim(remaining[1],'b')[0],303)
            self.assertEqual(self.claim(remaining[2],'b')[0],409)
        with patch('fp.core.now_iso',return_value='2026-09-16T00:00:00'):
            self.assertEqual(self.claim(owned,'a')[0],303)
            self.assertEqual(self.claim(remaining[2],'a')[0],303)
            self.assertEqual(self.claim(remaining[3],'a')[0],303)
            self.assertEqual(self.claim(remaining[4],'a')[0],409)
        c=db.connect();row=c.execute('SELECT claimed_at FROM exclusive_videos WHERE id=?',(owned,)).fetchone();c.close()
        self.assertEqual(row['claimed_at'],'2026-09-15T23:59:59')

    def test_daily_limit_counts_existing_claims_without_reassigning(self):
        ids=[]
        for n in range(5):
            vid=json.loads(self.upload(self.blob+bytes([n]))[2])['id'];self.publish(vid);ids.append(vid)
        c=db.connect();pid=c.execute("SELECT id FROM partners WHERE portal_token='token-a'").fetchone()['id']
        for vid in ids[:4]:
            c.execute('UPDATE exclusive_videos SET claimed_by=?,claimed_name=?,claimed_at=? WHERE id=?',(pid,'테스트가','2026-09-15T10:00:00',vid))
        c.commit();c.close()
        with patch('fp.core.now_iso',return_value='2026-09-15T23:59:59'):
            self.assertEqual(self.claim(ids[4],'a')[0],409)
            for vid in ids[:4]:self.assertEqual(self.claim(vid,'a')[0],303)
        with patch('fp.core.now_iso',return_value='2026-09-16T00:00:00'):
            self.assertEqual(self.claim(ids[4],'a')[0],303)

    def test_sync_credential_scope_and_upload(self):
        headers={'Authorization':'Bearer '+v.sync_token(),'X-Video-Title':quote('자동 등록'),'X-File-Name':'sync.mp4'}
        code,_,data=self.request('POST','/op/videos/upload',self.blob,headers=headers)
        self.assertEqual(code,201);vid=json.loads(data)['id']
        self.assertEqual(self.request('GET','/op/videos',headers=headers)[0],403)
        self.assertEqual(self.request('GET','/people',headers=headers)[0],303)
        rows=json.loads(self.request('GET','/op/videos/sync-status',headers=headers)[2])['videos']
        self.assertEqual(rows[0]['id'],vid);self.assertNotIn('claimed_name',rows[0])
        self.assertEqual(self.request('POST','/op/videos/publish',urlencode(dict(id=vid,published=1)),headers=headers)[0],303)
        self.assertEqual(self.request('GET',f'/videos/file/{vid}',headers=headers)[0],200)
        self.assertEqual(self.request('GET','/op/videos/sync-status',headers={'Authorization':'Bearer wrong'})[0],403)

    def test_atomic_claim_and_cookie(self):
        vid=json.loads(self.upload()[2])['id'];self.publish(vid)
        code,headers,_=self.request('GET','/videos?t=token-a')
        self.assertEqual(code,303);self.assertEqual(headers['Location'],'/videos');self.assertIn('HttpOnly',headers['Set-Cookie'])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda w:self.claim(vid,w)[0],['a','b']))
        self.assertEqual(sorted(results),[303,409])
        c=db.connect();before=dict(c.execute('SELECT * FROM exclusive_videos').fetchone());c.close()
        db.init_db()
        c=db.connect();after=dict(c.execute('SELECT * FROM exclusive_videos').fetchone());c.close()
        self.assertEqual(before,after)
        c=db.connect();c.execute('DELETE FROM partners WHERE id=?',(before['claimed_by'],));c.commit()
        archived=dict(c.execute('SELECT * FROM exclusive_videos').fetchone());c.close()
        self.assertIsNone(archived['claimed_by']);self.assertEqual(archived['claimed_at'],before['claimed_at'])
        remaining=self.a if results[0]==409 else self.b
        self.assertEqual(json.loads(self.request('GET','/videos/available',cookie=remaining)[2])['ids'],[])
        self.assertEqual(self.claim(vid,'a' if results[0]==409 else 'b')[0],409)


if __name__=='__main__': unittest.main()
