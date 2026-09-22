"""Окружение Alembic: URL из настроек коннектора, метаданные из моделей."""

from alembic import context
from sqlalchemy import create_engine, pool

from connector.config import settings
from connector.models import Base

target_metadata = Base.metadata


def _sync_url() -> str:
    """Alembic работает через синхронный драйвер: psycopg поддерживает оба
    режима, async-суффиксы других драйверов отбрасываются."""
    return settings.database_url.replace("+aiosqlite", "").replace("+asyncpg", "")


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_sync_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
