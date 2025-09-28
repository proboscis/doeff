"""High-level helpers for interacting with Seedream models."""

from __future__ import annotations

import base64
import io
import time
from collections.abc import Mapping
from typing import Any


from PIL import Image

from doeff import Ask, AtomicGet, AtomicUpdate, Await, Catch, EffectGenerator, Fail, Log, Retry, Step, do

from .client import SeedreamClient, get_seedream_client, track_api_call
from .costs import CostEstimate, calculate_cost
from .types import SeedreamImage, SeedreamImageEditResult

DEFAULT_MODEL = "doubao-seedream-4-0-250828"
DEFAULT_RESPONSE_FORMAT = "b64_json"
ALLOWED_OVERRIDE_KEYS = {
    "size",
    "response_format",
    "watermark",
    "stream",
    "seed",
    "image",
    "sequential_image_generation",
    "sequential_image_generation_options",
    "guidance_scale",
}


def _encode_image(image: Image.Image) -> str:
    """Encode a PIL image into the data URI format expected by the API.

    Ark accepts either publicly reachable URLs or ``data:image/...;base64``
    payloads. Encoding on the fly keeps the public API ergonomic while allowing
    tests to supply in-memory images.
    """

    image_format = (image.format or "PNG").upper()
    if image_format not in {"PNG", "JPEG", "JPG", "WEBP"}:
        image_format = "PNG"
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/{image_format.lower()};base64,{encoded}"


def _coerce_timeout(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive guard
        return None


def _build_payload(
    *,
    prompt: str,
    model: str,
    images: list[Image.Image] | None,
    generation_config_overrides: dict[str, Any] | None,
) -> tuple[dict[str, Any], float | None]:
    """Translate public arguments into the JSON payload Ark expects.

    The helper performs three tasks: populate the required ``model`` and
    ``prompt`` fields, forward acknowledged overrides, and serialise reference
    images when present. It returns the payload plus an optional request timeout
    extracted from ``generation_config_overrides``.
    """
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
    }

    overrides = generation_config_overrides or {}

    for key in ALLOWED_OVERRIDE_KEYS:
        if key in overrides and overrides[key] is not None:
            payload[key] = overrides[key]

    if "response_format" not in payload:
        payload["response_format"] = DEFAULT_RESPONSE_FORMAT

    if "watermark" not in payload:
        payload["watermark"] = overrides.get("watermark", True)

    if images:
        encoded_images = [_encode_image(image) for image in images]
        payload["image"] = encoded_images

    timeout = _coerce_timeout(overrides.get("timeout"))

    return payload, timeout


def _decode_images(
    response: dict[str, Any],
    *,
    expected_format: str,
) -> list[SeedreamImage]:
    """Normalise the Ark response into ``SeedreamImage`` instances.

    The API can return either inline base64 data or temporary URLs depending on
    ``response_format``. The result list matches the ``data`` list in the raw
    payload and preserves optional metadata such as the resolved size.
    """
    data = response.get("data")
    if not isinstance(data, list):
        raise ValueError("Seedream response did not include image data")

    results: list[SeedreamImage] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        b64_value = item.get("b64_json")
        url_value = item.get("url")
        size_value = item.get("size") if isinstance(item.get("size"), str) else None
        image_bytes: bytes | None = None
        if isinstance(b64_value, str):
            try:
                image_bytes = base64.b64decode(b64_value)
            except (TypeError, ValueError) as exc:  # pragma: no cover - defensive guard
                raise ValueError("Failed to decode Seedream base64 image payload") from exc
        results.append(
            SeedreamImage(
                image_bytes=image_bytes,
                mime_type="image/jpeg" if expected_format == "b64_json" else "image/jpeg",
                url=url_value if isinstance(url_value, str) else None,
                size=size_value,
            )
        )
    if not results:
        raise ValueError("Seedream response did not include any decodable images")
    return results


