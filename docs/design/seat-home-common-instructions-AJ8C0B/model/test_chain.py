"""実行モデルの正常例(反例を試す前の基準線)。pytest 不要・素の python で走る。"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

import chain


def _fixture(tmp: str):
    home = os.path.join(tmp, "home"); os.makedirs(os.path.join(home, ".claude"))
    canon_md = os.path.join(home, "dotfiles", "claude", "CLAUDE.md")
    canon_skills = os.path.join(home, "dotfiles", "agent", "skills", "probe-skill")
    os.makedirs(os.path.dirname(canon_md)); os.makedirs(canon_skills)
    open(canon_md, "w").write("# common\nCANON_BODY\n")
    open(os.path.join(canon_skills, "SKILL.md"), "w").write("---\nname: probe-skill\n---\n")
    config_dir = os.path.join(home, ".local/state/doeff/agentd-homes/claude/acct")
    declaration = {
        "server": "http://x", "token_file": "/t", "node_name": "n", "state_dir": "/s",
        "backend": "headless", "session_hooks": "inherit",
        "claude_settings_file": "~/dotfiles/claude-hooks/seat-settings.json",
        "claude_memory_file": "~/dotfiles/claude/CLAUDE.md",
        "claude_skills_dir": "~/dotfiles/agent/skills",
    }
    return home, declaration, config_dir


def run(name, fn):
    with tempfile.TemporaryDirectory() as tmp:
        try:
            fn(tmp)
        except Exception as exc:                                  # noqa: BLE001
            print(f"FAIL {name}: {type(exc).__name__}: {exc}"); return 1
    print(f"ok   {name}"); return 0


def t_installs(tmp):
    home, decl, cfg = _fixture(tmp)
    argv, log = chain.launch_beat(decl, home, cfg)
    md = os.path.join(cfg, "CLAUDE.md"); sk = os.path.join(cfg, "skills")
    assert os.path.isfile(md) and not os.path.islink(md), "user 記憶は実体 file"
    assert os.stat(md).st_nlink == 1, "hard link ではない"
    assert open(md).read() == "# common\nCANON_BODY\n"
    assert os.path.islink(sk) and os.readlink(sk).endswith("agent/skills")
    assert os.path.isfile(os.path.join(sk, "probe-skill", "SKILL.md"))
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings["claudeMdExcludes"] == [os.path.join(home, ".claude", "CLAUDE.md")]
    assert any("seat-instructions-installed" in line for line in log)


def t_absent_is_not_fatal(tmp):
    home, decl, cfg = _fixture(tmp)
    os.remove(os.path.join(home, "dotfiles", "claude", "CLAUDE.md"))
    shutil.rmtree(os.path.join(home, "dotfiles", "agent", "skills"))
    argv, log = chain.launch_beat(decl, home, cfg)
    assert not os.path.exists(os.path.join(cfg, "CLAUDE.md"))
    assert any("seat-memory-file-absent" in line for line in log)
    assert any("seat-skills-dir-absent" in line for line in log)
    assert argv[0] == "claude", "起きる(参加も起動も断らない)"


def t_unnamed_is_today(tmp):
    home, decl, cfg = _fixture(tmp)
    decl.pop("claude_memory_file"); decl.pop("claude_skills_dir")
    argv, log = chain.launch_beat(decl, home, cfg)
    assert not os.path.exists(os.path.join(cfg, "CLAUDE.md"))
    assert not os.path.exists(os.path.join(cfg, "skills"))
    assert not any("seat-instructions" in line for line in log)


def t_idempotent_and_no_churn(tmp):
    home, decl, cfg = _fixture(tmp)
    chain.launch_beat(decl, home, cfg)
    sk = os.path.join(cfg, "skills"); before = os.lstat(sk).st_ino
    _, log = chain.launch_beat(decl, home, cfg)
    assert os.lstat(sk).st_ino == before, "既に正しい symlink は張り替えない"
    assert not any("skills=" in line for line in log)


def t_owned_key_collision_is_loud(tmp):
    home, decl, cfg = _fixture(tmp)
    try:
        chain.launch_beat(decl, home, cfg,
                          extra_params={"claude_settings": {"claudeMdExcludes": ["/elsewhere"]}})
    except RuntimeError as exc:
        assert "claudeMdExcludes" in str(exc); return
    raise AssertionError("宣言が doeff の鍵を持っても断られなかった")


def t_cwd_relative_declaration_is_refused(tmp):
    home, decl, cfg = _fixture(tmp)
    decl["claude_memory_file"] = "dotfiles/claude/CLAUDE.md"
    try:
        chain.launch_beat(decl, home, cfg)
    except ValueError as exc:
        assert "絶対 path" in str(exc); return
    raise AssertionError("cwd 相対の宣言が通った")


def t_unknown_key_is_refused(tmp):
    home, decl, cfg = _fixture(tmp)
    decl["claude_agents_dir"] = "~/dotfiles/agent/agents"
    try:
        chain.launch_beat(decl, home, cfg)
    except ValueError as exc:
        assert "宣言に無い鍵" in str(exc); return
    raise AssertionError("知らない鍵が通った")


if __name__ == "__main__":
    bad = 0
    for name, fn in sorted((k[2:], v) for k, v in globals().items() if k.startswith("t_")):
        bad += run(name, fn)
    print("FAILED" if bad else "ALL OK")
    raise SystemExit(1 if bad else 0)
