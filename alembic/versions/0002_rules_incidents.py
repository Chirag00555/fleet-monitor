"""rules_and_incidents

Revision ID: 0002_rules_incidents
Revises: 0001_initial
Create Date: 2026-10-24 18:40:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0002_rules_incidents'
down_revision: Union[str, None] = '0001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Create rules table
    op.create_table(
        'rules',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('rule_type', sa.String(length=64), nullable=False),
        sa.Column('device_id', sa.String(length=64), nullable=True),
        sa.Column('comparator', sa.String(length=16), nullable=True),
        sa.Column('threshold_value', sa.Float(), nullable=True),
        sa.Column('payload_field', sa.String(length=64), nullable=True, server_default='temperature'),
        sa.Column('target_event_type', sa.String(length=64), nullable=True),
        sa.Column('silence_duration_seconds', sa.Integer(), nullable=True),
        sa.Column('severity', sa.String(length=32), nullable=False, server_default='warning'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_rules_is_enabled'), 'rules', ['is_enabled'], unique=False)
    op.create_index(op.f('ix_rules_rule_type'), 'rules', ['rule_type'], unique=False)
    op.create_index(op.f('ix_rules_device_id'), 'rules', ['device_id'], unique=False)

    # 2. Create incidents table
    op.create_table(
        'incidents',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('rule_id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='open'),
        sa.Column('severity', sa.String(length=32), nullable=False, server_default='warning'),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('dedup_key', sa.String(length=128), nullable=True),
        sa.Column('event_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('last_event_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['rule_id'], ['rules.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_incidents_status'), 'incidents', ['status'], unique=False)
    op.create_index(op.f('ix_incidents_device_id'), 'incidents', ['device_id'], unique=False)
    op.create_index(op.f('ix_incidents_rule_id'), 'incidents', ['rule_id'], unique=False)
    op.create_index(op.f('ix_incidents_dedup_key'), 'incidents', ['dedup_key'], unique=True)

    # 3. Create incident_events table
    op.create_table(
        'incident_events',
        sa.Column('incident_id', sa.Integer(), nullable=False),
        sa.Column('event_pk', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['event_pk'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('incident_id', 'event_pk')
    )
    op.create_index('idx_incident_events_event', 'incident_events', ['event_pk'], unique=False)

def downgrade() -> None:
    op.drop_table('incident_events')
    op.drop_table('incidents')
    op.drop_table('rules')
