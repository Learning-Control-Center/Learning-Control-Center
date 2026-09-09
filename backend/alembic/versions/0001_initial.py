"""Create the frozen canonical V1 schema.

The revision-local snapshot deliberately does not import application ORM metadata. Future model
additions therefore cannot change the schema emitted by the historical baseline revision.
"""

import importlib.util
from pathlib import Path

from alembic import op

_snapshot_path = Path(__file__).parents[1] / "v1_schema_snapshot.py"
_snapshot_spec = importlib.util.spec_from_file_location("lcc_v1_schema_snapshot", _snapshot_path)
if _snapshot_spec is None or _snapshot_spec.loader is None:
    raise RuntimeError("The frozen V1 schema snapshot could not be loaded.")
_snapshot = importlib.util.module_from_spec(_snapshot_spec)
_snapshot_spec.loader.exec_module(_snapshot)

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    _snapshot.create_v1_schema(op.get_bind())


def downgrade() -> None:
    _snapshot.drop_v1_schema(op.get_bind())
