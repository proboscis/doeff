"""Judge ⇄ HTTP の写し(純粋)。2 つの通信の形と、答えの読み。"""

import hashlib
import json

import pytest
from doeff_jev import JevTarget, cache_key, parse, prepare
from doeff_jev.wire import CALLER_HEADER, IMAGES_HEADER, PROXY_HEADER
from doeff_system_one import Image, Judge, JudgeError, choice, noul, score

DIRECT = JevTarget("https://api.typesafe.ai/v1/systemone", "jev-latest", "direct", "k-direct", "default")
GATEWAY = JevTarget("https://ai-gateway.vercel.sh/v4/ai/evaluation-model", "typesafe-ai/jev", "gateway", "k-gw", "default")
QUESTIONS = {"c": choice("lang", ["ja", "en"]), "n": noul("is ja"), "s": score("how ja", ["no", "some", "all"])}


def test_direct_request_shape() -> None:
    judge = Judge(state={"x": 1}, questions=QUESTIONS)
    prepared = prepare(DIRECT, judge)
    assert prepared.url == DIRECT.base_url
    assert prepared.headers["authorization"] == "Bearer k-direct"
    assert "ai-model-id" not in prepared.headers
    assert prepared.body["model"] == "jev-latest"
    assert prepared.body["state"] == {"x": 1}
    assert prepared.body["questions"]["c"] == {"type": "choice", "instructions": "lang", "criteria": {"ja": "ja", "en": "en"}}
    assert prepared.body["questions"]["n"] == {"type": "noul", "instructions": "is ja"}
    assert prepared.body["questions"]["s"] == {"type": "score", "instructions": "how ja", "criteria": ["no", "some", "all"]}


def test_gateway_request_translates_noul_to_boolean() -> None:
    judge = Judge(state="これは日本語", questions=QUESTIONS)
    prepared = prepare(GATEWAY, judge)
    assert prepared.headers["ai-model-id"] == "typesafe-ai/jev"
    assert "model" not in prepared.body
    assert prepared.body["questions"]["c"] == {"type": "choice", "instructions": "lang", "criteria": {"ja": "ja", "en": "en"}}
    assert prepared.body["questions"]["n"] == {"type": "boolean", "instructions": "is ja"}
    assert prepared.body["questions"]["s"]["criteria"] == ["no", "some", "all"]


def test_no_key_means_no_authorization_header() -> None:
    seimf = JevTarget("http://zeus:8646/v1/systemone", "seimf", "direct", None, "env")
    assert "authorization" not in prepare(seimf, Judge(state="s", questions={"n": noul("x")})).headers


def test_parse_direct_answers() -> None:
    judge = Judge(state="s", questions=QUESTIONS)
    payload = {"answers": {"c": {"type": "choice", "choice": "ja", "confidence": 0.9, "probabilities": {"ja": 0.9, "en": 0.1}},
                           "n": {"type": "noul", "noul": 0.2},
                           "s": {"type": "score", "score": 1.5, "confidence": 0.7}},
               "usage": {"input_tokens": 321}, "model": "jev-1.13.0"}
    verdict = parse(DIRECT, judge, 200, json.dumps(payload), elapsed_ms=12)
    assert verdict.model == "jev-1.13.0"
    assert verdict.input_tokens == 321
    assert verdict.elapsed_ms == 12
    c, n, s = verdict.answer("c"), verdict.answer("n"), verdict.answer("s")
    assert (c.value, c.confidence, c.probabilities["en"]) == ("ja", 0.9, 0.1)
    assert n.value == 0.2
    assert n.confidence == pytest.approx(0.6)
    assert (s.value, s.confidence) == (1.5, 0.7)


def test_parse_gateway_answers() -> None:
    judge = Judge(state="s", questions=QUESTIONS)
    payload = {"answers": {"c": {"type": "choice", "choice": "ja", "probabilities": {"ja": 1, "en": 0}},
                           "n": {"type": "boolean", "probability": 0.97},
                           "s": {"type": "score", "score": 1.99}},
               "usage": {"inputTokens": 295, "outputTokens": 20},
               "providerMetadata": {"typesafe": {"confidence": {"c": 1, "s": 0.98}}}}
    verdict = parse(GATEWAY, judge, 200, json.dumps(payload))
    assert verdict.input_tokens == 295
    assert verdict.model == "typesafe-ai/jev"
    assert (verdict.answer("c").value, verdict.answer("c").confidence) == ("ja", 1.0)
    assert verdict.answer("n").value == 0.97
    assert verdict.answer("n").confidence == pytest.approx(0.94)
    assert (verdict.answer("s").value, verdict.answer("s").confidence) == (1.99, 0.98)


