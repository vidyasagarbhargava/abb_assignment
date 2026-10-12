from alembic import context
from sqlalchemy import pool

import app.orm  # noqa: F401  (register tables)
from app.config import get_settings
from app.db import Base, UTCDateTime, make_engine

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):  # type: ignore[no-untyped-def]
    # Migrations must not import application types: freeze them as plain SQLAlchemy.
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations() -> None:
    url = get_settings().database_url
    if context.is_offline_mode():
        context.configure(
            url=url, target_metadata=target_metadata, literal_binds=True, render_item=render_item
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = make_engine(url)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_item=render_item,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
_ = pool
