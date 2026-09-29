import json

from fp import db, video_cafe, video_library as v
from tests.test_video_library import VideoHTTPTest


class VideoCafeTest(VideoHTTPTest):
    def test_verified_link_and_stale_video_hash(self):
        uploaded=json.loads(self.upload()[2]);vid=uploaded['id'];self.publish(vid)
        c=db.connect();row=v.get_video(c,vid)
        base=dict(video_sha256=uploaded['sha256'],source_key='D34sAzRiZmA',updated_at='2026-09-29T00:00:00+00:00')
        with self.assertRaises(ValueError):
            video_cafe.upsert(c,row,{**base,'status':'published_verified','cafe_title':'정리글','cafe_url':'https://cafe.naver.com/westudyssat/6219','topic_match_verified':False})
        video_cafe.upsert(c,row,{**base,'status':'published_verified','cafe_title':'정리글','cafe_url':'https://cafe.naver.com/westudyssat/6219','topic_match_verified':True,'clip_review_sha256':'a'*64,'article_sha256':'b'*64});c.close()
        page=self.request('GET','/videos',cookie=self.a)[2].decode()
        self.assertIn('영상 정리글 보기',page)
        payload=json.loads(self.request('GET',f'/videos/cafe/{vid}',cookie=self.a)[2])
        self.assertEqual(payload['source_key'],'D34sAzRiZmA')
        c=db.connect();c.execute('UPDATE exclusive_videos SET sha256=? WHERE id=?',('c'*64,vid));c.commit();c.close()
        self.assertNotIn('영상 정리글 보기',self.request('GET','/videos',cookie=self.a)[2].decode())
