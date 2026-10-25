"""add_device_pairing_and_tokens

Revision ID: 0004_device_pairing
Revises: 0003_add_operators
Create Date: 2026-10-25 19:40:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0004_device_pairing'
down_revision: Union[str, None] = '0003_add_operators'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Add api_token column to devices table
    op.add_column('devices', sa.Column('api_token', sa.String(length=128), nullable=True))
    op.create_index(op.f('ix_devices_api_token'), 'devices', ['api_token'], unique=True)

    # 2. Create device_pairing_codes table
    op.create_table(
        'device_pairing_codes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('code', sa.String(length=32), nullable=False),
        sa.Column('device_id', sa.String(length=64), nullable=False),
        sa.Column('device_name', sa.String(length=128), nullable=False),
        sa.Column('location', sa.String(length=256), nullable=True),
        sa.Column('is_used', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_device_pairing_codes_code'), 'device_pairing_codes', ['code'], unique=True)
    op.create_index(op.f('ix_device_pairing_codes_device_id'), 'device_pairing_codes', ['device_id'])

def downgrade() -> None:
    op.drop_index(op.f('ix_device_pairing_codes_device_id'), table_name='device_pairing_codes')
    op.drop_index(op.f('ix_device_pairing_codes_code'), table_name='device_pairing_codes')
    op.drop_table('device_pairing_codes')
    op.drop_index(op.f('ix_devices_api_token'), table_name='devices')
    op.drop_column('devices', 'api_token')
