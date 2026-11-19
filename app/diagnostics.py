"""Actionable startup diagnostics without printing credentials."""
import os,shutil,subprocess
from pathlib import Path
from sqlalchemy.engine import make_url

def database_message(exc, url=None):
    raw=str(getattr(exc,'orig',exc)).lower()
    target='configured PostgreSQL server'
    user=''
    if url is not None:
        try:
            u=make_url(url);target=f'{u.host or "127.0.0.1"}:{u.port or 5432} / {u.database or ""}'
            user=u.username or ''
        except Exception:pass
    if 'password authentication failed' in raw:
        return f'Database login rejected for user "{user}" at {target}. Check DB_USER and DB_PASSWORD or DATABASE_URL (for Supabase pooler use postgres.<project-ref> and an alphanumeric password without special characters).'
    if 'does not exist' in raw and 'database' in raw:
        return f'Database does not exist at {target}. Create the configured database or correct DB_NAME in .env.'
    if any(x in raw for x in ('connection refused','10061','actively refused')):
        return f'PostgreSQL is not accepting connections at {target}. Start the existing PostgreSQL service/container, or run START_DATABASE_SO.bat to create/start the bundled database. For database + OCR + bot, use START_DOCKER_SO.bat.'
    if 'timeout' in raw or 'timed out' in raw:
        return f'PostgreSQL connection timed out at {target}. Check DB_HOST, DB_PORT, Docker Desktop, and firewall access.'
    if 'could not translate host' in raw:
        return f'Cannot resolve PostgreSQL host at {target}. Check DB_HOST in .env.'
    return f'Database operation failed at {target}. Check connection settings and database permissions. Error type: {type(exc).__name__}.'

def tesseract_command():
    configured=os.getenv('TESSERACT_CMD','').strip().strip('"')
    if configured:return configured
    found=shutil.which('tesseract')
    if found:return found
    candidates=[Path(os.getenv('ProgramFiles','C:/Program Files'))/'Tesseract-OCR'/'tesseract.exe',Path(os.getenv('LOCALAPPDATA','C:/Users/Default/AppData/Local'))/'Programs'/'Tesseract-OCR'/'tesseract.exe']
    return str(next((p for p in candidates if p.is_file()),Path('tesseract')))

def check_ocr():
    try:
        p=subprocess.run([tesseract_command(),'--list-langs'],capture_output=True,text=True,timeout=10,check=True)
        langs=(p.stdout+'\n'+p.stderr).splitlines()
        if not any(x.strip()=='eng' for x in langs):return False,'Tesseract is installed but English language data (eng) is missing.'
        return True,'Tesseract and English language data are ready.'
    except (OSError,subprocess.SubprocessError):
        return False,'Tesseract is not available. Use START_DOCKER_SO.bat (OCR included), or install Tesseract for native Windows and set TESSERACT_CMD.'
