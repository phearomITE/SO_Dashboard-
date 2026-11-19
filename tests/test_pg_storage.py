import importlib.util
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db, email_auth as auth
from app.auth_store import AuthStore, PgAuthCache
from app.diagnostics import database_message


def test_normalize_database_url():
    assert db._normalize_database_url('postgres://user:pass@localhost:5432/db') == 'postgresql+psycopg2://user:pass@localhost:5432/db'
    assert db._normalize_database_url('postgresql://user:pass@127.0.0.1:5432/db') == 'postgresql+psycopg2://user:pass@127.0.0.1:5432/db'
    assert db._normalize_database_url(
        'postgresql://postgres.ref:SoAlert2026@Pass!@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres'
    ) == 'postgresql+psycopg2://postgres.ref:SoAlert2026%40Pass%21@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres?sslmode=require'
    assert db._normalize_database_url(
        'postgresql://postgres.ref:Sodashbaord2026%2005!@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres'
    ) == 'postgresql+psycopg2://postgres.ref:Sodashbaord2026%252005%21@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres?sslmode=require'
    assert db._normalize_database_url(
        'postgresql://postgres.ref:SoAlert2026%40Pass%21@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres'
    ) == 'postgresql+psycopg2://postgres.ref:SoAlert2026%40Pass%21@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres?sslmode=require'
    assert db._normalize_database_url(
        'postgresql://postgres.ref:secret@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres?sslmode=verify-full'
    ) == 'postgresql+psycopg2://postgres.ref:secret@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres?sslmode=verify-full'


