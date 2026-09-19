#!/usr/bin/env python3
"""Run the frozen Roadmap journey performance protocol on a disposable stack."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

from product_fixture_harness import (
    DEFAULT_CLOCK,
    DEFAULT_TIMEZONE,
    FixtureRun,
    JsonObject,
    ScenarioName,
    _seed_roadmap_scale,
    _verify_authority_read_parity,
)

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=int, choices=(100, 250), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--warmups", type=int, default=5)
    args = parser.parse_args()
    scenario = cast(ScenarioName, f"roadmap-{args.fixture}")
    run = FixtureRun(scenario=scenario, timezone=DEFAULT_TIMEZONE, clock_at=DEFAULT_CLOCK)
    success = False
    try:
        client = run.start()
        projection = _seed_roadmap_scale(client, run, args.fixture)
        authority = client.request("GET", "/api/v2/authority")
        _verify_authority_read_parity(client, authority, "v2")
        metadata_path = run.write_metadata(client, authority, "v2")
        metadata = cast(JsonObject, json.loads(metadata_path.read_text(encoding="utf-8")))
        environment = os.environ.copy()
        environment.update(
            {
                "LCC_PRODUCT_BASE_URL": run.frontend_url,
                "LCC_ROADMAP_FIXTURE_SIZE": str(args.fixture),
                "LCC_ROADMAP_FIXTURE_HASH": str(metadata["fixtureHash"]),
                "LCC_ROADMAP_BUILD_HASH": str(metadata["productionBuildHash"]),
                "LCC_ROADMAP_REPOSITORY_REVISION": str(metadata["repositoryRevision"]),
                "LCC_ROADMAP_REPEATS": str(args.repeats),
                "LCC_ROADMAP_WARMUPS": str(args.warmups),
            }
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["node", "scripts/measure-roadmap.mjs", str(args.output.resolve())],
            cwd=FRONTEND,
            env=environment,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Roadmap measurement runner failed.")
        payload = json.loads(args.output.read_text(encoding="utf-8"))
        layout_result = subprocess.run(
            [str(ROOT / ".venv" / "bin" / "python"), "scripts/roadmap_layout_benchmark.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        bundle_result = subprocess.run(
            [
                str(ROOT / ".venv" / "bin" / "python"),
                "scripts/roadmap_bundle_inventory.py",
                "--dist",
                "frontend/dist",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        payload["pureLayout"] = json.loads(layout_result.stdout)
        payload["bundle"] = json.loads(bundle_result.stdout)
        pure_budget = {25: (5, 10), 100: (15, 30), 250: (40, 75)}
        payload["budgetVerdicts"]["pureLayout"] = all(
            item["medianMs"] <= pure_budget[item["size"]][0]
            and item["p95Ms"] <= pure_budget[item["size"]][1]
            for item in payload["pureLayout"]
        )
        bundle_bytes = payload["bundle"]["combinedGzipBytes"]
        payload["budgetVerdicts"]["bundle"] = (
            bundle_bytes <= 220 * 1024 and bundle_bytes <= 189305 * 1.20
        )
        payload["projection"] = {
            "layoutPolicyVersion": projection["layoutPolicyVersion"],
            "outputHash": projection["outputHash"],
            "nodeCount": len(projection["nodes"]),
            "edgeCount": len(projection["edges"]),
        }
        args.output.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if not all(payload["budgetVerdicts"].values()):
            raise RuntimeError(f"Roadmap performance budget failed: {payload['budgetVerdicts']}")
        success = True
        print(
            json.dumps(
                {"output": str(args.output), "budgetVerdicts": payload["budgetVerdicts"]},
                sort_keys=True,
            )
        )
        return 0
    finally:
        run.finish(success=success)
        if not success:
            print(f"Measurement failure artifacts retained at {run.root}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
