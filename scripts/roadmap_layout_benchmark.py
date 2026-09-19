#!/usr/bin/env python3
"""Measure the pure Roadmap v3 layout independently of database/API setup."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.learning_graph.contracts import CompetencyEdgeProjectionPublicDTO  # noqa: E402
from app.profile_views import ProfileDomainPublicDTO, ProfileTargetProjectionPublicDTO  # noqa: E402
from app.roadmap_projection.layout import (  # noqa: E402
    ACTIVE_LAYOUT_POLICY,
    LayoutNode,
    build_layout,
)


def _inputs(
    size: int,
) -> tuple[
    list[LayoutNode],
    list[ProfileDomainPublicDTO],
    list[CompetencyEdgeProjectionPublicDTO],
]:
    domains = [
        ProfileDomainPublicDTO(
            f"domain-{index}", str(index), f"Domain {index}", "", None, None, index
        )
        for index in range(4)
    ]
    foundation_count = 4
    targets = [
        ProfileTargetProjectionPublicDTO(
            f"profile-target-{index}",
            f"target-{index}",
            f"target.{index}",
            f"node-{index}",
            None,
            None,
            domains[min(3, ((index - foundation_count) * 4) // (size - foundation_count))].id,
            "scale-v1",
            "level-2",
            2,
            "core",
            None,
            None,
            None,
            1,
        )
        for index in range(foundation_count, size)
    ]
    target_by_node = {target.competency_identity_id: target for target in targets}
    branches = {
        domain_index: [
            index
            for index in range(foundation_count, size)
            if min(3, ((index - foundation_count) * 4) // (size - foundation_count)) == domain_index
        ]
        for domain_index in range(4)
    }
    edge_pairs = [(index - 1, index) for index in range(1, foundation_count)]
    specialization_pairs: list[tuple[int, int]] = []
    for branch in branches.values():
        edge_pairs.append((foundation_count - 1, branch[0]))
        if len(branch) >= 4:
            edge_pairs.extend(
                [
                    (branch[0], branch[1]),
                    (branch[0], branch[2]),
                    (branch[1], branch[3]),
                    (branch[2], branch[3]),
                ]
            )
            edge_pairs.extend((branch[index - 1], branch[index]) for index in range(4, len(branch)))
        else:
            edge_pairs.extend((branch[index - 1], branch[index]) for index in range(1, len(branch)))
        if len(branch) >= 3:
            specialization_pairs.extend([(branch[0], branch[2]), (branch[1], branch[2])])
    edges = [
        CompetencyEdgeProjectionPublicDTO(
            f"edge-{index}",
            f"edge-identity-{index}",
            "prerequisite",
            f"node-{source}",
            f"node-{target}",
            f"definition-{source}",
            f"definition-{target}",
            index,
        )
        for index, (source, target) in enumerate(edge_pairs)
    ]
    edges.extend(
        CompetencyEdgeProjectionPublicDTO(
            f"specialization-{index}",
            f"specialization-identity-{index}",
            "specialization",
            f"node-{source}",
            f"node-{target}",
            f"definition-{source}",
            f"definition-{target}",
            len(edges) + index,
        )
        for index, (source, target) in enumerate(specialization_pairs)
    )
    predecessors: dict[int, list[int]] = {index: [] for index in range(size)}
    for source, target in edge_pairs:
        predecessors[target].append(source)
    depths: dict[int, int] = {}

    def depth(index: int) -> int:
        if index not in depths:
            depths[index] = max((depth(source) + 1 for source in predecessors[index]), default=0)
        return depths[index]

    nodes = [
        LayoutNode(
            f"node-{index}",
            f"competency.{index:03d}",
            depth(index),
            1 if any(target == index for _source, target in specialization_pairs) else 0,
            next(
                (f"node-{source}" for source, target in specialization_pairs if target == index),
                None,
            ),
            (target_by_node[f"node-{index}"],) if f"node-{index}" in target_by_node else (),
        )
        for index in range(size)
    ]
    return nodes, domains, edges


def benchmark(size: int, *, warmups: int, repeats: int) -> dict[str, float | int | str]:
    nodes, domains, edges = _inputs(size)
    for _ in range(warmups):
        build_layout(ACTIVE_LAYOUT_POLICY, nodes, domains=domains, edges=edges)
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        build_layout(ACTIVE_LAYOUT_POLICY, nodes, domains=domains, edges=edges)
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
    return {
        "policy": ACTIVE_LAYOUT_POLICY,
        "size": size,
        "warmups": warmups,
        "repeats": repeats,
        "medianMs": round(statistics.median(samples), 3),
        "p95Ms": round(ordered[p95_index], 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="+", type=int, default=[25, 100, 250])
    parser.add_argument("--warmups", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=30)
    arguments = parser.parse_args()
    print(
        json.dumps(
            [
                benchmark(size, warmups=arguments.warmups, repeats=arguments.repeats)
                for size in arguments.sizes
            ],
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
