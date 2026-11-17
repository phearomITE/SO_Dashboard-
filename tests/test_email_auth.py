import os
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import email_auth as auth
from app.auth_store import AuthStore,users,sessions
from sqlalchemy import create_engine,select,update
from sqlalchemy.pool import StaticPool
from datetime import datetime,timedelta,timezone

class MemoryRedis:
    """Small Redis double; Lua is additionally verified against real Redis."""
    def __init__(self): self.data={};self.ttls={}
    def set(self,key,value,nx=False,ex=None):
        if nx and key in self.data:return False
        self.data[key]=value;self.ttls[key]=ex;return True
    def setex(self,key,ttl,value):return self.set(key,value,ex=ttl)
    def get(self,key):return self.data.get(key)
    def hset(self,key,mapping):self.data[key]=dict(mapping)
    def expire(self,key,ttl):self.ttls[key]=ttl
    def delete(self,*keys):
        for key in keys:self.data.pop(key,None)
    def eval(self,script,n,key,*args):
        if script==auth.VERIFY_SCRIPT:
            entry=self.data.get(key)
            if not entry or entry['email']!=args[0]:return 0
            entry['attempts']=int(entry['attempts'])+1
            ok=entry['attempts']<=5 and entry['hash']==args[1]
            if ok or entry['attempts']>=5:self.delete(key)
            return int(ok)
        self.data[key]=int(self.data.get(key,0))+1
        if self.data[key]==1:self.ttls[key]=int(args[0])
        return self.data[key]

class EmailAuthTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'DASHBOARD_ADMIN_TOKEN':'x'*48,'SMTP_HOST':'smtp.test.invalid','SMTP_FROM':'sender@example.com','DASHBOARD_ADMIN_EMAILS':'admin@example.com','DASHBOARD_COOKIE_SECURE':'true'})
        self.env.start();self.addCleanup(self.env.stop)
        self.redis=MemoryRedis();app=FastAPI();app.state.redis=self.redis
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool);self.addCleanup(self.engine.dispose)
        app.state.auth_store=AuthStore(self.engine);app.state.auth_store.initialize()
        app.add_middleware(auth.DashboardAuthMiddleware);app.include_router(auth.router)
        @app.get('/api/v1/dashboard/snapshot')
        def read():return {'data':True}
        @app.post('/api/v1/admin/roster')
        def write():return {'changed':True}
        self.app=app;self.client=TestClient(app,base_url='https://dashboard.test')
        self.sender=patch.object(auth,'send_code');self.send=self.sender.start();self.addCleanup(self.sender.stop)
    def request(self,email='person@yahoo.com'):
        response=self.client.post('/api/v1/auth/email/request',json={'email':email})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['challenge_id'],self.send.call_args.args[1]
    def verify(self,challenge,code,email='person@yahoo.com'):
        return self.client.post('/api/v1/auth/email/verify',json={'email':email,'challenge_id':challenge,'code':code})
    def login(self,email='person@yahoo.com'):
        challenge,code=self.request(email);response=self.verify(challenge,code,email)
        self.assertEqual(response.status_code,200,response.text)
        return response
    def test_any_provider_normalization_validation(self):
        for email in ['a@gmail.com','a@outlook.com','a@yahoo.co.uk','employee@company.com.kh','name+tag@example.com']:
            self.assertEqual(auth.email_address(email),email)
        self.assertEqual(auth.email_address('  User@Example.COM  '),'user@example.com')
        for email in ['bad','a@@x.com','a@localhost','a..b@example.com','a@x.com\nBcc:x@y.com']:
            with self.assertRaises(Exception):auth.email_address(email)
    def test_single_use_hashed_code_and_session_logout(self):
        challenge,code=self.request()
        stored=self.redis.data[auth.PREFIX+'code:'+challenge]
        self.assertNotIn(code,str(stored));self.assertEqual(self.redis.ttls[auth.PREFIX+'code:'+challenge],600)
        response=self.verify(challenge,code);self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['role'],'viewer')
        cookie=response.headers['set-cookie'].lower()
        for flag in ['httponly','secure','samesite=strict']:self.assertIn(flag,cookie)
        self.assertEqual(self.verify(challenge,code).status_code,401)
        token=response.json()['access_token'];headers={'Authorization':'Bearer '+token}
        self.assertEqual(self.client.get('/api/v1/auth/me',headers=headers).json()['email'],'person@yahoo.com')
        self.assertEqual(self.client.post('/api/v1/auth/logout',headers=headers).status_code,200)
        self.assertEqual(self.client.get('/api/v1/auth/me',headers=headers).status_code,401)
    def test_attempt_limit_cooldown_and_expiration(self):
        challenge,code=self.request()
        self.assertEqual(self.client.post('/api/v1/auth/email/request',json={'email':'person@yahoo.com'}).status_code,429)
        wrong='000000' if code!='000000' else '000001'
        for _ in range(5):self.assertEqual(self.verify(challenge,wrong).status_code,401)
        self.assertEqual(self.verify(challenge,code).status_code,401)
        self.assertEqual(self.verify('a'*48,code).status_code,401)
    def test_access_roles_and_cookie_only_csrf(self):
        self.assertEqual(self.client.get('/api/v1/dashboard/snapshot').status_code,401)
        viewer=self.login().json()['access_token']
        self.assertEqual(self.client.get('/api/v1/dashboard/snapshot').status_code,200)
        self.assertEqual(self.client.post('/api/v1/admin/roster',headers={'Authorization':'Bearer '+viewer}).status_code,403)
        admin=self.login('admin@example.com').json()['access_token']
        self.assertEqual(self.client.post('/api/v1/admin/roster').status_code,403)
        self.assertEqual(self.client.post('/api/v1/admin/roster',headers={'Authorization':'Bearer '+admin}).status_code,200)
        with patch.dict(os.environ,{'DASHBOARD_ADMIN_EMAILS':''}):
            self.assertEqual(self.client.post('/api/v1/admin/roster',headers={'Authorization':'Bearer '+admin}).status_code,403)
    def test_sender_and_redis_failure_do_not_grant_access(self):
        self.send.side_effect=OSError('private sender secret')
        response=self.client.post('/api/v1/auth/email/request',json={'email':'person@yahoo.com'})
        self.assertEqual(response.status_code,503);self.assertNotIn('secret',response.text)
        self.assertFalse(any(':code:' in key for key in self.redis.data))
        self.app.state.redis=None
        self.assertEqual(self.client.get('/api/v1/auth/me',headers={'Authorization':'Bearer invalid'}).status_code,503)
    def test_placeholder_credentials_fail_before_delivery(self):
        with patch.dict(os.environ,{'SMTP_FROM':'your-sender@gmail.com','SMTP_PASSWORD':'your-google-app-password'}):
            response=self.client.post('/api/v1/auth/email/request',json={'email':'person@yahoo.com'})
            self.assertEqual(response.status_code,503)
            self.assertIn('example credentials',response.json()['detail'])
            self.send.assert_not_called()
    def test_rejected_password_has_actionable_error(self):
        import smtplib
        self.send.side_effect=smtplib.SMTPAuthenticationError(535,b'private-provider-details')
        response=self.client.post('/api/v1/auth/email/request',json={'email':'person@yahoo.com'})
        self.assertEqual(response.status_code,503)
        self.assertIn('App Password',response.json()['detail'])
        self.assertNotIn('private-provider-details',response.text)
        self.assertFalse(any(':code:' in key for key in self.redis.data))
    def test_gmail_password_display_spaces_removed(self):
        from unittest.mock import MagicMock
        smtp=MagicMock()
        with patch.dict(os.environ,{'SMTP_HOST':'smtp.gmail.com','SMTP_USERNAME':'sender@gmail.com','SMTP_FROM':'sender@gmail.com','SMTP_PASSWORD':'abcd efgh ijkl mnop'}), patch.object(auth.smtplib,'SMTP') as factory:
            factory.return_value.__enter__.return_value=smtp
            self.sender.stop()
            try:auth.send_code('recipient@example.com','123456')
            finally:self.sender.start()
            smtp.login.assert_called_once_with('sender@gmail.com','abcdefghijklmnop')
    def test_browser_reopen_restores_without_email_or_access_token(self):
        response=self.login();device=self.client.cookies.get(auth.DEVICE_COOKIE)
        self.assertTrue(device)
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(select(users.c.email)).scalar(),'person@yahoo.com')
            saved=conn.execute(select(sessions.c.token_hash)).scalar()
            self.assertEqual(saved,auth.digest(device));self.assertNotEqual(saved,device)
        new_client=TestClient(self.app,base_url='https://dashboard.test');new_client.cookies.set(auth.DEVICE_COOKIE,device,path='/api/v1/auth')
        self.redis.data.clear()
        result=new_client.post('/api/v1/auth/restore',json={})
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(self.send.call_count,1)
        self.assertEqual(new_client.get('/api/v1/auth/me').status_code,200)
        self.assertEqual(new_client.post('/api/v1/auth/logout',headers={'Authorization':'Bearer '+result.json()['access_token']}).status_code,200)
        self.assertEqual(self.client.get('/api/v1/auth/me',headers={'Authorization':'Bearer '+response.json()['access_token']}).status_code,401)
        self.assertEqual(new_client.post('/api/v1/auth/restore').status_code,401)
    def test_expired_device_and_unverified_email_cannot_restore(self):
        self.login()
        with self.engine.begin() as conn:conn.execute(update(sessions).values(expires_at=datetime.now(timezone.utc)-timedelta(days=1)))
        self.assertEqual(self.client.post('/api/v1/auth/restore').status_code,401)
        self.assertEqual(self.client.post('/api/v1/auth/restore',json={'email':'admin@example.com'}).status_code,401)
    def test_restore_rejects_foreign_origin_and_accepts_https_proxy(self):
        self.login()
        self.assertEqual(self.client.post('/api/v1/auth/restore',headers={'Origin':'https://evil.test'}).status_code,403)
        self.assertEqual(self.client.post('/api/v1/auth/restore',headers={'Origin':'https://dashboard.test'}).status_code,200)
    def test_valid_legacy_session_is_upgraded(self):
        self.redis.setex(auth.PREFIX+'session:'+auth.digest('legacy-token'),28800,'{"email":"person@yahoo.com"}')
        response=self.client.get('/api/v1/auth/me',headers={'Authorization':'Bearer legacy-token'})
        self.assertEqual(response.status_code,200,response.text);self.assertIn(auth.DEVICE_COOKIE,response.headers['set-cookie'])
        self.assertEqual(self.client.post('/api/v1/auth/restore').status_code,200)
    def test_email_bound_to_challenge(self):
        challenge,code=self.request()
        self.assertEqual(self.verify(challenge,code,'someone@gmail.com').status_code,401)
        self.assertEqual(self.verify(challenge,code).status_code,200)

if __name__=='__main__':unittest.main()
