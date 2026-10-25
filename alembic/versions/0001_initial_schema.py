"""initial_schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-10-24 18:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        'devices',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('location', sa.String(length=256), nullable=True),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('latest_report_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('server_last_received_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_devices_id'), 'devices', ['id'], unique=False)
    op.create_index(op.f('ix_devices_latest_report_time'), 'devices', ['latest_report_time'], unique=False)

    op.create_table(
        'events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('device_id', sa.String(length=64), nullable=False),
        sa.Column('event_id', sa.String(length=128), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('event_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('processing_status', sa.String(length=32), nullable=False, server_default='pending'),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id', 'event_id', name='uq_device_event_id')
    )
    op.create_index(op.f('ix_events_device_id'), 'events', ['device_id'], unique=False)
    op.create_index(op.f('ix_events_event_type'), 'events', ['event_type'], unique=False)
    op.create_index(op.f('ix_events_event_time'), 'events', ['event_time'], unique=False)
    op.create_index('idx_device_event_time', 'events', ['device_id', 'event_time'], unique=False)

    op.create_table(
        'work_items',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('event_pk', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('locked_by', sa.String(length=128), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['event_pk'], ['events.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_work_items_event_pk'), 'work_items', ['event_pk'], unique=False)
    op.create_index(op.f('ix_work_items_status'), 'work_items', ['status'], unique=False)
    op.create_index(op.f('ix_work_items_available_at'), 'work_items', ['available_at'], unique=False)
    op.create_index('idx_work_items_claim', 'work_items', ['status', 'available_at'], unique=False)

def downgrade() -> None:
    op.drop_table('work_items')
    op.drop_table('events')
    op.drop_table('devices')
