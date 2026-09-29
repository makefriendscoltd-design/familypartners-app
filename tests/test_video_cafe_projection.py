import hashlib,json,tempfile,unittest
from pathlib import Path
from deploy.build_video_cafe_projection import build

class ProjectionTest(unittest.TestCase):
    def test_source_key_alone_does_not_approve(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'reviews').mkdir();(p/'inv.json').write_text(json.dumps({'items':[{'video_id':1,'video_sha256':'a'*64,'source_key':'D34sAzRiZmA','distribution':'available'}]}));(p/'queue.json').write_text('{"entries":[]}');(p/'matches.json').write_text('{"reviews":[]}')
            row=build(p/'inv.json',p/'queue.json',p/'reviews',p/'matches.json',p)['rows'][0]
            self.assertFalse(row['clip_topic_match_verified']);self.assertIsNone(row['cafe_url'])
