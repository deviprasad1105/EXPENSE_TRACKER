"""One-off copy of the Expense Tracker tables from Oracle to Postgres.

    cd backend
    .venv/bin/pip install oracledb==2.5.1
    ORACLE_DATABASE_URL=oracle+oracledb://dev_dr:PASSWORD@localhost:1521/?service_name=FREEPDB1 \
        .venv/bin/python -m scripts.oracle_to_postgres

The target is DATABASE_URL (backend/.env). Only the app's own tables are copied, with their ids, and every identity
sequence is moved past the copied ids. It refuses to run if any target table already has rows.
"""

import os

from dotenv import dotenv_values
from sqlalchemy import create_engine, func, select, text, update

from app.database import Base, engine as pg
from app.migrate import sync_schema
import app.models  # noqa: F401  (registers the tables)


def main():
    url = os.environ.get("ORACLE_DATABASE_URL") or dotenv_values(".env").get("ORACLE_DATABASE_URL")
    if not url:
        raise SystemExit("Set ORACLE_DATABASE_URL")
    ora = create_engine(url)
    sync_schema(pg)
    tables = Base.metadata.sorted_tables

    with pg.connect() as c:
        busy = [t.name for t in tables if c.execute(select(func.count()).select_from(t)).scalar()]
    if busy:
        raise SystemExit(f"Target tables already hold data, nothing copied: {', '.join(busy)}")

    with ora.connect() as src, pg.begin() as dst:
        for table in tables:
            rows = [dict(r._mapping) for r in src.execute(select(table))]
            # Self-references (app_users.manager_id) may point at rows later in the batch: insert NULL, then fill in
            self_fks = [fk.parent.name for fk in table.foreign_keys if fk.column.table is table]
            later = [{**{k: r[k] for k in self_fks}, "_pk": r["id"]} for r in rows if any(r[k] for k in self_fks)]
            if rows:
                dst.execute(table.insert(), [{**r, **{k: None for k in self_fks}} for r in rows])
            for r in later:
                dst.execute(update(table).where(table.c.id == r.pop("_pk")).values(**r))
            if "id" in table.c and table.c.id.identity is not None:
                dst.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table.name}), 0) + 1, false)"
                ))
            print(f"{table.name}: {len(rows)}")


if __name__ == "__main__":
    main()
