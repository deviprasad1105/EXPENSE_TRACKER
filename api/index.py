"""Vercel entry point: serves the FastAPI backend (backend/app) as one Python function. See vercel.json."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

# The function's filesystem is read-only except /tmp (only matters while SMTP_HOST is empty)
os.environ.setdefault("MAIL_OUTBOX_DIR", "/tmp/mail_outbox")

from app.main import app, init_app  # noqa: E402

# Runs on each cold start, whether or not the runtime sends ASGI lifespan events
init_app()

__all__ = ["app"]
