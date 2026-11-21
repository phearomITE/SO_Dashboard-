"""Two-sheet GP delivery and a read-only, template-styled browser preview."""
import ast
import hashlib
import math
import operator
import re
from datetime import date, datetime
from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.workbook.properties import CalcProperties
from openpyxl.utils import get_column_letter, range_boundaries

SHEETS = ['Report GP', 'Report with Dealer']

def keep_summary_sheets(path):
    wb = load_workbook(path)
    try:
        if any(name not in wb.sheetnames for name in SHEETS):
            raise ValueError('Both GP summary sheets are required.')
        if wb.sheetnames == SHEETS:
            return
        for name in list(wb.sheetnames):
            if name not in SHEETS:
                del wb[name]
        wb.active = 0
        temp = path.with_name(path.stem + '.summary.xlsx')
        wb.save(temp)
        temp.replace(path)
    finally:
        wb.close()

class FormulaValues:
    """Evaluate only the arithmetic, SUM and IFERROR used by the GP template."""
    reference = re.compile(r"(?:(?:'([^']+)'|([A-Za-z_][A-Za-z_ ]*))!)?(\$?[A-Z]{1,3}\$?[1-9][0-9]*)\b")
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
    def __init__(self, workbook):
        self.workbook, self.cache, self.pending = workbook, {}, set()
    def cell(self, sheet, address):
        key = (sheet, address.replace('$', ''))
        from openpyxl.utils.cell import coordinate_to_tuple
        row,column=coordinate_to_tuple(key[1])
        if row>75 or column>44:raise ValueError('Cell reference is outside this report')
        if key in self.cache:
            return self.cache[key]
        if key in self.pending:
            raise ValueError('Circular formula')
        self.pending.add(key)
        try:
            value = self.workbook[sheet][key[1]].value
            value = self.expression(value[1:], sheet) if isinstance(value, str) and value.startswith('=') else value
            self.cache[key] = value
            return value
        finally:
            self.pending.remove(key)
    def expression(self, expression, sheet):
        expression = expression.strip()
        if expression.upper().startswith('IFERROR(') and expression.endswith(')'):
            inner, fallback = expression[8:-1].rsplit(',', 1)
            try:
                return self.expression(inner, sheet)
            except (ZeroDivisionError, ValueError, TypeError):
                return self.expression(fallback, sheet)
        if expression.upper().startswith('SUM(') and expression.endswith(')'):
            bounds = range_boundaries(expression[4:-1].replace('$', ''))
            if any(x is None for x in bounds) or bounds[2]>44 or bounds[3]>75 or bounds[0]<1 or bounds[1]<1:
                raise ValueError('SUM range is outside this report')
            return sum(self.cell(sheet, f'{get_column_letter(c)}{r}') or 0
                       for r in range(bounds[1], bounds[3]+1) for c in range(bounds[0], bounds[2]+1))
        values = []
        def substitute(match):
            value = self.cell(match[1] or match[2] or sheet, match[3])
            values.append(value or 0)
            return f'v{len(values)-1}'
        tree = ast.parse(self.reference.sub(substitute, expression), mode='eval')
        def calculate(node):
            if isinstance(node, ast.Expression): return calculate(node.body)
            if isinstance(node, ast.Constant) and type(node.value) in (int, float): return node.value
            if isinstance(node, ast.Name) and re.fullmatch(r'v\d+', node.id): return values[int(node.id[1:])]
            if isinstance(node, ast.BinOp) and type(node.op) in self.operations:
                return self.operations[type(node.op)](calculate(node.left), calculate(node.right))
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return calculate(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            raise ValueError('Unsupported template formula')
        return calculate(tree)

_THEME_BASE = {
    0: (255, 255, 255),  # lt1 (white)
    1: (0, 0, 0),        # dk1 (black)
    2: (231, 230, 230),  # lt2 (light gray)
    3: (68, 84, 106),    # dk2 (dark slate)
    4: (68, 114, 196),   # accent1
    5: (237, 125, 49),   # accent2
    6: (165, 165, 165),  # accent3
    7: (255, 192, 0),    # accent4
    8: (91, 155, 213),   # accent5
    9: (112, 173, 71),   # accent6
}


def _colour(colour, fallback):
    if not colour:
        return fallback
    if colour.type == 'rgb' and isinstance(colour.rgb, str):
        return '#' + colour.rgb[-6:]
    if colour.type == 'theme' and isinstance(colour.theme, int) and colour.theme in _THEME_BASE:
        r, g, b = _THEME_BASE[colour.theme]
        tint = float(getattr(colour, 'tint', 0.0) or 0.0)
        if tint < 0:
            factor = 1.0 + tint
            r, g, b = int(round(r * factor)), int(round(g * factor)), int(round(b * factor))
        elif tint > 0:
            r = int(round(r + (255 - r) * tint))
            g = int(round(g + (255 - g) * tint))
            b = int(round(b + (255 - b) * tint))
        return f'#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}'
    return fallback

def _display(value, number_format):
    if value is None: return ''
    if isinstance(value, (date, datetime)): return value.strftime('%d %b %Y')
    if isinstance(value, (int, float)):
        pattern = number_format.split(';')[0]
        precision = len(re.search(r'\.([0#]+)', pattern)[1]) if re.search(r'\.([0#]+)', pattern) else 0
        precision = min(precision, 4)
        if '%' in pattern: return f'{value*100:,.{precision}f}%'
        if precision: return f'{value:,.{precision}f}'
        return f'{value:,.0f}'
    return str(value)

def workbook_preview(path):
    wb = load_workbook(path)
    try:
        evaluator = FormulaValues(wb)
        sheets, errors = [], []
        for name in SHEETS:
            sheet = wb[name]
            covered, spans = set(), {}
            for area in sheet.merged_cells.ranges:
                spans[(area.min_row, area.min_col)] = (area.max_row-area.min_row+1, area.max_col-area.min_col+1)
                covered.update((r,c) for r in range(area.min_row,area.max_row+1) for c in range(area.min_col,area.max_col+1)
                               if (r,c)!=(area.min_row,area.min_col))
            rows = []
            for r in range(1, sheet.max_row+1):
                cells = []
                for c in range(1, sheet.max_column+1):
                    if (r,c) in covered: continue
                    cell = sheet.cell(r,c)
                    try: value = evaluator.cell(name, cell.coordinate)
                    except (ValueError, TypeError, SyntaxError, KeyError, IndexError, ZeroDivisionError):
                        errors.append(f'{name}!{cell.coordinate}'); value = '—'
                    row_span, col_span = spans.get((r,c),(1,1))
                    cells.append({'address':cell.coordinate,'row':r,'column':c,'raw':str(cell.value) if cell.value is not None else '', 'text':_display(value,cell.number_format), 'formula':cell.value if cell.data_type=='f' else '',
                        'rowSpan':row_span,'colSpan':col_span,'style':{
                        'backgroundColor':_colour(cell.fill.fgColor,'#ffffff') if cell.fill.patternType=='solid' else '#ffffff',
                        'color':_colour(cell.font.color,'#20344f'),'fontWeight':700 if cell.font.bold else 400,
                        'fontStyle':'italic' if cell.font.italic else 'normal',
                        'textAlign':cell.alignment.horizontal if cell.alignment.horizontal in ('left','right','center') else ('right' if isinstance(value,(int,float)) else 'left'),
                        'fontSize':min(20,max(10,cell.font.sz or 11))}})
                rows.append({'height':min(60,max(25,sheet.row_dimensions[r].height or 25)),'cells':cells})
            widths = [min(190,max(50,(sheet.column_dimensions[get_column_letter(c)].width or 10)*7)) for c in range(1,sheet.max_column+1)]
            hidden=sorted({c for dimension in sheet.column_dimensions.values() if dimension.hidden for c in range(dimension.min or 1,(dimension.max or dimension.min or 1)+1) if c<=sheet.max_column})
            sheets.append({'name':name,'widths':widths,'rows':rows,'hidden_columns':hidden})
        return {'sheets':sheets,'formula_errors':errors,'revision':report_revision(path)}
    finally:
        wb.close()


def report_revision(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def edit_workbook(source, target, changes, visibility=None):
    """Write an edited copy, keeping all template styles and merged cells."""
    wb=load_workbook(source)
    try:
        if wb.sheetnames!=SHEETS:raise ValueError('Expected the two GP report sheets.')
        for change in changes:
            name,address,value=change['sheet'],change['address'].upper(),change['value']
            if name not in SHEETS or not re.fullmatch(r'[A-Z]{1,2}[1-9][0-9]{0,2}',address):
                raise ValueError('Choose a cell in one of the two report sheets.')
            sheet=wb[name]
            from openpyxl.utils.cell import coordinate_to_tuple
            row,column=coordinate_to_tuple(address)
            if row>sheet.max_row or column>sheet.max_column:
                raise ValueError(f'{name}!{address} is outside the report.')
            cell=sheet[address]
            if isinstance(cell,MergedCell):raise ValueError('Edit the first cell of a merged area.')
            if len(value)>2000:raise ValueError('Cell text must be at most 2,000 characters.')
            if value.startswith('='):
                if len(value)>256:raise ValueError('Formula is too long.')
                cell.value=value
            elif not value:
                cell.value=None
            elif re.fullmatch(r'-?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?',value.strip()):
                number=float(value.strip().replace(',',''))
                if not math.isfinite(number) or abs(number)>1e15:raise ValueError('Number is outside the supported range.')
                cell.value=int(number) if number.is_integer() else number
            else:
                cell.value=value
                cell.data_type='s'
        for setting in visibility or []:
            name=setting['sheet'];hidden=set(setting['hidden_columns'])
            if name not in SHEETS:raise ValueError('Unknown report sheet.')
            sheet=wb[name]
            if any(type(c)!=int or c<1 or c>sheet.max_column for c in hidden):raise ValueError('Column is outside the report.')
            if len(hidden)>=sheet.max_column:raise ValueError('Keep at least one column visible.')
            # Split grouped dimensions so hiding one column does not hide its neighbours.
            from copy import copy
            dimensions={c:copy(sheet.column_dimensions[get_column_letter(c)]) for c in range(1,sheet.max_column+1)}
            for dimension in list(sheet.column_dimensions.values()):
                if dimension.max and dimension.min and dimension.max>dimension.min:
                    for c in range(dimension.min,min(dimension.max,sheet.max_column)+1):dimensions[c]=copy(dimension)
            sheet.column_dimensions.clear()
            for c,dimension in dimensions.items():
                letter=get_column_letter(c);dimension.index=letter;dimension.min=c;dimension.max=c;dimension.hidden=c in hidden
                sheet.column_dimensions[letter]=dimension
        values=FormulaValues(wb)
        for sheet in wb:
            for row in sheet:
                for cell in row:
                    if cell.data_type=='f':
                        try:
                            value=values.cell(sheet.title,cell.coordinate)
                            if not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('Invalid result')
                        except Exception as exc:
                            raise ValueError(f'{sheet.title}!{cell.coordinate}: use valid cell arithmetic, SUM(range), or IFERROR(formula,0).') from exc
        wb.calculation=CalcProperties(calcId=0,fullCalcOnLoad=True,forceFullCalc=True,calcMode='auto')
        wb.save(target)
    finally:wb.close()
