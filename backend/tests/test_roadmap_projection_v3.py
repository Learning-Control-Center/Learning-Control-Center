from __future__ import annotations

import gzip
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.determinism import content_hash
from app.learning_graph.contracts import CompetencyEdgeProjectionPublicDTO
from app.profile_views import ProfileDomainPublicDTO, ProfileTargetProjectionPublicDTO
from app.roadmap_projection.layout import (
    ACTIVE_LAYOUT_POLICY,
    COLUMN_GAP,
    NODE_HEIGHT,
    NODE_WIDTH,
    ROW_GAP,
    LayoutNode,
    LayoutPosition,
    build_layout,
)
from app.roadmap_projection.service import (
    _current_analysis_gap_references,
    _specialization_parents,
)
from sqlalchemy.orm import Session

from scripts.roadmap_bundle_inventory import inventory


def _edge(
    index: int, source: str, target: str, edge_type: str = "prerequisite"
) -> CompetencyEdgeProjectionPublicDTO:
    return CompetencyEdgeProjectionPublicDTO(
        id=f"edge-{index}",
        edge_identity_id=f"edge-identity-{index}",
        edge_type=edge_type,
        source_competency_identity_id=source,
        target_competency_identity_id=target,
        source_semantic_definition_id=f"definition-{source}",
        target_semantic_definition_id=f"definition-{target}",
        order_index=index,
    )


def _target(
    node_id: str, domain_id: str, suffix: str = "overall"
) -> ProfileTargetProjectionPublicDTO:
    return ProfileTargetProjectionPublicDTO(
        id=f"profile-target-{node_id}-{suffix}",
        target_identity_id=f"target-{node_id}-{suffix}",
        stable_key=f"target.{node_id}.{suffix}",
        competency_identity_id=node_id,
        dimension_key=None if suffix == "overall" else suffix,
        dimension_id=None if suffix == "overall" else f"dimension-{suffix}",
        profile_domain_id=domain_id,
        scale_version_id="scale-v1",
        target_level_id="level-2",
        target_level_ordinal=2,
        priority="core",
        target_date=None,
        target_month=None,
        freshness_override_days=None,
        activated_at=1,
    )


def _fixture(
    size: int,
) -> tuple[
    list[LayoutNode],
    list[ProfileDomainPublicDTO],
    list[CompetencyEdgeProjectionPublicDTO],
]:
    domains = [
        ProfileDomainPublicDTO(
            id=f"domain-{index}",
            stable_key=f"domain.{index}",
            title=f"Domain {index}",
            description="",
            minimum_percent=None,
            maximum_percent=None,
            order_index=index,
        )
        for index in range(4)
    ]
    edges = [_edge(index, f"node-{index - 1}", f"node-{index}") for index in range(1, size)]
    edges.extend(
        _edge(size + index, f"node-{max(0, index - 2)}", f"node-{index}", "specialization")
        for index in range(2, size, 7)
    )
    nodes = [
        LayoutNode(
            node_id=f"node-{index}",
            stable_key=f"competency.{index:03d}",
            prerequisite_depth=index,
            specialization_depth=index // 7,
            presentation_parent_id=f"node-{index - 2}" if index >= 2 and index % 7 == 2 else None,
            targets=(
                ()
                if index % 9 == 0
                else (_target(f"node-{index}", domains[index % len(domains)].id),)
            ),
        )
        for index in range(size)
    ]
    return nodes, domains, edges


def _overlap(first: LayoutPosition, second: LayoutPosition) -> bool:
    return not (
        first.x + NODE_WIDTH + COLUMN_GAP <= second.x
        or second.x + NODE_WIDTH + COLUMN_GAP <= first.x
        or first.y + NODE_HEIGHT + ROW_GAP <= second.y
        or second.y + NODE_HEIGHT + ROW_GAP <= first.y
    )


@pytest.mark.parametrize("size", [25, 100, 250])
def test_v3_layout_is_deterministic_bounded_and_non_overlapping(size: int) -> None:
    nodes, domains, edges = _fixture(size)
    first = build_layout(ACTIVE_LAYOUT_POLICY, nodes, domains=domains, edges=edges)
    second = build_layout(ACTIVE_LAYOUT_POLICY, list(reversed(nodes)), domains=domains, edges=edges)

    assert first == second
    first_hash = content_hash({key: asdict(value) for key, value in sorted(first.items())})
    second_hash = content_hash({key: asdict(value) for key, value in sorted(second.items())})
    assert first_hash == second_hash
    assert max(item.x for item in first.values()) <= 64 + (size - 1) * (NODE_WIDTH + COLUMN_GAP)
    positioned = list(first.items())
    for index, (left_id, left) in enumerate(positioned):
        for right_id, right in positioned[index + 1 :]:
            assert not _overlap(left, right), f"{left_id} overlaps {right_id}"


