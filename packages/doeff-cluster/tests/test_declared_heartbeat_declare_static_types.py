"""worker の拍の読みと綴り(worker/protocol/declared.pyi・worker/protocol/heartbeat.pyi)と宣言の送り(shared/entry/declare.pyi)の
型の宣言の失敗ケース(#2824)。

3 つの module は Hy の module で型の宣言が無かったので、defk にした declared-job-specs・status-rows-json・apply-declaration を `<-` で
受ける使い手の strict に、書き手に直せない赤(Type of "declared_job_specs" is unknown・Type of "status_rows_json" is unknown・受けた値の
Argument type is unknown)が出ていた。→ 3 つの .pyi を道具 doeff_hy.static_stub が .hy の契約(:pre / :post と deff の `#^`)から作る。
.pyi を外すとこの検が赤になる。.pyi が .hy から離れると tests/test_generated_stubs.py(作り直した物 == commit された物)が赤になる。

ここが見るのは、答えの型が使い手の受け方に届く細かさか — 契約を素の `tuple`・`list`・`dict` に戻すと、道具はそのまま写し、使い手には
partially unknown の赤(import の名・`<-` で受けた行・引数)が 12 件出る。test_generated_stubs.py の名の検は全く分からない名(is unknown)
だけを見るので、この粗さはここでしか捕まらない。
"""

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

# 使い手の模擬の世界の 1 拍(状態の行を本番の綴りで heartbeat の本文に載せ、返事の job と task を本番の読みで受ける)と、使い手の
# 宣言の命令(宣言を送り、答えの真偽で終了の番号を決める)と同じ受け方。答えの型は使い手が `<-` に書く型のまま。
PROBE = """\
(require doeff-hy.macros [defk <- val])
(import pathlib [Path])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.service_model [Declaration])
(import doeff_cluster.worker.protocol.declared [declared-job-specs task-specs] doeff_cluster.worker.protocol.heartbeat [status-rows-json heartbeat-body])
(import doeff_cluster.shared.entry.declare [apply-declaration])

(defk probe-beat []
  {:pre [] :post [(: % int)] :tags {:context "probe" :role "protocol"}}
  "状態の行の組・返事の job と task の組の型が読める。"
  (<- rows (get tuple #((get dict #(str object)) ...)) (status-rows-json #()))
  (<- body (get dict #(str object)) (heartbeat-body :name "w" :provides #("probe") :exclusive #() :node "n" :capacity 1 :task-reserve 0 :versions {}
                                                    :statuses (list rows) :endpoint "probe://w" :boot "b" :boot-at 0 :tools {} :kept #()))
  (<- desired (get tuple #(JobSpec ...)) (declared-job-specs [{"name" "web" "entry" "probe" "revision" "r"}]))
  (<- tasks (get tuple #(JobSpec ...)) (task-specs [] (Path "tasks")))
  (+ (len body) (len desired) (len tasks)))

(defk probe-apply [declaration]
  {:pre [(: declaration Declaration)] :post [(: % int)] :tags {:context "probe" :role "main"}}
  "宣言の送りの答えの真偽が読める。"
  (<- applied bool (apply-declaration "http://coordinator" declaration "probe"))
  (if applied 0 1))
"""


@dataclass(frozen=True)
class Checked:
    """型検査の道具の 1 回の結末: 終了の番号と、error の診断(規則・行・文)。"""

    returncode: int
    errors: tuple[tuple[str, int, str], ...]


def _checked(root: Path) -> Checked:
    # 型検査の道具は hook と同じく `python -m doeff_hy.static_check` で撃つ(検の module が道具の中身を import しない)。
    done = subprocess.run(
        [sys.executable, "-m", "doeff_hy.static_check", "--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")],
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    text = done.stdout
    diagnostics = json.loads(text) if text.strip() else []
    return Checked(
        returncode=done.returncode,
        errors=tuple((str(d["rule"]), int(str(d["line"])), str(d["message"])) for d in diagnostics if d["severity"] == "error"),
    )


@needs_pyright
def test_users_of_the_beat_reads_and_the_declaration_get_no_red(tmp_path: Path) -> None:
    # 宣言の答えの型(tuple[JobSpec, ...]・状態の行の組・bool)が、使い手の `<-` の型にそのまま入る(Unknown も食い違いも無い)。
    # 終了の番号 0 も見る(道具が走れずに診断が空 = 緑、にしない)。
    (tmp_path / "probe.hy").write_text(PROBE, encoding="utf-8")
    checked = _checked(tmp_path)
    assert checked.errors == (), checked.errors
    assert checked.returncode == 0, checked
