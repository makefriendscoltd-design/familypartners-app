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
        for did in (a1, a2, b1):                                   # 디자인 상관없이 전부 보인다
            self.assertIn(f"data-deck='{did}'", page)
        self.assertEqual(self.claim_deck(b1, 'a')[0], 303)          # 아무 디자인이나 고를 수 있다
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