def test_v3_layout_preserves_cross_domain_and_multi_dimension_targets() -> None:
    domains = [
        ProfileDomainPublicDTO("domain-a", "a", "A", "", None, None, 0),
        ProfileDomainPublicDTO("domain-b", "b", "B", "", None, None, 1),
    ]
    targets = (
        _target("shared", "domain-a", "reading"),
        _target("shared", "domain-b", "speaking"),
    )
    nodes = [LayoutNode("shared", "shared", 0, 0, None, targets)]

    result = build_layout(ACTIVE_LAYOUT_POLICY, nodes, domains=domains, edges=[])

    assert result["shared"].lane_id == "cross-domain-targets"
    assert result["shared"].lane_title == "Cross-domain targets"


def test_v3_layout_is_stable_for_diamonds_permutations_and_opposite_edges() -> None:
    domains = [
        ProfileDomainPublicDTO("domain-a", "a", "A", "", None, None, 0),
        ProfileDomainPublicDTO("domain-b", "b", "B", "", None, None, 1),
    ]
    nodes = [
        LayoutNode("root", "root", 0, 0, None, (_target("root", "domain-a"),)),
        LayoutNode("left", "left", 1, 0, None, (_target("left", "domain-a"),)),
        LayoutNode("right", "right", 1, 0, None, (_target("right", "domain-a"),)),
        LayoutNode("join", "join", 2, 0, None, (_target("join", "domain-b"),)),
    ]
    edges = [
        _edge(0, "root", "left"),
        _edge(1, "root", "right"),
        _edge(2, "left", "join"),
        _edge(3, "right", "join"),
        _edge(4, "join", "root", "supports"),
        _edge(5, "right", "left", "related"),
    ]

    canonical = build_layout(ACTIVE_LAYOUT_POLICY, nodes, domains=domains, edges=edges)
    permuted = build_layout(
        ACTIVE_LAYOUT_POLICY,
        [nodes[2], nodes[0], nodes[3], nodes[1]],
        domains=list(reversed(domains)),
        edges=list(reversed(edges)),
    )

    assert permuted == canonical
    assert canonical["root"].column == 0
    assert canonical["left"].column == canonical["right"].column == 1
    assert canonical["join"].column == 2


def test_v3_layout_keeps_same_column_specialization_families_contiguous() -> None:
    domain = ProfileDomainPublicDTO("domain-a", "a", "A", "", None, None, 0)
    nodes = [
        LayoutNode("root-b", "z-root", 0, 0, None, (_target("root-b", domain.id),)),
        LayoutNode("child-b", "a-child", 0, 1, "root-b", (_target("child-b", domain.id),)),
        LayoutNode("root-a", "a-root", 0, 0, None, (_target("root-a", domain.id),)),
        LayoutNode("child-a2", "z-child", 0, 1, "root-a", (_target("child-a2", domain.id),)),
        LayoutNode("child-a1", "b-child", 0, 1, "root-a", (_target("child-a1", domain.id),)),
        LayoutNode(
            "grandchild-a",
            "grandchild",
            0,
            2,
            "child-a1",
            (_target("grandchild-a", domain.id),),
        ),
    ]

    result = build_layout(ACTIVE_LAYOUT_POLICY, nodes, domains=[domain], edges=[])
    by_row = [node_id for node_id, position in sorted(result.items(), key=lambda item: item[1].row)]

    assert by_row == [
        "root-a",
        "child-a1",
        "grandchild-a",
        "child-a2",
        "root-b",
        "child-b",
    ]


