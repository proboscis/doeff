"""預かり所の契約の写し ``custody_lender_availability.json`` の検(card acp:kanban-issue:ki-fd0f3b234a38・設計 v2 §10.1 / §12)。

runner の断りの分類の表(``CUSTODY_LENDER_TRANSIENT``)と接続が答えない借りを待つ窓(``CUSTODY_UNANSWERED_WINDOW_MS``)は
この写しから導く(直書きしない)。時間で晴れる組の定義点は預かり所の契約 1 か所で、ここはその写しが

* 形(``custody.lender-availability.copy.v1`` — agora-controllers の写しと同じ形)を守り、
* 正本(``source.commit`` の ``docs/contracts/custody-api.json#/lenderAvailability``)と JSON として一致し、
* 分類が読む値がこの写しの導出ちょうどである

ことを撃つ。正本の checkout は env ``CUSTODY_CHECKOUT``(無ければ ``~/repos/custody``)。checkout が無い・pin の commit が
読めない機体では名指しで赤(黙って skip しない — 写しの腐敗を隠さない)。写しを進める = 正本の新しい commit の節で
file を書き直し ``source.commit`` を進める(手で節を直さない)。
"""

import json
import os
import subprocess
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from doeff_agents.sessionhost.acp import effects
from doeff_agents.sessionhost.acp.effects import (
    CUSTODY_LENDER_COPY,
    CUSTODY_LENDER_COPY_PACKAGE,
    CUSTODY_LENDER_COPY_RESOURCE,
    CUSTODY_LENDER_COPY_SCHEMA,
    CUSTODY_LENDER_TRANSIENT,
    CUSTODY_STAGE_LEASE,
    CUSTODY_STAGE_REDEEM,
    CUSTODY_UNANSWERED_RETRY_MS,
    CUSTODY_UNANSWERED_WINDOW_MS,
    CustodyStage,
    custody_lender_section_of,
    custody_lender_terms_of,
)

CANON_REPO = "proboscis/custody"
CANON_PATH = "docs/contracts/custody-api.json"
CANON_POINTER = "/lenderAvailability"


def _copy_document() -> dict[str, Any]:
    """同梱の写しを file から読み直す(effects の import の拍の値とは別の読み — 導出の一致を撃つため)。"""
    text = files(CUSTODY_LENDER_COPY_PACKAGE).joinpath(CUSTODY_LENDER_COPY_RESOURCE).read_text(encoding="utf-8")
    document = json.loads(text)
    assert isinstance(document, dict)
    return document


def _custody_checkout() -> Path:
    declared = os.environ.get("CUSTODY_CHECKOUT")
    return Path(declared).expanduser() if declared else Path.home() / "repos" / "custody"


def _pointer_value(document: Any, pointer: str) -> Any:
    node = document
    for raw in pointer.split("/")[1:]:
        part = raw.replace("~1", "/").replace("~0", "~")
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def test_the_copy_has_the_shared_form() -> None:
    """形: schema・source の repo / path / pointer・commit(40 桁の小文字 16 進)・copy の object。"""
    document = _copy_document()
    assert document["schema"] == CUSTODY_LENDER_COPY_SCHEMA
    source = document["source"]
    assert (source["repo"], source["path"], source["pointer"]) == (CANON_REPO, CANON_PATH, CANON_POINTER)
    commit = source["commit"]
    assert isinstance(commit, str), commit
    assert len(commit) == 40, commit
    assert all(c in "0123456789abcdef" for c in commit), commit
    assert isinstance(document["copy"], dict)
    assert set(document) == {"schema", "source", "copy"}, sorted(document)


