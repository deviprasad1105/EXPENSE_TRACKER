"""Tests run against an in-memory SQLite database, so they need neither Postgres nor a running server."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import ROLE_ADMIN, STATUS_APPROVED, User
from app.routers import auth
from app.security import create_access_token, hash_password

PASSWORD = "correct-horse-1"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = Session()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    auth.login_limiter.reset()
    auth.public_form_limiter.reset()
    yield session
    session.close()
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def client(db):
    # Not used as a context manager, so the lifespan (schema sync, scheduler) doesn't run
    return TestClient(app)


@pytest.fixture
def make_user(db):
    def make(username, *, admin=False, manager=None, **features):
        user = User(
            username=username,
            email=f"{username}@example.com",
            full_name=username.title(),
            password_hash=hash_password(PASSWORD),
            role=ROLE_ADMIN if admin else "USER",
            status=STATUS_APPROVED,
            manager_id=manager.id if manager else None,
            **{f"feature_{k}": v for k, v in features.items()},
        )
        db.add(user)
        db.commit()
        return user

    return make


def headers(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.token_version)}"}
