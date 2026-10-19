import copy
import json
import subprocess
import unittest
from pathlib import Path
import test_video_library as fixture
from fp import db, dropqueue, core

class ThreadMigrationTest(unittest.TestCase):
    setUp = fixture.VideoHTTPTest.setUp
    tearDown = fixture.VideoHTTPTest.tearDown

    def test_mixed_formats_preserve_existing_ids_without_new_posts(self):
        old = json.loads(subprocess.check_output(['git', 'show', 'b0c7804:config/drops_queue.json']))
        path = Path(self.tmp.name) / 'official-before.json'
        path.write_text(json.dumps(old))
        dropqueue.sync(path, verbose=False)
        c = db.connect()
        before = {r['id']: dict(r) for r in c.execute('SELECT * FROM drops')}
        c.close()
        self.assertEqual(dropqueue.sync(verbose=False), {'added': 0, 'updated': 20, 'skipped': 45, 'bad': 0})
        c = db.connect()
        after = {r['id']: dict(r) for r in c.execute('SELECT * FROM drops')}
        c.close()
        self.assertEqual(set(before), set(after))
        self.assertEqual(sum(before[i] != after[i] for i in before), 20)
        for i in before:
            self.assertEqual(before[i]['created_at'], after[i]['created_at'])
            self.assertEqual(before[i]['drop_date'], after[i]['drop_date'])
        self.assertEqual(dropqueue.sync(verbose=False), {'added': 0, 'updated': 0, 'skipped': 65, 'bad': 0})

    def test_real_reserved_queue_renames_in_place_preserves_dates_and_replays_safely(self):
        old=json.loads(subprocess.check_output(['git','show','9c5c341:config/drops_queue.json']))
        path=Path(self.tmp.name)/'before.json';path.write_text(json.dumps(old))
        first=dropqueue.sync(path,verbose=False)
        self.assertEqual(first['added'],62)
        c=db.connect();before={r['id']:dict(r) for r in c.execute('SELECT * FROM drops')};c.close()
        changes=dropqueue.sync(verbose=False)
        # Twenty reservations and two supplemental posts now have revised copy.
        self.assertEqual(changes,{'added':3,'updated':22,'skipped':40,'bad':0})
        c=db.connect();after={r['id']:dict(r) for r in c.execute('SELECT * FROM drops')};c.close()
        self.assertTrue(set(before).issubset(after))
        self.assertEqual(len(set(after)-set(before)),3)
        changed=[]
        for i,r in before.items():
            self.assertEqual(r['drop_date'],after[i]['drop_date'])
            self.assertEqual(r['created_at'],after[i]['created_at'])
            self.assertEqual(r['assets'],after[i]['assets'])
            if r!=after[i]:changed.append(i)
        self.assertEqual(len(changed),22)
        self.assertEqual(dropqueue.sync(verbose=False),{'added':0,'updated':0,'skipped':65,'bad':0})

    def test_deleted_reservation_is_not_recreated_under_new_title(self):
        d=next(x for x in json.loads(dropqueue.QUEUE_PATH.read_text())['drops'] if x.get('previous_title'))
        c=db.connect();core.set_setting(c,f"seeded_drop:{d['date']}:{d['previous_title']}",'old-fingerprint');c.close()
        path=Path(self.tmp.name)/'one.json';path.write_text(json.dumps({'drops':[d]}))
        self.assertEqual(dropqueue.sync(path,verbose=False)['added'],0)
        c=db.connect();self.assertEqual(c.execute('SELECT COUNT(*) FROM drops').fetchone()[0],0);c.close()
