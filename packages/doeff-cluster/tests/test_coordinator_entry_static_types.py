"""coordinator の起動の読み直しと保存の行の読みの型の宣言(coordinator/entry/main.pyi・coordinator/protocol/cluster_json.pyi)の失敗ケース(#2760)。

main.hy と cluster_json.hy は Hy の module で型の宣言が無かったので、load-state・naming-from-json・task-record-from-json を使う使い手
(使い手の repo の模擬の世界の検)の strict に、書き手に直せない Unknown の赤(Type of "load_state" is unknown ほか)が出る。
#2760 で task-record-from-json を defk にし、#2751 で load-state を defk にしたので、使い手は答えの Program を <- で受ける形に
書き直す — その時に型が読めないと、使い手の型検査の門が新しい赤で止まる。→ 2 つの .pyi で宣言する。宣言を外すと赤になる。
.pyi は doeff_hy.static_stub が .hy から作り(#2826)、実装との一致は tests/test_generated_stubs.py(作り直した物 == commit された物)が検める。
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk <- val])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState TaskRecord])
(import doeff_cluster.coordinator.protocol.store [DurableStore])
(import doeff_cluster.coordinator.protocol.cluster_json [naming-from-json task-record-from-json])
(import doeff_cluster.coordinator.entry.main [load-state])

(defk probe-boot [state-file store now]
  {:pre [(: state-file str) (: store DurableStore) (: now int)] :post [(: % ClusterState)] :tags {:context "probe" :role "program"}}
  "coordinator の起動の読み直しの答えを <- で受ける(使い手の模擬の世界の起動と同じ形)。"
  (<- state ClusterState (load-state state-file store now))
  state)

(defk probe-task [data]
  {:pre [(: data (get dict #(str object)))] :post [(: % TaskRecord)] :tags {:context "probe" :role "program"}}
  "保存の task の行の読みの答えを <- で受ける。"
  (<- task TaskRecord (task-record-from-json data))
  task)

(defk probe-naming [text]
  {:pre [(: text str)] :post [(: % str)] :tags {:context "probe" :role "program"}}
  "名の規則の読みの答えの欄が読める。"
  (val naming (naming-from-json text))
  naming.owner-scope)
"""


def _errors(root: Path) -> list[tuple[str, int, str]]:
    # 型検査の道具は hook と同じく `python -m doeff_hy.static_check` で撃つ(検の module が道具の中身を import しない)。
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
def test_users_of_the_coordinator_boot_read_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors
