"""event-loop の macro を、state の型を書いて使う係の型検査の失敗ケース(agora-redesign #3079・#3080)。

macro の展開は、節の値を「state の型か、抜ける印 LoopStop」の注記つきで束ねていた。使い手の pyright strict に、書き手に直せない赤
(Expected type arguments for generic class "LoopStop"・展開の変数と state の型が Unknown)が出た(automation の係の手本を macro で
書いた時に commit の hook が名指した)。型の引数を足しても、注記の型が Program の答えの型の推論へ逆に流れ込んで Unknown が残る。
→ 展開は節の値を注記せずに束ね、実行時は assert で確かめる(静的には state の名へ書き戻す所で state の型として読まれる)。
直しを外すと、ここの検(state の型を書いた係を strict で読む)が赤になる。
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
(require doeff-events.macros [event-loop])
(import doeff_events [TimerFired])

(defk counted []
  {:pre [] :post [(: % int)] :tags {:context "probe" :role "program"}}
  "期限の出来事を数え、終わりの期限で抜ける係(state の型 int を書いた形)。"
  (event-loop [count int 0]
    (:stop _reason)       count
    (TimerFired :tag tag) (if (= tag "end")
                              (stop count)
                              (+ count 1))))
"""

# durable の keeper の形(c3-w48 が #3102 で当たった形): state が record・節の本体が (do (<- …) …)・止めの節の本体が do。
RECORD_MODULE = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.record [defrecord])
(require doeff-events.macros [event-loop])
(import dataclasses [dataclass])
(import doeff_core_effects.effects [slog])
(import doeff_events [TimerFired])

(defrecord Lap
  "周回の state(回った数)。"
  (#^ int passes))

(defk stepped [lap]
  {:pre [(: lap Lap)] :post [(: % Lap)] :tags {:context "probe" :role "program"}}
  "1 周回して次の state を返す。"
  (Lap :passes (+ lap.passes 1)))

(defk keeper []
  {:pre [] :post [(: % Lap)] :tags {:context "probe" :role "program"}}
  "期限ごとに 1 周回し、10 周か終わりの期限で抜ける係(record の state・do の本体・do の止めの節)。"
  (event-loop [lap Lap (Lap :passes 0)]
    (:stop reason)        (do (<- (slog "止め" :reason reason :passes lap.passes))
                              lap)
    (TimerFired :tag tag) (if (= tag "end")
                              (stop lap)
                              (do (<- timed Lap (stepped lap))
                                  (if (> timed.passes 9) (stop timed) timed)))))
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
@pytest.mark.parametrize("source", [MODULE, RECORD_MODULE], ids=["int-state", "record-state-do-bodies"])
def test_a_loop_with_a_typed_state_gets_no_type_errors_from_the_expansion(tmp_path: Path, source: str) -> None:
    (tmp_path / "probe.hy").write_text(source, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert errors == [], errors
