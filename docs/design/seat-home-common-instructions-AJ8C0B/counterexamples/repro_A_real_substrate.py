"""盲検 A の反例を**実物の substrate handler**で撃つ(逐語の転写ではない)。

1. 正本を移した日(canonA → canonB)、`<家>/skills` の whole-dir symlink が
   `FsLinkArtifact` では張り替わらず、黙って `target-conflict` になる。
2. 同じ家へ 2 席が同拍で `FsWriteTextAtomic` を撃つと、tmp path が
   `path + suffix` で書き手ごとに一意でないため os.replace が競う。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading

import hy  # noqa: F401
from doeff import run
from doeff.do import do
from doeff_agents.sessionhost import effects as fx
from doeff_agents.sessionhost.substrate import real_substrate


def _run(program):
    return run(real_substrate("tmux")(program))


@do
def link(source, target):
    outcome = yield fx.FsLinkArtifact(source_path=source, target_path=target)
    return outcome


@do
def write(path, text):
    yield fx.FsWriteTextAtomic(path=path, text=text, tmp_suffix=".agentd-tmp")
    return None


def case_relink() -> int:
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        canon_a = os.path.join(tmp, "canonA"); os.makedirs(canon_a)
        canon_b = os.path.join(tmp, "canonB"); os.makedirs(canon_b)
        open(os.path.join(canon_a, "a-skill.md"), "w").write("A")
        open(os.path.join(canon_b, "b-skill.md"), "w").write("B")
        home = os.path.join(tmp, "home"); os.makedirs(home)
        target = os.path.join(home, "skills")

        first = _run(link(canon_a, target))
        second = _run(link(canon_b, target))          # 正本を移した日の 2 回目
        points_at = os.readlink(target)
        seat_sees = sorted(os.listdir(target))
        print(f"  1st  outcome={first!r}")
        print(f"  2nd  outcome={second!r}   (正本を canonB へ移した拍)")
        print(f"  家の link の先 = {os.path.basename(points_at)}")
        print(f"  席が読む skills = {seat_sees}")
        if second != "target-conflict":
            print("  ! 予想外: 2 回目が target-conflict でない"); bad += 1
        if points_at != canon_a:
            print("  ! 予想外: link が張り替わった"); bad += 1
        if seat_sees != ["a-skill.md"]:
            print("  ! 予想外: 席が新しい正本を読んだ"); bad += 1
        print("  ⇒ 張り替えは値 'target-conflict' で**黙って**落ち、席は旧い正本を読み続ける"
              if bad == 0 else "  ⇒ 反例は不成立")
    return bad


def case_concurrent_write() -> int:
    """同じ家へ 2 席が同拍で書く。tmp が書き手ごとに一意でないので replace が競う。"""
    text_a, text_b = "A" * 62696, "B" * 62696
    errors, torn = [], 0
    rounds = 200
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "CLAUDE.md")
        for _ in range(rounds):
            box: list[BaseException] = []

            def writer(text):
                try:
                    _run(write(path, text))
                except BaseException as exc:                     # noqa: BLE001
                    box.append(exc)

            ts = [threading.Thread(target=writer, args=(t,)) for t in (text_a, text_b)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            errors.extend(box)
            got = open(path, encoding="utf-8").read()
            if got not in (text_a, text_b):
                torn += 1
    kinds = {}
    for exc in errors:
        kinds[type(exc).__name__] = kinds.get(type(exc).__name__, 0) + 1
    print(f"  {rounds} 回 × 2 席: 例外 {len(errors)} 件 {kinds}  torn(中身が A でも B でもない) {torn} 件")
    print("  ⇒ 同拍の 2 席で片方の launch が例外で落ちる(席が起きない)"
          if errors else "  ⇒ この機体では競合を観測できなかった")
    return 0 if errors else 1


if __name__ == "__main__":
    bad = 0
    print("## 反例 A-1 — 正本を移した日に whole-dir symlink が張り替わらない(実物の substrate)")
    bad += case_relink()
    print()
    print("## 反例 A-2 — 同じ家への同拍の書きが競う(実物の substrate)")
    bad += case_concurrent_write()
    print()
    print("反例は成立" if bad == 0 else f"不成立の項 {bad} 件")
    sys.exit(0)
