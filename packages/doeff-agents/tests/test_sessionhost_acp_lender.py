"""貸す側(預かり所)の途絶の焦点の検(card acp:kanban-issue:ki-fd0f3b234a38・設計 v2 §10.1・ADR-DOE-AGENTS-012 R63)。

* ``sessionhost_acp_lender_deftests.hy`` の ``test_*`` をすべて pytest に出す(``test_sessionhost_acp_lease.py`` と同じ形 —
  足した deftest が黙って走らないことを無くす)。
* 借りの handler(``handlers.CustodyHttp._borrow``)の 2 つの腕を HTTP の fake で撃つ: 断りが処理ステージと本文の
  機械の語(code・why)を運ぶこと(設計 v2 §10.1 — 以前は error の文だけを写していた)と、引換に失敗した拍に
  master が出した貸与を返すこと(接続が答えない間のやり直しで貸与の行を積まない)。

deftest は包み直さず、そのまま公開する。包むと ``pytestmark``(skipif /
marks / parametrize)が関数の ``__dict__`` ごと落ち、書いた宣言が黙って
効かなくなる(ADR-DOE-HY-002 law deftest-params-are-honored:
``params_silently_dropped == 0``)。実行時の ``doeff_interpreter`` は
conftest.py の fixture が供給する(同 R3)。
"""

import importlib
import sys
from collections.abc import Mapping
from pathlib import Path

import doeff_hy  # noqa: F401  # registers Hy import hooks for deftest modules
import pytest
from doeff_agents.sessionhost.acp import handlers
from doeff_agents.sessionhost.acp.effects import (
    CUSTODY_STAGE_LEASE,
    CUSTODY_STAGE_REDEEM,
    JSON,
    CustodyLeaseBorrow,
    LeaseRefused,
)

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

_deftests = importlib.import_module("sessionhost_acp_lender_deftests")


_names = [name for name in dir(_deftests) if name.startswith("test_")]
assert _names, "sessionhost_acp_lender_deftests exposes no test_* deftests"
for _name in _names:
    globals()[_name] = getattr(_deftests, _name)


# ---------------------------------------------------------------------------
# 借りの handler: 断りの処理ステージと機械の語・引換の失敗で貸与を返す
# ---------------------------------------------------------------------------

_HOLD = "2026-09-23T15:00:00.000Z"


def _grant() -> dict[str, JSON]:
    return {
        "ok": True,
        "leaseId": "lease-7",
        "renewed": False,
        "voucher": "vch-0123456789ABCDEFGHJKMNPQRS",
        "workerUrl": "https://worker.test/",
        "holdExpiresAt": _HOLD,
    }


class _Custody:
    """HTTP の fake: 撃たれた (method, url) を覚え、宣言した答えを返す。"""

    def __init__(self, lease: handlers.HttpReply, redeem: handlers.HttpReply, revoke: handlers.HttpReply) -> None:
        self.seen: list[tuple[str, str]] = []
        self._lease = lease
        self._redeem = redeem
        self._revoke = revoke

    def __call__(
        self,
        connections: handlers.HttpConnections,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: JSON,
        timeout: float,
    ) -> handlers.HttpReply:
        self.seen.append((method, url))
        if url.endswith("/lease/claude"):
            return self._lease
        if url.endswith("/redeem"):
            return self._redeem
        if url.endswith("/revoke"):
            return self._revoke
        raise AssertionError(f"宣言に無い要求: {method} {url}")


def _borrow(monkeypatch: pytest.MonkeyPatch, fake: _Custody) -> handlers.LeaseOutcome:
    monkeypatch.setattr(handlers, "_http_json", fake)
    return handlers.CustodyHttp("http://master.test", "borrower-key")._borrow(
        CustodyLeaseBorrow(kind="claude", account="acct", purpose="agent-job s-1")
    )