def test_the_copy_is_the_custody_contract_at_the_pinned_commit() -> None:
    """正本との一致: pin の commit の custody-api.json の lenderAvailability の節と JSON として一致(手で直した写しは赤)。"""
    document = _copy_document()
    commit = document["source"]["commit"]
    checkout = _custody_checkout()
    if not (checkout / ".git").exists():
        pytest.fail(
            f"預かり所の契約の正本の checkout が無い: {checkout}(env CUSTODY_CHECKOUT で {CANON_REPO} の checkout を名指す)"
            " — 写しと正本の一致を検められない"
        )
    shown = subprocess.run(
        ["git", "-C", str(checkout), "show", f"{commit}:{CANON_PATH}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if shown.returncode != 0:
        pytest.fail(
            f"{checkout} で pin の commit {commit} の {CANON_PATH} が読めない(git fetch が要る?): {shown.stderr.strip()}"
        )
    canon = _pointer_value(json.loads(shown.stdout), CANON_POINTER)
    copied = document["copy"]
    differing = sorted(key for key in set(canon) | set(copied) if canon.get(key) != copied.get(key))
    assert differing == [], f"写しが正本 {CANON_REPO}@{commit[:8]}{CANON_POINTER} と食い違う鍵: {differing}"


def test_the_classifier_reads_exactly_the_derivation_of_the_copy() -> None:
    """分類の表と窓は写しの導出ちょうど(effects の import の拍の値 = file を読み直して導いた値)。"""
    terms = custody_lender_terms_of(custody_lender_section_of(_copy_document()))
    assert dict(CUSTODY_LENDER_TRANSIENT) == dict(terms.transient)
    assert terms.unanswered_window_ms == CUSTODY_UNANSWERED_WINDOW_MS
    assert _copy_document() == CUSTODY_LENDER_COPY


def test_the_derived_table_names_the_contract_words() -> None:
    """導出の単体: 今日の契約(f32083ed)の 2 行 — 貸与の口の 503 worker-unreachable は時間で晴れる why の 2 語、
    引換の口の 503 master-unreachable は why を読まない(None)。窓 = workerStaleSeconds 90 + workerHeartbeatSeconds 30。
    処理ステージの綴りは契約の stages の鍵と同じ(runner の CustodyStage の語彙)。"""
    section = custody_lender_section_of(_copy_document())
    assert dict(CUSTODY_LENDER_TRANSIENT) == {
        ("lease", 503, "worker-unreachable"): frozenset({"heartbeat-stale", "worker-store-unreadable"}),
        ("redeem", 503, "master-unreachable"): None,
    }
    stale = section["workerStaleSeconds"]
    heartbeat = section["workerHeartbeatSeconds"]
    assert isinstance(stale, int)
    assert isinstance(heartbeat, int)
    assert (stale + heartbeat) * 1000 == CUSTODY_UNANSWERED_WINDOW_MS
    assert CUSTODY_UNANSWERED_WINDOW_MS == 120_000
    stages: tuple[CustodyStage, ...] = (CUSTODY_STAGE_LEASE, CUSTODY_STAGE_REDEEM)
    declared_stages = section["stages"]
    assert isinstance(declared_stages, dict)
    assert set(declared_stages) == set(stages)
    assert {stage for stage, _, _ in CUSTODY_LENDER_TRANSIENT} <= set(stages)


def test_the_retry_interval_fits_inside_the_window() -> None:
    """やり直しの間隔(15 秒)は契約に無い runner の数。固定するのは「窓より短い = 窓の中で少なくとも 1 度はやり直す」の
    関係だけ(比は固定しない — 間隔を変えても窓の導出は動かない)。"""
    assert 0 < CUSTODY_UNANSWERED_RETRY_MS < CUSTODY_UNANSWERED_WINDOW_MS
    assert effects.CUSTODY_UNANSWERED_STATUS == 0


def test_a_new_word_that_clears_with_time_moves_the_table_with_the_copy() -> None:
    """設計 §11 S1: 預かり所が時間で晴れる語を足したら、写しを進めるだけで表が変わる(runner の code は変えない)。"""
    section = json.loads(json.dumps(custody_lender_section_of(_copy_document())))
    section["why"]["worker-restarting"] = {"clearsWithTime": True, "note": "worker が再起動の途中"}
    section["transientRefusals"][0]["why"].append("worker-restarting")
    terms = custody_lender_terms_of(section)
    assert "worker-restarting" in (terms.transient[("lease", 503, "worker-unreachable")] or frozenset())


def test_a_copy_that_contradicts_itself_is_refused() -> None:
    """写しが自分の宣言と食い違う(表が clearsWithTime の偽の語・語彙に無い語を名乗る・数が正でない)時は読まない。"""
    base = custody_lender_section_of(_copy_document())
    standing = json.loads(json.dumps(base))
    standing["transientRefusals"][0]["why"].append("worker-revoked")
    unknown = json.loads(json.dumps(base))
    unknown["transientRefusals"][0]["why"].append("no-such-word")
    zero = json.loads(json.dumps(base))
    zero["workerStaleSeconds"] = 0
    for broken, named in (
        (standing, "not declared to clear with time"),
        (unknown, "not declared to clear with time"),
        (zero, "workerStaleSeconds is not a positive integer"),
    ):
        with pytest.raises(ValueError, match=named):
            custody_lender_terms_of(broken)
    with pytest.raises(ValueError, match=CUSTODY_LENDER_COPY_SCHEMA):
        custody_lender_section_of({"schema": "other", "copy": base})
