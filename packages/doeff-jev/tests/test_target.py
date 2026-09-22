"""宛先の解き方(純粋・環境と file は注入)。"""

import json

from doeff_jev import JevTarget, key_required, resolve_target
from doeff_jev.target import (
    CONFIG_FILE,
    DIRECT_KEY_FILE,
    DIRECT_URL,
    GATEWAY_KEY_FILE,
    GATEWAY_URL,
)


def _files(**contents):
    def read_text(path):
        return contents.get(path)
    return read_text


def test_default_is_direct_typesafe_with_env_key() -> None:
    target = resolve_target({"TYPESAFE_API_KEY": "k-direct"}, _files())
    assert (target.base_url, target.model, target.wire, target.api_key, target.source) == (
        DIRECT_URL, "jev-latest", "direct", "k-direct", "default")
    assert key_required(target)


def test_gateway_by_declaration_reads_gateway_key_file() -> None:
    target = resolve_target({}, _files(**{GATEWAY_KEY_FILE: "k-gw\n"}), wire="gateway")
    assert (target.base_url, target.model, target.wire, target.api_key) == (
        GATEWAY_URL, "typesafe-ai/jev", "gateway", "k-gw")


def test_gateway_url_in_env_is_recognised_as_gateway_wire() -> None:
    target = resolve_target({"JEV_BASE_URL": GATEWAY_URL, "AI_GATEWAY_API_KEY": "k"}, _files())
    assert target.wire == "gateway"
    assert target.model == "typesafe-ai/jev"
    assert target.api_key == "k"


def test_env_url_wins_over_config_file_and_foreign_host_needs_no_key() -> None:
    files = _files(**{CONFIG_FILE: json.dumps({"base_url": "http://file.example/v1", "model": "m-file"})})
    target = resolve_target({"JEV_BASE_URL": "http://zeus:8646/v1/systemone", "JEV_MODEL": "seimf-27b"}, files)
    assert (target.base_url, target.model, target.wire, target.source) == (
        "http://zeus:8646/v1/systemone", "seimf-27b", "direct", "env")
    assert target.api_key is None
    assert not key_required(target)
    from_file = resolve_target({}, files)
    assert (from_file.base_url, from_file.model, from_file.source) == ("http://file.example/v1", "m-file", "file")


def test_key_file_from_env_and_default_direct_key_file() -> None:
    explicit = resolve_target({"JEV_API_KEY_FILE": "/tmp/k"}, _files(**{"/tmp/k": " k-file "}))
    assert explicit.api_key == "k-file"
    default = resolve_target({}, _files(**{DIRECT_KEY_FILE: "k-default"}))
    assert default.api_key == "k-default"


def test_host_property() -> None:
    assert JevTarget("https://api.typesafe.ai/v1/systemone", "m", "direct", None, "x").host == "api.typesafe.ai"
    assert JevTarget("http://zeus:8646/v1", "m", "direct", None, "x").host == "zeus:8646"
