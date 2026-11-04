from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from .config import settings

# A serverless instance can be frozen between requests, so it opens a fresh connection each time
# (point DATABASE_URL at the provider's pooled endpoint there)
engine = (
    create_engine(settings.sqlalchemy_url, poolclass=NullPool)
    if settings.serverless
    else create_engine(settings.sqlalchemy_url, pool_pre_ping=True)
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
