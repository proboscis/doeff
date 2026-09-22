"""Judge ⇄ HTTP の写し(純粋)。通信はしない。

2 つの通信の形:
- direct = TypeSafe の `/v1/systemone`。body {state, model, questions:{名: {type, instructions, criteria}}}、
  答え {answers:{名: {choice|noul|score, confidence?, probabilities?}}, usage.input_tokens, model}。
- gateway = Vercel AI Gateway の evaluation-model v4(実測 2026-09-22)。種類は choice / score /
  boolean(noul は boolean に写す)、choice の criteria は record({id: 説明})、答えの確信度は
  providerMetadata.typesafe.confidence[名]、boolean の答えは probability、usage は camelCase。
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from doeff_system_one import Answer, Judge, JudgeError, Verdict

from doeff_jev.target import JevTarget


@dataclass(frozen=True)
class Prepared:
    url: str
    headers: Mapping[str, str]
    body: Mapping[str, Any]


def _gateway_question(payload: Mapping[str, Any]) -> dict[str, Any]:
    """gateway の綴り: noul は boolean。criteria の形(choice = record・score = 列)は direct と同じ。"""
    kind = payload.get("type", "choice")
    out: dict[str, Any] = {"type": "boolean" if kind == "noul" else kind,
                           "instructions": payload.get("instructions", "")}
    criteria = payload.get("criteria")
    if criteria is not None:
        out["criteria"] = criteria
    return out


def prepare(target: JevTarget, judge: Judge) -> Prepared:
    """Judge を宛先の形の request に写す。"""
    questions = {name: question.as_payload() for name, question in judge.questions.items()}
    headers: dict[str, str] = {"content-type": "application/json"}
    if target.api_key:
        headers["authorization"] = "Bearer " + target.api_key
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
    return Prepared(url=target.base_url, headers=headers, body=body)


def _parse_answer(kind: str, raw: Mapping[str, Any], gateway_confidence: float | None) -> Answer:
    if kind == "noul":
        value = raw.get("noul", raw.get("probability"))
        confidence = None if value is None else abs(float(value) - 0.5) * 2
    elif kind == "score":
        value = raw.get("score")
        confidence = raw.get("confidence", gateway_confidence)
    else:
        value = raw.get("choice")
        confidence = raw.get("confidence", gateway_confidence)
    return Answer(kind=kind, value=value,  # type: ignore[arg-type]
                  confidence=None if confidence is None else float(confidence),
                  probabilities=raw.get("probabilities"), raw=dict(raw))


def parse(target: JevTarget, judge: Judge, status: int, text: str, *,
          elapsed_ms: int = 0) -> Verdict:
    """HTTP の答えを Verdict に写す。状態が 4xx/5xx・本文が壊れている時は JudgeError。"""
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
                   input_tokens=int(tokens), elapsed_ms=int(elapsed_ms))


def cache_key(target: JevTarget, judge: Judge) -> str:
    """答えの cache の鍵 — 宛先・model・state・問いの綴りで決まる(process をまたいで安定)。"""
    material = json.dumps({
        "url": target.base_url, "model": target.model, "state": judge.state,
        "questions": {name: q.as_payload() for name, q in judge.questions.items()},
    }, ensure_ascii=False, sort_keys=True, default=repr).encode("utf-8")
    return "jev:" + hashlib.sha256(material).hexdigest()
