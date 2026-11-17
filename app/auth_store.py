"""Durable verified users and remembered devices. Only token hashes are stored."""
from datetime import datetime, timezone
from sqlalchemy import MetaData,Table,Column,String,DateTime,ForeignKey,select,update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

metadata=MetaData()
users=Table('dashboard_users_v1',metadata,
    Column('email',String(254),primary_key=True),
    Column('created_at',DateTime(timezone=True),nullable=False),
    Column('verified_at',DateTime(timezone=True),nullable=False),
    Column('last_login_at',DateTime(timezone=True),nullable=False))
sessions=Table('dashboard_sessions_v1',metadata,
    Column('token_hash',String(64),primary_key=True),
    Column('email',String(254),ForeignKey('dashboard_users_v1.email'),nullable=False,index=True),
    Column('created_at',DateTime(timezone=True),nullable=False),
    Column('last_used_at',DateTime(timezone=True),nullable=False),
    Column('expires_at',DateTime(timezone=True),nullable=False,index=True),
    Column('revoked_at',DateTime(timezone=True)),
    Column('user_agent',String(300),nullable=False))

class AuthStore:
    def __init__(self,engine):self.engine=engine
    def initialize(self):metadata.create_all(self.engine)
    def register(self,email,token_hash,user_agent,expires):
        now=datetime.now(timezone.utc)
        factory=pg_insert if self.engine.dialect.name=='postgresql' else sqlite_insert
        statement=factory(users).values(email=email,created_at=now,verified_at=now,last_login_at=now)
        statement=statement.on_conflict_do_update(index_elements=['email'],set_={'verified_at':now,'last_login_at':now})
        with self.engine.begin() as conn:
            conn.execute(statement)
            conn.execute(sessions.insert().values(token_hash=token_hash,email=email,created_at=now,last_used_at=now,expires_at=expires,revoked_at=None,user_agent=user_agent[:300]))
    def lookup(self,token_hash,touch=False):
        now=datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            row=conn.execute(select(sessions.c.email,sessions.c.expires_at).where(sessions.c.token_hash==token_hash,sessions.c.revoked_at.is_(None),sessions.c.expires_at>now)).mappings().first()
            if row and touch:conn.execute(update(sessions).where(sessions.c.token_hash==token_hash).values(last_used_at=now))
        return dict(row) if row else None
    def revoke(self,token_hash):
        with self.engine.begin() as conn:conn.execute(update(sessions).where(sessions.c.token_hash==token_hash,sessions.c.revoked_at.is_(None)).values(revoked_at=datetime.now(timezone.utc)))
