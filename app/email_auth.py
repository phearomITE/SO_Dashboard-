"""Email ownership verification. Redis holds only keyed code digests and sessions."""
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from datetime import datetime,timedelta,timezone
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
COOKIE = "so_dashboard_session"
DEVICE_COOKIE = "so_dashboard_device"
REMEMBER_SECONDS = 90 * 24 * 3600
PREFIX = "so-auth:v1:"
SESSION_SECONDS = 8 * 3600
CODE_SECONDS = 600

def secret():
    key = os.getenv("DASHBOARD_ADMIN_TOKEN", "").strip()
    if len(key) < 32:
        raise HTTPException(503, "Dashboard authentication is not configured.")
    return key

def digest(value):
    return hmac.new(secret().encode(), value.encode(), hashlib.sha256).hexdigest()

def email_address(value):
    value = value.strip()
    if len(value) > 254 or any(c.isspace() or ord(c) < 32 for c in value) or value.count("@") != 1:
        raise HTTPException(422, "Enter a valid email address.")
    local, domain = value.rsplit("@", 1)
    try:
        domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise HTTPException(422, "Enter a valid email address.")
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}", local) or local.startswith('.') or local.endswith('.') or '..' in local:
        raise HTTPException(422, "Enter a valid email address.")
    if '.' not in domain or len(domain) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in domain.split('.')):
        raise HTTPException(422, "Enter a valid email address.")
    return local.lower() + '@' + domain

def role_for(email):
    admins = {s.strip().casefold() for s in os.getenv("DASHBOARD_ADMIN_EMAILS", "").split(',') if s.strip()}
    return "admin" if email.casefold() in admins else "viewer"

def client_for(request):
    client = getattr(request.app.state, 'redis', None) or getattr(request.app.state, 'auth_kv', None)
    if client is None:
        raise HTTPException(503, "Sign-in is temporarily unavailable. Please try again later.")
    return client

def _https_provider():
    if os.getenv('BREVO_API_KEY', '').strip():
        return 'brevo'
    if os.getenv('RESEND_API_KEY', '').strip():
        return 'resend'
    if os.getenv('EMAIL_WEBHOOK_URL', '').strip():
        return 'webhook'
    return None

def sender_configuration_error():
    """Return actionable setup guidance without exposing credentials."""
    provider = _https_provider()
    if provider == 'brevo':
        sender = (os.getenv('EMAIL_FROM') or os.getenv('SMTP_FROM') or '').strip()
        if not sender or 'your-sender' in sender.lower():
            return "Brevo email delivery needs a verified sender address in EMAIL_FROM or SMTP_FROM."
        return None
    if provider == 'resend':
        return None
    if provider == 'webhook':
        url = os.getenv('EMAIL_WEBHOOK_URL', '').strip()
        if not url.startswith('https://'):
            return "EMAIL_WEBHOOK_URL must start with https://."
        return None
    values = {key:os.getenv(key, '').strip() for key in ('SMTP_HOST','SMTP_FROM','SMTP_USERNAME','SMTP_PASSWORD')}
    if any(marker in value.lower() for value in values.values() for marker in ('your-sender','your-google-app-password','your-app-password','your-smtp-password')):
        return "Email delivery uses example credentials. The dashboard owner must replace them with a real sender email and SMTP App Password in .env, then restart the dashboard."
    if not values['SMTP_HOST'] or not values['SMTP_FROM']:
        return "Email delivery is not configured. The dashboard owner must set SMTP_HOST and a real SMTP_FROM sender address in .env, then restart the dashboard."
    if values['SMTP_USERNAME'] and not values['SMTP_PASSWORD']:
        return "The email sender password is missing. The dashboard owner must set SMTP_PASSWORD in .env and restart the dashboard."
    if values['SMTP_HOST'].lower() in ('smtp.gmail.com','smtp.googlemail.com') and not (values['SMTP_USERNAME'] and values['SMTP_PASSWORD']):
        return "Gmail email delivery needs the sender's Gmail address and Google App Password. Set SMTP_USERNAME and SMTP_PASSWORD in .env, then restart the dashboard."
    if os.getenv('SMTP_SECURITY','starttls').lower() not in ('starttls','ssl'):
        return "The email sender must use SMTP_SECURITY=starttls or ssl."
    try:
        port=int(os.getenv('SMTP_PORT','465' if os.getenv('SMTP_SECURITY')=='ssl' else '587'))
        if not 1 <= port <= 65535:raise ValueError()
    except ValueError:
        return "The email sender SMTP_PORT must be a valid port number."
    return None

def configured():
    return sender_configuration_error() is None

def require_sender_configuration():
    problem=sender_configuration_error()
    if problem:raise HTTPException(503,problem)