def test_v3_layout_uses_cross_lane_prerequisites_for_crossing_reduction() -> None:
    domains = [
        ProfileDomainPublicDTO("domain-a", "a", "A", "", None, None, 0),
        ProfileDomainPublicDTO("domain-b", "b", "B", "", None, None, 1),
        ProfileDomainPublicDTO("domain-c", "c", "C", "", None, None, 2),
    ]
    nodes = [
        LayoutNode("source-top", "source-top", 0, 0, None, (_target("source-top", "domain-a"),)),
        LayoutNode(
            "source-bottom",
            "source-bottom",
            0,
            0,
            None,
            (_target("source-bottom", "domain-b"),),
        ),
        LayoutNode(
            "target-from-bottom",
            "a-target-from-bottom",
            1,
            0,
            None,
            (_target("target-from-bottom", "domain-c"),),
        ),
        LayoutNode(
            "target-from-top",
            "z-target-from-top",
            1,
            0,
            None,
            (_target("target-from-top", "domain-c"),),
        ),
    ]
    edges = [
        _edge(0, "source-top", "target-from-top"),
        _edge(1, "source-bottom", "target-from-bottom"),
    ]

    result = build_layout(ACTIVE_LAYOUT_POLICY, nodes, domains=domains, edges=edges)

    assert result["source-top"].row == result["source-bottom"].row == 0
    assert result["source-top"].y < result["source-bottom"].y
    assert result["target-from-top"].row == 0
    assert result["target-from-bottom"].row == 1


def test_foundation_partition_keeps_boundary_and_oversized_families_together() -> None:
    boundary_nodes = [
        LayoutNode(f"single-{index}", f"a-single-{index:02d}", 0, 0, None, ())
        for index in range(11)
    ]
    boundary_nodes.extend(
        [
            LayoutNode("family-root", "z-family-root", 0, 0, None, ()),
            LayoutNode("family-child", "z-family-child", 0, 1, "family-root", ()),
        ]
    )
    boundary = build_layout(ACTIVE_LAYOUT_POLICY, boundary_nodes, domains=[], edges=[])
    assert boundary["family-root"].lane_id == boundary["family-child"].lane_id
    assert boundary["family-root"].lane_id == "graph-foundations-2"

    oversized_nodes = [
        LayoutNode(
            f"family-{index}",
            f"family-{index:02d}",
            0,
            index,
            f"family-{index - 1}" if index else None,
            (),
        )
        for index in range(13)
    ]
    oversized = build_layout(ACTIVE_LAYOUT_POLICY, oversized_nodes, domains=[], edges=[])
    assert {item.lane_id for item in oversized.values()} == {"graph-foundations-1"}


def test_foundation_partition_orders_more_than_nine_lanes_numerically() -> None:
    nodes = [
        LayoutNode(f"node-{index:03d}", f"node-{index:03d}", 0, 0, None, ()) for index in range(121)
    ]

    result = build_layout(ACTIVE_LAYOUT_POLICY, nodes, domains=[], edges=[])
    lane_orders = {position.lane_id: position.lane_order for position in result.values()}

    assert lane_orders == {f"graph-foundations-{number}": number - 1 for number in range(1, 12)}
    assert result["node-108"].lane_id == "graph-foundations-10"
    assert result["node-108"].y < result["node-120"].y


def test_specialization_parent_selection_prefers_same_domain_then_stable_order() -> None:
    domain_a = ProfileDomainPublicDTO("domain-a", "a", "A", "", None, None, 0)
    domain_b = ProfileDomainPublicDTO("domain-b", "b", "B", "", None, None, 1)
    targets = {
        "parent-cross": _target("parent-cross", domain_b.id),
        "parent-z": _target("parent-z", domain_a.id),
        "parent-a": _target("parent-a", domain_a.id),
        "child": _target("child", domain_a.id),
    }
    domains = {
        node_id: domain_a if target.profile_domain_id == domain_a.id else domain_b
        for node_id, target in targets.items()
    }
    edges = [
        _edge(0, "parent-cross", "child", "specialization"),
        _edge(1, "parent-z", "child", "specialization"),
        _edge(2, "parent-a", "child", "specialization"),
    ]

    selected = _specialization_parents(
        list(reversed(edges)),
        domain_by_identity=domains,
        target_by_identity=targets,
        specialization_depths={node_id: 0 for node_id in targets},
    )

    assert selected["child"] == "parent-a"


