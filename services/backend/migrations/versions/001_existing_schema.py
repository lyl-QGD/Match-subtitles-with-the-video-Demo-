"""Validate and adopt existing manually installed schema, without recreating tables."""
from alembic import op
from sqlalchemy import text
revision = "001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    connection = op.get_bind()
    installed = connection.execute(text("SELECT version FROM dialogue.schema_versions WHERE version='001'")).scalar()
    if installed != '001':
        raise RuntimeError("Install supabase/001_schema.sql first; baseline not found")

def downgrade():
    raise RuntimeError("Baseline downgrade is intentionally disabled; preserve existing data")