def _send_code_https(provider, email, subject, body_text):
    if provider == 'brevo':
        url = 'https://api.brevo.com/v3/smtp/email'
        sender = (os.getenv('EMAIL_FROM') or os.getenv('SMTP_FROM') or '').strip()
        headers = {
            'api-key': os.environ['BREVO_API_KEY'].strip(),
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'SO-Alert-Dashboard/1.0',
        }
        payload = {
            'sender': {'name': 'SO Alert Dashboard', 'email': sender},
            'to': [{'email': email}],
            'subject': subject,
            'textContent': body_text,
        }
    elif provider == 'resend':
        url = 'https://api.resend.com/emails'
        sender = (os.getenv('EMAIL_FROM') or os.getenv('SMTP_FROM') or 'onboarding@resend.dev').strip()
        headers = {
            'Authorization': 'Bearer ' + os.environ['RESEND_API_KEY'].strip(),
            'Content-Type': 'application/json',
            'User-Agent': 'SO-Alert-Dashboard/1.0',
        }
        payload = {
            'from': sender,
            'to': [email],
            'subject': subject,
            'text': body_text,
        }
    else:
        url = os.environ['EMAIL_WEBHOOK_URL'].strip()
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'SO-Alert-Dashboard/1.0',
        }
        payload = {
            'to': email,
            'subject': subject,
            'text': body_text,
            'secret': os.getenv('EMAIL_WEBHOOK_SECRET', '').strip(),
        }

    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read(16384).decode('utf-8', errors='replace')
            if provider == 'webhook' and raw.strip():
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict) and parsed.get('ok') is False:
                    raise HTTPException(503, "The HTTPS email relay rejected the request. Check EMAIL_WEBHOOK_SECRET and script permissions.")
    except HTTPException:
        raise
    except urllib.error.HTTPError as exc:
        raise HTTPException(503, f"HTTPS email provider returned HTTP {exc.code}. Check your API key, sender email, or webhook configuration.") from None
    except (urllib.error.URLError, OSError):
        raise HTTPException(503, "The dashboard could not reach the HTTPS email provider. Check network access and provider URL.") from None

def send_code(email, code):
    require_sender_configuration()
    subject = 'Your SO Alert Dashboard sign-in code'
    body_text = f"Your SO Alert Dashboard verification code is: {code}\n\nIt expires in 10 minutes and can be used once. Do not share this code.\nIf you did not request this, you can ignore this email."
    provider = _https_provider()
    if provider is not None:
        _send_code_https(provider, email, subject, body_text)
        return
    mode = os.getenv('SMTP_SECURITY', 'starttls').lower()
    if mode not in ('starttls', 'ssl'):
        raise HTTPException(503, "Email sender must use TLS.")
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = os.environ['SMTP_FROM']
    msg['To'] = email
    msg.set_content(body_text)
    context = ssl.create_default_context()
    host = os.environ['SMTP_HOST'].strip()
    port = int(os.getenv('SMTP_PORT', '465' if mode == 'ssl' else '587'))
    factory = smtplib.SMTP_SSL if mode == 'ssl' else smtplib.SMTP
    args = {'timeout': 15}
    if mode == 'ssl': args['context'] = context
    with factory(host, port, **args) as smtp:
        smtp.ehlo()
        if mode == 'starttls':
            smtp.starttls(context=context)
            smtp.ehlo()
        username = os.getenv('SMTP_USERNAME', '').strip()
        if username:
            password = os.getenv('SMTP_PASSWORD', '')
            if host.lower() in ('smtp.gmail.com','smtp.googlemail.com'):
                password = ''.join(password.split())
            try:
                smtp.login(username, password)
            except smtplib.SMTPServerDisconnected:
                raise HTTPException(503, "The email provider closed the sender sign-in connection. The owner must check that the App Password belongs to the SMTP_USERNAME account and check the sender account's security notices.") from None
        smtp.send_message(msg)

class EmailBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)

class VerifyBody(EmailBody):
    challenge_id: str = Field(pattern=r'^[a-f0-9]{48}$')
    code: str = Field(pattern=r'^\d{6}$')

def rate_limit(client, key, limit, seconds):
    count = client.eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n", 1, PREFIX+key, seconds)
    if int(count) > limit:
        raise HTTPException(429, "Too many attempts. Please wait before trying again.", headers={'Retry-After': str(seconds)})

