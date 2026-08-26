"""Engine and session factory."""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from config import config
from database.models import Base

log = logging.getLogger(__name__)

engine = create_engine(f"sqlite:///{config.db_path}", echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _add_missing_columns() -> None:
    """Add columns that appeared after a table was first created.

    create_all() creates missing tables and silently ignores missing columns, so a
    schema change lands on a fresh machine and not on the one actually being used.
    This is a single-user local app with a derived database, so a full migration tool
    is not worth the weight, but silently reading a table that lacks a column is not
    the alternative. Additive only: it never drops or retypes anything.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                ddl = column.type.compile(engine.dialect)
                default = ""
                if column.default is not None and getattr(column.default, "is_scalar", False):
                    default = f" DEFAULT {column.default.arg!r}"
                connection.execute(
                    text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl}{default}')
                )
                log.warning("schema: added %s.%s", table.name, column.name)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