@do
def edit_image__seedream4(
    prompt: str,
    model: str = DEFAULT_MODEL,
    images: list[Image.Image] | None = None,
    max_output_tokens: int = 8192,  # noqa: ARG001 - kept for signature parity
    temperature: float = 0.9,  # noqa: ARG001 - kept for signature parity
    top_p: float | None = None,  # noqa: ARG001 - kept for signature parity
    top_k: int | None = None,  # noqa: ARG001 - kept for signature parity
    candidate_count: int = 1,  # noqa: ARG001 - kept for signature parity
    system_instruction: str | None = None,  # noqa: ARG001 - kept for signature parity
    safety_settings: list[dict[str, Any]] | None = None,  # noqa: ARG001 - kept for parity
    tools: list[dict[str, Any]] | None = None,  # noqa: ARG001 - kept for signature parity
    tool_config: dict[str, Any] | None = None,  # noqa: ARG001 - kept for parity
    response_modalities: list[str] | None = None,  # noqa: ARG001 - kept for parity
    generation_config_overrides: dict[str, Any] | None = None,
    max_retries: int = 3,
) -> EffectGenerator[SeedreamImageEditResult]:
    """Generate or edit an image using the Seedream 4.0 model.

    Parameters mirror :func:`doeff_gemini.structured_llm.edit_image__gemini` so call
    sites can switch providers without needing conditional logic. Only the
    ``prompt`` argument and ``generation_config_overrides`` are consumed by the
    Seedream API; the other keyword parameters are accepted for interface
    compatibility and ignored by the request payload.

    Parameters
    ----------
    prompt:
        Natural-language instructions that describe the desired image(s).
    model:
        Fully-qualified Seedream model identifier. Defaults to
        ``doubao-seedream-4-0-250828``.
    images:
        Optional list of reference images. Each image is encoded as a data URI
        before being sent to the API, allowing single or multi-image editing
        workflows.
    generation_config_overrides:
        Provider specific keyword arguments that are forwarded to the Ark JSON
        payload. Useful keys include ``size`` (``"1K"``/``"2K"``/``"4K"`` or a
        resolution string), ``response_format`` (``"url"`` or ``"b64_json"``),
        ``watermark`` (``True``/``False``), ``sequential_image_generation`` and
        ``sequential_image_generation_options``. Unsupported keys are ignored.
    max_retries:
        Number of times a failed request should be retried with a 1 second backoff.

    Returns
    -------
    SeedreamImageEditResult
        Convenience container that surfaces the decoded images alongside the raw
        JSON response for inspection.

    ASCII architecture
    ------------------

    .. code-block:: text

        +------------------+       +---------------------+       +------------------+
        | doeff Program    | ----> | edit_image__seedream4 | ---> | SeedreamClient   |
        | (@do generator)  |       | builds payload       |       | posts to Ark API |
        +------------------+       +---------------------+       +------------------+
                                                                     |
                                                                     v
                                                            +------------------+
                                                            | Ark /images/...  |
                                                            +------------------+
                                                                     |
                                                                     v
        +------------------+       +---------------------+       +------------------+
        | RunResult / Step | <---- | _decode_images(...) | <---- | JSON response    |
        +------------------+       +---------------------+       +------------------+

    Mermaid sequence
    ----------------

    .. code-block:: mermaid

        sequenceDiagram
            participant Program as doeff Program
            participant Helper as edit_image__seedream4
            participant Client as SeedreamClient
            participant Ark as Ark API
            participant Decoder as _decode_images

            Program->>Helper: yield edit_image__seedream4(...)
            Helper->>Client: a_generate_images(payload)
            Client->>Ark: POST /images/generations
            Ark-->>Client: JSON response
            Client-->>Helper: response
            Helper->>Decoder: decode(data)
            Decoder-->>Helper: SeedreamImage list
            Helper-->>Program: SeedreamImageEditResult
    """

    if system_instruction or safety_settings or tools or tool_config or response_modalities:
        yield Log(
            "Seedream image generation ignores system/tool configuration parameters; "
            "pass provider-specific overrides via generation_config_overrides instead."
        )

    def ask_optional(name: str):
        return Catch(Ask(name), lambda exc: None if isinstance(exc, KeyError) else None)  # type: ignore[return-value]

    payload, timeout = _build_payload(
        prompt=prompt,
        model=model,
        images=images,
        generation_config_overrides=generation_config_overrides,
    )

    requested_size = payload.get("size") if isinstance(payload.get("size"), str) else None

    cost_default_value = yield ask_optional("seedream_cost_per_image_usd")
    cost_per_size_value = yield ask_optional("seedream_cost_per_size_usd")
    if not isinstance(cost_per_size_value, Mapping):
        cost_per_size_value = None

    response_format = str(payload.get("response_format", DEFAULT_RESPONSE_FORMAT))

    yield Log(
        "Preparing Seedream image request using model=%s with %d reference image(s)" % (
            model,
            len(images) if images else 0,
        )
    )

    client = yield get_seedream_client()

    request_summary = {
        "model": model,
        "has_images": bool(payload.get("image")),
        "response_format": response_format,
    }

    @do
    def make_api_call() -> EffectGenerator[dict[str, Any]]:
        attempt_start = time.time()

        @do
        def api_call() -> EffectGenerator[dict[str, Any]]:
            response = yield Await(
                client.a_generate_images(
                    payload,
                    timeout=timeout,
                )
            )
            yield track_api_call(
                operation="images.generate",
                model=model,
                request_payload=payload,
                response=response,
                start_time=attempt_start,
                error=None,
            )
            return response

        @do
        def handle_error(exc: Exception) -> EffectGenerator[dict[str, Any]]:
            yield track_api_call(
                operation="images.generate",
                model=model,
                request_payload=payload,
                response=None,
                start_time=attempt_start,
                error=exc,
            )
            yield Fail(exc)

        return (yield Catch(api_call(), handle_error))

    response: dict[str, Any] = yield Retry(
        make_api_call(),
        max_attempts=max_retries,
        delay_ms=1000,
    )

    try:
        images_decoded = _decode_images(response, expected_format=response_format)
    except ValueError as exc:
        yield Fail(exc)

    result = SeedreamImageEditResult(
        images=images_decoded,
        prompt=prompt,
        model=model,
        raw_response=response,
    )

    usage = response.get("usage") if isinstance(response, dict) else None
    generated_images = usage.get("generated_images") if isinstance(usage, dict) else None

    result_size = None
    if result.images:
        primary_size = result.images[0].size
        if isinstance(primary_size, str) and primary_size:
            result_size = primary_size
    if result_size is None and isinstance(response.get("size"), str):
        result_size = str(response.get("size"))
    if result_size is None:
        result_size = requested_size

    generated_count = generated_images if isinstance(generated_images, int) and generated_images > 0 else len(result.images)
    cost_estimate: CostEstimate | None = None
    if generated_count > 0:
        try:
            cost_estimate = calculate_cost(
                model=model,
                generated_images=generated_count,
                size=result_size,
                cost_per_size=cost_per_size_value,
                default_cost=cost_default_value,
            )
        except ValueError:
            cost_estimate = None

    step_value = {
        "result_type": type(result).__name__,
        "image_count": len(result.images),
    }
    if generated_images is not None:
        step_value["generated_images"] = generated_images
    if cost_estimate:
        step_value["estimated_cost_usd"] = cost_estimate.total_cost

    if cost_estimate:
        previous_total = yield AtomicGet("seedream_total_cost_usd")
        if previous_total is None:
            previous_total = 0.0
        new_total = float(previous_total) + cost_estimate.total_cost

        yield AtomicUpdate(
            "seedream_total_cost_usd",
            lambda current: (current or 0.0) + cost_estimate.total_cost,
            default_factory=lambda: 0.0,
        )

        model_cost_key = f"seedream_cost_{model}"
        yield AtomicUpdate(
            model_cost_key,
            lambda current: (current or 0.0) + cost_estimate.total_cost,
            default_factory=lambda: 0.0,
        )

        def _append_call(current: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
            entries = list(current) if current else []
            entries.append(
                {
                    "model": model,
                    "size": cost_estimate.size,
                    "generated_images": cost_estimate.generated_images,
                    "per_image_cost": cost_estimate.per_image_cost,
                    "total_cost": cost_estimate.total_cost,
                    "source": cost_estimate.source,
                }
            )
            return entries

        yield AtomicUpdate(
            "seedream_api_calls",
            _append_call,
            default_factory=list,
        )

        yield Log(
            "Seedream estimated cost $%.4f for %d image(s) (%s); cumulative total $%.4f"
            % (
                cost_estimate.total_cost,
                cost_estimate.generated_images,
                cost_estimate.source,
                new_total,
            )
        )

    step_meta = {
        "model": model,
        "has_reference_images": request_summary["has_images"],
    }
    if cost_estimate:
        step_meta["cost_source"] = cost_estimate.source
        step_meta["per_image_cost_usd"] = cost_estimate.per_image_cost

    yield Step(
        value=step_value,
        meta=step_meta,
    )

    return result


__all__ = ["edit_image__seedream4", "DEFAULT_MODEL", "DEFAULT_RESPONSE_FORMAT"]
