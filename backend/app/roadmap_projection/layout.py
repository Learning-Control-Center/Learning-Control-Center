from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.learning_graph.contracts import CompetencyEdgeProjectionPublicDTO
from app.profile_views import ProfileDomainPublicDTO, ProfileTargetProjectionPublicDTO

FROZEN_LAYOUT_POLICY = "roadmap-layout/v2.0"
ACTIVE_LAYOUT_POLICY = "roadmap-layout/v3.0"
REGISTERED_LAYOUT_POLICIES = frozenset({FROZEN_LAYOUT_POLICY, ACTIVE_LAYOUT_POLICY})

NODE_WIDTH = 240
NODE_HEIGHT = 140
COLUMN_GAP = 80
ROW_GAP = 40
LANE_PADDING_X = 64
LANE_PADDING_Y = 64
LANE_GAP = 56
FOUNDATION_LANE_DENSITY = 12
TARGET_PRIORITY_ORDER = {
    "critical": 0,
    "core": 1,
    "important": 2,
    "supporting": 3,
    "optional": 4,
}


@dataclass(frozen=True)
class LayoutNode:
    node_id: str
    stable_key: str
    prerequisite_depth: int
    specialization_depth: int
    presentation_parent_id: str | None
    targets: tuple[ProfileTargetProjectionPublicDTO, ...]


@dataclass(frozen=True)
class LayoutPosition:
    x: int
    y: int
    lane_id: str
    lane_title: str
    lane_order: int
    column: int
    row: int


def require_registered_layout_policy(version: str) -> str:
    if version not in REGISTERED_LAYOUT_POLICIES:
        raise ValueError(f"Unregistered Roadmap layout policy: {version}")
    return version


def _v2_positions(
    nodes: Sequence[LayoutNode],
    *,
    domains: Sequence[ProfileDomainPublicDTO],
) -> dict[str, LayoutPosition]:
    """Frozen Phase-2 geometry used only to verify declared portable checkpoints."""
    domain_order = {item.id: index for index, item in enumerate(domains)}
    layer_counts: dict[tuple[int, int], int] = {}
    domain_stride = (max((item.prerequisite_depth for item in nodes), default=0) + 2) * 300
    positions: dict[str, LayoutPosition] = {}
    for node in nodes:
        domain_id = node.targets[-1].profile_domain_id if node.targets else None
        domain_index = (
            domain_order.get(domain_id, len(domain_order))
            if domain_id is not None
            else len(domain_order)
        )
        key = (domain_index, node.prerequisite_depth)
        row = layer_counts.get(key, 0)
        layer_counts[key] = row + 1
        lane_title = (
            domains[domain_index].title if domain_index < len(domains) else "Graph foundations"
        )
        positions[node.node_id] = LayoutPosition(
            x=domain_index * domain_stride + node.prerequisite_depth * 300,
            y=row * 200,
            lane_id=(domain_id or "graph-foundations"),
            lane_title=lane_title,
            lane_order=domain_index,
            column=node.prerequisite_depth,
            row=row,
        )
    return positions


def _target_lane_id(node: LayoutNode) -> str | None:
    domain_ids = {target.profile_domain_id for target in node.targets}
    if len(domain_ids) > 1:
        return "cross-domain-targets"
    return next(iter(domain_ids), None)


