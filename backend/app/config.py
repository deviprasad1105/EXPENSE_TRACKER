import os

from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_JWT_SECRET = "change-me"
DEFAULT_ADMIN_PASSWORD = "admin123"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "production" refuses to start with default secrets, hides the API docs and sends HSTS
    environment: str = "development"

    database_url: str = "postgresql+psycopg://dev_dr_expense:YOUR_PASSWORD@localhost:5445/dev_dr_expense"

    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_expire_minutes: int = 720

    admin_username: str = "admin"
    admin_password: str = DEFAULT_ADMIN_PASSWORD
    admin_email: str = "admin@example.com"

    # Sign-in protection: after LOGIN_MAX_FAILURES wrong passwords the account is blocked for LOGIN_LOCK_MINUTES
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    # And at most LOGIN_IP_MAX_ATTEMPTS sign-in attempts per IP address per minute
    login_ip_max_attempts: int = 20

    edit_window_days: int = 3
    username_lock_days: int = 15
    max_avatar_bytes: int = 2_000_000
    max_background_bytes: int = 5_000_000

    # Forgot password: at most RESET_MAX_PER_WINDOW reset emails per RESET_WINDOW_DAYS
    reset_max_per_window: int = 2
    reset_window_days: int = 15
    reset_token_minutes: int = 60
    frontend_url: str = "http://localhost:5173"

    reminder_hour: int = 21
    reminder_minute: int = 0
    timezone: str = "Asia/Kolkata"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    # Used when SMTP_HOST is empty: emails are written here as .eml files instead of being sent
    mail_outbox_dir: str = "mail_outbox"

    # Subscriptions: charges and "renews tomorrow" reminders are processed every hour at this minute
    subscription_job_minute: int = 5

    cors_origins: str = "http://localhost:5173"

    # Vercel Cron sends "Authorization: Bearer <CRON_SECRET>" to /api/cron/*; empty disables those endpoints
    cron_secret: str = ""

    @property
    def serverless(self) -> bool:
        """True on Vercel: no background scheduler (Vercel Cron calls /api/cron/*) and no connection pool."""
        return bool(os.environ.get("VERCEL"))

    @property
    def sqlalchemy_url(self) -> str:
        """Hosted Postgres providers hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the psycopg driver."""
        url = self.database_url
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ("prod", "production")

    def insecure_defaults(self) -> list[str]:
        """Settings that must be changed before the app is exposed to anyone."""
        problems = []
        if self.jwt_secret == DEFAULT_JWT_SECRET or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET is the default or shorter than 32 characters")
        if self.admin_password == DEFAULT_ADMIN_PASSWORD:
            problems.append("ADMIN_PASSWORD is the default 'admin123'")
        return problems


settings = Settings()
