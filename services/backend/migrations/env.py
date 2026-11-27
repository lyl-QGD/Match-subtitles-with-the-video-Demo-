from alembic import context
from sqlalchemy import create_engine
from app.database import connect, inspect_schema

config = context.config

def run():
    if context.is_offline_mode():
        raise RuntimeError("Use online migration to validate the existing schema")
    engine = create_engine("postgresql+psycopg://", creator=lambda: connect(migration=True))
    with engine.connect() as connection:
        result = inspect_schema(connection.connection.driver_connection)
        if not result['ok']:
            raise RuntimeError('Schema baseline validation failed; do not adopt inconsistent tables')
        context.configure(connection=connection, version_table_schema="dialogue")
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()

run()
