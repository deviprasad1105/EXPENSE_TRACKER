from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from .config import settings

# Transaction-mode poolers (Supabase :6543, PgBouncer) hand each transaction a different server connection,
# so psycopg's automatic server-side prepared statements would collide; turn them off for Postgres
_connect_args = {"prepare_threshold": None} if settings.sqlalchemy_url.startswith("postgresql+psycopg") else {}

# A serverless instance can be frozen between requests, so it opens a fresh connection each time
# (point DATABASE_URL at the provider's pooled endpoint there)
engine = (
    create_engine(settings.sqlalchemy_url, poolclass=NullPool, connect_args=_connect_args)
    if settings.serverless
    else create_engine(settings.sqlalchemy_url, pool_pre_ping=True, connect_args=_connect_args)
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
