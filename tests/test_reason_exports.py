import unittest
from io import BytesIO
from openpyxl import load_workbook
from app.reason_exports import aggregate_users,employee_reason_xlsx,HEADERS
class EmployeeReasonExport(unittest.TestCase):
 def row(self,uid='1',employee='001',**kw):return {'telegram_id':uid,'employee_id':employee,'name':'Test employee','region':'R1','dealer':'CA1','recorded_at':'2026-11-13T09:00','is_reason':True,'summary_km':'គ្មានសេវាអ៊ីនធឺណិត។','summary_en':'No internet coverage.','message':'អត់សេវា',**kw}
 def test_multiple_replies_and_alias_ids_do_not_duplicate_users(self):
  rows=[self.row(),self.row(employee='manual:1'),self.row(uid=None)]
  users=aggregate_users(rows,[{'telegram_id':1,'employee_id':'001','total_outlets':3},{'telegram_id':None,'employee_id':'001','total_outlets':2}])
  self.assertEqual(len(users),1);self.assertEqual(users[0]['total_outlets'],5);self.assertEqual(users[0]['employee_id'],'001');self.assertEqual(users[0]['reply_count'],3);self.assertEqual(users[0]['summary'],'គ្មានសេវាអ៊ីនធឺណិត។')
 def test_same_names_are_separate_and_employee_search_is_applied(self):
  users=aggregate_users([self.row(),self.row(uid='2',employee='002')],[],search='002')
  self.assertEqual(len(users),1);self.assertEqual(users[0]['employee_id'],'002');self.assertEqual(users[0]['total_outlets'],0)
 def test_non_reasons_and_unclear_replies_are_labelled(self):
  no=aggregate_users([self.row(is_reason=False)],[],language='en')[0]
  self.assertEqual(no['summary'],'No stated reason for the missed report.')
  review=aggregate_users([self.row(is_reason=None)],[],language='en')[0]
  self.assertTrue(review['summary'].startswith('Needs review:'))
 def test_excel_columns_text_ids_unicode_and_print_layout(self):
  users=aggregate_users([self.row(name='=HYPERLINK("bad")')],[{'telegram_id':1,'employee_id':'001','total_outlets':7}],language='both')
  report={'date':'2026-11-13','language':'both','region':'R1','dealer':'CA1','employees':1,'total_outlets':7,'items':users}
  content=employee_reason_xlsx(report);wb=load_workbook(BytesIO(content));sheet=wb.active
  self.assertEqual(wb.sheetnames,['Employee Summary']);self.assertEqual([sheet.cell(7,c).value for c in range(1,7)],HEADERS)
  self.assertEqual(sheet['B8'].value,'001');self.assertEqual(sheet['B8'].data_type,'s');self.assertEqual(sheet['A8'].data_type,'s');self.assertEqual(sheet['E8'].value,7);self.assertIn('គ្មានសេវា',sheet['F8'].value);self.assertIn('No internet',sheet['F8'].value)
  self.assertEqual(sheet['E5'].value,7);self.assertEqual(sheet.freeze_panes,'C8');self.assertEqual(sheet.page_setup.orientation,'landscape');self.assertEqual(sheet.auto_filter.ref,'A7:F8');wb.close()
 def test_range_is_identified_in_export(self):
  report={'date':'2026-11-12','start':'2026-11-12','end':'2026-11-14','language':'km','region':'All Regions','dealer':'All Dealers','employees':0,'total_outlets':0,'items':[]}
  wb=load_workbook(BytesIO(employee_reason_xlsx(report)))
  self.assertEqual(wb.active['B3'].value,'2026-11-12 to 2026-11-14');wb.close()
if __name__=='__main__':unittest.main()
