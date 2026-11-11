"""owner log posts

Revision ID: c3f1a7e2b9d4
Revises: adb2e9310ff6
Create Date: 2026-11-12 02:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.database.types


revision: str = 'c3f1a7e2b9d4'
down_revision: Union[str, Sequence[str], None] = 'adb2e9310ff6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'owner_log_posts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('listing_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('message_id', sa.BigInteger(), nullable=True),
        sa.Column('claimed_by', sa.BigInteger(), nullable=True),
        sa.Column('claimed_at', app.database.types.UTCDateTime(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', app.database.types.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(['listing_id'], ['listings.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('listing_id'),
    )
    op.create_index('ix_owner_log_posts_status', 'owner_log_posts', ['status'])


def downgrade() -> None:
    op.drop_index('ix_owner_log_posts_status', table_name='owner_log_posts')
    op.drop_table('owner_log_posts')