def _foundation_partitions(nodes: Sequence[LayoutNode]) -> dict[str, str]:
    foundations = {node.node_id: node for node in nodes if not node.targets}
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in foundations}
    for node in foundations.values():
        parent_id = node.presentation_parent_id
        if parent_id is not None and parent_id in foundations:
            adjacency[node.node_id].add(parent_id)
            adjacency[parent_id].add(node.node_id)

    def node_key(node_id: str) -> tuple[int, int, str, str]:
        return (
            foundations[node_id].prerequisite_depth,
            foundations[node_id].specialization_depth,
            foundations[node_id].stable_key,
            node_id,
        )

    components: list[list[str]] = []
    remaining = set(foundations)
    while remaining:
        seed = min(remaining, key=node_key)
        frontier = [seed]
        found_component: set[str] = set()
        while frontier:
            current = frontier.pop()
            if current in found_component:
                continue
            found_component.add(current)
            frontier.extend(
                sorted(adjacency[current] - found_component, key=node_key, reverse=True)
            )
        remaining -= found_component
        components.append(sorted(found_component, key=node_key))

    lane_number = 1
    lane_size = 0
    lane_by_node: dict[str, str] = {}
    for component in components:
        if lane_size and lane_size + len(component) > FOUNDATION_LANE_DENSITY:
            lane_number += 1
            lane_size = 0
        lane_id = f"graph-foundations-{lane_number}"
        for node_id in component:
            lane_by_node[node_id] = lane_id
        lane_size += len(component)
        # An oversized family remains intact in one lane; the next component
        # starts a new lane instead of compounding the overflow.
        if lane_size >= FOUNDATION_LANE_DENSITY:
            lane_number += 1
            lane_size = 0
    return lane_by_node


def _specialization_family_order(
    candidates: Sequence[LayoutNode],
    children_by_parent: dict[str, list[LayoutNode]],
    sort_key: Callable[[LayoutNode], tuple[float, int, int, str, str]],
) -> list[LayoutNode]:
    candidate_ids = {node.node_id for node in candidates}
    roots = [
        node
        for node in candidates
        if node.presentation_parent_id is None or node.presentation_parent_id not in candidate_ids
    ]
    ordered: list[LayoutNode] = []
    emitted: set[str] = set()

    def emit(node: LayoutNode) -> None:
        if node.node_id in emitted:
            return
        emitted.add(node.node_id)
        ordered.append(node)
        for child in sorted(children_by_parent[node.node_id], key=sort_key):
            emit(child)

    for root in sorted(roots, key=sort_key):
        emit(root)
    for remaining in sorted(candidates, key=sort_key):
        emit(remaining)
    return ordered