def test_analysis_gap_references_exclude_missing_stale_and_wrong_profile(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.analysis.v3.public as analysis_public
    import app.analysis.v3.service as analysis_service

    monkeypatch.setattr(
        analysis_service,
        "current_analysis",
        lambda *_args, **_kwargs: {"status": "missing", "snapshot": None},
    )
    assert _current_analysis_gap_references(db, profile_version_id="profile-current") == {}

    monkeypatch.setattr(
        analysis_service,
        "current_analysis",
        lambda *_args, **_kwargs: {"status": "stale", "snapshot": {"id": "old"}},
    )
    assert _current_analysis_gap_references(db, profile_version_id="profile-current") == {}

    monkeypatch.setattr(
        analysis_service,
        "current_analysis",
        lambda *_args, **_kwargs: {"status": "current", "snapshot": {"id": "wrong"}},
    )
    monkeypatch.setattr(
        analysis_public,
        "load_public_analysis_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(
            target_profile_version_id="profile-old", gaps=(), snapshot_id="wrong"
        ),
    )
    assert _current_analysis_gap_references(db, profile_version_id="profile-current") == {}

    monkeypatch.setattr(
        analysis_public,
        "load_public_analysis_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(
            target_profile_version_id="profile-current",
            snapshot_id="snapshot-current",
            gaps=(
                SimpleNamespace(
                    stable_key="gap.target.current",
                    comparison_status="below_target",
                    target_fact=SimpleNamespace(target_identity_id="target-current"),
                ),
            ),
        ),
    )
    assert _current_analysis_gap_references(db, profile_version_id="profile-current") == {
        "target-current": {
            "snapshotId": "snapshot-current",
            "gapStableKey": "gap.target.current",
            "comparisonStatus": "below_target",
        }
    }


def test_roadmap_performance_protocol_is_pinned_and_machine_readable() -> None:
    root = Path(__file__).resolve().parents[2]
    protocol = json.loads(
        (root / "frontend/performance/roadmap-v3-protocol.json").read_text(encoding="utf-8")
    )

    assert protocol["baseline"]["phase2Ref"] == "aee26be"
    assert protocol["fixtures"]["sizes"] == [25, 100, 250]
    assert protocol["sampling"] == {
        "warmups": 5,
        "repeats": 30,
        "statistics": ["median", "p95"],
        "cacheModes": ["cold", "warm"],
        "longTaskThresholdMs": 50,
    }
    assert protocol["reviewRules"]["failedMetricBlocksCheckpoint3"] is True
    assert protocol["fixtures"]["requiredMetadata"][-4:] == [
        "roadmapProjection.layoutPolicyVersion",
        "roadmapProjection.outputHash",
        "roadmapProjection.nodeCount",
        "roadmapProjection.edgeCount",
    ]
    assert protocol["measurementRunnerContract"]["resultSchema"]["percentileMethod"] == (
        "nearest-rank: sorted[ceil(0.95 * sampleCount) - 1]"
    )
    assert protocol["budgets"]["navigationStartToRoadmapReadyMs"]["stress250"]
    assert set(protocol["budgets"]) == {
        "navigationStartToRoadmapReadyMs",
        "pureLayoutMs",
        "selectionToSettledDetailMs",
        "longTasks",
        "reactCommitsPerSelection",
        "journeyNodeRendersPerSelection",
        "compressedRoadmapRouteKiB",
    }


def test_bundle_inventory_follows_manifest_static_imports(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    manifest_path = dist / ".vite" / "manifest.json"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    manifest_path.parent.mkdir(parents=True)
    files = {
        "app.js": b"shared application",
        "roadmap.js": b"roadmap route",
        "react-flow.js": b"static roadmap dependency",
    }
    for name, content in files.items():
        (assets / name).write_bytes(content)
    manifest_path.write_text(
        json.dumps(
            {
                "index.html": {"file": "assets/app.js", "isEntry": True},
                "src/features/roadmap/v2.ts": {
                    "file": "assets/roadmap.js",
                    "isDynamicEntry": True,
                    "imports": ["index.html", "_react-flow.js"],
                },
                "_react-flow.js": {"file": "assets/react-flow.js"},
            }
        ),
        encoding="utf-8",
    )

    result = inventory(dist, roadmap_source="src/features/roadmap/v2.ts")

    assert result["roadmapRoute"] == {
        "files": ["assets/react-flow.js", "assets/roadmap.js"],
        "rawBytes": len(files["react-flow.js"]) + len(files["roadmap.js"]),
        "gzipBytes": len(gzip.compress(files["react-flow.js"], compresslevel=9, mtime=0))
        + len(gzip.compress(files["roadmap.js"], compresslevel=9, mtime=0)),
    }
    assert result["sharedApplication"] == {
        "files": ["assets/app.js"],
        "rawBytes": len(files["app.js"]),
        "gzipBytes": len(gzip.compress(files["app.js"], compresslevel=9, mtime=0)),
    }
