"""defhandler の session val / session var の型検査のための展開(agora-redesign #2293・#2214 / #2279 と同じ種類)の失敗ケース。

実行時の展開は名を `(yield (Get key))` の答え(Some の中身)か初期化の値に束ねる。型検査のための展開がこれをそのまま写すと、
Get の答えに型が無いので名とそれを使う式が全部 Unknown になり、strict で書き手に直せない赤が 1 handler に十数件出た
(vg-w39 が #2251 で実測 — daily_verify の cache を session val へ移す直しが型検査の hook で止まった)。加えて
`doeff_hy.session` に stub が無く、import に「stub が無い」の赤が出た。展開が名を初期化の値に束ね、stub を置けば:

- strict で、session val(型のある dict)と session var(:= で書き換える数)を持つ handler と、:= の無い handler に赤が 0 件。
- 名の型は逃げていない: 文字の session val に数を足せば型の取り違えで赤。
- 実行時の展開は今までどおり(Get / Put の往復 — tests/test_val_var_lazy.py が見る)。
"""

import contextlib
import io
import json
import shutil
from pathlib import Path

import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

EFFECTS = """\
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Tick [(get EffectBase int)]
  #^ int step)

(defclass [(dataclass :frozen True)] Name [(get EffectBase str)]
  #^ str prefix)
"""

PROBE = """\
(require doeff-hy.macros [defk defhandler <- val])
(import probe_effects [Tick Name])

(defhandler remembering
  "Tick を数え、Name の答えを覚える。"
  {:tags {:context "probe" :role "protocol"}}
  (session val answered ((get dict #(str str))))
  (session var count 0)
  (Tick [step]
    (:= count (+ count step))
    (resume count))
  (Name [prefix]
    (when (not-in prefix answered)
      (setv (get answered prefix) (+ prefix "!")))
    (resume (get answered prefix))))

(defhandler labelled
  "Name に固定の前置きで答える(session val だけ — := の無い handler)。"
  {:tags {:context "probe" :role "protocol"}}
  (session val head "x-")
  (Name [prefix]
    (resume {LABEL})))
"""

BASE = {"LABEL": "(+ head prefix)"}


def _render(change: dict[str, str]) -> str:
    text = PROBE
    for key, value in (BASE | change).items():
        text = text.replace("{" + key + "}", value)
    return text


def _check(root: Path, text: str) -> list[dict[str, object]]:
    from doeff_hy.static_check import main

    (root / "probe_effects.hy").write_text(EFFECTS, encoding="utf-8")
    (root / "probe.hy").write_text(text, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--no-cache", "--strict", str(root / "probe.hy")])
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    return [d for d in found if d["severity"] == "error"]


def _line_of(text: str, fragment: str) -> int:
    return next(index for index, line in enumerate(text.splitlines(), 1) if fragment in line)


@needs_pyright
def test_strict_has_no_error_from_session_expansions(tmp_path: Path) -> None:
    assert _check(tmp_path, _render({})) == []


@needs_pyright
def test_session_names_keep_their_types(tmp_path: Path) -> None:
    # head は文字の session val — 数を足せば型の取り違えで赤(名は Unknown へ逃げていない)。
    text = _render({"LABEL": "(+ head 1)"})
    errors = _check(tmp_path, text)
    assert _line_of(text, "(resume (+ head 1))") in [d["line"] for d in errors], errors
