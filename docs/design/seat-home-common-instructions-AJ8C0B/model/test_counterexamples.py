"""盲検 A・B が返した反例を、修正後の設計モデルに対して撃つ。

修正前の振る舞い(= 実物の doeff の現状)は
`counterexamples/repro_A_real_substrate.py` が実測している。ここは**修正後に
正常例が通り、反例が意図した理由で拒否されること**を示す側。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading

import chain
from test_chain import _fixture, run


# ---------------------------------------------------------------- 盲検 A-1
def t_A1_moving_the_canon_repoints_the_home_symlink(tmp):
    """反例 A-1: 正本を canonA → canonB へ移した日、家の skills が張り替わらないと
    席は旧い正本を読み続ける(実物の FsLinkArtifact は target-conflict で黙って落ちた)。"""
    home, decl, cfg = _fixture(tmp)
    other = os.path.join(home, "opt", "agent-canon", "skills", "moved-skill")
    os.makedirs(other)
    open(os.path.join(other, "SKILL.md"), "w").write("---\nname: moved-skill\n---\n")

    chain.launch_beat(decl, home, cfg)
    link = os.path.join(cfg, "skills")
    assert os.readlink(link).endswith("dotfiles/agent/skills")

    decl["claude_skills_dir"] = "~/opt/agent-canon/skills"          # 正本を移した日
    _, log = chain.launch_beat(decl, home, cfg)
    assert os.readlink(link).endswith("opt/agent-canon/skills"), \
        f"張り替わっていない: {os.readlink(link)}"
    assert sorted(os.listdir(link)) == ["moved-skill"], "席が旧い正本を読んでいる"
    assert any("skills=linked" in line for line in log), f"結末を名乗っていない: {log}"


def t_A1b_a_real_entity_in_the_way_is_loud_not_silent(tmp):
    """実体が居る家は**黙って置換しない**(erosion guard)。落ちるなら名前つきで落ちる。"""
    home, decl, cfg = _fixture(tmp)
    os.makedirs(cfg)
    os.makedirs(os.path.join(cfg, "skills"))                        # 実 dir が居る
    try:
        chain.launch_beat(decl, home, cfg)
    except RuntimeError as exc:
        assert "erosion guard" in str(exc)
        return
    raise AssertionError("実体を黙って置換した")


# ---------------------------------------------------------------- 盲検 A-2
def t_A2_two_seats_writing_the_shared_home_do_not_break_each_other(tmp):
    """反例 A-2: 家は同じ資格の複数 session が共有する。固定 tmp では os.replace が
    競って片方の launch が FileNotFoundError で落ちた(実測 123/200)。"""
    path = os.path.join(tmp, "CLAUDE.md")
    a, b = "A" * 62696, "B" * 62696
    errors: list[BaseException] = []
    torn = 0
    for _ in range(200):
        def writer(text):
            try:
                chain._write_atomic(path, text)
            except BaseException as exc:                            # noqa: BLE001
                errors.append(exc)
        ts = [threading.Thread(target=writer, args=(t,)) for t in (a, b)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        got = open(path, encoding="utf-8").read()
        if got not in (a, b):
            torn += 1
    assert not errors, f"同拍の書きが落ちた: {len(errors)} 件 {type(errors[0]).__name__}"
    assert torn == 0, f"torn read {torn} 件"
    residue = [n for n in os.listdir(tmp) if n != "CLAUDE.md"]
    assert not residue, f"tmp の残骸: {residue}"


# ---------------------------------------------------------------- 盲検 B
def _decoys(home: str) -> None:
    """盲検 B の戻り先の綴りに、現に読める file を置く(env HOME も据わっている)。"""
    for rel in ("dotfiles/claude/CLAUDE.md", ".claude/CLAUDE.md"):
        path = os.path.join(home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write("DECOY-CANON\n")
    for rel in ("dotfiles/agent/skills/decoy-skill", ".claude/skills/decoy-skill"):
        path = os.path.join(home, rel)
        os.makedirs(path, exist_ok=True)
        open(os.path.join(path, "SKILL.md"), "w").write("---\nname: decoy-skill\n---\n")


def t_B_no_declaration_means_the_home_is_untouched_even_with_canon_on_disk(tmp):
    """反例 B: 「宣言が読めない日は旧側の値へ戻す」を据え付け層が持つと、**宣言が
    1 鍵も無い宿**へ誰も宣言していない条文が届き、計器は成功を名乗る。

    ⚠ 盲検 B の逃げ道は「検の世界は env が空なので HOME が None ⇒ 戻り先の枝が
    1 度も実行されない」だった ⇒ この検は **HOME を据え、戻り先の綴りに実 file を置く**。"""
    home, decl, cfg = _fixture(tmp)
    _decoys(home)
    decl.pop("claude_memory_file"); decl.pop("claude_skills_dir")
    os.environ["HOME"] = home                                        # 世界に HOME が在る
    trace: list[tuple[str, str]] = []
    _, log = chain.launch_beat(decl, home, cfg, trace=trace)
    assert not os.path.exists(os.path.join(cfg, "CLAUDE.md")), "宣言が無いのに条文が届いた"
    assert not os.path.exists(os.path.join(cfg, "skills")), "宣言が無いのに skills が届いた"
    assert not any("seat-instructions" in line for line in log), f"計器が成功を名乗った: {log}"
    assert trace == [], f"宣言が無いのに正本を探した: {trace}"


def t_B_the_installer_reads_only_what_the_declaration_named(tmp):
    """一般形: 据え付けが触る正本は**宣言が名指した path ちょうど**。
    戻り先・候補列・env からの組み立てはどれもこの不変量を破る。

    ⚠ **degrade の日に撃つ**(宣言は在るが名指した file がその日の checkout に無い)。
    正常な日は候補列も 1 本目で break するので弁別できない — 初版のこの検は
    そこで弁別力が無く、違反例が素通りした(2026-09-21 の再検証で判明し、条件を移した)。"""
    home, decl, cfg = _fixture(tmp)
    _decoys(home)
    os.remove(os.path.join(home, "dotfiles", "claude", "CLAUDE.md"))   # degrade の日
    import shutil
    shutil.rmtree(os.path.join(home, "dotfiles", "agent", "skills"))
    decl["claude_memory_file"] = "~/opt/agent-canon/CLAUDE.md"          # 名指しは別の正本
    decl["claude_skills_dir"] = "~/opt/agent-canon/skills"
    os.environ["HOME"] = home
    trace: list[tuple[str, str]] = []
    _, log = chain.launch_beat(decl, home, cfg, trace=trace)
    named = {os.path.join(home, "opt", "agent-canon", "CLAUDE.md"),
             os.path.join(home, "opt", "agent-canon", "skills")}
    touched = {path for _, path in trace}
    assert touched == named, f"宣言が名指していない path を触った: {sorted(touched - named)}"
    assert not os.path.exists(os.path.join(cfg, "CLAUDE.md")), "名指しが読めないのに条文が届いた"
    assert any("seat-memory-file-absent" in line for line in log), f"不在を名乗っていない: {log}"


def t_B_excludes_branch_is_observable_when_the_world_declares_home(tmp):
    """盲検 B の指摘の裏: `claudeMdExcludes` の枝も HOME が無い世界では観測できない。
    検は HOME を据えて**出口の argv**で見る。"""
    import json
    home, decl, cfg = _fixture(tmp)
    argv, _ = chain.launch_beat(decl, home, cfg)
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings["claudeMdExcludes"] == [os.path.join(home, ".claude", "CLAUDE.md")]


if __name__ == "__main__":
    bad = 0
    for name, fn in sorted((k[2:], v) for k, v in globals().items() if k.startswith("t_")):
        bad += run(name, fn)
    print("FAILED" if bad else "ALL OK")
    sys.exit(1 if bad else 0)
