"""doeff-traverse の examples/ の例が script として最後まで走る事の検。

例は `hy examples/<名>.hy` で走らせる読み物で、どの検も走らせていなかったので、doeff-hy の macro が変わっても
誰も気づかなかった: 2026-04-07 の fa37eaaf1 が fold macro を消し、defk の :pre/:post を必須にした後、
ex09_for_do_when.hy は使ってもいない fold の require で落ちていた(card ki-7e6a11f56bd5 の担い手の所見)。

examples/ の `.hy` は全部この検に入る(新しい例も書いただけで入る)。今の macro で走らない例は BROKEN の表に
理由つきで置き、表の例は今も落ちる事を確かめる — 直した例が表に残ると赤になり、行を外せと名指す。
"""

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

# 今の macro で走らない例(file の名 → 理由)。直したら行を外す。
_FOLD = "消えた fold macro を require して使う(fa37eaaf1 で消えた・Reduce で書き直す)"
BROKEN: dict[str, str] = {
    "ex04_traverse_macro.hy": _FOLD,
    "ex07_proboscis_ema_rewrite.hy": _FOLD,
    "ex08_tqdm_progress.hy": _FOLD,
}


def _examples() -> list[str]:
    return sorted(path.name for path in EXAMPLES.glob("*.hy"))


def _run_example(name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "hy", str(EXAMPLES / name)],
        capture_output=True,
        check=False,
        text=True,
        timeout=50,
    )


@pytest.mark.parametrize("name", [name for name in _examples() if name not in BROKEN])
def test_example_runs(name: str) -> None:
    result = _run_example(name)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_broken_example_is_still_broken(name: str) -> None:
    assert (EXAMPLES / name).is_file(), f"BROKEN の行 {name} の例が examples/ に無い — 行を外す"
    result = _run_example(name)
    assert result.returncode != 0, (
        f"{name} は走るようになった — BROKEN の行(理由: {BROKEN[name]})を外す"
    )
