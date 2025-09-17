"""Simple demonstration of streaming graph effects to the Cytoscape web UI.

Run this script with ``uv run python examples/webui_demo.py`` and open the
displayed URL (defaults to ``http://127.0.0.1:8765``) in a browser to watch
the graph structure update live while the program executes.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from typing import Tuple

try:
    from PIL import Image, ImageDraw
except ImportError as exc:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "Pillow is required for this demo. Install it with `uv pip install pillow`."
    ) from exc

from doeff import (
    Annotate,
    Await,
    Catch,
    Fail,
    Gather,
    Step,
    do,
    stream_program_to_webui,
    Program,
    ProgramInterpreter,
)
from doeff.types import EffectGenerator


def create_demo_image(size: Tuple[int, int] = (240, 160)) -> Image.Image:
    """Create a simple gradient image so the web UI can render a preview."""

    width, height = size
    image = Image.new("RGB", size, color="white")
    draw = ImageDraw.Draw(image)

    for x in range(width):
        ratio = x / max(width - 1, 1)
        # Smooth gradient using sine for nicer visuals
        intensity = int(128 + 127 * math.sin(ratio * math.pi))
        draw.line([(x, 0), (x, height)], fill=(intensity, 80, 255 - intensity))

    draw.text((16, 16), "Model Preview", fill="black")
    return image


@do
def demo_program() -> EffectGenerator[str]:
    """Large, animated pipeline illustrating parallel branches and recovery."""

    async def fetch_remote_config() -> dict[str, float]:
        await asyncio.sleep(0.2)
        return {"quality_threshold": 0.7}

    yield Step("Bootstrap", {"stage": "init"})
    yield Await(asyncio.sleep(0.2))
    yield Annotate({"timestamp": datetime.now(UTC).isoformat()})
    yield Await(asyncio.sleep(0.2))

    config = yield Await(fetch_remote_config())
    yield Step("Fetched remote config", {"stage": "init", **config})
    yield Await(asyncio.sleep(0.2))

    aggregated_metrics: list[dict[str, float | int | str]] = []

    @do
    def compute_feature(
        batch: int, name: str, duration: float
    ) -> EffectGenerator[tuple[str, float]]:
        yield Step(
            f"Compute {name}",
            {"stage": "feature", "batch": batch, "duration": duration},
        )
        yield Await(asyncio.sleep(0.2))

        async def worker() -> float:
            await asyncio.sleep(duration)
            return round(math.sin(duration * math.pi) + 1.2, 3)

        score = yield Await(worker())
        yield Annotate({"feature": name, "batch": batch, "score": score})
        yield Await(asyncio.sleep(0.2))
        return (name, score)

    def make_quality_evaluator(
        batch: int, feature_scores: dict[str, float]
    ) -> Program[str]:
        @do
        def evaluate_quality() -> EffectGenerator[str]:
            yield Step("Evaluate quality", {"stage": "quality", "batch": batch})
            yield Await(asyncio.sleep(0.2))
            total_score = sum(feature_scores.values())
            if total_score < config["quality_threshold"]:
                yield Annotate(
                    {
                        "total_score": total_score,
                        "severity": "warning",
                        "batch": batch,
                    }
                )
                yield Await(asyncio.sleep(0.2))
                yield Fail(ValueError("insufficient feature confidence"))
            yield Annotate(
                {
                    "total_score": total_score,
                    "severity": "ok",
                    "batch": batch,
                }
            )
            yield Await(asyncio.sleep(0.2))
            return "pass"

        return evaluate_quality()

    def handle_quality_error(batch: int, exc: Exception) -> Program[str]:
        @do
        def recovery() -> EffectGenerator[str]:
            yield Step(
                "Fallback quality",
                {"stage": "quality", "batch": batch, "reason": str(exc)},
            )
            yield Await(asyncio.sleep(0.2))
            yield Annotate({"strategy": "fallback", "batch": batch})
            yield Await(asyncio.sleep(0.2))
            return "fallback"

        return recovery()

    for batch in range(1, 11):
        yield Step(
            f"Load data batch {batch}",
            {"stage": "load", "batch": batch, "items": 256 + batch},
        )
        yield Await(asyncio.sleep(0.2))
        yield Annotate({"batch": batch, "timestamp": datetime.now(UTC).isoformat()})
        yield Await(asyncio.sleep(0.2))

        yield Step(
            f"Preprocess batch {batch}",
            {"stage": "preprocess", "batch": batch, "items": 128 + batch},
        )
        yield Await(asyncio.sleep(0.2))
        yield Annotate({"status": "ok", "batch": batch})
        yield Await(asyncio.sleep(0.2))

        feature_specs = [
            (f"embedding_{batch}", 0.15 + batch * 0.02),
            (f"segmentation_{batch}", 0.12 + batch * 0.018),
            (f"texture_{batch}", 0.1 + batch * 0.015),
            (f"saliency_{batch}", 0.09 + batch * 0.017),
        ]

        feature_results = yield Gather(
            *(
                compute_feature(batch, name, duration)
                for name, duration in feature_specs
            )
        )
        yield Await(asyncio.sleep(0.2))

        feature_scores = {name: score for name, score in feature_results}
        yield Step(
            {"feature_scores": feature_scores},
            {"stage": "feature", "batch": batch, "summary": "parallel"},
        )
        yield Await(asyncio.sleep(0.2))

        quality_status = yield Catch(
            make_quality_evaluator(batch, feature_scores),
            lambda exc: handle_quality_error(batch, exc),
        )
        yield Await(asyncio.sleep(0.2))

        batch_metrics = {
            "batch": batch,
            "accuracy": round(0.85 + 0.01 * batch, 3),
            "loss": round(0.2 - 0.01 * batch, 3),
            "quality": quality_status,
        }
        aggregated_metrics.append(batch_metrics)
        yield Step(batch_metrics, {"stage": "metrics", "batch": batch})
        yield Await(asyncio.sleep(0.2))

    preview_image = create_demo_image()
    yield Step(preview_image, {"stage": "inference", "notes": "PIL image"})
    yield Await(asyncio.sleep(0.2))

    yield Step(
        {
            "batches": len(aggregated_metrics),
            "avg_accuracy": round(
                sum(item["accuracy"] for item in aggregated_metrics)
                / len(aggregated_metrics),
                3,
            ),
        },
        {"stage": "summary"},
    )
    yield Await(asyncio.sleep(0.2))
    yield Annotate({"status": "complete"})
    yield Await(asyncio.sleep(0.2))

    return "Pipeline finished"


async def main() -> None:
    interpreter = ProgramInterpreter()
    program = stream_program_to_webui(demo_program())
    result = await interpreter.run(program)
    print(f"Result: {result.value}")
    print("Open http://127.0.0.1:8765 in your browser to view the graph.")
    print("Press Ctrl+C when you're done to shut down the web UI.")


if __name__ == "__main__":
    asyncio.run(main())
