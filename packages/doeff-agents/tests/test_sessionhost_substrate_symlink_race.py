"""D8 の据え付けの競り — 本当に 2 process で撃つ焦点の検(受入 11 の追補)。

なぜ逐次の 3 値の検(``sessionhost_substrate_deftests.hy`` の
``test-fs-ensure-symlink-three-outcomes``)では足りないか: 席の家は**資格ごと**で、
1 つの ``CLAUDE_CONFIG_DIR`` を複数の会話が同時に読む。pool の入れ替えの直後は排水中に
溜まった郵便が一斉に手番になるので、同じ資格の複数の席が**空の家へ同拍で**降りる。
逐次に呼ぶ検はこの形を 1 度も通らない。

ここで押さえる 2 つ(計画段の実測 ``docs/design/seat-home-common-instructions-AJ8C0B/
evidence/symlink_install_race.{py,log}`` — 直す前の実装で A = 200 回中 200 回・B = 読みの 52 %):

A  空の家へ 2 席が同拍で張る → 例外 0(落ちた席は起きない)
B  1 席が別の先へ張り替え続ける間、もう 1 席が読み続ける → 根の無い瞬間 0
   (本体は skills の dir を discovery で読むので、根の無い拍に当たった席は user 層 0 件)

⚠ この 2 本だけは ``multiprocessing``(fork)で撃つ。thread では「確認と据え付けの間に
割り込む」拍が GIL の切り替えに掛かり、赤が安定しない。
⚠ 子との待ち合わせと報せに ``Barrier`` / ``Queue`` / ``Event`` を使わない: fork した子が
親の thread の持つ lock を継ぐと deadlock し得る(本体が ``DeprecationWarning`` で名指す形)。
待ち合わせは go file の spin・報せは file と ``is_alive`` ちょうどで、子は lock を 1 つも取らない。
"""

from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path
import pytest

import doeff_hy  # noqa: F401  # registers Hy import hooks for the substrate module

from doeff_agents.sessionhost.substrate import ensure_symlink_outcome

FORK_AVAILABLE = "fork" in mp.get_all_start_methods()
pytestmark = [
    pytest.mark.skipif(
        not FORK_AVAILABLE, reason="需要: fork(親の import を子が継ぐ)— darwin / linux のみ"
    ),
    # 子は lock を取らないので、この検では「多 thread の親からの fork」は危なくない。
    pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning"),
]

CONCURRENT_INSTALL_ROUNDS = 60
RELINK_ROUNDS = 20000
MIN_READS = 200


def _install_worker(go: str, link: str, target: str, report: str) -> None:
    """2 席の据え付けを go file で揃えて 1 回撃つ(子 process の本体)。"""
    while not os.path.exists(go):  # spin — sleep を挟むと拍が揃わない
        pass
    try:
        line = f"outcome:{ensure_symlink_outcome(link, target)}"
    except BaseException as error:  # noqa: BLE001 — 例外の**名前**を数えるのがこの検の目的
        line = f"raised:{type(error).__name__}"
    with open(report, "w", encoding="utf-8") as handle:
        handle.write(line)
    os._exit(0)  # 親から継いだ後始末(atexit / flush)を子で走らせない


def test_two_seats_installing_into_one_empty_home_never_raise(tmp_path: Path) -> None:
    """A: 空の家へ 2 席が同拍で張っても、どちらの席も落ちない。

    直す前: 片方が ``FileExistsError`` で落ちる = その席は起きない(実測 200/200)。
    """
    ctx = mp.get_context("fork")
    raised: dict[str, int] = {}
    outcomes: list[str] = []
    for round_index in range(CONCURRENT_INSTALL_ROUNDS):
        home = tmp_path / f"home-{round_index}"
        target = home / "src"
        target.mkdir(parents=True)
        link = home / "skills"
        go = home / "go"
        reports = [home / f"report-{seat}" for seat in (0, 1)]
        procs = [
            ctx.Process(target=_install_worker, args=(str(go), str(link), str(target), str(report)))
            for report in reports
        ]
        for proc in procs:
            proc.start()
        go.write_text("go", encoding="utf-8")
        for proc in procs:
            proc.join(timeout=60)
            assert proc.exitcode == 0, f"round {round_index}: 子が {proc.exitcode} で終わった"
        for report in reports:
            kind, _, value = report.read_text(encoding="utf-8").partition(":")
            if kind == "raised":
                raised[value] = raised.get(value, 0) + 1
            else:
                outcomes.append(value)
        # 勝ち負けに関わらず、この拍のあとの家は正しい先を指す 1 本の symlink。
        assert link.is_symlink(), f"round {round_index}: link が symlink でない"
        assert os.readlink(link) == str(target), f"round {round_index}: 先が違う"

    assert raised == {}, f"同拍の据え付けで落ちた席が居る: {raised}"
    assert len(outcomes) == 2 * CONCURRENT_INSTALL_ROUNDS
    assert set(outcomes) <= {"linked", "unchanged"}, f"想定外の 3 値: {sorted(set(outcomes))}"


def _relink_worker(link: str, first: str, second: str, rounds: int) -> None:
    """張り替えを繰り返す側(子 process の本体)。終わりは process の死で報せる。"""
    for index in range(rounds):
        ensure_symlink_outcome(link, first if index % 2 else second)
    os._exit(0)


def test_a_reader_never_sees_the_link_missing_while_it_is_repointed(tmp_path: Path) -> None:
    """B: 張り替えの間に読み続けても、根の無い瞬間が 1 度も無い。

    直す前: ``unlink`` → ``symlink`` の 2 手の間が読めてしまう(実測 読みの 52 %)。
    """
    ctx = mp.get_context("fork")
    home = tmp_path / "home"
    first = home / "s1"
    second = home / "s2"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    link = home / "skills"
    assert ensure_symlink_outcome(str(link), str(first)) == "linked"

    proc = ctx.Process(
        target=_relink_worker, args=(str(link), str(first), str(second), RELINK_ROUNDS)
    )
    proc.start()
    reads = 0
    missing = 0
    stale: set[str] = set()
    while proc.is_alive():
        reads += 1
        try:
            seen = os.readlink(link)
        except OSError:
            missing += 1
            continue
        if seen not in (str(first), str(second)):
            stale.add(seen)
    proc.join(timeout=60)
    assert proc.exitcode == 0, f"張り替えの子が {proc.exitcode} で終わった"

    assert missing == 0, f"根の無い瞬間を {missing} / {reads} 回見た"
    assert stale == set(), f"どちらの先でもない綴りを見た: {sorted(stale)}"
    assert reads >= MIN_READS, f"読みが {reads} 回では競りを見張れていない(検が空振り)"