def test_a_lease_refusal_carries_the_stage_and_the_machine_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """面 1: master の貸与の口の 503 は処理ステージ lease と本文の code・why をそのまま運ぶ(貸与が無いので返さない)。"""
    body: dict[str, JSON] = {
        "ok": False,
        "code": "worker-unreachable",
        "why": "heartbeat-stale",
        "error": "口座の worker personal へ届かない",
        "worker": "personal",
    }
    fake = _Custody(handlers.HttpReply(503, body), handlers.HttpReply(500, {}), handlers.HttpReply(500, {}))
    refused = _borrow(monkeypatch, fake)
    assert isinstance(refused, LeaseRefused)
    assert (refused.status, refused.stage, refused.code, refused.why, refused.error) == (
        503,
        CUSTODY_STAGE_LEASE,
        "worker-unreachable",
        "heartbeat-stale",
        "口座の worker personal へ届かない",
    )
    assert refused.unreturned_lease_id is None
    assert fake.seen == [("POST", "http://master.test/lease/claude")], "貸与の断りで引換か返却を撃った"


def test_a_failed_redeem_names_its_stage_and_returns_the_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    """面 2: 引換の口の 503 master-unreachable は処理ステージ redeem で運び(本文に why は無い)、master が出した貸与を返す。"""
    fake = _Custody(
        handlers.HttpReply(200, _grant()),
        handlers.HttpReply(503, {"ok": False, "code": "master-unreachable", "error": "master に届かない"}),
        handlers.HttpReply(200, {"ok": True}),
    )
    refused = _borrow(monkeypatch, fake)
    assert isinstance(refused, LeaseRefused)
    assert (refused.status, refused.stage, refused.code, refused.why) == (
        503,
        CUSTODY_STAGE_REDEEM,
        "master-unreachable",
        None,
    )
    assert fake.seen == [
        ("POST", "http://master.test/lease/claude"),
        ("POST", "https://worker.test/redeem"),
        ("POST", "http://master.test/lease/lease-7/revoke"),
    ], "引換に失敗した貸与を返していない"
    assert refused.unreturned_lease_id is None, "返せた貸与を返せなかったと名乗った"


def test_an_unanswered_redeem_returns_the_lease_and_the_refusal_is_the_same_when_the_return_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """面 3: 引換の接続が答えない(status 0)拍も貸与を返す。返すのに失敗しても断りの語は変わらない(返せなかった id を
    運ぶだけ — 呼び手が log に名乗る)。hold は今日どおり master の答えから運ぶ。"""
    unanswered = handlers.HttpReply(0, {"error": "unreachable: [Errno 111] Connection refused"})
    returned = _Custody(handlers.HttpReply(200, _grant()), unanswered, handlers.HttpReply(200, {"ok": True}))
    kept = _Custody(handlers.HttpReply(200, _grant()), unanswered, handlers.HttpReply(0, {"error": "unreachable"}))
    first = _borrow(monkeypatch, returned)
    second = _borrow(monkeypatch, kept)
    assert isinstance(first, LeaseRefused)
    assert isinstance(second, LeaseRefused)
    assert first.unreturned_lease_id is None
    assert second.unreturned_lease_id == "lease-7"
    for refused in (first, second):
        assert (refused.status, refused.stage, refused.code, refused.why, refused.error) == (
            0,
            CUSTODY_STAGE_REDEEM,
            None,
            None,
            "unreachable: [Errno 111] Connection refused",
        )
        assert refused.hold_expires_at_ms is not None
    assert kept.seen[-1] == ("POST", "http://master.test/lease/lease-7/revoke")


def test_a_redeem_409_carries_the_redeem_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    """設計 v2 F3: 引換券の使用済み(409 voucher-spent)は処理ステージ redeem を名乗る — 分類が貸与の錠(time)と
    取り違えない材料(判定は sessionhost_acp_lender_deftests.hy の test-a-redeem-409-is-not-a-held-lease)。"""
    fake = _Custody(
        handlers.HttpReply(200, _grant()),
        handlers.HttpReply(409, {"ok": False, "code": "voucher-spent", "error": "引換券は使用済み"}),
        handlers.HttpReply(200, {"ok": True}),
    )
    refused = _borrow(monkeypatch, fake)
    assert isinstance(refused, LeaseRefused)
    assert (refused.status, refused.stage, refused.code) == (409, CUSTODY_STAGE_REDEEM, "voucher-spent")
    assert refused.hold_expires_at_ms is not None, "hold は今日どおり master の答えから運ぶ(分類が処理ステージで分ける)"