def test_new_engine_database_password_and_cloud_pool(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://postgres.ref:placeholder@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres')
    monkeypatch.setenv('DATABASE_PASSWORD', 'Raw%20@Pass!')
    monkeypatch.delenv('DB_POOL_SIZE', raising=False)
    monkeypatch.delenv('DB_MAX_OVERFLOW', raising=False)
    eng = db.new_engine()
    try:
        assert eng.url.password == 'Raw%20@Pass!'
        assert eng.pool.size() == 3
        assert eng.pool._max_overflow == 3
    finally:
        eng.dispose()


def test_database_message_includes_username_not_password():
    err = OperationalError('connect', {}, Exception('FATAL: password authentication failed for user "postgres"'))
    msg = database_message(err, 'postgresql://postgres.ref:super-secret@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres')
    assert 'postgres.ref' in msg
    assert 'super-secret' not in msg


def test_report_image_roundtrip():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    db.ReportImage.__table__.create(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    try:
        payload = b'\xff\xd8\xff\xe0test-jpeg-bytes'
        with Session() as s:
            db.save_report_image(s, '-100123_456.jpg', -100123, 456, payload)
            s.commit()
        loaded = db.load_report_image(engine, '-100123_456.jpg')
        assert loaded == (payload, 'image/jpeg')

        if importlib.util.find_spec('shapely') is not None:
            from app.dashboard import report_image
            with patch.object(db, 'engine', engine):
                resp = report_image('-100123_456.jpg')
            assert resp.status_code == 200
            assert resp.body == payload
            assert resp.media_type == 'image/jpeg'
    finally:
        engine.dispose()


def test_pg_auth_cache_email_flow_without_redis(monkeypatch):
    for k in ('BREVO_API_KEY', 'RESEND_API_KEY', 'EMAIL_WEBHOOK_URL'):
        monkeypatch.delenv(k, raising=False)
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    store = AuthStore(engine)
    store.initialize()
    kv = PgAuthCache(engine)
    app_state = SimpleNamespace(redis=None, auth_kv=kv, auth_store=store)
    app_obj = SimpleNamespace(state=app_state)

    def make_req(headers=None, cookies=None):
        return SimpleNamespace(
            app=app_obj,
            client=SimpleNamespace(host='127.0.0.1'),
            headers=headers or {},
            cookies=cookies or {},
        )

    env = {
        'DASHBOARD_ADMIN_TOKEN': 'y' * 48,
        'SMTP_HOST': 'smtp.test.invalid',
        'SMTP_FROM': 'sender@example.com',
        'DASHBOARD_ADMIN_EMAILS': 'admin@example.com',
        'DASHBOARD_COOKIE_SECURE': 'true',
    }
    try:
        with patch.dict(os.environ, env), patch.object(auth, 'send_code') as mock_send:
            resp = Response()
            req_res = auth.request_code(auth.EmailBody(email='admin@example.com'), make_req(), resp)
            challenge_id = req_res['challenge_id']
            code = mock_send.call_args.args[1]

            wrong = '000000' if code != '000000' else '000001'
            try:
                auth.verify_code(auth.VerifyBody(email='admin@example.com', challenge_id=challenge_id, code=wrong), make_req(), Response())
                raise AssertionError('Expected 401 for wrong code')
            except HTTPException as exc:
                assert exc.status_code == 401

            verify_resp = Response()
            verified = auth.verify_code(auth.VerifyBody(email='admin@example.com', challenge_id=challenge_id, code=code), make_req(), verify_resp)
            token = verified['access_token']
            assert verified['role'] == 'admin'

            user = auth.identity(make_req(headers={'authorization': 'Bearer ' + token}))
            assert user['email'] == 'admin@example.com'
            assert user['role'] == 'admin'

            auth.logout(make_req(headers={'authorization': 'Bearer ' + token, 'host': 'dashboard.test', 'origin': 'https://dashboard.test'}), Response())
            try:
                auth.identity(make_req(headers={'authorization': 'Bearer ' + token}))
                raise AssertionError('Expected 401 after logout')
            except HTTPException as exc:
                assert exc.status_code == 401
    finally:
        engine.dispose()


class _FakeHttpResp:
    def __init__(self, body='{}'):
        self._body = body.encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _n=-1):
        return self._body


def test_https_email_brevo_provider(monkeypatch):
    for k in ('BREVO_API_KEY', 'RESEND_API_KEY', 'EMAIL_WEBHOOK_URL', 'EMAIL_FROM', 'SMTP_HOST', 'SMTP_FROM'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('BREVO_API_KEY', 'xkeysib-secret')
    monkeypatch.setenv('SMTP_FROM', 'sender@example.com')
    assert auth.sender_configuration_error() is None

    captured = {}

    def fake_urlopen(req, timeout=0):
        captured['url'] = req.full_url
        captured['headers'] = {k.lower(): v for k, v in req.header_items()}
        captured['body'] = json.loads(req.data.decode('utf-8'))
        return _FakeHttpResp('{"messageId":"1"}')

    with patch.object(auth.urllib.request, 'urlopen', side_effect=fake_urlopen):
        auth.send_code('user@example.com', '123456')

    assert captured['url'] == 'https://api.brevo.com/v3/smtp/email'
    assert captured['headers']['api-key'] == 'xkeysib-secret'
    assert captured['body']['sender']['email'] == 'sender@example.com'
    assert captured['body']['to'] == [{'email': 'user@example.com'}]
    assert '123456' in captured['body']['textContent']


def test_https_email_resend_provider(monkeypatch):
    for k in ('BREVO_API_KEY', 'RESEND_API_KEY', 'EMAIL_WEBHOOK_URL', 'EMAIL_FROM', 'SMTP_HOST', 'SMTP_FROM'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('RESEND_API_KEY', 're_secret_123')
    assert auth.sender_configuration_error() is None

    captured = {}

    def fake_urlopen(req, timeout=0):
        captured['url'] = req.full_url
        captured['headers'] = {k.lower(): v for k, v in req.header_items()}
        captured['body'] = json.loads(req.data.decode('utf-8'))
        return _FakeHttpResp('{"id":"abc"}')

    with patch.object(auth.urllib.request, 'urlopen', side_effect=fake_urlopen):
        auth.send_code('user@example.com', '654321')

    assert captured['url'] == 'https://api.resend.com/emails'
    assert captured['headers']['authorization'] == 'Bearer re_secret_123'
    assert captured['body']['from'] == 'onboarding@resend.dev'
    assert captured['body']['to'] == ['user@example.com']
    assert '654321' in captured['body']['text']


def test_https_email_webhook_provider(monkeypatch):
    for k in ('BREVO_API_KEY', 'RESEND_API_KEY', 'EMAIL_WEBHOOK_URL', 'EMAIL_WEBHOOK_SECRET', 'SMTP_HOST', 'SMTP_FROM'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('EMAIL_WEBHOOK_URL', 'http://insecure.example.com/exec')
    assert 'https://' in (auth.sender_configuration_error() or '')

    monkeypatch.setenv('EMAIL_WEBHOOK_URL', 'https://script.google.com/macros/s/abc/exec')
    monkeypatch.setenv('EMAIL_WEBHOOK_SECRET', 'relay-secret')
    assert auth.sender_configuration_error() is None

    captured = {}

    def fake_ok(req, timeout=0):
        captured['url'] = req.full_url
        captured['body'] = json.loads(req.data.decode('utf-8'))
        return _FakeHttpResp('{"ok":true}')

    with patch.object(auth.urllib.request, 'urlopen', side_effect=fake_ok):
        auth.send_code('user@example.com', '777888')

    assert captured['url'] == 'https://script.google.com/macros/s/abc/exec'
    assert captured['body']['to'] == 'user@example.com'
    assert captured['body']['secret'] == 'relay-secret'
    assert '777888' in captured['body']['text']

    with patch.object(auth.urllib.request, 'urlopen', return_value=_FakeHttpResp('{"ok":false}')):
        try:
            auth.send_code('user@example.com', '777888')
            raise AssertionError('Expected 503 when webhook returns ok=false')
        except HTTPException as exc:
            assert exc.status_code == 503


def test_render_smtp_blocked_message(monkeypatch):
    for k in ('BREVO_API_KEY', 'RESEND_API_KEY', 'EMAIL_WEBHOOK_URL'):
        monkeypatch.delenv(k, raising=False)
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    store = AuthStore(engine)
    store.initialize()
    kv = PgAuthCache(engine)
    app_obj = SimpleNamespace(state=SimpleNamespace(redis=None, auth_kv=kv, auth_store=store))
    req = SimpleNamespace(app=app_obj, client=SimpleNamespace(host='127.0.0.1'), headers={}, cookies={})
    env = {
        'RENDER': 'true',
        'DASHBOARD_ADMIN_TOKEN': 'z' * 48,
        'SMTP_HOST': 'smtp.gmail.com',
        'SMTP_FROM': 'sender@gmail.com',
        'SMTP_USERNAME': 'sender@gmail.com',
        'SMTP_PASSWORD': 'app-password',
    }
    try:
        with patch.dict(os.environ, env), patch.object(auth, 'send_code', side_effect=OSError('Network unreachable')):
            try:
                auth.request_code(auth.EmailBody(email='user@example.com'), req, Response())
                raise AssertionError('Expected 503 on Render SMTP failure')
            except HTTPException as exc:
                assert exc.status_code == 503
                assert 'Render Free blocks outbound SMTP ports' in exc.detail
                assert 'EMAIL_WEBHOOK_URL' in exc.detail
    finally:
        engine.dispose()


def test_current_admin_works_without_signin(monkeypatch):
    from pathlib import Path
    admin_src = (Path(__file__).resolve().parents[1] / 'app' / 'admin_api.py').read_text(encoding='utf-8')
    fn_block = admin_src.split('def current_admin(request: Request) -> str:', 1)[1].split('\n\ndef ', 1)[0]
    assert 'identity(' not in fn_block
    assert 'DASHBOARD_ADMIN_NAME' in fn_block
    if importlib.util.find_spec('python_multipart') is not None:
        from app import admin_api
        monkeypatch.setenv('DASHBOARD_ADMIN_NAME', 'SO Operations Admin')
        req = SimpleNamespace(state=SimpleNamespace())
        assert admin_api.current_admin(req) == 'SO Operations Admin'
