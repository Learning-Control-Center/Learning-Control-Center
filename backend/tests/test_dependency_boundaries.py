from __future__ import annotations

import ast
from pathlib import Path


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_v1_policy_has_no_persistence_or_framework_dependencies() -> None:
    root = Path(__file__).resolve().parents[1]
    imports = _imports(root / "app" / "recommendation" / "v1_policy.py")
    forbidden = {
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "app.analytics",
        "app.models",
        "app.sessions",
        "app.settings_api",
        "app.roadmap",
    }
    assert not {
        name
        for name in imports
        if any(name == item or name.startswith(f"{item}.") for item in forbidden)
    }


def test_analysis_layer_does_not_depend_on_recommendation_policy() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "analysis"
    imports = set().union(*(_imports(path) for path in root.rglob("*.py")))
    assert not {name for name in imports if name.startswith("app.recommendation")}


def test_recommendation_v2_policy_only_consumes_its_public_input_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    imports = _imports(root / "app" / "recommendation" / "v2" / "policy.py")
    forbidden = {
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "app.analysis",
        "app.analytics",
        "app.curriculum",
        "app.learning_graph",
        "app.models",
        "app.profile_views",
        "app.projects",
        "app.roadmap",
        "app.sessions",
    }
    assert not {
        name
        for name in imports
        if any(name == item or name.startswith(f"{item}.") for item in forbidden)
    }


def test_recommendation_v2_candidate_generation_is_orm_free_and_dto_only() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "app" / "recommendation" / "v2" / "candidates.py"
    imports = _imports(path)
    forbidden = {
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "app.models",
        "app.analysis.v3.service",
        "app.curriculum.service",
        "app.learning_graph.service",
        "app.projects.service",
        "app.sessions",
    }
    assert not {
        name
        for name in imports
        if any(name == item or name.startswith(f"{item}.") for item in forbidden)
    }
    assert "Any" not in path.read_text()


def test_v2_profile_authoring_does_not_depend_on_v1_roadmap_services() -> None:
    root = Path(__file__).resolve().parents[1]
    imports = _imports(root / "app" / "v2_profiles.py")
    assert "app.roadmap" not in imports
