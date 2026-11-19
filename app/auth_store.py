"""Durable verified users, remembered devices, and PostgreSQL auth KV fallback."""
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import MetaData, Table, Column, String, DateTime, Text, ForeignKey, select, update, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

metadata = MetaData()
users = Table('dashboard_users_v1', metadata,
    Column('email', String(254), primary_key=True),
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('verified_at', DateTime(timezone=True), nullable=False),
    Column('last_login_at', DateTime(timezone=True), nullable=False))
sessions = Table('dashboard_sessions_v1', metadata,
    Column('token_hash', String(64), primary_key=True),
    Column('email', String(254), ForeignKey('dashboard_users_v1.email'), nullable=False, index=True),
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('last_used_at', DateTime(timezone=True), nullable=False),
    Column('expires_at', DateTime(timezone=True), nullable=False, index=True),
    Column('revoked_at', DateTime(timezone=True)),
    Column('user_agent', String(300), nullable=False))
auth_kv = Table('dashboard_auth_kv_v1', metadata,
    Column('key', String(255), primary_key=True),
    Column('value', Text, nullable=False),
    Column('expires_at', DateTime(timezone=True), nullable=False, index=True))


class AuthStore:
    def __init__(self, engine):
        self.engine = engine

    def initialize(self):
        metadata.create_all(self.engine)

    def register(self, email, token_hash, user_agent, expires):
        now = datetime.now(timezone.utc)
        factory = pg_insert if self.engine.dialect.name == 'postgresql' else sqlite_insert
        statement = factory(users).values(email=email, created_at=now, verified_at=now, last_login_at=now)
        statement = statement.on_conflict_do_update(index_elements=['email'], set_={'verified_at': now, 'last_login_at': now})
        with self.engine.begin() as conn:
            conn.execute(statement)
            conn.execute(sessions.insert().values(token_hash=token_hash, email=email, created_at=now, last_used_at=now,
                                                  expires_at=expires, revoked_at=None, user_agent=user_agent[:300]))

    def lookup(self, token_hash, touch=False):
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            row = conn.execute(select(sessions.c.email, sessions.c.expires_at).where(
                sessions.c.token_hash == token_hash, sessions.c.revoked_at.is_(None), sessions.c.expires_at > now
            )).mappings().first()
            if row and touch:
                conn.execute(update(sessions).where(sessions.c.token_hash == token_hash).values(last_used_at=now))
        return dict(row) if row else None

    def revoke(self, token_hash):
        with self.engine.begin() as conn:
            conn.execute(update(sessions).where(
                sessions.c.token_hash == token_hash, sessions.c.revoked_at.is_(None)
            ).values(revoked_at=datetime.now(timezone.utc)))


class PgAuthCache:
    """PostgreSQL-backed key-value store for email login codes, sessions, and rate limits."""

    def __init__(self, engine):
        self.engine = engine

    def _now(self):
        return datetime.now(timezone.utc)

    def _upsert(self, conn, key, value, expires_at):
        factory = pg_insert if self.engine.dialect.name == 'postgresql' else sqlite_insert
        stmt = factory(auth_kv).values(key=key, value=value, expires_at=expires_at)
        stmt = stmt.on_conflict_do_update(index_elements=['key'], set_={'value': value, 'expires_at': expires_at})
        conn.execute(stmt)

    def set(self, key, value, nx=False, ex=None):
        now = self._now()
        ttl = max(1, int(ex or 600))
        expires_at = now + timedelta(seconds=ttl)
        with self.engine.begin() as conn:
            conn.execute(delete(auth_kv).where(auth_kv.c.expires_at <= now))
            if nx:
                existing = conn.execute(select(auth_kv.c.key).where(
                    auth_kv.c.key == key, auth_kv.c.expires_at > now
                )).first()
                if existing:
                    return False
            self._upsert(conn, key, str(value), expires_at)
            return True

    def setex(self, key, ttl, value):
        return self.set(key, value, ex=ttl)

    def get(self, key):
        now = self._now()
        with self.engine.connect() as conn:
            row = conn.execute(select(auth_kv.c.value, auth_kv.c.expires_at).where(auth_kv.c.key == key)).first()
            if not row:
                return None
            expires_at = row[1]
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                return None
            return row[0]

    def hset(self, key, mapping):
        now = self._now()
        expires_at = now + timedelta(seconds=600)
        with self.engine.begin() as conn:
            self._upsert(conn, key, json.dumps(dict(mapping)), expires_at)

    def expire(self, key, ttl):
        expires_at = self._now() + timedelta(seconds=max(1, int(ttl)))
        with self.engine.begin() as conn:
            conn.execute(update(auth_kv).where(auth_kv.c.key == key).values(expires_at=expires_at))

    def delete(self, *keys):
        if not keys:
            return 0
        with self.engine.begin() as conn:
            return conn.execute(delete(auth_kv).where(auth_kv.c.key.in_(list(keys)))).rowcount

    def eval(self, script, _numkeys, key, *args):
        now = self._now()
        with self.engine.begin() as conn:
            conn.execute(delete(auth_kv).where(auth_kv.c.expires_at <= now))
            row = conn.execute(select(auth_kv.c.value, auth_kv.c.expires_at).where(
                auth_kv.c.key == key, auth_kv.c.expires_at > now
            )).first()
            if len(args) == 2:
                # Verification challenge script: args = (email, expected_hash)
                if not row:
                    return 0
                entry = json.loads(row[0])
                if entry.get('email') != args[0]:
                    return 0
                attempts = int(entry.get('attempts', 0)) + 1
                if attempts > 5:
                    conn.execute(delete(auth_kv).where(auth_kv.c.key == key))
                    return 0
                if entry.get('hash') == args[1]:
                    conn.execute(delete(auth_kv).where(auth_kv.c.key == key))
                    return 1
                if attempts >= 5:
                    conn.execute(delete(auth_kv).where(auth_kv.c.key == key))
                    return 0
                entry['attempts'] = str(attempts)
                conn.execute(update(auth_kv).where(auth_kv.c.key == key).values(value=json.dumps(entry)))
                return 0
            # Rate-limit script: args = (ttl_seconds,)
            ttl = max(1, int(args[0]))
            if not row:
                self._upsert(conn, key, '1', now + timedelta(seconds=ttl))
                return 1
            count = int(row[0]) + 1
            conn.execute(update(auth_kv).where(auth_kv.c.key == key).values(value=str(count)))
            return count
