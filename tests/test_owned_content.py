import json, unittest, http.client
from urllib.parse import urlencode
import test_video_library as fixture
import test_cardnews_library as decks
from fp import db, video_library as vl

class OwnedContentTest(unittest.TestCase):
    setUp=fixture.VideoHTTPTest.setUp
    tearDown=fixture.VideoHTTPTest.tearDown
    request=fixture.VideoHTTPTest.request
    upload=fixture.VideoHTTPTest.upload
    publish=fixture.VideoHTTPTest.publish
    claim=fixture.VideoHTTPTest.claim
    up=decks.CardnewsTest.up
    claim_deck=decks.CardnewsTest.claim_deck

    def seed(self):
        vid=json.loads(self.upload()[2])['id'];self.publish(vid);self.assertEqual(self.claim(vid,'a')[0],303)
        did=self.up('01-gradient','mine');self.assertEqual(self.claim_deck(did,'a')[0],303)
        other=json.loads(self.upload(self.blob+b'other')[2])['id'];self.publish(other);self.claim(other,'b')
        c=db.connect();c.execute('UPDATE exclusive_videos SET title=? WHERE id=?',('다른 사람 비밀 영상',other));c.execute('UPDATE cardnews_decks SET caption=? WHERE id=?',('카드뉴스 전용 캡션',did));c.commit();c.close()
        return vid,did,other

    def test_owner_only_filters_and_download_cookies_without_new_claims(self):
        vid,did,other=self.seed()
        c=http.client.HTTPConnection('127.0.0.1',self.http.server_port)
        c.request('GET','/my-content?t=token-a',headers={'X-Forwarded-Proto':'https'})
        r=c.getresponse();self.assertEqual(r.status,200);html=r.read().decode();hs=r.getheaders();c.close()
        self.assertIn(f"data-owned='video-{vid}'",html);self.assertIn(f"data-owned='cardnews-{did}'",html);self.assertNotIn('다른 사람 비밀 영상',html)
        self.assertIn('카드뉴스 전용 캡션',html)
        cookies=[v for k,v in hs if k.lower()=='set-cookie'];self.assertEqual(len(cookies),3)
        self.assertTrue(all('HttpOnly' in x and 'Secure' in x for x in cookies))
        for prefix,fileid in [('videos',vid),('cardnews',did)]:
            cookie=next(x.split(';')[0] for x in cookies if 'Path=/'+prefix+';' in x)
            self.assertEqual(self.request('GET',f'/{prefix}/file/{fileid}',cookie=cookie)[0],200)
        owncookie=next(x.split(';')[0] for x in cookies if 'Path=/my-content;' in x)
        self.assertEqual(self.request('GET','/my-content',cookie=owncookie)[0],200)
        filtered=self.request('GET','/my-content?t=token-a&kind=cardnews')[2].decode()
        self.assertNotIn(f"data-owned='video-{vid}'",filtered);self.assertIn(f"data-owned='cardnews-{did}'",filtered)
        searched=self.request('GET','/my-content?'+urlencode(dict(t='token-a',q='없는 제목')))[2].decode();self.assertIn('검색 결과가 없어요',searched)
        c=db.connect();self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM exclusive_videos WHERE claimed_by=1').fetchone()['n'],1);c.close()

    def test_denies_invalid_or_inactive_identity_and_preserves_purged_history(self):
        self.assertEqual(self.request('GET','/my-content')[1]['Location'],'/find')
        self.assertEqual(self.request('GET','/my-content?t=wrong')[0],403)
        vid,did,_=self.seed();c=db.connect();c.execute('UPDATE exclusive_videos SET purged_at=? WHERE id=?',('2026-10-01',vid));c.commit();c.close()
        html=self.request('GET','/my-content?t=token-a')[2].decode();self.assertIn(f"data-owned='video-{vid}'",html);self.assertNotIn(f"/videos/file/{vid}",html);self.assertIn('원본이 정리됐어요',html)
        c=db.connect();c.execute("UPDATE partners SET status='inactive' WHERE portal_token='token-a'");c.commit();c.close()
        self.assertEqual(self.request('GET','/my-content?t=token-a')[0],403)

    def test_empty_and_pagination_escape_search(self):
        html=self.request('GET','/my-content?t=token-a')[2].decode();self.assertIn('아직 받은 콘텐츠가 없어요',html)
        code,_,body=self.request('GET','/my-content?'+urlencode(dict(t='token-a',q='<script>x</script>',page='bad')))
        self.assertEqual(code,200);self.assertNotIn('<script>x</script>',body.decode())

    def test_pagination_keeps_all_owned_records_accessible(self):
        c=db.connect()
        for n in range(25):
            c.execute('INSERT INTO exclusive_videos(title,file_key,orig_name,size,sha256,created_at,claimed_by,claimed_at,purged_at) VALUES(?,?,?,?,?,?,?,?,?)',
                      (f'보관 영상 {n}',f'k{n}','test.mp4',10,f'sha{n}','2026-01-01',1,'2026-01-01','2026-02-01'))
        c.commit();c.close()
        first=self.request('GET','/my-content?t=token-a')[2].decode()
        second=self.request('GET','/my-content?t=token-a&page=2')[2].decode()
        self.assertEqual(first.count('data-owned='),24);self.assertEqual(second.count('data-owned='),1)
        self.assertIn('page=2',first);self.assertIn('전체 25',second)