@router.post('/email/request')
def request_code(body: EmailBody, request: Request, response: Response):
    email = email_address(body.email)
    secret()
    require_sender_configuration()
    client = client_for(request)
    challenge = secrets.token_hex(24)
    key = PREFIX+'code:'+challenge
    cooldown = PREFIX+'resend:'+digest(email)
    try:
        # Socket peer is used; never trust a caller-supplied forwarding header.
        rate_limit(client, 'request-ip:'+digest(request.client.host if request.client else 'unknown'), 100, 600)
        rate_limit(client, 'request-email:'+digest(email), 10, 3600)
        if not client.set(cooldown, challenge, nx=True, ex=60):
            raise HTTPException(429, "Wait 60 seconds before requesting another code.", headers={'Retry-After':'60'})
        code = f"{secrets.randbelow(1000000):06d}"
        client.hset(key, mapping={'email':email, 'hash':digest(challenge+':'+email+':'+code), 'attempts':'0'})
        client.expire(key, CODE_SECONDS)
        try:
            send_code(email, code)
        except HTTPException:
            client.delete(key, cooldown)
            raise
        except smtplib.SMTPAuthenticationError:
            client.delete(key, cooldown)
            raise HTTPException(503, "The email provider rejected the sender credentials. The dashboard owner must check SMTP_USERNAME and SMTP_PASSWORD. For Gmail, use a Google App Password, then restart the dashboard.") from None
        except (OSError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected):
            client.delete(key, cooldown)
            if os.getenv('RENDER'):
                raise HTTPException(503, "Render Free blocks outbound SMTP ports (25, 465, 587). Configure EMAIL_WEBHOOK_URL, BREVO_API_KEY, or RESEND_API_KEY in Render Environment for HTTPS email delivery.") from None
            raise HTTPException(503, "The dashboard could not connect to the email provider. The owner must check SMTP_HOST, SMTP_PORT, TLS settings and network access.") from None
        except Exception:
            client.delete(key, cooldown)
            raise HTTPException(503, "The email provider could not deliver the verification email. The dashboard owner must check the sender account and provider settings.") from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Sign-in is temporarily unavailable. Please try again later.") from None
    response.headers['Cache-Control'] = 'no-store'
    return {'challenge_id':challenge, 'expires_in':CODE_SECONDS, 'resend_after':60, 'message':'Check your email for a six-digit code.'}

VERIFY_SCRIPT = """
if redis.call('EXISTS',KEYS[1])==0 then return 0 end
if redis.call('HGET',KEYS[1],'email')~=ARGV[1] then return 0 end
local attempts=redis.call('HINCRBY',KEYS[1],'attempts',1)
if attempts>5 then redis.call('DEL',KEYS[1]); return 0 end
if redis.call('HGET',KEYS[1],'hash')==ARGV[2] then redis.call('DEL',KEYS[1]); return 1 end
if attempts>=5 then redis.call('DEL',KEYS[1]) end
return 0
"""

def store_for(request):
    store=getattr(request.app.state,'auth_store',None)
    if store is None:raise HTTPException(503,'Persistent sign-in is temporarily unavailable.')
    return store

def secure_cookie():return os.getenv('DASHBOARD_COOKIE_SECURE','true').lower()!='false'

def browser_origin_check(request):
    origin=request.headers.get('origin')
    if request.headers.get('sec-fetch-site')=='cross-site' or (origin and (urlsplit(origin).scheme not in ('http','https') or urlsplit(origin).netloc.casefold()!=request.headers.get('host','').casefold())):
        raise HTTPException(403,'Open sign-in from this dashboard.')

def issue_access(request,response,email,device_hash):
    token=secrets.token_urlsafe(48)
    client_for(request).setex(PREFIX+'session:'+digest(token),SESSION_SECONDS,json.dumps({'email':email,'device_hash':device_hash}))
    response.headers['Cache-Control']='no-store'
    response.set_cookie(COOKIE,token,max_age=SESSION_SECONDS,httponly=True,secure=secure_cookie(),samesite='strict',path='/')
    return {'access_token':token,'token_type':'bearer','expires_in':SESSION_SECONDS,'name':email,'email':email,'role':role_for(email)}

@router.post('/email/verify')
def verify_code(body: VerifyBody, request: Request, response: Response):
    email=email_address(body.email)
    client=client_for(request)
    try:
        rate_limit(client,'verify-ip:'+digest(request.client.host if request.client else 'unknown'),100,600)
        ok=client.eval(VERIFY_SCRIPT,1,PREFIX+'code:'+body.challenge_id,email,digest(body.challenge_id+':'+email+':'+body.code))
        if not ok:raise HTTPException(401,'The code is incorrect or expired. Request a new code if needed.')
        device=secrets.token_urlsafe(48);device_hash=digest(device)
        store_for(request).register(email,device_hash,request.headers.get('user-agent',''),datetime.now(timezone.utc)+timedelta(seconds=REMEMBER_SECONDS))
        result=issue_access(request,response,email,device_hash)
        response.set_cookie(DEVICE_COOKIE,device,max_age=REMEMBER_SECONDS,httponly=True,secure=secure_cookie(),samesite='strict',path='/api/v1/auth')
        return result
    except HTTPException:raise
    except Exception:
        raise HTTPException(503,'Sign-in is temporarily unavailable. Please try again later.') from None

