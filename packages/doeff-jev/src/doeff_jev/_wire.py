"""Judge ⇄ HTTP の写し(純粋)。通信はしない。

2 つの通信の形:
- direct = TypeSafe の `/v1/systemone`。body {state, model, questions:{名: {type, instructions, criteria}}}、
  答え {answers:{名: {choice|noul|score, confidence?, probabilities?}}, usage.input_tokens, model}。
- gateway = Vercel AI Gateway の evaluation-model v4(実測 2026-09-22)。種類は choice / score /
  boolean(noul は boolean に写す)、choice の criteria は record({id: 説明})、答えの確信度は
  providerMetadata.typesafe.confidence[名]、boolean の答えは probability、usage は camelCase。

画像(Judge の assets)は direct だけが運ぶ: 本文の根の assets と見出し IMAGES_HEADER(https://docs.typesafe.ai/images.md —
Jev 1.13 以降。見出しの無い要求を Jev は 400 で断る)。gateway の綴りは画像の形を持たないので、画像つきの Judge は JudgeError。

中継(jev-proxy)との見出し: 呼び手の名は要求の見出し CALLER_HEADER(例 `rulebook-advisor/Stop`・`doeff-linter/DOEFF205`)で送る —
本文に入れると中継の答えの鍵が変わるので見出しにする。中継は答えの見出し PROXY_HEADER で答えの出所(hit / miss / coalesced /
refreshed)を名乗り、parse がそれを Verdict.proxy に写す。どちらの見出しも、中継を通らない宛先には意味を持たない(送っても読まれない)。
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
#: 見出しの値に残す字(ほかは %XX に写す — HTTP の見出しの値は ASCII に限るので、日本語の名でも要求を壊さない)。
_CALLER_SAFE = "/:@._-+="


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


def caller_header_value(caller: str) -> str:
    """呼び手の名を見出しの値に写す(ASCII の英数字と _CALLER_SAFE の字はそのまま・ほかは %XX)。"""
    return quote(caller, safe=_CALLER_SAFE)


def prepare(target: JevTarget, judge: Judge, *, caller: str = "") -> Prepared:
    """Judge を宛先の形の request に写す。caller が空でなければ見出し CALLER_HEADER で呼び手の名を送る。"""
    questions = {name: question.as_payload() for name, question in judge.questions.items()}
    headers: dict[str, str] = {"content-type": "application/json"}
    if target.api_key:
        headers["authorization"] = "Bearer " + target.api_key
    if caller:
        headers[CALLER_HEADER] = caller_header_value(caller)
    if judge.assets and target.wire == "gateway":
        raise JudgeError("unsupported", "gateway の綴りは画像(assets)を運ばない — direct の宛先で問う")
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
    """答えの見出しから中継の名乗った答えの出所を読む(見出しの名の大小は問わない)。名乗りが無ければ None。"""
    for name, value in headers.items():
        if name.lower() == PROXY_HEADER:
            return value.strip().lower() or None
    return None


def parse(target: JevTarget, judge: Judge, status: int, text: str, *,
          elapsed_ms: int = 0, headers: Mapping[str, str] | None = None) -> Verdict:
    """HTTP の答えを Verdict に写す。状態が 4xx/5xx・本文が壊れている時は JudgeError。

    headers = 答えの見出し(中継の名乗り PROXY_HEADER を Verdict.proxy に写す)。
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
    """答えの cache の鍵 — 宛先・model・state・問いの綴りで決まる(process をまたいで安定)。"""
    keyed: dict[str, Any] = {
        "url": target.base_url, "model": target.model, "state": judge.state,
        "questions": {name: q.as_payload() for name, q in judge.questions.items()},
    }
    # 画像は在る時だけ鍵に入れる(画像の無い判定の鍵は前と同じ — 覚えた答えを外さない)。
    if judge.assets:
        keyed["assets"] = {name: asset.as_payload() for name, asset in judge.assets.items()}
    material = json.dumps(keyed, ensure_ascii=False, sort_keys=True, default=repr).encode("utf-8")
    return "jev:" + hashlib.sha256(material).hexdigest()
