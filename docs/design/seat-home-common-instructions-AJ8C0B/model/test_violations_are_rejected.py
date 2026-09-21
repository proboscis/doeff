"""違反例が**意図した理由で**拒否されることを示す(検の弁別力の証明)。

修正前の振る舞いと盲検 B の実装を model に据え直し、`test_counterexamples.py` の
検がそれぞれ**落ちる**こと、かつ落ちる理由が狙った責務違反であることを確かめる。
"""
from __future__ import annotations

import os
import sys
import tempfile

import chain
import test_counterexamples as ce


def expect_failure(name, fn, needle):
    with tempfile.TemporaryDirectory() as tmp:
        try:
            fn(tmp)
        except AssertionError as exc:
            if needle in str(exc):
                print(f"ok   {name}: 狙った理由で拒否 — {str(exc)[:110]}")
                return 0
            print(f"FAIL {name}: 落ちたが理由が違う — {exc}")
            return 1
        except Exception as exc:                                     # noqa: BLE001
            print(f"FAIL {name}: AssertionError 以外で落ちた({type(exc).__name__}: {exc})")
            return 1
    print(f"FAIL {name}: 違反例が**通った**(検に弁別力が無い)")
    return 1


# --- 修正前の A-1: FsLinkArtifact の意味(別実体は触らず target-conflict) -------
def _link_artifact_semantics(target: str, link: str) -> str:
    if os.path.islink(link) or os.path.exists(link):
        try:
            if os.path.samefile(link, target):
                return "unchanged"
        except OSError:
            pass
        return "target-conflict"          # ⚠ 触らない(黙って落ちる)
    os.symlink(target, link)
    return "linked"


# --- 盲検 B の実装: 宣言が読めない日の戻り先を据え付け層が持つ -------------------
_FALLBACK_MEMORY = ["{home}/dotfiles/claude/CLAUDE.md", "{home}/.claude/CLAUDE.md"]
_FALLBACK_SKILLS = ["{home}/dotfiles/agent/skills", "{home}/.claude/skills"]


def _instruction_sources_with_fallbacks(env, agent_type, log, trace=None):
    if agent_type != "claude":
        return {}
    params = {}
    home = os.environ.get("HOME", "")
    for declared, shapes, key, kind in (
        (env.get(chain.CLAUDE_MEMORY_FILE_ENV, ""), _FALLBACK_MEMORY, "claude_memory_text", "read"),
        (env.get(chain.CLAUDE_SKILLS_DIR_ENV, ""), _FALLBACK_SKILLS, "claude_skills_dir", "stat"),
    ):
        candidates = [declared.strip()] if declared.strip() else []
        if home:
            candidates += [shape.format(home=home) for shape in shapes]
        for path in candidates:
            if trace is not None:
                trace.append((kind, path))
            if kind == "read" and os.path.isfile(path):
                params[key] = open(path, encoding="utf-8").read(); break
            if kind == "stat" and os.path.isdir(path):
                params[key] = path; break
    return params


if __name__ == "__main__":
    bad = 0
    print("## 修正前の A-1(FsLinkArtifact の意味をそのまま使う)")
    saved = chain.fs_ensure_symlink
    chain.fs_ensure_symlink = _link_artifact_semantics
    bad += expect_failure("A1_moving_the_canon_repoints_the_home_symlink",
                          ce.t_A1_moving_the_canon_repoints_the_home_symlink,
                          "張り替わっていない")
    chain.fs_ensure_symlink = saved

    print()
    print("## 修正前の A-2(tmp が `path + suffix` 固定)")
    saved_write = chain._write_atomic

    def _fixed_tmp_write(path, text, tmp_suffix=".agentd-tmp"):
        tmp = f"{path}{tmp_suffix}"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)

    chain._write_atomic = _fixed_tmp_write
    bad += expect_failure("A2_two_seats_writing_the_shared_home_do_not_break_each_other",
                          ce.t_A2_two_seats_writing_the_shared_home_do_not_break_each_other,
                          "同拍の書きが落ちた")
    chain._write_atomic = saved_write

    print()
    print("## 盲検 B の実装(据え付け層が宿の file layout の戻り先を持つ)")
    saved_sources = chain.instruction_sources
    chain.instruction_sources = _instruction_sources_with_fallbacks
    bad += expect_failure("B_no_declaration_means_the_home_is_untouched…",
                          ce.t_B_no_declaration_means_the_home_is_untouched_even_with_canon_on_disk,
                          "宣言が無いのに条文が届いた")
    bad += expect_failure("B_the_installer_reads_only_what_the_declaration_named",
                          ce.t_B_the_installer_reads_only_what_the_declaration_named,
                          "宣言が名指していない path を触った")
    chain.instruction_sources = saved_sources

    print()
    print("FAILED" if bad else "ALL OK — 4 件の違反例がすべて狙った理由で拒否された")
    sys.exit(1 if bad else 0)