def identity(request):
    header=request.headers.get('authorization','')
    token=header[7:] if header.lower().startswith('bearer ') else request.cookies.get(COOKIE,'')
    if not token or len(token)>256:raise HTTPException(401,'Sign in with your email to continue.')
    try:
        raw=client_for(request).get(PREFIX+'session:'+digest(token));data=json.loads(raw) if raw else None
        if data and data.get('device_hash') and not store_for(request).lookup(data['device_hash']):data=None
    except HTTPException:raise
    except Exception:raise HTTPException(503,'Sign-in is temporarily unavailable.') from None
    if not data or not data.get('email'):raise HTTPException(401,'Your session expired. Sign in again.')
    return {**data,'name':data['email'],'role':role_for(data['email'])}

@router.get('/me')
def me(request: Request,response:Response):
    user=identity(request)
    # Upgrade a still-valid pre-migration email session without a second code.
    if not user.get('device_hash'):
        try:
            device=secrets.token_urlsafe(48);device_hash=digest(device)
            store_for(request).register(user['email'],device_hash,request.headers.get('user-agent',''),datetime.now(timezone.utc)+timedelta(seconds=REMEMBER_SECONDS))
            header=request.headers.get('authorization','')
            token=header[7:] if header.lower().startswith('bearer ') else request.cookies.get(COOKIE,'')
            client_for(request).setex(PREFIX+'session:'+digest(token),SESSION_SECONDS,json.dumps({'email':user['email'],'device_hash':device_hash}))
            response.set_cookie(DEVICE_COOKIE,device,max_age=REMEMBER_SECONDS,httponly=True,secure=secure_cookie(),samesite='strict',path='/api/v1/auth')
        except Exception:raise HTTPException(503,'Could not remember this browser. Please retry.') from None
    return {'authenticated':True,'email':user['email'],'name':user['name'],'role':user['role']}

@router.post('/restore')
def restore(request:Request,response:Response):
    browser_origin_check(request)
    device=request.cookies.get(DEVICE_COOKIE,'')
    if not device or len(device)>256:raise HTTPException(401,'Sign in with your email to continue.')
    try:
        device_hash=digest(device);saved=store_for(request).lookup(device_hash,touch=True)
        if not saved:raise HTTPException(401,'This remembered sign-in has expired. Verify your email again.')
        return issue_access(request,response,saved['email'],device_hash)
    except HTTPException:raise
    except Exception:raise HTTPException(503,'Could not restore sign-in. Please try again later.') from None

@router.post('/logout')
def logout(request:Request,response:Response):
    browser_origin_check(request)
    header=request.headers.get('authorization','')
    token=header[7:] if header.lower().startswith('bearer ') else request.cookies.get(COOKIE,'')
    device=request.cookies.get(DEVICE_COOKIE,'')
    try:
        if device and len(device)<=256:store_for(request).revoke(digest(device))
        if token and len(token)<=256:
            raw=client_for(request).get(PREFIX+'session:'+digest(token));data=json.loads(raw) if raw else {}
            if data and data.get('device_hash'):store_for(request).revoke(data['device_hash'])
            client_for(request).delete(PREFIX+'session:'+digest(token))
    except Exception:raise HTTPException(503,'Could not sign out. Please try again.') from None
    response.delete_cookie(COOKIE,path='/');response.delete_cookie(DEVICE_COOKIE,path='/api/v1/auth')
    response.headers['Cache-Control']='no-store'
    return {'signed_out':True}

class DashboardAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        public = {'/api/v1/auth/email/request','/api/v1/auth/email/verify','/api/v1/auth/restore','/api/v1/auth/logout','/health','/'}
        protected = path.startswith(('/api/', '/media/', '/static/')) and path not in public and not path.startswith('/static/admin/assets/')
        if protected:
            try:
                # Redis is synchronous; don't block the event loop.
                from starlette.concurrency import run_in_threadpool
                user = await run_in_threadpool(identity, request)
                request.state.dashboard_user = user
                if request.method not in ('GET','HEAD','OPTIONS'):
                    if not request.headers.get('authorization','').lower().startswith('bearer '):
                        raise HTTPException(403, "A dashboard session token is required.")
                    if path != '/api/v1/auth/logout' and user['role'] != 'admin':
                        raise HTTPException(403, "This action requires an administrator.")
                if path in ('/api/v1/admin/settings','/api/v1/admin/audit') and user['role'] != 'admin':
                    raise HTTPException(403, "This page requires an administrator.")
            except HTTPException as exc:
                return JSONResponse({'detail':exc.detail}, status_code=exc.status_code, headers={'Cache-Control':'no-store'})
        response = await call_next(request)
        if protected: response.headers['Cache-Control']='no-store'
        return response
