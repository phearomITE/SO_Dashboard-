import json,unittest
from unittest.mock import patch
from types import SimpleNamespace
from app import admin_api as api
class Engine:
 def __init__(self,changed=1):self.reads=[];self.writes=[];self.write_sql=[];self.changed=changed;self.rows=[{'key':'reason_review:1:2','data':{'date':'2026-11-13','message':'ខ្ញុំមិនបានផ្ញើ ព្រោះអត់សេវា','is_reason':False,'summary':'Old English summary','employee_id':'123'}}]
 def connect(self):return self
 def begin(self):return self
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def execute(self,sql,params):
  if str(sql).startswith('SELECT'):self.reads.append(str(sql));return self
  self.write_sql.append(str(sql));self.writes.append(params);return SimpleNamespace(rowcount=self.changed)
 def mappings(self):return self
 def all(self):return self.rows
class KhmerReprocessing(unittest.TestCase):
 def test_previously_negative_reply_gets_khmer_without_changing_source(self):
  engine=Engine();result={'is_reason':True,'summary_km':'គ្មានសេវាអ៊ីនធឺណិត។','summary_en':'No internet coverage.','summary':'No internet coverage.','ai_status':'completed'}
  with patch.object(api.db,'engine',engine),patch('app.reason_analysis.analyze_reason',return_value=result):
   api._reanalyze_pending_replies('2026-11-13',None,None,scope='missing_khmer')
  self.assertIn('summary_km',engine.reads[0]);self.assertNotIn("data->>'is_reason' IS NULL",engine.reads[0])
  updated=json.loads(engine.writes[0]['data']);self.assertEqual(updated['message'],engine.rows[0]['data']['message']);self.assertEqual(updated['employee_id'],'123');self.assertTrue(updated['is_reason']);self.assertTrue(updated['summary_km'])
 def test_failure_preserves_prior_classification(self):
  engine=Engine()
  with patch.object(api.db,'engine',engine),patch('app.reason_analysis.analyze_reason',return_value={'ai_status':'unavailable','ai_error':'offline','is_reason':None}):
   api._reanalyze_pending_replies('2026-11-13',None,None)
  updated=json.loads(engine.writes[0]['data']);self.assertFalse(updated['is_reason']);self.assertEqual(updated['summary'],'Old English summary');self.assertEqual(updated['ai_status'],'unavailable')
 def test_changed_source_is_not_overwritten(self):
  engine=Engine(changed=0)
  with patch.object(api.db,'engine',engine),patch('app.reason_analysis.analyze_reason',return_value={'is_reason':None,'summary_km':'ត្រូវពិនិត្យបន្ថែម។','ai_status':'completed'}):
   api._reanalyze_pending_replies('2026-11-13',None,None,scope='all')
  self.assertIn('data::jsonb=CAST(:previous AS jsonb)',engine.write_sql[0])
  self.assertEqual(api._REASON_REPROCESS_STATUS['running'],False)
if __name__=='__main__':unittest.main()
