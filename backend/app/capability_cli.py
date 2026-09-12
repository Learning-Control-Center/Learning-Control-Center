from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from app.capability import drain_projection_invalidations, enqueue_full_capability_rebuild
from app.database import SessionLocal, initialize_database, run_migrations
from app.models import ProjectionInvalidation


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair deterministic V2 capability projections.")
    parser.add_argument(
        "--enqueue-full-rebuild",
        action="store_true",
        help="Queue every active competency before draining pending capability work.",
    )
    arguments = parser.parse_args()
    run_migrations()
    initialize_database()
    with SessionLocal() as db:
        queued = 0
        if arguments.enqueue_full_rebuild:
            queued = enqueue_full_capability_rebuild(db, source_fact_id="operator:full-rebuild")
            db.commit()
        processed = drain_projection_invalidations(db, recover_running=True)
        failures = db.scalars(
            select(ProjectionInvalidation).where(
                ProjectionInvalidation.status == "permanent_failure"
            )
        ).all()
    print(
        json.dumps(
            {
                "queued": queued,
                "processed": processed,
                "permanentFailures": [
                    {
                        "id": item.id,
                        "projectionKind": item.projection_kind,
                        "subjectType": item.subject_type,
                        "subjectId": item.subject_id,
                        "error": json.loads(item.error_json) if item.error_json else None,
                    }
                    for item in failures
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
