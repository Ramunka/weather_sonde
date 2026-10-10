"""initial schema

Squashed baseline for the 'sonde' schema (replaces the 17 pre-2026 revisions, which could
not build a fresh database). For an existing database created by the old revisions, run
db/align_existing_db.sql and then `flask db stamp --purge 0001_initial` (see README).

Revision ID: 0001_initial
Revises: 
Create Date: 2026-10-09 23:15:33.850719

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS sonde")
    op.create_table('devices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('device_sn', sa.String(), nullable=False),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('device_sn'),
    schema='sonde'
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=80), nullable=False),
    sa.Column('password', sa.String(length=256), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username'),
    schema='sonde'
    )
    op.create_table('flights',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('device_id', sa.Integer(), nullable=True),
    sa.Column('mission_number', sa.String(length=50), nullable=False),
    sa.Column('equipment', sa.String(length=100), nullable=True),
    sa.Column('comments', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('start_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('end_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('mask', sa.String(), nullable=False),
    sa.Column('start_latitude', sa.Float(), nullable=True),
    sa.Column('start_longitude', sa.Float(), nullable=True),
    sa.Column('elevation', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['device_id'], ['sonde.devices.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['sonde.users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('mission_number'),
    schema='sonde'
    )
    op.create_table('data_selection',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('flight_id', sa.Integer(), nullable=False),
    sa.Column('start_ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('end_ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('exclusions', sa.JSON(), nullable=True),
    sa.Column('gap_info', sa.JSON(), nullable=True),
    sa.Column('verified_by', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['flight_id'], ['sonde.flights.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('flight_id'),
    schema='sonde'
    )
    op.create_table('flight_status',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('flight_id', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('flight_phase', sa.String(length=20), nullable=True),
    sa.Column('measurement_age', sa.Integer(), nullable=True),
    sa.Column('last_ascent_rate', sa.Float(), nullable=True),
    sa.Column('max_altitude', sa.Float(), nullable=True),
    sa.Column('min_pressure', sa.Integer(), nullable=True),
    sa.Column('release_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('release_altitude', sa.Float(), nullable=True),
    sa.Column('release_pressure', sa.Float(), nullable=True),
    sa.Column('burst_detected', sa.Boolean(), nullable=True),
    sa.Column('burst_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('burst_altitude', sa.Float(), nullable=True),
    sa.Column('burst_pressure', sa.Float(), nullable=True),
    sa.Column('end_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('balloon_position', sa.Float(), nullable=True),
    sa.Column('parachute_position', sa.Float(), nullable=True),
    sa.Column('burst_position', sa.Float(), nullable=True),
    sa.Column('receiver_ok', sa.Boolean(), nullable=True),
    sa.Column('parser_ok', sa.Boolean(), nullable=True),
    sa.Column('sensor_ok', sa.Boolean(), nullable=True),
    sa.Column('signal_level', sa.String(length=6), nullable=True),
    sa.Column('packet_ok', sa.Boolean(), nullable=True),
    sa.Column('age_warn', sa.Boolean(), nullable=True),
    sa.Column('calibrated', sa.Boolean(), nullable=True),
    sa.Column('temp_low', sa.Boolean(), nullable=True),
    sa.Column('data_degrad', sa.Boolean(), nullable=True),
    sa.Column('gps_fix', sa.Boolean(), nullable=True),
    sa.Column('gps_degrad', sa.String(length=6), nullable=True),
    sa.ForeignKeyConstraint(['flight_id'], ['sonde.flights.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('flight_id'),
    schema='sonde'
    )
    op.create_table('ground_reference',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('flight_id', sa.Integer(), nullable=False),
    sa.Column('timestamp', sa.DateTime(timezone=True), nullable=True),
    sa.Column('gps_latitude', sa.Float(), nullable=True),
    sa.Column('gps_longitude', sa.Float(), nullable=True),
    sa.Column('gps_altitude', sa.Integer(), nullable=True),
    sa.Column('temperature', sa.Float(), nullable=True),
    sa.Column('pressure', sa.Integer(), nullable=True),
    sa.Column('humidity', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['flight_id'], ['sonde.flights.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('flight_id'),
    schema='sonde'
    )
    op.create_table('logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('flight_id', sa.Integer(), nullable=True),
    sa.Column('timestamp', sa.DateTime(timezone=True), nullable=True),
    sa.Column('level', sa.String(length=16), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['flight_id'], ['sonde.flights.id'], ),
    sa.PrimaryKeyConstraint('id'),
    schema='sonde'
    )
    op.create_table('telemetry',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('flight_id', sa.Integer(), nullable=False),
    sa.Column('timestamp', sa.DateTime(timezone=True), nullable=True),
    sa.Column('measurement_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('processed_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('gps_latitude', sa.Float(), nullable=True),
    sa.Column('gps_longitude', sa.Float(), nullable=True),
    sa.Column('gps_altitude', sa.Integer(), nullable=True),
    sa.Column('hdop', sa.Float(), nullable=True),
    sa.Column('sats', sa.Integer(), nullable=True),
    sa.Column('pressure', sa.Integer(), nullable=True),
    sa.Column('temperature', sa.Float(), nullable=True),
    sa.Column('humidity', sa.Float(), nullable=True),
    sa.Column('signal_strength', sa.Integer(), nullable=True),
    sa.Column('speed', sa.Float(), nullable=True),
    sa.Column('ascent_rate', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['flight_id'], ['sonde.flights.id'], ),
    sa.PrimaryKeyConstraint('id'),
    schema='sonde'
    )
    with op.batch_alter_table('telemetry', schema='sonde') as batch_op:
        batch_op.create_index('ix_telemetry_flight_timestamp', ['flight_id', 'timestamp'], unique=False)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('telemetry', schema='sonde') as batch_op:
        batch_op.drop_index('ix_telemetry_flight_timestamp')

    op.drop_table('telemetry', schema='sonde')
    op.drop_table('logs', schema='sonde')
    op.drop_table('ground_reference', schema='sonde')
    op.drop_table('flight_status', schema='sonde')
    op.drop_table('data_selection', schema='sonde')
    op.drop_table('flights', schema='sonde')
    op.drop_table('users', schema='sonde')
    op.drop_table('devices', schema='sonde')
    # ### end Alembic commands ###
