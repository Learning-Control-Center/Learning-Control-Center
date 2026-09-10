# ruff: noqa: E501
from __future__ import annotations

import uuid

NAMESPACE = uuid.UUID("c20b6799-bc36-5db4-92af-3eae1f16bcb3")
BUILTIN_CREATED_AT = 1_788_912_000_000

TECHNICAL_LEVELS = (
    (
        "unexposed",
        0,
        "Unexposed",
        "Explicit current information shows no meaningful encounter or performance; absence of evidence is insufficient.",
    ),
    (
        "familiar",
        1,
        "Familiar",
        "Can recognize the competency, explain its purpose and vocabulary, and follow representative examples without claiming task performance.",
    ),
    (
        "guided",
        2,
        "Guided",
        "Can complete representative bounded tasks when a guide supplies material steps or decisions.",
    ),
    (
        "independent",
        3,
        "Independent",
        "Can select an approach, complete representative tasks with ordinary reference documentation, and handle defined common failures.",
    ),
    (
        "strong",
        4,
        "Strong",
        "Repeatedly performs independently across varied non-trivial contexts, explains trade-offs, handles important failures, and produces maintainable outcomes.",
    ),
    (
        "advanced",
        5,
        "Advanced",
        "Independently handles complex or novel contexts, adapts alternatives, diagnoses systemic failures, and can design, review, or teach within scope.",
    ),
)

CEFR_LEVELS = (
    (
        "a1",
        1,
        "A1",
        "Can understand and use very basic familiar language and interact simply with substantial contextual support.",
    ),
    (
        "a2",
        2,
        "A2",
        "Can handle simple routine communication and describe immediate needs or familiar matters with limited complexity.",
    ),
    (
        "b1",
        3,
        "B1",
        "Can understand clear standard input, manage common independent situations, and produce connected communication on familiar topics.",
    ),
    (
        "b2",
        4,
        "B2",
        "Can understand complex material, interact with practical fluency, and communicate detailed positions across a broad range of topics.",
    ),
    (
        "c1",
        5,
        "C1",
        "Can understand demanding material, communicate fluently and flexibly, and produce well-structured language for complex purposes.",
    ),
    (
        "c2",
        6,
        "C2",
        "Can integrate difficult information from varied sources and communicate precisely with fine distinctions in highly complex contexts.",
    ),
)

CEFR_DIMENSIONS = ("speaking", "listening", "reading", "writing", "grammar", "vocabulary")


def stable_v2_id(value: str) -> str:
    return str(uuid.uuid5(NAMESPACE, value))


def builtin_scale_tables() -> dict[str, list[dict[str, object]]]:
    scales: list[dict[str, object]] = []
    dimensions: list[dict[str, object]] = []
    levels: list[dict[str, object]] = []
    for key, label, description in (
        ("technical", "Technical", "Ordinal technical capability."),
        ("cefr", "CEFR", "CEFR-aligned language capability."),
    ):
        scales.append(
            {
                "id": stable_v2_id(f"scale:{key}:v1"),
                "scale_stable_key": key,
                "scale_version": "v1",
                "display_name": label,
                "description": description,
                "created_at": BUILTIN_CREATED_AT,
            }
        )
    for order_index, key in enumerate(CEFR_DIMENSIONS):
        dimensions.append(
            {
                "id": stable_v2_id(f"scale:cefr:v1:dimension:{key}"),
                "scale_version_id": stable_v2_id("scale:cefr:v1"),
                "stable_key": key,
                "display_label": key.title(),
                "description": f"CEFR {key} capability evaluated independently.",
                "order_index": order_index,
            }
        )
    for scale_key, scale_levels in (("technical", TECHNICAL_LEVELS), ("cefr", CEFR_LEVELS)):
        for key, rank, label, description in scale_levels:
            levels.append(
                {
                    "id": stable_v2_id(f"scale:{scale_key}:v1:level:{key}"),
                    "scale_version_id": stable_v2_id(f"scale:{scale_key}:v1"),
                    "stable_key": key,
                    "ordinal_rank": rank,
                    "display_label": label,
                    "description": description,
                    "criterion_policy_reference": "criterion-evaluation-policy/v1",
                    "evidence_policy_reference": "evidence-qualification-policy/v1",
                }
            )
    return {
        "capability_scale_versions": scales,
        "capability_scale_dimensions": dimensions,
        "capability_scale_levels": levels,
    }
