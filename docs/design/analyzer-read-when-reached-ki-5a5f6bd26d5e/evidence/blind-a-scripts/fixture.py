"""盲検 A の実験の fixture: Ping と Pong の 2 節を持つ handler。両方の節が同じ Program 関数 common を呼ぶ。

common は Far を出す。正しい答え: Ping も Pong も、この handler だけの env では Far が gap になる。
filler の行数で common の module の source の大きさ(初めて読む時の parse の時間)を変える。
"""

import importlib
import sys
import textwrap
import uuid
from pathlib import Path

EFFECTS = """
from doeff_vm import EffectBase


class Ping(EffectBase):
    pass


class Pong(EffectBase):
    pass


class Far(EffectBase):
    pass
"""

COMMON = """
from doeff import do

from {pkg}.effects import Far


@do
def common():
    yield Far()
"""

HANDLERS = """
from doeff import Pass, Transfer

from {pkg}.common import common
from {pkg}.effects import Ping, Pong


def two_clauses(effect, k):
    if isinstance(effect, Ping):
        yield common()
        return (yield Transfer(k, None))
    if isinstance(effect, Pong):
        yield common()
        return (yield Transfer(k, None))
    yield Pass(effect, k)
"""


def make(root: Path, filler: int = 0) -> str:
    """root の下に新しい package を書き、名を返す(sys.path に root を足す)。"""
    root.mkdir(parents=True, exist_ok=True)
    name = f"fxa_{uuid.uuid4().hex[:8]}"
    pkg = root / name
    pkg.mkdir()
    common = textwrap.dedent(COMMON) + "".join(
        f"\n\ndef _filler_{i}(a, b):\n    return [a + b for _ in range(3)]\n" for i in range(filler)
    )
    files = {"__init__.py": "", "effects.py": textwrap.dedent(EFFECTS), "common.py": common, "handlers.py": textwrap.dedent(HANDLERS)}
    for filename, text in files.items():
        (pkg / filename).write_text(text.replace("{pkg}", name), encoding="utf-8")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    return name


def effects(name: str) -> object:
    return importlib.import_module(f"{name}.effects")


def gaps(coverage: object) -> list[str]:
    return [gap.effect.__name__ for gap in coverage.gaps]  # type: ignore[attr-defined]
