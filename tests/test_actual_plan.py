import unittest
from datetime import date
from fastapi import HTTPException
from app.actual_plan import summarize_actual_plan,outlet_targets_from_rows
from app.report_dates import reporting_period

class ActualPlanTests(unittest.TestCase):
 def roster(self):return [{'employee':'001','uid':1,'name':'Employee One','region':'R1','dealer':'CA1','source':'excel'},{'employee':'002','uid':2,'name':'No checkins','region':'R1','dealer':'CA1','source':'excel'}]
 def plan(self,code='001201',day='2026-11-13',employee='001'):
  return {'employee':employee,'day':day,'data':{'commune_code':code,'commune':'Commune A','district':'District A','region':'R1','dealer':'CA1','sale_name':'Employee One'}}
 def visit(self,code='1201',day='2026-11-13',count=3,**kw):return {'employee_id':'001','telegram_id':1,'real_name':'Employee One','region':'R1','dealer':'CA1','report_date':day,'actual_commune_code':code,'actual_commune':'Commune A' if code else '', 'actual_district':'District A','checkins':count,**kw}
 def test_repeated_visits_complete_only_one_commune_day(self):
  result=summarize_actual_plan(self.roster(),[self.plan(),self.plan(),self.plan(day='2026-11-14')],[self.visit()],{'2026-11-13','2026-11-14'})
  row=result['items'][0];self.assertEqual(row['scheduled'],2);self.assertEqual(row['completed'],1);self.assertEqual(row['commune_completion_percent'],50);self.assertEqual(row['total_checkins'],3);self.assertEqual(row['correct'],3)
  self.assertEqual(len(result['items']),2);self.assertEqual(result['items'][1]['status'],'No outlet target')
 def test_wrong_commune_and_missing_location_are_distinct(self):
  result=summarize_actual_plan(self.roster(),[self.plan()],[self.visit(code='9999',count=2),self.visit(code='',count=4)],{'2026-11-13'})
  row=result['items'][0];self.assertEqual(row['incorrect'],2);self.assertEqual(row['unassessed'],4);self.assertEqual(row['completed'],0)
 def test_no_workbook_day_is_unassessed(self):
  row=summarize_actual_plan(self.roster(),[],[self.visit()],set())['items'][0]
  self.assertEqual(row['unassessed'],3);self.assertIsNone(row['actual_plan_percent'])
 def test_manual_identity_maps_without_duplicate_employees(self):
  roster=self.roster()+[{'employee':'manual:1','uid':1,'name':'Employee One','region':'R1','dealer':'CA1','source':'manual'}]
  result=summarize_actual_plan(roster,[self.plan(employee='manual:1')],[self.visit(employee_id='manual:1')],{'2026-11-13'},search='001')
  self.assertEqual(len(result['items']),1);self.assertEqual(result['items'][0]['employee_id'],'001');self.assertEqual(result['items'][0]['completed'],1)
 def test_requested_outlet_percentage_and_position(self):
  roster=self.roster();roster[0]['position']='SO'
  pool=[{'employee_id':'001','name':'Employee One','region':'R1','dealer':'CA1','total_outlets':400}]
  row=summarize_actual_plan(roster,[self.plan()],[self.visit(count=100)],{'2026-11-13'},outlet_targets=pool)['items'][0]
  self.assertEqual(row['total_outlets'],400);self.assertEqual(row['actual_plan_percent'],25);self.assertEqual(row['position'],'SO')
 def test_outlet_pool_deduplication_includes_unmarked_rows(self):
  def row(code,ws,hor):return {'values':{'employee id':'001','sale name':'Employee One','region':'R1','dealer':'CA1','com.code':code,'#ws/ds':ws,'#horeca outlet':hor}}
  pool=outlet_targets_from_rows([row('001201',300,100),row('1201',300,100),row('1202',20,30)])
  self.assertEqual(sum(r['total_outlets'] for r in pool),450)
 def test_repeat_visits_may_exceed_target_and_zero_is_undefined(self):
  pool=[{'employee_id':'001','name':'Employee One','region':'R1','dealer':'CA1','total_outlets':10}]
  report=summarize_actual_plan(self.roster(),[],[self.visit(count=15)],set(),outlet_targets=pool)
  self.assertEqual(report['items'][0]['actual_plan_percent'],150);self.assertEqual(report['totals']['actual_plan_percent'],150)
  self.assertIsNone(report['items'][1]['actual_plan_percent'])
 def test_inclusive_date_range_validation(self):
  d=date(2026,11,13);self.assertEqual(reporting_period(d),(d,d));self.assertEqual(reporting_period(None,d,date(2026,11,14)),(d,date(2026,11,14)))
  for start,end in [(d,None),(date(2026,11,14),d),(date(2026,1,1),d)]:
   with self.assertRaises(HTTPException):reporting_period(None,start,end)
if __name__=='__main__':unittest.main()
