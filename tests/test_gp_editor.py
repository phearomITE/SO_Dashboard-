"""GP editing regression checks run with python -m unittest tests.test_gp_editor."""
import json
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch
from openpyxl import Workbook,load_workbook
from openpyxl.styles import PatternFill
from app.gp_preview import edit_workbook,workbook_preview,report_revision

class ReportEditing(unittest.TestCase):
 def setUp(self):
  self.directory=tempfile.TemporaryDirectory();self.root=Path(self.directory.name)
  self.source=self.root/'report.xlsx';self.target=self.root/'edited.xlsx'
  wb=Workbook();a=wb.active;a.title='Report GP';b=wb.create_sheet('Report with Dealer')
  for sheet in wb:
   sheet['A1']='Region';sheet.merge_cells('A1:B1');sheet['D10']=2;sheet['E10']=3;sheet['F10']=1
   sheet['G10']='=D10+E10-F10';sheet['J10']=8;sheet['K10']='=IFERROR(J10/G10,0)';sheet['G18']='=SUM(G10:G17)'
   sheet['D10'].fill=PatternFill('solid',fgColor='FFE699');sheet['K10'].number_format='0.00'
  b['D18']="='Report GP'!D10"
  wb.save(self.source);wb.close()
 def tearDown(self):self.directory.cleanup()
 def test_edit_preserves_template_and_recalculates_both_sheets(self):
  edit_workbook(self.source,self.target,[{'sheet':'Report GP','address':'D10','value':'7'},{'sheet':'Report with Dealer','address':'A2','value':'ចែ ធី'}])
  wb=load_workbook(self.target)
  self.assertEqual(wb.sheetnames,['Report GP','Report with Dealer'])
  self.assertEqual(wb['Report GP']['D10'].fill.fgColor.rgb,'00FFE699')
  self.assertIn('A1:B1',str(wb['Report GP'].merged_cells))
  self.assertEqual(wb['Report GP']['G10'].value,'=D10+E10-F10');wb.close()
  preview=workbook_preview(self.target);self.assertEqual(preview['formula_errors'],[])
  values={s['name']:{c['address']:c['text'] for row in s['rows'] for c in row['cells']} for s in preview['sheets']}
  self.assertEqual(values['Report GP']['G10'],'9');self.assertEqual(values['Report GP']['G18'],'9')
  self.assertEqual(values['Report GP']['K10'],'0.89');self.assertEqual(values['Report with Dealer']['D18'],'7')
  self.assertEqual(values['Report with Dealer']['A2'],'ចែ ធី')
 def test_invalid_edits_leave_original_unchanged(self):
  revision=report_revision(self.source)
  for address,value in [('B1','bad'),('Z99','bad'),('G10','=G10'),('D10','=SUM(A1:XFD1048576)'),('G10','=WEBSERVICE("https://example.org")')]:
   with self.subTest(address=address,value=value):
    with self.assertRaises(ValueError):edit_workbook(self.source,self.target,[{'sheet':'Report GP','address':address,'value':value}])
    self.assertEqual(report_revision(self.source),revision)
 def test_hide_unhide_columns_preserves_data_and_formulas(self):
  edit_workbook(self.source,self.target,[],[{'sheet':'Report GP','hidden_columns':[4,5]}])
  preview=workbook_preview(self.target)
  self.assertEqual(preview['sheets'][0]['hidden_columns'],[4,5])
  self.assertEqual(preview['sheets'][1]['hidden_columns'],[])
  self.assertEqual(preview['formula_errors'],[])
  wb=load_workbook(self.target)
  self.assertTrue(wb['Report GP'].column_dimensions['D'].hidden)
  self.assertEqual(wb['Report GP']['D10'].value,2)
  self.assertEqual(wb['Report GP']['G10'].value,'=D10+E10-F10');wb.close()
  unhidden=self.root/'unhidden.xlsx'
  edit_workbook(self.target,unhidden,[],[{'sheet':'Report GP','hidden_columns':[]}])
  self.assertEqual(workbook_preview(unhidden)['sheets'][0]['hidden_columns'],[])
 def test_reject_hiding_all_or_unknown_columns(self):
  for hidden in [list(range(1,12)),[0],[12]]:
   with self.subTest(hidden=hidden):
    with self.assertRaises(ValueError):edit_workbook(self.source,self.target,[],[{'sheet':'Report GP','hidden_columns':hidden}])
 def test_save_api_backups_conflicts_and_zip(self):
  from fastapi import HTTPException
  from app import gp_reports as gp
  key=str(uuid.uuid4());directory=self.root/key;output=directory/'outputs';output.mkdir(parents=True)
  import shutil;shutil.copy2(self.source,output/'report.xlsx')
  job={'id':key,'status':'completed','files':[{'name':'report.xlsx','validation':{'sheets':2}}]}
  (directory/'job.json').write_text(json.dumps(job))
  revision=report_revision(output/'report.xlsx')
  body=gp.WorkbookEdits(revision=revision,visibility=[gp.ColumnVisibility(sheet='Report GP',hidden_columns=[4])])
  with patch.object(gp,'JOBS',self.root):
   result=gp.save_workbook(key,'report.xlsx',body,_admin={})
   self.assertNotEqual(result['revision'],revision)
   self.assertEqual(result['sheets'][0]['hidden_columns'],[4])
   self.assertEqual(len(list((directory/'edit_backups').glob('*/report.xlsx'))),1)
   with zipfile.ZipFile(directory/'reports.zip') as archive:self.assertEqual(archive.namelist(),['report.xlsx'])
   with self.assertRaises(HTTPException) as conflict:gp.save_workbook(key,'report.xlsx',body,_admin={})
   self.assertEqual(conflict.exception.status_code,409)
   with self.assertRaises(HTTPException) as unknown:gp.save_workbook(key,'other.xlsx',body,_admin={})
   self.assertEqual(unknown.exception.status_code,404)

if __name__=='__main__':unittest.main()