def test_parse_errors_are_judge_errors() -> None:
    judge = Judge(state="s", questions={"n": noul("x")})
    with pytest.raises(JudgeError) as http:
        parse(DIRECT, judge, 400, '{"error":"invalid discriminator"}')
    assert http.value.kind == "http"
    assert http.value.status == 400
    assert "invalid discriminator" in http.value.detail
    with pytest.raises(JudgeError) as bad_json:
        parse(DIRECT, judge, 200, "not json")
    assert bad_json.value.kind == "malformed"
    with pytest.raises(JudgeError) as missing:
        parse(DIRECT, judge, 200, json.dumps({"answers": {"other": {"noul": 0.1}}}))
    assert missing.value.kind == "malformed"
    assert "n" in missing.value.detail


def test_cache_key_depends_on_target_state_and_questions() -> None:
    a = Judge(state="same", questions={"n": noul("x")})
    b = Judge(state="same", questions={"n": noul("x")})
    c = Judge(state="other", questions={"n": noul("x")})
    assert cache_key(DIRECT, a) == cache_key(DIRECT, b)
    assert cache_key(DIRECT, a) != cache_key(DIRECT, c)
    assert cache_key(DIRECT, a) != cache_key(GATEWAY, a)
    assert cache_key(DIRECT, a).startswith("jev:")


def test_direct_request_carries_images_and_the_images_header() -> None:
    poster = Image(format="png", data_base64="iVBORw0KGgo=")
    judge = Judge(state={"poster": "p-1"}, questions={"n": noul("Is `poster` text only?")}, assets={"p-1": poster})
    prepared = prepare(DIRECT, judge)
    assert prepared.body["assets"] == {"p-1": {"type": "image", "format": "png", "data_base64": "iVBORw0KGgo="}}
    assert prepared.headers[IMAGES_HEADER] == "true"
    plain = prepare(DIRECT, Judge(state={"x": 1}, questions={"n": noul("x")}))
    assert "assets" not in plain.body
    assert IMAGES_HEADER not in plain.headers
    with pytest.raises(JudgeError, match="unsupported"):
        prepare(GATEWAY, judge)


def test_images_enter_the_cache_key_only_when_present() -> None:
    questions = {"n": noul("Is `poster` text only?")}
    a = Judge(state={"poster": "p-1"}, questions=questions, assets={"p-1": Image(format="png", data_base64="AAAA")})
    b = Judge(state={"poster": "p-1"}, questions=questions, assets={"p-1": Image(format="png", data_base64="BBBB")})
    assert cache_key(DIRECT, a) != cache_key(DIRECT, b)
    plain = Judge(state={"poster": "p-1"}, questions=questions)
    legacy = "jev:" + hashlib.sha256(json.dumps({
        "url": DIRECT.base_url, "model": DIRECT.model, "state": plain.state,
        "questions": {name: q.as_payload() for name, q in plain.questions.items()},
    }, ensure_ascii=False, sort_keys=True, default=repr).encode("utf-8")).hexdigest()
    assert cache_key(DIRECT, plain) == legacy


def test_image_and_judge_refuse_malformed_assets() -> None:
    with pytest.raises(ValueError):
        Image(format="webp", data_base64="AAAA")
    with pytest.raises(ValueError):
        Image(format="png", data_base64="")
    with pytest.raises(TypeError):
        Judge(state="s", questions={"n": noul("x")}, assets={"p": {"format": "png"}})  # type: ignore[dict-item]


def test_caller_travels_as_a_header_and_not_in_the_body() -> None:
    judge = Judge(state="s", questions={"n": noul("x")})
    prepared = prepare(DIRECT, judge, caller="rulebook-advisor/Stop")
    assert prepared.headers[CALLER_HEADER] == "rulebook-advisor/Stop"
    assert "caller" not in json.dumps(prepared.body)
    assert CALLER_HEADER not in prepare(DIRECT, judge).headers
    # 見出しの値は ASCII に限る — 日本語の名は %XX に写して要求を壊さない。
    assert prepare(DIRECT, judge, caller="部品/問い").headers[CALLER_HEADER] == "%E9%83%A8%E5%93%81/%E5%95%8F%E3%81%84"
    # 呼び手の名は答えの鍵に入らない(同じ問いは呼び手が違っても中継と手元の cache で当たる)。
    assert cache_key(DIRECT, judge) == cache_key(DIRECT, Judge(state="s", questions={"n": noul("x")}))


def test_parse_reads_the_proxy_header_case_insensitively() -> None:
    judge = Judge(state="s", questions={"n": noul("x")})
    text = json.dumps({"answers": {"n": {"type": "noul", "noul": 0.9}}, "usage": {"input_tokens": 7}})
    assert parse(DIRECT, judge, 200, text, headers={PROXY_HEADER: "hit"}).proxy == "hit"
    assert parse(DIRECT, judge, 200, text, headers={"X-Jev-Proxy": "Coalesced"}).proxy == "coalesced"
    assert parse(DIRECT, judge, 200, text, headers={"content-type": "application/json"}).proxy is None
    verdict = parse(DIRECT, judge, 200, text)
    assert (verdict.proxy, verdict.cached) == (None, False)
