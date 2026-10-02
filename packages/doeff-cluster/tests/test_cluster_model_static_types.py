"""coordinator の型の宣言(doeff_cluster/coordinator/intent/cluster_model.pyi)の失敗ケース(#2447)。

cluster_model.hy は Hy の module で型の宣言が無かったので、Rollout の宣言の型 RolloutSpec を名指す使い手(使い手の repo の模擬の
世界の反例)の strict に、書き手に直せない Unknown の赤(Type of "RolloutSpec" is unknown ほか)が出た。→ cluster_model.pyi で
宣言する。宣言を外すと、ここの検(欄と要素の型を読む使い手)が赤になる。

.pyi は #2907 から doeff_hy.static_stub が cluster_model.hy から作る(要素の型は .hy の欄の注記に書く)。宣言と実装の一致
(名・欄の順・型)は tests/test_generated_stubs.py の一致の検が見る — 手書きの時代の「欄の名と順・enum の値を照らす」2 本は外した。
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState RolloutSpec RolloutTarget])

(defk probe-old-first [spec]
  {:pre [(: spec RolloutSpec)] :post [(: % RolloutTarget)] :tags {:context "probe" :role "judgment"}}
  "宣言の旧の相手を読む(欄の型が読める)。"
  spec.from-target)

(defk probe-rollouts [state]
  {:pre [(: state ClusterState)] :post [(: % (get list str))] :tags {:context "probe" :role "judgment"}}
  "状態の Rollout の新の相手の名を読む(状態の欄の要素の型が読める)。"
  (lfor r (.values state.rollouts) r.spec.to-target.name))

(defk probe-target [name]
  {:pre [(: name str)] :post [(: % RolloutTarget)] :tags {:context "probe" :role "judgment"}}
  "相手を作る(構成子の欄が読める)。"
  (RolloutTarget :kind "Service" :name name))
"""


def _errors(root: Path) -> list[tuple[str, int, str]]:
    # 型検査の道具は hook と同じく `python -m doeff_hy.static_check` で撃つ。
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "doeff_hy.static_check",
            "--root",
            str(root),
            "--json",
            "--strict",
            "--no-cache",
            str(root / "probe.hy"),
        ],
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    text = done.stdout
    diagnostics = json.loads(text) if text.strip() else []
    return [
        (str(d["rule"]), int(str(d["line"])), str(d["message"]))
        for d in diagnostics
        if d["severity"] == "error"
    ]


@needs_pyright
def test_users_of_rollout_spec_and_cluster_state_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors
