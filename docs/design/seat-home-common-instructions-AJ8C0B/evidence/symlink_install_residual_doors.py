"""本線 ef0eaa55 の ensure-symlink-outcome に残る扉を、動詞そのものを呼んで測る。

測るのは 3 つ:
  扉 1  try の外の syscall(makedirs / symlink)から raise が抜けるか
  扉 2  replace の except OSError が「実体が居る」以外の失敗まで occupied と名乗るか
  扉 3  unchanged の判定が綴りの一致か、指す先の一致か(何回の張り替えになるか)
"""

import os
import platform
import stat
import sys
import tempfile

import hy  # noqa: F401  (Hy の import hook)
from doeff_agents.sessionhost import substrate

VERB = substrate.ensure_symlink_outcome


def call(link, target):
    """動詞の結末を 1 語で。raise はそのまま '語彙の外' として名乗る。"""
    try:
        return VERB(link, target)
    except OSError as error:
        name = type(error).__name__
        return f"RAISED:{name}/{os.strerror(error.errno) if error.errno else '?'}"
    except Exception as error:  # noqa: BLE001 — 何が抜けるかを測るのが目的
        return f"RAISED:{type(error).__name__}"


def canon(base, name="canon"):
    path = os.path.join(base, name)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "a.md"), "w") as handle:
        handle.write("x")
    return path


def door1(base):
    print("-- 扉 1: try の外の syscall から raise が抜けるか --")
    target = canon(base, "d1-canon")

    # 1a: 親の位置に実体 file が居る(lstat は ENOTDIR → 『何も居ない』へ落ちる)
    home = os.path.join(base, "d1a-home")
    with open(home, "w") as handle:
        handle.write("operator の物")
    print(f"  親が実体 file       → {call(os.path.join(home, 'skills'), target)}")

    # 1b: 家が書けない(r-x)
    home = os.path.join(base, "d1b-home")
    os.makedirs(home)
    os.chmod(home, 0o500)
    try:
        print(f"  家が書けない(r-x)  → {call(os.path.join(home, 'skills'), target)}")
    finally:
        os.chmod(home, 0o700)

    # 1c: 親を作れない(祖父が書けない)
    grand = os.path.join(base, "d1c-grand")
    os.makedirs(grand)
    os.chmod(grand, 0o500)
    try:
        link = os.path.join(grand, "home", "skills")
        print(f"  親 dir を作れない   → {call(link, target)}")
    finally:
        os.chmod(grand, 0o700)


def door2(base):
    print("-- 扉 2: replace の except は何を occupied と名乗るか --")
    target = canon(base, "d2-canon")

    # 正しく occupied を名乗るべき形(実体の dir)
    for label, make in (
        ("実体 dir(空)", lambda p: os.makedirs(p)),
        ("実体 dir(中身入り)", lambda p: (os.makedirs(p), open(os.path.join(p, "keep.md"), "w").write("k"))),
        ("実体 file", lambda p: open(p, "w").write("operator の物")),
    ):
        home = os.path.join(base, f"d2-{abs(hash(label)) % 10000}")
        os.makedirs(home)
        link = os.path.join(home, "skills")
        make(link)
        outcome = call(link, target)
        after = "symlink" if os.path.islink(link) else ("dir" if os.path.isdir(link) else "file")
        print(f"  {label:20s} → {outcome:24s} 後に居る物 = {after}")

    # replace の段だけを取り出して、実体が 1 つも居ない失敗が何になるかを見る
    print("  -- replace の段を単独で(動詞の中の except が何を掴むか) --")
    for label, errno_case in (("同じ dir 内の欠けた仮", "ENOENT"),):
        home = os.path.join(base, f"d2-step-{errno_case}")
        os.makedirs(home)
        link = os.path.join(home, "skills")
        staged = os.path.join(home, ".skills.tmp")
        try:
            os.replace(staged, link)  # 仮が無い
            print(f"  {label:20s} → 成功(想定外)")
        except OSError as error:
            seated = os.path.lexists(link)
            print(f"  {label:20s} → {type(error).__name__}/{os.strerror(error.errno)}"
                  f" ・実体は居るか = {seated} ⇒ 動詞なら occupied-by-real-entity を名乗る")


def door3(base):
    print("-- 扉 3: unchanged は綴りの一致か、指す先の一致か --")
    target = canon(base, "d3-canon")
    for label, declared in (
        ("宣言 = 据わっている綴り", target),
        ("宣言に末尾 / が付いた", target + "/"),
        ("宣言に ./ が挟まった", os.path.join(os.path.dirname(target), ".", os.path.basename(target))),
    ):
        home = os.path.join(base, f"d3-{abs(hash(label)) % 10000}")
        os.makedirs(home)
        link = os.path.join(home, "skills")
        os.symlink(target, link)  # 据わっている綴りは正準形
        first = call(link, declared)
        second = call(link, declared)
        third = call(link, declared)
        same_place = os.path.realpath(link) == os.path.realpath(target)
        print(f"  {label:24s} 1 回目={first:9s} 2 回目={second:9s} 3 回目={third:9s}"
              f" 指す先は正しいか={same_place}")


def main():
    print(f"=== {platform.platform()} / python {platform.python_version()} ===")
    print(f"=== 検体 {substrate.__file__} ===")
    # ⚠ 作業 dir は **repo の外**(system の temp)。この計器を repo の中で撃つと、
    #    dir= を自分の隣に向けた版は検体を追跡外の dir として置き去りにする。
    base = tempfile.mkdtemp(prefix="residual-doors-")
    door1(base)
    door2(base)
    door3(base)


if __name__ == "__main__":
    main()
