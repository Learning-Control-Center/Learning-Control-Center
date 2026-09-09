from __future__ import annotations

PORTABLE_SCHEMA_CURRENT = 2
PORTABLE_SCHEMA_READABLE = frozenset({1, 2})
PORTABLE_V2_FOUNDATION_TABLES = frozenset({"analysis_runs", "analysis_snapshots"})
PORTABLE_V1_FORBIDDEN_TABLES = frozenset(
    {"analysis_runs", "analysis_snapshots", "projection_invalidations"}
)
PORTABLE_V2_MANIFEST = {
    "includedCanonicalDomains": ["learning_state"],
    "includedImmutableHistory": ["analysis_runs", "analysis_snapshots"],
    "omittedRebuildableState": ["projection_invalidations"],
    "restoreActions": ["clear_projection_invalidations"],
}


def supports_portable_schema(version: int) -> bool:
    return version in PORTABLE_SCHEMA_READABLE
