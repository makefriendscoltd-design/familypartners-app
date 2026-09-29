"""카드뉴스방: 템플릿 고정 배정, 하루 1개, ZIP 배포."""
import io, json, zipfile
from urllib.parse import urlencode, quote
import test_video_library as base
from fp import db, cardnews_library as cn, video_library as v


def deck_zip(tag):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for i in range(1, 11):
            zf.writestr(f'{tag}/{i:02}.png', b'png' + bytes([i]) + tag.encode())
    return buf.getvalue()


class CardnewsTest(base.VideoHTTPTest):
    def up(self, template, tag):
        status, _, body = self.request('POST', '/op/cardnews/upload', deck_zip(tag), self.admin,
                                       {'X-CSRF-Token': v.csrf('admin'), 'X-Deck-Title': quote('n8n 에이전트 5단계'),
                                        'X-Template': template})
        self.assertEqual(status, 201, body)
        did = json.loads(body)['id']
        jpeg = b'\xff\xd8\xff' + b'thumb' + b'\xff\xd9'
        self.assertEqual(self.request('POST', f'/op/cardnews/thumbnail/{did}', jpeg, self.admin,
                                      {'X-CSRF-Token': v.csrf('admin')})[0], 200)
        return did

    def claim_deck(self, did, who):
        token, name, cookie = ('token-a', '테스트가', self.a) if who == 'a' else ('token-b', '테스트나', self.b)
        return self.request('POST', '/cardnews/claim', urlencode(dict(id=did, name=name, t=token, csrf=v.csrf(token))), cookie)

    def test_open_gallery_daily_one_and_zip_download(self):
        self.assertEqual(self.request('POST', '/op/cardnews/upload', b'not a zip at all 123456789', self.admin,
                                      {'X-CSRF-Token': v.csrf('admin'), 'X-Deck-Title': 'x', 'X-Template': '01-gradient'})[0], 400)
        a1 = self.up('01-gradient', 'a1'); a2 = self.up('02-shortcut', 'a2'); b1 = self.up('05-hero', 'b1')
        page = self.request('GET', '/cardnews', cookie=self.a)[2].decode()
        self.assertIn('img.deck-cover{aspect-ratio:1/1', page)    # 정사각형 표지가 잘리지 않게
        for did in (a1, a2, b1):                                   # 디자인 상관없이 전부 보인다
            self.assertIn(f"data-deck='{did}'", page)
        self.assertEqual(self.claim_deck(b1, 'a')[0], 303)          # 아무 디자인이나 고를 수 있다
        got = self.claim_deck(b1, 'a')
        self.assertEqual(got[1]['Location'], f'/cardnews?got={b1}')
        c = db.connect(); c.execute('UPDATE cardnews_decks SET caption=? WHERE id=?', ('테스트 캡션\n\n댓글에 AIMAX 남기면', b1)); c.commit(); c.close()
        page = self.request('GET', f'/cardnews?got={b1}', cookie=self.a)[2].decode()
        self.assertIn('카드뉴스를 받았어요', page); self.assertIn('테스트 캡션', page); self.assertIn(f'/cardnews/file/{b1}', page)
        self.assertEqual(self.claim_deck(a2, 'a')[0], 409)          # 하루 1개
        self.assertEqual(self.claim_deck(b1, 'a')[0], 303)          # 받은 건 다시 받기 가능
        self.assertEqual(self.claim_deck(b1, 'b')[0], 409)          # 한 묶음은 한 사람만
        self.assertNotIn(f"data-deck='{b1}'", self.request('GET', '/cardnews', cookie=self.b)[2].decode())
        status, headers, body = self.request('GET', f'/cardnews/file/{b1}', cookie=self.a)
        self.assertEqual(status, 200); self.assertEqual(headers['Content-Type'], 'application/zip')
        self.assertEqual(len(zipfile.ZipFile(io.BytesIO(body)).namelist()), 10)
        self.assertEqual(self.request('GET', f'/cardnews/file/{b1}', cookie=self.b)[0], 403)
        self.assertEqual(self.claim_deck(a1, 'b')[0], 303)
        status = json.loads(self.request('GET', '/op/cardnews/sync-status', cookie=self.admin)[2])
        self.assertEqual(status['stock']['available'], 1)
        self.assertEqual(json.loads(self.request('GET', '/op/videos/sync-status', cookie=self.admin)[2])['videos'], [])
        admin_page = self.request('GET', '/op/cardnews', cookie=self.admin)[2].decode()
        self.assertIn('지금 파트너에게 보이는 카드뉴스', admin_page)
        self.assertIn(f"/cardnews/thumb/{a2}", admin_page)             # 관리자도 표지를 본다
        self.assertEqual(self.request('GET', f'/cardnews/thumb/{a2}', cookie=self.admin)[0], 200)
        self.assertIn('받을 수 있는 카드뉴스', cn.dashboard_card(admin=True))


class RoomsHubTest(base.VideoHTTPTest):
    def test_workroom_shows_three_rooms_and_feed_is_writing_only(self):
        page = self.request('GET', '/me?t=token-a')[2].decode()
        for room in ('✍️', '글감방', '🎬', '영상방', '🗂', '카드뉴스방'):
            self.assertIn(room, page)
        self.assertIn("href='/cardnews?t=token-a'", page)
        self.assertIn('오늘 0/3편 받음', page)
        self.assertNotIn('id=partner-videos', page)             # 미리보기 대신 방 버튼
        feed = self.request('GET', '/feed?t=token-a')[2].decode()
        self.assertNotIn('id=partner-videos', feed); self.assertNotIn('id=partner-cardnews', feed)
