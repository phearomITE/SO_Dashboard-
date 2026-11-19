import importlib.util
import os
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db, email_auth as auth
from app.auth_store import AuthStore, PgAuthCache


def test_normalize_database_url():
    assert db._normalize_database_url('postgres://user:pass@host:5432/db') == 'postgresql+psycopg2://user:pass@host:5432/db'
    assert db._normalize_database_url('postgresql://user:pass@host:5432/db?sslmode=require') == 'postgresql+psycopg2://user:pass@host:5432/db?sslmode=require'


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


def test_pg_auth_cache_email_flow_without_redis():
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
