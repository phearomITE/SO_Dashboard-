from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from pathlib import Path
import posixpath
import sqlite3
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

from openpyxl import load_workbook
from .excel_links import set_clickable_link

MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
DOC_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def _without_hyperlink_records(path: Path) -> tuple[BytesIO, dict[str, dict[str, str]]]:
    """Read raw XLSX XML so broken hyperlink relationships need not be loaded."""
    stream = BytesIO()
    links: dict[str, dict[str, str]] = {}
    with ZipFile(path) as source, ZipFile(stream, 'w', ZIP_DEFLATED) as clean:
        changes = {}
        for name in source.namelist():
            if not name.startswith('xl/worksheets/') or not name.endswith('.xml') or '/_rels/' in name:
                continue
            xml = ET.fromstring(source.read(name))
            rel_name = posixpath.dirname(name) + '/_rels/' + posixpath.basename(name) + '.rels'
            relationships = ET.fromstring(source.read(rel_name)) if rel_name in source.namelist() else None
            targets = {}
            if relationships is not None:
                for relationship in list(relationships):
                    if relationship.get('Type', '').endswith('/hyperlink'):
                        targets[relationship.get('Id')] = relationship.get('Target', '')
                        relationships.remove(relationship)
                changes[rel_name] = ET.tostring(relationships, encoding='utf-8', xml_declaration=True)
            sheet_links = {}
            for container in list(xml.findall(f'{{{MAIN}}}hyperlinks')):
                for item in container:
                    target = targets.get(item.get(f'{{{DOC_REL}}}id'), '')
                    if item.get('ref') and target:
                        sheet_links[item.get('ref')] = target
                xml.remove(container)
            links[name] = sheet_links
            changes[name] = ET.tostring(xml, encoding='utf-8', xml_declaration=True)
        for item in source.infolist():
            clean.writestr(item, changes.get(item.filename, source.read(item.filename)))
    stream.seek(0)
    return stream, links


def _sheet_paths(path: Path) -> dict[str, str]:
    with ZipFile(path) as source:
        workbook = ET.fromstring(source.read('xl/workbook.xml'))
        relationships = ET.fromstring(source.read('xl/_rels/workbook.xml.rels'))
        targets = {item.get('Id'): item.get('Target') for item in relationships}
        result = {}
        for sheet in workbook.findall(f'{{{MAIN}}}sheets/{{{MAIN}}}sheet'):
            target = targets.get(sheet.get(f'{{{DOC_REL}}}id'), '')
            result[sheet.get('name')] = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
        return result


def _photo_url(group_id, message_id) -> str:
    group = str(int(group_id))
    if not group.startswith('-100') or not group[4:] or int(message_id) <= 0:
        return ''
    return f'https://t.me/c/{group[4:]}/{int(message_id)}'


def _photo_cache(database: Path | None) -> tuple[dict, list]:
    if database is None:
        return {}, []
    if not database.is_file():
        raise FileNotFoundError(f'Database does not exist: {database}')
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as con:
        con.row_factory = sqlite3.Row
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='product_display_media'").fetchone():
            return {}, []
        cache = {(int(row['group_id']), int(row['form_message_id'])): _photo_url(row['group_id'], row['image_message_id'])
                 for row in con.execute('SELECT * FROM product_display_media')}
        visits = [dict(row) for row in con.execute('SELECT group_id,message_id,submitted_at,dealer,sender_id,latitude,longitude FROM outlet_visits')]
    return cache, visits


def _date_key(value) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime('%Y-%m-%d')
    return str(value or '')[:10]


def repair_workbook(source: Path, output: Path, database: Path | None = None) -> dict[str, int]:
    """Create a repaired copy; preserve the original workbook and database."""
    if source.resolve() == output.resolve():
        raise ValueError('Choose a different output filename to preserve the original')
    paths = _sheet_paths(source)
    stream, old_links = _without_hyperlink_records(source)
    wb = load_workbook(stream)
    restored = 0
    for sheet in wb:
        for address, target in old_links.get(paths.get(sheet.title), {}).items():
            if ':' in address:  # Excel link ranges are unsupported; do not invent a destination cell.
                continue
            if urlsplit(target).scheme not in {'http', 'https'}:
                continue
            cell = sheet[address]
            label = str(cell.value or 'Open Link')
            try:
                set_clickable_link(cell, target, label)
            except ValueError:
                continue
            restored += 1
    cache, visits = _photo_cache(database)
    recovered = unresolved = 0
    # Index strong Pin Detail identity once. Multiple differing photo URLs are
    # ambiguous and remain blank rather than attaching another outlet's image.
    visit_index = {}
    for visit in visits:
        key = (_date_key(visit['submitted_at']), str(visit['dealer']), str(visit['sender_id'] or ''))
        visit_index.setdefault(key, []).append(visit)
    for title in ('Detail', 'Pin Detail'):
        if title not in wb:
            continue
        sheet = wb[title]
        columns = {str(cell.value or '').strip(): cell.column for cell in sheet[1]}
        if 'Image' not in columns:
            continue
        for row in range(2, sheet.max_row + 1):
            cell = sheet.cell(row, columns['Image'])
            if cell.data_type == 'f' and str(cell.value).startswith('=HYPERLINK('):
                continue
            url = ''
            if title == 'Detail' and {'Telegram Group ID', 'Message ID'}.issubset(columns):
                try:
                    key = (int(sheet.cell(row, columns['Telegram Group ID']).value),
                           int(sheet.cell(row, columns['Message ID']).value))
                    url = cache.get(key, '')
                except (ValueError, TypeError):
                    continue  # Header/total/blank rows are not source reports.
            elif title == 'Pin Detail' and {'Date','Dealer','Telegram ID','Lat','Lon'}.issubset(columns):
                value = lambda name: sheet.cell(row, columns[name]).value
                key = (_date_key(value('Date')), str(value('Dealer') or ''), str(value('Telegram ID') or ''))
                matches = set()
                for visit in visit_index.get(key, []):
                    try:
                        if abs(float(visit['latitude']) - float(value('Lat'))) > 1e-7 or abs(float(visit['longitude']) - float(value('Lon'))) > 1e-7:
                            continue
                    except (TypeError, ValueError):
                        continue
                    target = cache.get((int(visit['group_id']),int(visit['message_id'])), '')
                    if target:
                        matches.add(target)
                if len(matches) == 1:
                    url = matches.pop()
            if url:
                set_clickable_link(cell, url, 'Open Image')
                recovered += 1
            elif cell.value:
                cell.value = None
                unresolved += 1
    wb.calculation.calcMode = 'auto'
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
    output.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output)
    wb.close()
    with ZipFile(output) as archive:
        for name in archive.namelist():
            if name.startswith('xl/worksheets/') and name.endswith('.xml') and '/_rels/' not in name:
                xml = ET.fromstring(archive.read(name))
                if xml.find(f'{{{MAIN}}}hyperlinks') is not None:
                    raise RuntimeError('Repair left a worksheet hyperlink record')
    return {'links_converted':restored, 'images_recovered_from_database':recovered, 'unmatched_images_cleared':unresolved}
