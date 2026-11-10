"""Safety contracts independent of model-produced prose."""
import datetime,importlib.util,json,unittest,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('drop_maintenance',ROOT/'deploy/drop_maintenance.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class BufferContracts(unittest.TestCase):
 def test_old_short_threads_cannot_pass_automatic_authoring(self):
  for count in (2,3,6):
   with self.assertRaises(RuntimeError):m.validate_reply_depth([{'replies':['내용']*count}])
  for count in (4,5):m.validate_reply_depth([{'replies':['내용']*count}])
 def test_source_anchor_preserves_list_prefix_and_uses_lf(self):
  body='4. 나중에 꺼내 쓸 기록을 남겨두세요.\r\n5. 결과와 원문을 함께 확인하세요.'
  pack={'sources':{'originals':{'key':'originals','title':'초기 원문','content':[{'title':'컨텐츠 25','body':body}]}}}
  with tempfile.TemporaryDirectory() as folder:
   w=Path(folder);out=m.text_snapshots(w,pack);text=(w/'originals.txt').read_bytes().decode()
   self.assertNotIn('\r',text)
   anchors=list(out['sources']['originals']['evidence_anchors'].values())
   self.assertIn('4. 나중에 꺼내 쓸 기록을 남겨두세요.',anchors)
   self.assertTrue(all(x in text for x in anchors))
   self.assertEqual(out['sources']['originals']['content'][0]['body'],body)
 def test_json_escape_representation_still_requires_literal_match(self):
  self.assertEqual(m.literal_quote('hello\\nworld','hello\nworld'),'hello\nworld')
  with self.assertRaises(RuntimeError):m.literal_quote('hello invented','hello\nworld')
 def test_crlf_evidence_is_preserved_as_literal_bytes(self):
  text=b'hello\r\nworld'.decode('utf-8')
  self.assertEqual(m.literal_quote('hello\\r\\nworld',text),text)
 def test_model_crlf_and_lf_both_match_canonical_lf_snapshot(self):
  self.assertEqual(m.literal_quote('hello\r\nworld','hello\nworld'),'hello\nworld')
  self.assertEqual(m.literal_quote('hello\nworld','hello\nworld'),'hello\nworld')
  with self.assertRaises(RuntimeError):m.literal_quote('hello changed world','hello\nworld')
 def test_today_is_not_skipped(self):
  for mode in ('four_plus_two','easy_news'):
   q={'drops':[]};r=m.gaps(q,datetime.date(2026,11,3),mode=mode)
   self.assertEqual(r[0]['day'],'2026-11-03');self.assertEqual(r[-1]['day'],'2026-11-08')
  q={'drops':[]};r=m.gaps(q,datetime.date(2026,11,3))
  self.assertEqual(r[0]['day'],'2026-11-03');self.assertEqual(r[-1]['day'],'2026-11-08')
 def test_partial_day_is_flagged_instead_of_replaced(self):
  r=m.gaps({'drops':[{'date':'2026-11-03'}]},datetime.date(2026,11,3),mode='four_plus_two')
  self.assertTrue(r[0]['partial']);self.assertEqual(r[0]['regular'],1)
 def test_six_wrong_kind_posts_do_not_count_as_full(self):
  q={'drops':[{'date':'2026-11-03'} for _ in range(6)]}
  self.assertEqual(m.gaps(q,datetime.date(2026,11,3),mode='four_plus_two')[0]['guidance'],0)
 def test_four_cases_and_two_proposals_cover_today(self):
  q={'drops':[{'date':'2026-11-03'} for _ in range(4)]+[{'date':'2026-11-03','supplemental':True,'editorial_kind':'original_guidance'} for _ in range(2)]}
  self.assertEqual(m.gaps(q,datetime.date(2026,11,3),mode='four_plus_two')[0]['day'],'2026-11-04')
  self.assertEqual(m.gaps(q,datetime.date(2026,11,3),mode='easy_news')[0]['day'],'2026-11-04')
if __name__=='__main__':unittest.main()
