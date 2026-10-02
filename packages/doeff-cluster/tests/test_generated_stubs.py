"""doeff-cluster の Hy の module の型の宣言のうち、doeff_hy.static_stub が作った .pyi の失敗ケース(#2826・#2841)。

使い手の repo が import する doeff-cluster の `.hy` は .pyi が無く、使い手がその名を使う行を足すと、strict の型の門が書き手に直せない
Unknown の赤で止まった。.pyi は道具が .hy から作る。

- 使い手が import する名を 1 つずつ束ねた検の module に、「型が分からない」の赤が出ない(.pyi を外すと赤になる)。
- 一致の検 = 作り直した物 == commit された物。.hy の公開面(関数の契約・record の欄・定数)を変えたら、
  `python -m doeff_hy.static_stub --write <.hy>` で .pyi を作り直して同じ commit に入れる(忘れると、名指された .hy が並んで赤になる)。
  手で書いた .pyi(先頭に道具の印が無い物)は照らさない。
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from doeff_hy.static_stub import stale_in

SOURCE = Path(__file__).resolve().parents[1] / "src"

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

# 使い手の repo の main が import する名のうち、道具の .pyi で宣言する module の物(module ごと・2026-10-02 の数え)。
USED = {
    "shared.protocol.checkout_reads": ("SENDER-SOURCE-DIR", "checkout-reads", "checkout-root", "checkout-state-at", "file-sha256"),
    "shared.protocol.inbox": ("http-request",),
    "sim.checkout_git_script": ("GitCheckout", "GitRemote", "GitRev", "git-command"),
    "shared.core.job_rules": ("spec-hash",),
    "shared.intent.env_marker_model": ("ENV-MARKER", "FileSha256"),
    "cluster_foundation": ("lease-holder-of", "with-cluster-handlers"),
    "foundation.record_codec": ("DECISION", "EffectCodec", "OUTPUT", "READ", "register", "registered-types", "type-name"),
    "shared.intent.process_model": ("AwaitProcessEnded", "ProcessEnded"),
    "worker.core.env_prepare": ("env-marker->json",),
    "worker.intent.env_prepare_model": ("EnvMarker",),
    "foundation.foundation_check": ("FoundationClosure", "closed?", "foundation-closure"),
    "shared.core.declaring": ("declaring-refusal",),
    "shared.core.semaphore_handlers": ("SemaphoreSession", "cluster-semaphore", "lease-fence"),
    "shared.intent.checkout_model": (
        "CheckoutRoot",
        "CheckoutState",
        "LocalCheckout",
        "ProjectOfCheckout",
        "ReadCheckout",
        "SenderSourceRoot",
    ),
    "shared.intent.runtime_identity_model": ("ModuleOrigin", "ProcessFacts", "RuntimeIdentityMismatch"),
    "shared.protocol.runtime_facts": ("given-runtime-facts", "process-runtime-facts"),
    "worker.core.drain_client": ("DRAIN-DEADLINE-SECONDS", "DRAIN-INTERVAL-SECONDS", "await-drained", "drain-outcome", "ready-of"),
    "coordinator.entry.handler_sets": ("MemoryWalStore",),
    "foundation.wal_store": ("WalStore",),
    "shared.core.lease_rules": ("live-holders", "semaphore-key"),
    "shared.core.runtime_env": ("runtime-env-of-checkouts",),
    "shared.core.runtime_identity": ("check-runtime-identity",),
    "shared.intent.shared_model": ("WriteShared",),
    "shared.protocol.detached": ("detached-path",),
    "shared.protocol.program_codec": ("decode-outcome", "encode-program"),
    "worker.core.worker_rules": ("code-key",),
}


def _probe() -> str:
    """名を 1 つずつ別の名に束ねる検の module(束ねた名の型が Unknown なら、その行に strict の赤が出る)。"""
    imports = [f"(import doeff_cluster.{module} [{' '.join(names)}])" for module, names in USED.items()]
    bindings = [
        f"(setv used-{index} {name})" for index, name in enumerate(name for names in USED.values() for name in names)
    ]
    return "\n".join([*imports, "", *bindings, ""])


def _errors(root: Path) -> list[str]:
    """検の module を、使い手の型の門と同じ strict で検めた赤の文。"""
    done = subprocess.run(
        [sys.executable, "-m", "doeff_hy.static_check", "--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")],
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    diagnostics = json.loads(done.stdout) if done.stdout.strip() else []
    return [f"{d['line']}: {d['rule']}: {d['message']}" for d in diagnostics if d["severity"] == "error"]


@needs_pyright
def test_names_the_users_import_are_not_unknown(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(_probe(), encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if "hy-compile" in e], errors
    assert not [e for e in errors if re.search(r"is unknown|could not be resolved|unknown import symbol", e)], errors


def test_generated_stubs_are_what_the_tool_makes() -> None:
    assert [f"{s.source.relative_to(SOURCE)}: {s.reason}" for s in stale_in(SOURCE)] == []
