"""doeff-cluster の Hy の module の型の宣言のうち、doeff_hy.static_stub が作った .pyi の失敗ケース(#2826・#2841)。

使い手の repo が import する doeff-cluster の `.hy` は .pyi が無く、使い手がその名を使う行を足すと、strict の型の門が書き手に直せない
Unknown の赤で止まった。.pyi は道具が .hy から作る。

- 使い手が import する名を 1 つずつ束ねた検の module に、「型が分からない」の赤が出ない(.pyi を外すと赤になる)。
- 一致の検 = 作り直した物 == commit された物。.hy の公開面(関数の契約・record の欄・定数)を変えたら、
  `python -m doeff_hy.static_stub --write <.hy>` で .pyi を作り直して同じ commit に入れる(忘れると、名指された .hy が並んで赤になる)。
  手で書いた .pyi(先頭に道具の印が無い物)は照らさない。
"""

import shutil
from pathlib import Path

import pytest

from doeff_hy.static_stub import UsedModule, stale_in, strict_errors, unknown_in_users

SOURCE = Path(__file__).resolve().parents[1] / "src"

# 使い手の repo の main が import する名のうち、道具の .pyi で宣言する module の物(module ごと・2026-10-02 の数え)。
USED = (
    UsedModule("shared.protocol.checkout_reads", ("SENDER-SOURCE-DIR", "checkout-reads", "checkout-root", "checkout-state-at", "file-sha256")),
    UsedModule("shared.protocol.inbox", ("http-request",)),
    UsedModule("sim.checkout_git_script", ("GitCheckout", "GitRemote", "GitRev", "git-command")),
    UsedModule("shared.core.job_rules", ("spec-hash",)),
    UsedModule("shared.intent.env_marker_model", ("ENV-MARKER", "FileSha256")),
    UsedModule("shared.entry.cluster_foundation", ("lease-holder-of", "with-cluster-handlers")),
    UsedModule("foundation.record_codec", ("DECISION", "EffectCodec", "OUTPUT", "READ", "register", "registered-types", "type-name")),
    UsedModule("shared.intent.process_model", ("AwaitProcessEnded", "ProcessEnded")),
    UsedModule("worker.core.env_prepare", ("env-marker->json",)),
    UsedModule("worker.intent.env_prepare_model", ("EnvMarker",)),
    UsedModule("foundation.foundation_check", ("FoundationClosure", "closed?", "foundation-closure")),
    UsedModule("shared.core.declaring", ("declaring-refusal",)),
    UsedModule("shared.core.semaphore_handlers", ("SemaphoreSession", "cluster-semaphore", "lease-fence")),
    UsedModule(
        "shared.intent.checkout_model",
        ("CheckoutRoot", "CheckoutState", "LocalCheckout", "ProjectOfCheckout", "ReadCheckout", "SenderSourceRoot"),
    ),
    UsedModule("shared.intent.runtime_identity_model", ("ModuleOrigin", "ProcessFacts", "RuntimeIdentityMismatch")),
    UsedModule("shared.protocol.runtime_facts", ("given-runtime-facts", "process-runtime-facts")),
    UsedModule(
        "worker.core.drain_client",
        ("DRAIN-DEADLINE-SECONDS", "DRAIN-INTERVAL-SECONDS", "await-drained", "drain-outcome", "ready-of"),
    ),
    UsedModule("coordinator.entry.handler_sets", ("MemoryWalStore",)),
    UsedModule("foundation.wal_store", ("WalStore",)),
    UsedModule("shared.core.lease_rules", ("live-holders", "semaphore-key")),
    UsedModule("shared.core.runtime_env", ("runtime-env-of-checkouts",)),
    UsedModule("shared.core.runtime_identity", ("check-runtime-identity",)),
    UsedModule("shared.intent.shared_model", ("WriteShared",)),
    UsedModule("shared.protocol.detached", ("detached-path",)),
    UsedModule("shared.protocol.program_codec", ("decode-outcome", "encode-program")),
    UsedModule("worker.core.worker_rules", ("code-key",)),
    # worker の拍の読みと綴り・宣言の送り(#2824 — 使い手の模擬の世界の 1 拍と宣言の命令が `<-` で受ける defk)。
    UsedModule("worker.protocol.declared", ("DeclaredReply", "declared-job-specs", "declared-reply-of-json")),
    UsedModule("worker.protocol.heartbeat", ("status-rows-json",)),
    UsedModule("shared.entry.declare", ("apply-declaration",)),
    # coordinator と worker の判断・入口(#2841 の残り — 並走の便 #2804・#2819・#2760 の着地の後に道具で作った)。
    UsedModule("coordinator.core.api_policy", ("ALIVE-MARK-MS", "tick")),
    UsedModule("worker.core.policy", ("plan", "records-after", "statuses")),
    UsedModule("coordinator.core.cluster_policy", ("IMAGE-FOLLOW-KEYS", "spec-of-declaration")),
    UsedModule("coordinator.entry.main", ("load-state",)),
    # 子 process の入口(根の job_entry.pyi が読み直していた main — 根の旧い入口を消した後、今の置き場に宣言が無かった)。
    UsedModule("worker.entry.job_entry", ("main",)),
    UsedModule("coordinator.protocol.cluster_json", ("naming-from-json",)),
    UsedModule("coordinator.core.program", ("run-coordinator",)),
    # coordinator の状態と Rollout の宣言の型(#2907 — 手書きの cluster_model.pyi を道具の出力へ置き換えた)。
    UsedModule(
        "coordinator.intent.cluster_model",
        ("ClusterJob", "ClusterNaming", "ClusterState", "RolloutSpec", "RolloutStatus", "TargetView"),
    ),
    # 手元の 1 台の cluster の入口と置き方(#3033 の 2b — 使い手の日次の検が同じ Program を手元の 1 台で走らせる)。
    UsedModule("sim.machine", ("GitSource", "LocalMachine", "local-machine-cluster")),
    # 時刻の物差し(#3366 — 使い手の版上げの名簿の読みが Program で今の時刻を取る)。
    UsedModule("shared.core.clock", ("now-epoch-ms",)),
    # 退きの知らせ(#3672 — 使い手の常駐の job が入れ替えで退く事を出来事で知り、本番の答え手を土台に並べる)。
    UsedModule("worker.intent.retirement_model", ("AwaitRetirement", "Retirement")),
    UsedModule("worker.entry.retirement_notices", ("pipe-retirement-notices",)),
)


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")
def test_names_the_users_import_are_not_unknown(tmp_path: Path) -> None:
    assert unknown_in_users(tmp_path, "doeff_cluster", USED) == ()


