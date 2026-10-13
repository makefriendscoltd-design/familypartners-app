import re
import unittest
import test_video_library as fixture
from fp import db

class FeedArchiveTest(unittest.TestCase):
    setUp = fixture.VideoHTTPTest.setUp
    tearDown = fixture.VideoHTTPTest.tearDown
    request = fixture.VideoHTTPTest.request

    def test_archive_beyond_365_in_both_orders_and_future_hidden(self):
        c=db.connect()
        for i in range(401):
            c.execute('INSERT INTO drops(title,body,drop_date,created_at) VALUES (?,?,?,CURRENT_TIMESTAMP)',
                      (f'글감 {i+1}', '본문', f'2020-01-{1+i//20:02d}'))
        c.execute('INSERT INTO drops(title,body,drop_date,created_at) VALUES (?,?,?,CURRENT_TIMESTAMP)',('예약글','비공개','2999-01-01'))
        c.commit();c.close()
        for order in ('latest','oldest'):
            previous=[]
            for n in (60,120,180,240,300,360,420):
                status,_,body=self.request('GET',f'/feed?n={n}&order={order}&t=token-a')
                self.assertEqual(status,200)
                html=body.decode()
                ids=[int(x) for x in re.findall(r"class='tile[^']*' href='#d(\d+)'",html)]
                self.assertEqual(len(ids),min(n,401))
                self.assertEqual(ids[:len(previous)],previous)
                self.assertEqual(ids,sorted(ids,reverse=order=='latest'))
                self.assertNotIn('예약글',html)
                if n<401:
                    self.assertIn(f'/feed?n={n+60}&order={order}&t=token-a',html)
                else:self.assertNotIn('더 보기 ↓',html)
                previous=ids
            self.assertEqual(set(previous),set(range(1,402)))
        status,_,body=self.request('GET','/feed?n=999999999999999999999999999999')
        self.assertEqual(status,200)
        self.assertIn('전체 401개 중 401개 표시',body.decode())
