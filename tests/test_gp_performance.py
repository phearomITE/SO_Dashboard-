import tempfile,time,unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from app.gp_performance import cached_preview,scan_plan,reuse_artifacts,store_artifacts
class FakeDB:
 def __init__(self,rows):self.rows=rows
 def groups(self):return [{'group_id':1},{'group_id':2}]
 def connect(self):return self
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def execute(self,*args):return self.rows
class FakeRedis:
 def __init__(self):self.data={};self.reads=0
 def get(self,key):self.reads+=1;return self.data.get(key)
 def setex(self,key,ttl,value):self.data[key]=value
class PerformanceChecks(unittest.TestCase):
 def test_complete_history_reuses_only_finished_groups(self):
  db=FakeDB([(1,'2026-11-03','2026-11-03T18:00:00+00:00'),(2,'2026-11-03','2026-11-03T10:00:00+00:00')])
  result=scan_plan(db,'2026-11-03','2026-11-03',now=datetime(2026,11,13,tzinfo=timezone.utc))
  self.assertEqual([g['group_id'] for g in result[0][1]],[2]);self.assertEqual(result[0][2],1)
  self.assertEqual(len(scan_plan(db,'2026-11-03','2026-11-03',force=True)[0][1]),2)
 def test_today_cache_expires_and_missing_group_is_not_invented(self):
  db=FakeDB([(1,'2026-11-13','2026-11-13T05:59:30+00:00')]);now=datetime(2026,11,13,6,tzinfo=timezone.utc)
  self.assertEqual(scan_plan(db,'2026-11-13','2026-11-13',now=now)[0][2],1)
  self.assertEqual(scan_plan(db,'2026-11-13','2026-11-13',now=now.replace(minute=2))[0][2],0)
 def test_redis_preview_hit_and_file_change_invalidation(self):
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'report.xlsx';path.write_text('first');calls=[];client=FakeRedis()
   def build(path):calls.append(1);return {'value':path.read_text()}
   self.assertEqual(cached_preview(path,build,client),{'value':'first'})
   self.assertEqual(cached_preview(path,build,client),{'value':'first'});self.assertEqual(len(calls),1)
   path.write_text('updated workbook')
   self.assertEqual(cached_preview(path,build,client),{'value':'updated workbook'});self.assertEqual(len(calls),2)
 def test_artifact_cache_invalidates_sources_and_isolates_manual_edits(self):
  import json
  with tempfile.TemporaryDirectory() as directory:
   base=Path(directory);output=base/'jobs'/'first'/'outputs';output.mkdir(parents=True)
   (output/'day.xlsx').write_bytes(b'original generated report');(output.parent/'reports.zip').write_bytes(b'zip')
   job={'id':'first','start':'2026-11-03','end':'2026-11-03','source':'auto','files':[{'name':'day.xlsx'}]}
   with patch('app.gp_performance.source_fingerprint',return_value='source-v1'):
    store_artifacts(base,job,None)
    (output/'day.xlsx').write_bytes(b'manually edited report')
    next_job={**job,'id':'second'};self.assertTrue(reuse_artifacts(base,next_job,None))
    self.assertEqual((base/'jobs/second/outputs/day.xlsx').read_bytes(),b'original generated report')
   with patch('app.gp_performance.source_fingerprint',return_value='source-v2'):
    self.assertFalse(reuse_artifacts(base,{**job,'id':'third'},None))
 def test_auto_does_not_reuse_saved_only_snapshot(self):
  with tempfile.TemporaryDirectory() as directory:
   base=Path(directory);output=base/'jobs'/'first'/'outputs';output.mkdir(parents=True)
   (output/'day.xlsx').write_bytes(b'report');(output.parent/'reports.zip').write_bytes(b'zip')
   job={'id':'first','start':'2026-11-03','end':'2026-11-03','source':'saved','files':[{'name':'day.xlsx'}]}
   with patch('app.gp_performance.source_fingerprint',return_value='source-v1'):
    store_artifacts(base,job,None)
    self.assertFalse(reuse_artifacts(base,{**job,'id':'second','source':'auto'},None))
 def test_unavailable_redis_falls_back(self):
  class Broken:
   def get(self,*args):raise OSError()
   def setex(self,*args):raise OSError()
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'report.xlsx';path.write_text('data')
   self.assertEqual(cached_preview(path,lambda p:{'ok':True},Broken()),{'ok':True})
if __name__=='__main__':unittest.main()