def test_generated_stubs_are_what_the_tool_makes() -> None:
    assert [f"{s.source.relative_to(SOURCE)}: {s.reason}" for s in stale_in(SOURCE)] == []


# 使い手の repo の渡し方(置き場の口に WalStore を ByteLog として渡す)を写した検の module(#2972)。
WAL_STORE_AS_BYTE_LOG = """\
(require doeff-hy.macros [defk <-])
(import doeff_cluster.foundation.wal_store [WalStore])
(import doeff_cluster.coordinator.protocol.store [ByteLog])

(defk seq-of [log]
  {:pre [(: log ByteLog)] :post [(: % int)] :tags {:context "probe" :role "judgment"}}
  "置き場の形の欄を読むため。"
  log.seq)

(defk seq-of-wal-store [store]
  {:pre [(: store WalStore)] :post [(: % int)] :tags {:context "probe" :role "judgment"}}
  "WalStore を置き場の形 ByteLog として渡すため。"
  (<- n (seq-of store))
  n)
"""


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")
def test_the_wal_store_satisfies_the_byte_log_shape(tmp_path: Path) -> None:
    # 失敗ケース(#2972): 道具が WalStore の __init__ で置く欄(seq・max_log_bytes・snapshot・log)を .pyi に宣言せず、WalStore は
    # それらを求める ByteLog を満たさなかった — 使い手の repo の正しい渡し方に型の赤が 2 件(17 本目の pin)。
    assert strict_errors(tmp_path, WAL_STORE_AS_BYTE_LOG) == ()
