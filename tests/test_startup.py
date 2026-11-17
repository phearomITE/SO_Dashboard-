import warnings
from unittest.mock import Mock
from sqlalchemy.exc import OperationalError
from sqlalchemy import create_engine,inspect
from app import db,check
from app.diagnostics import database_message,check_ocr
from app.imports import readonly_excel_warnings

def refused():return OperationalError('connect',{},Exception('connection refused (10061)'))

def test_database_error_is_actionable_and_hides_password():
    msg=database_message(refused(),'postgresql+psycopg2://postgres:secret-password@127.0.0.1:5435/SA_Alert')
    assert '5435' in msg and 'START_DATABASE_SO.bat' in msg and 'secret-password' not in msg

def test_check_refused_returns_failure_without_traceback(monkeypatch,capsys):
    monkeypatch.setattr(db,'probe_connection',Mock(side_effect=refused()))
    monkeypatch.setattr(check,'check_ocr',lambda:(True,'ready'))
    assert check.main(['--db'])==1
    out=capsys.readouterr()
    assert 'NOT READY:' in out.out and 'Traceback' not in out.out+out.err

def test_check_missing_ocr_blocks_native_start(monkeypatch,capsys):
    monkeypatch.setattr(check,'check_ocr',lambda:(False,'Tesseract missing'))
    assert check.main(['--require-ocr'])==1
    assert 'Photo timestamp OCR is not ready' in capsys.readouterr().out

def test_probe_is_read_only(tmp_path,monkeypatch):
    url='sqlite:///'+str(tmp_path/'probe.db')
    monkeypatch.setenv('DATABASE_URL',url)
    assert db.probe_connection()
    engine=create_engine(url)
    assert inspect(engine).get_table_names()==[]
    engine.dispose()

def test_authentication_diagnostic():
    e=OperationalError('connect',{},Exception('password authentication failed'))
    assert 'DB_PASSWORD' in database_message(e)
    e=OperationalError('connect',{},Exception('database example does not exist'))
    assert 'DB_NAME' in database_message(e)

def test_english_language_data_required(monkeypatch):
    import app.diagnostics as d
    monkeypatch.setattr(d.subprocess,'run',lambda *a,**k:Mock(stdout='List of available languages:\nkhm\n',stderr=''))
    ok,message=check_ocr()
    assert not ok and 'eng' in message

def test_unrelated_warnings_stay_visible():
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        with readonly_excel_warnings():warnings.warn('Unrelated data issue',UserWarning)
    assert any('Unrelated data issue' in str(w.message) for w in captured)
