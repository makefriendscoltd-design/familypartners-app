"""카드뉴스방: 디자인 자유 선택, 하루 2개, ZIP 배포."""
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

    def test_hf_existing_cards_and_instagram_source(self):
        # 2026-11-14: 블루 템플릿은 파트너스 페이지에 안 올린다. 인스타 릴스 출처 지원은 유지.
        self.assertEqual(self.request('POST', '/op/cardnews/upload', b'PK' + b'0' * 40, self.admin,
                                      {'X-CSRF-Token': v.csrf('admin'), 'X-Deck-Title': 'x', 'X-Template': '11-hf-blue'})[0], 400)
        did = self.up('08-poster', 'hf-source-cards')
        c = db.connect()
        sha = cn.get_deck(c, did)['sha256']
        c.close()
        source = 'https://www.instagram.com/reel/DfIuvawOTza/'
        headers = {'X-CSRF-Token': v.csrf('admin')}
        status, _, body = self.request('POST', f'/op/cardnews/source/{did}',
                                      urlencode(dict(sha256=sha, source_url=source)), self.admin, headers)
        self.assertEqual(status, 200, body)
        status = json.loads(self.request('GET', '/op/cardnews/sync-status', cookie=self.admin)[2])
        row = next(r for r in status['decks'] if r['id'] == did)
        self.assertEqual(row['source_url'], source)
        self.assertIn(f"data-deck='{did}'", self.request('GET', '/cardnews', cookie=self.a)[2].decode())
        self.assertEqual(self.claim_deck(did, 'a')[0], 303)
        status, _, body = self.request('GET', f'/cardnews/file/{did}', cookie=self.a)
        self.assertEqual(status, 200)
        self.assertEqual(len(zipfile.ZipFile(io.BytesIO(body)).namelist()), 10)
        for unsafe in ('http://www.instagram.com/reel/abc/', 'https://www.instagram.com.evil.test/reel/abc/',
                       'https://www.instagram.com/accounts/login/', 'https://www.instagram.com/reel/abc/?token=x'):
            self.assertFalse(cn.valid_source_url(unsafe))
        self.assertFalse(v.valid_source_url(source))

    def test_same_source_only_once(self):
        h = {'X-CSRF-Token': v.csrf('admin')}
        src = 'https://www.youtube.com/watch?v=ABCDEFGHIJK'
        first = self.up('08-poster', 's1')
        c = db.connect(); sha1 = cn.get_deck(c, first)['sha256']; c.close()
        self.assertEqual(self.request('POST', f'/op/cardnews/source/{first}', urlencode(dict(sha256=sha1, source_url=src)), self.admin, h)[0], 200)
        # 출처를 미리 알려주면 업로드 자체를 거부
        status, _, body = self.request('POST', '/op/cardnews/upload', deck_zip('s2'), self.admin,
                                       dict(h, **{'X-Deck-Title': quote('x'), 'X-Template': '09-editorial', 'X-Source-Url': quote(src + '/')}))
        self.assertEqual(status, 409, body); self.assertEqual(json.loads(body)['duplicate_of'], first)
        # 출처를 나중에 붙이면 거부하고 그 묶음은 목록에서 내린다
        late = self.up('10-grunge', 's3')
        c = db.connect(); sha3 = cn.get_deck(c, late)['sha256']; c.close()
        self.assertEqual(self.request('POST', f'/op/cardnews/source/{late}', urlencode(dict(sha256=sha3, source_url=src)), self.admin, h)[0], 409)
        page = self.request('GET', '/cardnews', cookie=self.a)[2].decode()
        self.assertIn(f"data-deck='{first}'", page); self.assertNotIn(f"data-deck='{late}'", page)

    def test_purge_retired_unclaimed_only(self):
        free_old = self.up('08-poster', 'p1'); got_old = self.up('08-poster', 'p2'); keep_new = self.up('09-editorial', 'p3')
        self.assertEqual(self.claim_deck(got_old, 'a')[0], 303)
        c = db.connect(); c.execute("UPDATE cardnews_decks SET template='03-negative' WHERE id IN (?,?)", (free_old, got_old)); c.commit()
        key = cn.get_deck(c, free_old)['file_key']; c.close()
        self.assertTrue((cn.storage_dir() / key).is_file())
        h = {'X-CSRF-Token': v.csrf('admin')}
        dry = json.loads(self.request('POST', '/op/cardnews/purge-retired', urlencode(dict(dry=1)), self.admin, h)[2])
        self.assertEqual((dry['count'], dry['deleted']), (1, 0))
        self.assertEqual(self.request('POST', '/op/cardnews/purge-retired', urlencode(dict(expect=5)), self.admin, h)[0], 409)
        done = json.loads(self.request('POST', '/op/cardnews/purge-retired', urlencode(dict(expect=1)), self.admin, h)[2])
        self.assertEqual(done['deleted'], 1)
        c = db.connect()
        self.assertIsNone(cn.get_deck(c, free_old)); self.assertIsNotNone(cn.get_deck(c, got_old)); self.assertIsNotNone(cn.get_deck(c, keep_new)); c.close()
        self.assertFalse((cn.storage_dir() / key).exists())
        self.assertEqual(self.request('GET', f'/cardnews/file/{got_old}', cookie=self.a)[0], 200)

    def test_old_templates_retired_but_claimed_ones_stay_downloadable(self):
        # 2026-11-13 디자인 교체: 옛 7종은 새로 못 올리고, 안 받은 옛 묶음은 목록에서 빠진다. 받은 사람은 계속 받는다.
        self.assertEqual(self.request('POST', '/op/cardnews/upload', b'PK' + b'0' * 40, self.admin,
                                      {'X-CSRF-Token': v.csrf('admin'), 'X-Deck-Title': 'x', 'X-Template': '01-gradient'})[0], 400)
        old_free = self.up('08-poster', 'old1'); old_got = self.up('09-editorial', 'old2'); new = self.up('10-grunge', 'new1')
        self.assertEqual(self.claim_deck(old_got, 'a')[0], 303)
        c = db.connect(); c.execute("UPDATE cardnews_decks SET template='01-gradient' WHERE id IN (?,?)", (old_free, old_got)); c.commit(); c.close()
        page = self.request('GET', '/cardnews', cookie=self.b)[2].decode()
        self.assertNotIn(f"data-deck='{old_free}'", page); self.assertIn(f"data-deck='{new}'", page)
        self.assertEqual(self.claim_deck(old_free, 'b')[0], 409)
        self.assertEqual(self.claim_deck(old_got, 'a')[0], 303)
        self.assertEqual(self.request('GET', f'/cardnews/file/{old_got}', cookie=self.a)[0], 200)

    def test_open_gallery_daily_two_and_zip_download(self):
        self.assertEqual(self.request('POST', '/op/cardnews/upload', b'not a zip at all 123456789', self.admin,
                                      {'X-CSRF-Token': v.csrf('admin'), 'X-Deck-Title': 'x', 'X-Template': '08-poster'})[0], 400)
        a1 = self.up('08-poster', 'a1'); a2 = self.up('09-editorial', 'a2'); b1 = self.up('10-grunge', 'b1'); a3 = self.up('08-poster', 'a3')
        page = self.request('GET', '/cardnews', cookie=self.a)[2].decode()
        self.assertIn('img.deck-cover{aspect-ratio:1/1', page)    # 정사각형 표지가 잘리지 않게
        for did in (a1, a2, b1, a3):                                   # 디자인 상관없이 전부 보인다
            self.assertIn(f"data-deck='{did}'", page)
        self.assertEqual(self.claim_deck(b1, 'a')[0], 303)          # 아무 디자인이나 고를 수 있다
        got = self.claim_deck(b1, 'a')
        self.assertEqual(got[1]['Location'], f'/cardnews?got={b1}')
        c = db.connect(); c.execute('UPDATE cardnews_decks SET caption=? WHERE id=?', ('테스트 캡션\n\n댓글에 AIMAX 남기면', b1)); c.commit(); c.close()
        page = self.request('GET', f'/cardnews?got={b1}', cookie=self.a)[2].decode()
        self.assertIn('카드뉴스를 받았어요', page); self.assertIn('테스트 캡션', page); self.assertIn(f'/cardnews/file/{b1}', page)
        self.assertEqual(self.claim_deck(a2, 'a')[0], 303)          # 두 번째 수령 허용
        self.assertEqual(self.claim_deck(a1, 'a')[0], 409)          # 세 번째 수령 차단
        self.assertIn('계정당 하루 2개', page)
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
        self.assertIn(f"/cardnews/thumb/{a3}", admin_page)             # 관리자도 표지를 본다
        self.assertEqual(self.request('GET', f'/cardnews/thumb/{a3}', cookie=self.admin)[0], 200)
        self.assertIn('받을 수 있는 카드뉴스', cn.dashboard_card(admin=True))


class RoomsHubTest(base.VideoHTTPTest):
    def test_workroom_shows_three_rooms_and_feed_is_writing_only(self):
        page = self.request('GET', '/me?t=token-a')[2].decode()
        for room in ('✍️', '글감방', '🎬', '영상방', '🗂', '카드뉴스방'):
            self.assertIn(room, page)
        self.assertIn("href='/cardnews?t=token-a'", page)
        self.assertIn('오늘 0/2편 받음', page)
        self.assertNotIn('id=partner-videos', page)             # 미리보기 대신 방 버튼
        feed = self.request('GET', '/feed?t=token-a')[2].decode()
        self.assertNotIn('id=partner-videos', feed); self.assertNotIn('id=partner-cardnews', feed)
