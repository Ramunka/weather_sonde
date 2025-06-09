from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '1f9a7956a6a5'  # use the actual revision ID from your file
down_revision = '23ddd62d7e64'  # your real baseline revision
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'flight_status',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('flight_id', sa.Integer(), unique=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('measurement_age', sa.Integer(), nullable=True),
        sa.Column('transmission_age', sa.Integer(), nullable=True),
        sa.Column('flight_phase', sa.String(length=20), nullable=True),
        sa.Column('burst_detected', sa.Boolean(), nullable=True),
        sa.Column('burst_altitude', sa.Float(), nullable=True),
        sa.Column('current_ascent_rate', sa.Float(), nullable=True),
        sa.Column('max_altitude', sa.Float(), nullable=True),
        sa.Column('min_pressure', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['flight_id'], ['sonde.flights.id']),
        schema='sonde'
    )


def downgrade():
    op.drop_table('flight_status', schema='sonde')
