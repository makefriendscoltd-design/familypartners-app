"""Safety contracts independent of model-produced prose."""
import datetime,importlib.util,json,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('drop_maintenance',ROOT/'deploy/drop_maintenance.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class BufferContracts(unittest.TestCase):
 def test_json_escape_representation_still_requires_literal_match(self):
  self.assertEqual(m.literal_quote('hello\\nworld','hello\nworld'),'hello\nworld')
  with self.assertRaises(RuntimeError):m.literal_quote('hello invented','hello\nworld')
 def test_crlf_evidence_is_preserved_as_literal_bytes(self):
  text=b'hello\r\nworld'.decode('utf-8')
  self.assertEqual(m.literal_quote('hello\\r\\nworld',text),text)
 def test_today_is_not_skipped(self):
  q={'drops':[]};r=m.gaps(q,datetime.date(2026,11,3))
  self.assertEqual(r[0]['day'],'2026-11-03');self.assertEqual(r[-1]['day'],'2026-11-08')
 def test_partial_day_is_flagged_instead_of_replaced(self):
  r=m.gaps({'drops':[{'date':'2026-11-03'}]},datetime.date(2026,11,3))
  self.assertTrue(r[0]['partial']);self.assertEqual(r[0]['regular'],1)
 def test_six_wrong_kind_posts_do_not_count_as_full(self):
  q={'drops':[{'date':'2026-11-03'} for _ in range(6)]}
  self.assertEqual(m.gaps(q,datetime.date(2026,11,3))[0]['guidance'],0)
 def test_four_cases_and_two_proposals_cover_today(self):
  q={'drops':[{'date':'2026-11-03'} for _ in range(4)]+[{'date':'2026-11-03','supplemental':True,'editorial_kind':'original_guidance'} for _ in range(2)]}
  self.assertEqual(m.gaps(q,datetime.date(2026,11,3))[0]['day'],'2026-11-04')
if __name__=='__main__':unittest.main()
