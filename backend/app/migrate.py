"""Startup schema sync: create missing tables and add columns that newer versions of the models introduced.

`create_all` only creates tables that don't exist yet, so a column added to a model later would be missing from
an existing database. This adds such columns (never drops or changes anything), with the model's default so
existing rows get a value.
"""

import logging

from sqlalchemy import Engine, inspect, text

from .database import Base

log = logging.getLogger(__name__)


def _literal(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _upgrade_split_tables(engine: Engine) -> None:
    """Split bills first stored participants as user ids; now they are split_members rows (users or guests).

    The bill, share and settlement tables are recreated (only while they are still empty), and split_members
    gets a nullable user_id without the (group, user) unique key, since guests have no user.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "split_shares" not in tables or "user_id" not in {c["name"].lower() for c in inspector.get_columns("split_shares")}:
        return
    old = ("split_shares", "split_settlements", "split_bills")
    with engine.begin() as conn:
        rows = sum(conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in old)
        if rows:
            raise RuntimeError(
                "Split bill tables hold data in the old layout (participants by user id). "
                "Move it by hand before upgrading: see docs/DATABASE.md"
            )
        for t in old:
            conn.execute(text(f"DROP TABLE {t}"))
        if engine.dialect.name == "oracle":
            conn.execute(text("ALTER TABLE split_members DROP CONSTRAINT uq_split_member"))
            conn.execute(text("ALTER TABLE split_members MODIFY (user_id NULL)"))
    log.warning("Schema update: split bill tables moved to member-based participants")


def sync_schema(engine: Engine) -> None:
    _upgrade_split_tables(engine)
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    oracle = engine.dialect.name == "oracle"
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"].lower() for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name.lower() in existing:
                    continue
                ddl = f"{col.name} {col.type.compile(dialect=engine.dialect)}"
                default = col.default.arg if col.default is not None and col.default.is_scalar else None
                if default is not None:
                    ddl += f" DEFAULT {_literal(default)}"
                if not col.nullable and default is not None:
                    ddl += " NOT NULL"
                sql = f"ALTER TABLE {table.name} ADD ({ddl})" if oracle else f"ALTER TABLE {table.name} ADD COLUMN {ddl}"
                conn.execute(text(sql))
                log.warning("Schema update: added column %s.%s", table.name, col.name)
