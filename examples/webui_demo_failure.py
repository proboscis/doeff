"""Failure-oriented demo that builds a sizeable graph before a branch error.

This example purposefully drives ~50 graph nodes (with branching via ``Gather``)
so that the Cytoscape UI has a richer structure to animate. The final batch
triggers a ``ValueError`` inside one gather branch, showcasing how
``stream_program_to_webui`` highlights branch-local failures.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from typing import Iterable, Tuple

from doeff import (
    Annotate,
    Await,
    Gather,
    Step,
    do,
    stream_program_to_webui,
    ProgramInterpreter,
)
from doeff.types import EffectGenerator


def feature_specs_for(batch: int) -> Iterable[Tuple[str, float, bool]]:
    base = 0.05
    return (
        (f"embedding_{batch}", base + 0.010 * batch, False),
        (f"segmentation_{batch}", base + 0.012 * batch, False),
        (f"texture_{batch}", base + 0.008 * batch, False),
        (f"saliency_{batch}", base + 0.009 * batch, False),
        (f"normals_{batch}", base + 0.011 * batch, False),
        (f"anomaly_{batch}", base + 0.015 * batch, batch == 6),
    )


@do
def compute_feature(
    batch: int, name: str, delay: float, *, should_fail: bool
) -> EffectGenerator[tuple[str, float]]:
    yield Step(
        f"Compute {name}",
        {"stage": "feature", "batch": batch, "duration": delay},
    )
    yield Await(asyncio.sleep(delay))
    score = round(math.sin(batch + delay) + 1.5, 3)
    if should_fail:
        yield Annotate(
            {
                "feature": name,
                "score": score,
                "batch": batch,
                "status": "failing",
            }
        )
        raise ValueError(f"branch failure while computing {name}")
    yield Annotate({"feature": name, "score": score, "batch": batch})
    return (name, score)


@do
def failing_program() -> EffectGenerator[str]:
    yield Step("Boot demo", {"stage": "init"})
    yield Annotate({"timestamp": datetime.now(UTC).isoformat()})

    aggregated: list[dict[str, float | int | str]] = []

    for batch in range(1, 7):
        yield Step(
            f"Load batch {batch}",
            {"stage": "load", "batch": batch, "items": 256 + batch},
        )
        yield Await(asyncio.sleep(0.05))
        yield Annotate({"batch": batch, "status": "loaded"})

        yield Step(
            f"Preprocess batch {batch}",
            {"stage": "preprocess", "batch": batch, "items": 192 + batch},
        )
        yield Await(asyncio.sleep(0.05))
        yield Annotate({"batch": batch, "status": "preprocessed"})

        try:
            feature_results = yield Gather(
                *(
                    compute_feature(
                        batch,
                        name,
                        delay,
                        should_fail=should_fail,
                    )
                    for name, delay, should_fail in feature_specs_for(batch)
                )
            )
        except Exception as exc:
            yield Step(
                f"Branch failure for batch {batch}",
                {
                    "stage": "feature",
                    "batch": batch,
                    "error": str(exc),
                },
            )
            yield Annotate(
                {
                    "batch": batch,
                    "status": "branch-error",
                }
            )
            raise

        feature_map = {name: score for name, score in feature_results}
        yield Step(
            {"features": feature_map},
            {"stage": "feature", "batch": batch, "summary": "gather"},
        )
        yield Annotate({"batch": batch, "feature_count": len(feature_map)})

        yield Step(
            f"Validate batch {batch}",
            {"stage": "validation", "batch": batch},
        )
        yield Await(asyncio.sleep(0.03))

        quality = round(sum(feature_map.values()) / len(feature_map), 3)
        metrics = {
            "batch": batch,
            "quality": quality,
            "status": "pass" if quality > 1.2 else "warn",
        }
        aggregated.append(metrics)
        yield Annotate(metrics)

    yield Step(
        {
            "batches": len(aggregated),
            "avg_quality": round(
                sum(item["quality"] for item in aggregated) / len(aggregated),
                3,
            ),
        },
        {"stage": "summary"},
    )
    yield Annotate({"status": "raising"})

    raise ValueError("Demo error: synthesized failure for UI highlighting")


async def main() -> None:
    interpreter = ProgramInterpreter()
    program = stream_program_to_webui(failing_program())
    result = await interpreter.run(program)
    if result.is_err:
        print("Program failed as expected; check the web UI for the red error node.")


if __name__ == "__main__":
    asyncio.run(main())