def _v3_positions(
    nodes: Sequence[LayoutNode],
    *,
    domains: Sequence[ProfileDomainPublicDTO],
    edges: Sequence[CompetencyEdgeProjectionPublicDTO],
) -> dict[str, LayoutPosition]:
    domain_by_id = {item.id: item for item in domains}
    foundation_lane_by_node = _foundation_partitions(nodes)
    lane_by_node = {
        node.node_id: _target_lane_id(node) or foundation_lane_by_node[node.node_id]
        for node in nodes
    }
    foundation_ids = sorted(
        set(foundation_lane_by_node.values()),
        key=lambda lane_id: int(lane_id.rsplit("-", 1)[1]),
    )
    lane_ids = ["cross-domain-targets"]
    lane_ids.extend(item.id for item in domains)
    lane_ids.extend(foundation_ids)
    used_lane_ids = {lane_by_node[node.node_id] for node in nodes}
    lane_ids = [lane_id for lane_id in lane_ids if lane_id in used_lane_ids]
    lane_order = {lane_id: index for index, lane_id in enumerate(lane_ids)}

    prerequisite_predecessors: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge.edge_type == "prerequisite":
            prerequisite_predecessors[edge.target_competency_identity_id].add(
                edge.source_competency_identity_id
            )

    ordered_by_lane_column: dict[tuple[str, int], list[LayoutNode]] = defaultdict(list)
    row_by_node: dict[str, int] = {}
    for node in nodes:
        ordered_by_lane_column[(lane_by_node[node.node_id], node.prerequisite_depth)].append(node)

    lane_max_rows: dict[str, int] = {}
    for (lane_id, _column), lane_nodes in ordered_by_lane_column.items():
        lane_max_rows[lane_id] = max(lane_max_rows.get(lane_id, 0), len(lane_nodes))
    lane_top: dict[str, int] = {}
    cursor = 0
    for lane_id in lane_ids:
        lane_top[lane_id] = cursor
        lane_height = LANE_PADDING_Y * 2 + max(1, lane_max_rows[lane_id]) * NODE_HEIGHT
        lane_height += max(0, lane_max_rows[lane_id] - 1) * ROW_GAP
        cursor += lane_height + LANE_GAP

    def placed_y(node_id: str) -> int:
        return (
            lane_top[lane_by_node[node_id]]
            + LANE_PADDING_Y
            + row_by_node[node_id] * (NODE_HEIGHT + ROW_GAP)
        )

    for column in sorted({node.prerequisite_depth for node in nodes}):
        for lane_id in lane_ids:
            candidates = ordered_by_lane_column.get((lane_id, column), [])
            children_by_parent: dict[str, list[LayoutNode]] = defaultdict(list)
            for node in candidates:
                parent_id = node.presentation_parent_id
                if parent_id is not None:
                    children_by_parent[parent_id].append(node)

            def crossing_key(node: LayoutNode) -> tuple[float, int, int, str, str]:
                anchors = [
                    placed_y(item)
                    for item in prerequisite_predecessors.get(node.node_id, set())
                    if item in row_by_node
                ]
                if node.presentation_parent_id in row_by_node:
                    assert node.presentation_parent_id is not None
                    anchors.append(placed_y(node.presentation_parent_id))
                barycenter = sum(anchors) / len(anchors) if anchors else 1_000_000.0
                target_priority = min(
                    (TARGET_PRIORITY_ORDER[target.priority] for target in node.targets), default=4
                )
                return (
                    barycenter,
                    node.specialization_depth,
                    target_priority,
                    node.stable_key,
                    node.node_id,
                )

            # A specialization family may share a prerequisite column. Sort the
            # family roots by the prior-column barycenter, then emit descendants
            # immediately after their chosen presentation parent. This keeps the
            # hierarchy visually contiguous without changing graph authority.
            ordered = _specialization_family_order(candidates, children_by_parent, crossing_key)

            for row, node in enumerate(ordered):
                row_by_node[node.node_id] = row

    def lane_title(lane_id: str) -> str:
        if lane_id == "cross-domain-targets":
            return "Cross-domain targets"
        if lane_id.startswith("graph-foundations-"):
            number = int(lane_id.rsplit("-", 1)[1])
            return (
                "Graph foundations" if len(foundation_ids) == 1 else f"Graph foundations {number}"
            )
        return domain_by_id[lane_id].title

    positions: dict[str, LayoutPosition] = {}
    for node in nodes:
        lane_id = lane_by_node[node.node_id]
        row = row_by_node[node.node_id]
        positions[node.node_id] = LayoutPosition(
            x=LANE_PADDING_X + node.prerequisite_depth * (NODE_WIDTH + COLUMN_GAP),
            y=lane_top[lane_id] + LANE_PADDING_Y + row * (NODE_HEIGHT + ROW_GAP),
            lane_id=lane_id,
            lane_title=lane_title(lane_id),
            lane_order=lane_order[lane_id],
            column=node.prerequisite_depth,
            row=row,
        )
    return positions


def build_layout(
    version: str,
    nodes: Sequence[LayoutNode],
    *,
    domains: Sequence[ProfileDomainPublicDTO],
    edges: Sequence[CompetencyEdgeProjectionPublicDTO],
) -> dict[str, LayoutPosition]:
    require_registered_layout_policy(version)
    canonical_domains = tuple(sorted(domains, key=lambda item: (item.order_index, item.id)))
    canonical_edges = tuple(
        sorted(
            edges,
            key=lambda item: (
                item.edge_type,
                item.order_index,
                item.source_competency_identity_id,
                item.target_competency_identity_id,
                item.id,
            ),
        )
    )
    if version == FROZEN_LAYOUT_POLICY:
        return _v2_positions(nodes, domains=canonical_domains)
    return _v3_positions(nodes, domains=canonical_domains, edges=canonical_edges)


def layout_policy_contract() -> dict[str, object]:
    return {
        "version": ACTIVE_LAYOUT_POLICY,
        "nodeSlot": {"width": NODE_WIDTH, "height": NODE_HEIGHT},
        "columnGap": COLUMN_GAP,
        "rowGap": ROW_GAP,
        "lanePadding": {"x": LANE_PADDING_X, "y": LANE_PADDING_Y},
        "laneGap": LANE_GAP,
        "foundationLaneDensity": FOUNDATION_LANE_DENSITY,
    }
