"""Mapping between Judge and HTTP (pure). No communication happens here.

The two wire formats:
- direct = TypeSafe's `/v1/systemone`. body {state, model, questions:{name: {type, instructions, criteria}}},
  answer {answers:{name: {choice|noul|score, confidence?, probabilities?}}, usage.input_tokens, model}.
- gateway = Vercel AI Gateway's evaluation-model v4 (measured 2026-09-22). The kinds are choice / score /
  boolean (noul is mapped to boolean), the criteria of choice is a record ({id: description}), the answer's confidence is
  providerMetadata.typesafe.confidence[name], the answer of boolean is probability, and usage is camelCase.

Images (Judge's assets) are carried only by direct: the root assets of the body and the header IMAGES_HEADER (https://docs.typesafe.ai/images.md,
Jev 1.13 and later. Jev rejects a request without the header with 400). The gateway format has no image shape, so a Judge with images is a JudgeError.

Headers with the relay (jev-proxy): the caller name is sent in the request header CALLER_HEADER (e.g. `rulebook-advisor/Stop`, `doeff-linter/DOEFF205`),
because putting it in the body would change the relay's answer key. The relay declares the origin of the answer (hit / miss / coalesced /
refreshed) in the response header PROXY_HEADER, and parse maps it to Verdict.proxy. Neither header has meaning for a destination that does not go through the relay (sent but not read).
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from doeff_system_one import Answer, Judge, JudgeError, Verdict

from doeff_jev._target import JevTarget


IMAGES_HEADER = "TypeSafe-Danger-Zone-Images"
CALLER_HEADER = "x-jev-caller"
PROXY_HEADER = "x-jev-proxy"
#: Characters kept in header values (the rest become %XX; HTTP header values are limited to ASCII, so even Japanese names do not break the request).
_CALLER_SAFE = "/:@._-+="


@dataclass(frozen=True)
class Prepared:
    url: str
    headers: Mapping[str, str]
    body: Mapping[str, Any]


def _gateway_question(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The gateway format: noul is boolean. The shape of criteria (choice = record, score = list) is the same as direct."""
    kind = payload.get("type", "choice")
    out: dict[str, Any] = {"type": "boolean" if kind == "noul" else kind,
                           "instructions": payload.get("instructions", "")}
    criteria = payload.get("criteria")
    if criteria is not None:
        out["criteria"] = criteria
    return out


def caller_header_value(caller: str) -> str:
    """Map the caller name to a header value (ASCII alphanumerics and the characters of _CALLER_SAFE stay as they are; the rest become %XX)."""
    return quote(caller, safe=_CALLER_SAFE)


def prepare(target: JevTarget, judge: Judge, *, caller: str = "") -> Prepared:
    """Map a Judge to a request in the destination's shape. If caller is non-empty, the caller name is sent in the header CALLER_HEADER."""
    questions = {name: question.as_payload() for name, question in judge.questions.items()}
    headers: dict[str, str] = {"content-type": "application/json"}
    if target.api_key:
        headers["authorization"] = "Bearer " + target.api_key
    if caller:
        headers[CALLER_HEADER] = caller_header_value(caller)
    if judge.assets and target.wire == "gateway":
        raise JudgeError("unsupported", "the gateway format does not carry images (assets); ask a direct destination")
    if target.wire == "gateway":
        headers.update({
            "ai-gateway-protocol-version": "0.0.1",
            "ai-evaluation-model-specification-version": "4",
            "ai-model-id": target.model,
        })
        body: dict[str, Any] = {"state": judge.state,
                                "questions": {name: _gateway_question(q) for name, q in questions.items()}}
    else:
        body = {"state": judge.state, "questions": questions, "model": target.model}
        if judge.assets:
            body["assets"] = {name: asset.as_payload() for name, asset in judge.assets.items()}
            headers[IMAGES_HEADER] = "true"
    return Prepared(url=target.base_url, headers=headers, body=body)


def _parse_answer(kind: str, raw: Mapping[str, Any], gateway_confidence: float | None) -> Answer:
    if kind == "noul":
        value = raw.get("noul", raw.get("probability"))
        if value is not None:
            return Answer.noul(float(value), probabilities=raw.get("probabilities"), raw=dict(raw))
        confidence = None
    elif kind == "score":
        value = raw.get("score")
        confidence = raw.get("confidence", gateway_confidence)
    else:
        value = raw.get("choice")
        confidence = raw.get("confidence", gateway_confidence)
    return Answer(kind=kind, value=value,  # type: ignore[arg-type]
                  confidence=None if confidence is None else float(confidence),
                  probabilities=raw.get("probabilities"), raw=dict(raw))


def proxy_outcome(headers: Mapping[str, str]) -> str | None:
    """Read the answer origin declared by the relay from the response headers (header names are case-insensitive). None if not declared."""
    for name, value in headers.items():
        if name.lower() == PROXY_HEADER:
            return value.strip().lower() or None
    return None


def parse(target: JevTarget, judge: Judge, status: int, text: str, *,
          elapsed_ms: int = 0, headers: Mapping[str, str] | None = None) -> Verdict:
    """Map an HTTP response to a Verdict. JudgeError when the status is 4xx/5xx or the body is broken.

    headers = the response headers (maps the relay's self-identification PROXY_HEADER to Verdict.proxy).
    """
    if status >= 400:
        raise JudgeError("http", f"{status} {text[:160].strip()}", status=status)
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise JudgeError("malformed", f"not json: {text[:120]!r}") from exc
    answers_raw = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers_raw, dict):
        raise JudgeError("malformed", "answers missing: " + json.dumps(payload)[:120])
    gateway_conf: Mapping[str, Any] = {}
    if target.wire == "gateway":
        meta = payload.get("providerMetadata") or {}
        gateway_conf = (meta.get("typesafe") or {}).get("confidence") or {}
    answers: dict[str, Answer] = {}
    for name, kind in judge.kinds().items():
        raw = answers_raw.get(name)
        if not isinstance(raw, dict):
            raise JudgeError("malformed", "answer missing: " + name)
        conf = gateway_conf.get(name)
        answers[name] = _parse_answer(kind, raw, None if conf is None else float(conf))
    usage = payload.get("usage") or {}
    tokens = usage.get("input_tokens", usage.get("inputTokens")) or 0
    return Verdict(answers=answers, model=str(payload.get("model") or target.model),
                   input_tokens=int(tokens), elapsed_ms=int(elapsed_ms),
                   proxy=None if headers is None else proxy_outcome(headers))


def cache_key(target: JevTarget, judge: Judge) -> str:
    """The answer cache key: determined by the destination, model, state, and the shape of the questions (stable across processes)."""
    keyed: dict[str, Any] = {
        "url": target.base_url, "model": target.model, "state": judge.state,
        "questions": {name: q.as_payload() for name, q in judge.questions.items()},
    }
    # Images go into the key only when present (the key of an image-less judgment stays as before, so remembered answers are not invalidated).
    if judge.assets:
        keyed["assets"] = {name: asset.as_payload() for name, asset in judge.assets.items()}
    material = json.dumps(keyed, ensure_ascii=False, sort_keys=True, default=repr).encode("utf-8")
    return "jev:" + hashlib.sha256(material).hexdigest()
