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
