"""盲検の後に残っていた変更シナリオ S2 / S4 / S5 を**実際に撃つ**(正常例と違反例の両方)。

design.md §7 のとおり、器は最小の実行モデル(chain.py)と据わっている本体の計器
(check_body_contract.py)。本実装ではない。
"""
from __future__ import annotations

import difflib
import importlib.util
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import chain  # noqa: E402
import check_body_contract  # noqa: E402

THIRD_KIND_LINE = (
    '    CarriedSource(key="claude_agents_dir", env="DOEFF_AGENTD_CLAUDE_AGENTS_DIR", '
    'param="claude_agents_dir", kind="dir-link", home_name="agents", label="agents"),\n'
)
ANCHOR = "CARRIED_INSTRUCTION_SOURCES = (\n"


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # dataclass が自分の module を引くため
    spec.loader.exec_module(module)
    return module


def _canon(tmp: str) -> dict:
    """宿の checkout(正本)を作る。"""
    for rel, body in (
        ("dotfiles/claude/CLAUDE.md", "# 共通の条文\n"),
        ("dotfiles/agent/skills/a-skill/SKILL.md", "# a\n"),
        ("dotfiles/agent/agents/a-agent.md", "# agent a\n"),
    ):
        path = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
    return {
        "memory": os.path.join(tmp, "dotfiles/claude/CLAUDE.md"),
        "skills": os.path.join(tmp, "dotfiles/agent/skills"),
        "agents": os.path.join(tmp, "dotfiles/agent/agents"),
    }


# ---------------------------------------------------------------------------
# S2 `effects` — 運ぶ物が 1 種類増える
# ---------------------------------------------------------------------------
def t_S2_positive_a_third_carried_kind_is_one_roster_line(tmp: str) -> str:
    """正常例: 3 種目を足す変更が**名簿の 1 行 + 宣言**に収まり、席まで届く。"""
    src = os.path.join(HERE, "chain.py")
    with open(src, encoding="utf-8") as fh:
        before = fh.readlines()
    assert ANCHOR in before, "名簿の座が見つからない"
    after = list(before)
    after.insert(after.index(ANCHOR) + 1, THIRD_KIND_LINE)

    dst = os.path.join(tmp, "chain_third_kind.py")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.writelines(after)
    diff = list(difflib.unified_diff(before, after, n=0))
    added = [d for d in diff if d.startswith("+") and not d.startswith("+++")]
    removed = [d for d in diff if d.startswith("-") and not d.startswith("---")]
    assert len(added) == 1 and not removed, f"1 行で済んでいない: +{len(added)} -{len(removed)}"

    grown = _load(dst, "chain_third_kind")
    canon = _canon(tmp)
    home = os.path.join(tmp, "home")
    config_dir = os.path.join(home, ".agentd", "claude", "acct")
    declaration = {
        "claude_memory_file": canon["memory"],
        "claude_skills_dir": canon["skills"],
        "claude_agents_dir": canon["agents"],       # 3 種目(宣言の 1 行)
    }
    argv, log = grown.launch_beat(declaration, home, config_dir)
    link = os.path.join(config_dir, "agents")
    assert os.path.islink(link), "3 種目が家に据わっていない"
    assert os.readlink(link) == canon["agents"]
    assert os.path.isfile(os.path.join(link, "a-agent.md")), "席から読めない"
    assert any("agents=linked" in line for line in log), f"名乗りが出ていない: {log}"
    assert "--settings" in argv
    # 運ぶ物が増えても、回る側の関数の本文は 1 文字も変わっていない
    for name in ("join_env_of", "instruction_sources", "install_into_home"):
        assert (getattr(grown, name).__code__.co_code
                == getattr(chain, name).__code__.co_code), f"{name} が変わっている"
    return "名簿 1 行 + 宣言 1 行で 3 種目が席まで届いた(回る側の 3 関数は不変)"


def t_S2_negative_a_kind_outside_the_roster_is_refused_loudly(tmp: str) -> str:
    """違反例: 名簿に無い運び物を宣言に書くと、参加が**声を上げて**断る。

    d8472e1a の壊れ方(wire の受理形で**黙って**落ちる)と逆であることを見る。
    """
    canon = _canon(tmp)
    home = os.path.join(tmp, "home")
    declaration = {
        "claude_memory_file": canon["memory"],
        "claude_agents_dir": canon["agents"],       # 名簿に無い
    }
    try:
        chain.join_env_of(declaration, home)
    except ValueError as exc:
        assert "claude_agents_dir" in str(exc), f"名指していない: {exc}"
        env = chain.join_env_of({"claude_memory_file": canon["memory"]}, home)
        assert "DOEFF_AGENTD_CLAUDE_AGENTS_DIR" not in env
        return f"狙った理由で拒否 — {exc}"
    raise AssertionError("名簿に無い運び物が黙って通った(d8472e1a の壊れ方)")


# ---------------------------------------------------------------------------
# S4 `distribution` — 宿が 3 台から N 台へ / 版が混ざる
# ---------------------------------------------------------------------------
SECTION = "[agentd-join.agentd]"


def _needle(dirpath: str, sources) -> list[tuple[str, str]]:
    """もれなさの針: 母集団を**列挙せず導く**(宣言の節を持つ file 全部)。

    要求する鍵も名簿から導くので、運ぶ物が増えた日にこの針が自動で厳しくなる。
    """
    missing: list[tuple[str, str]] = []
    for name in sorted(os.listdir(dirpath)):
        if not name.endswith(".toml"):
            continue
        with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
            text = fh.read()
        if SECTION not in text:
            continue
        for source in sources:
            if f"{source.key} =" not in text:
                missing.append((name, source.key))
    return missing


def t_S4_positive_the_needle_derives_the_population(tmp: str) -> str:
    """正常例: 4 台目を足した日に、針が**自動で**その宿を数える。"""
    hosts = os.path.join(tmp, "cron_management")
    os.makedirs(hosts)
    good = (f'{SECTION}\nserver = "x"\n'
            'claude_memory_file = "${home}/dotfiles/claude/CLAUDE.md"\n'
            'claude_skills_dir = "${home}/dotfiles/agent/skills"\n')
    for name in ("acp-single-mac.toml", "acp-proboscis-mbp.toml", "acp-pool.toml"):
        with open(os.path.join(hosts, name), "w", encoding="utf-8") as fh:
            fh.write(good)
    with open(os.path.join(hosts, "unrelated.toml"), "w", encoding="utf-8") as fh:
        fh.write("[other]\nkey = 1\n")      # 節を持たない file は母集団の外
    assert _needle(hosts, chain.CARRIED_INSTRUCTION_SOURCES) == [], "緑で始まらない"

    fourth = os.path.join(hosts, "acp-fourth-host.toml")
    with open(fourth, "w", encoding="utf-8") as fh:
        fh.write(f'{SECTION}\nserver = "x"\n')          # 4 台目・鍵を名乗らない
    missing = _needle(hosts, chain.CARRIED_INSTRUCTION_SOURCES)
    assert sorted(missing) == [("acp-fourth-host.toml", "claude_memory_file"),
                               ("acp-fourth-host.toml", "claude_skills_dir")], missing
    with open(fourth, "w", encoding="utf-8") as fh:
        fh.write(good)
    assert _needle(hosts, chain.CARRIED_INSTRUCTION_SOURCES) == [], "直しても赤のまま"
    return "4 台目を足すだけで針が赤くなり、名乗らせると緑に戻った(列挙していない)"


def t_S4_negative_an_older_agentd_refuses_the_new_declaration(tmp: str) -> str:
    """違反例: 鍵を知らない版の agentd は、席を**条文なしで**起こさずに参加を断る。"""
    canon = _canon(tmp)
    declaration = {"server": "x", "claude_memory_file": canon["memory"],
                   "claude_skills_dir": canon["skills"]}
    old_keys = chain.AGENTD_BASE_KEYS          # 鍵を知らない版 = 名簿を持たない
    unknown = sorted(set(declaration) - old_keys)
    assert unknown == ["claude_memory_file", "claude_skills_dir"], unknown
    saved = chain.AGENTD_KEYS
    chain.AGENTD_KEYS = old_keys
    try:
        chain.declared_values_of(declaration)
    except ValueError as exc:
        assert "claude_memory_file" in str(exc)
        return f"狙った理由で拒否 — 旧版は参加を断る(黙って条文なしで起きない): {exc}"
    finally:
        chain.AGENTD_KEYS = saved
    raise AssertionError("旧版が新しい宣言を黙って受けた")


# ---------------------------------------------------------------------------
# S5 `hardware` — 本体の版が動く
# ---------------------------------------------------------------------------
def t_S5_positive_the_instrument_is_green_on_the_seated_body(tmp: str) -> str:
    """正常例: 据わっている本体に対して緑(設計の前提 P3 が生きている)。"""
    body = check_body_contract.DEFAULT_BODY
    assert os.path.isfile(body), f"本体が居ない: {body}"
    result = check_body_contract.inspect(body)
    assert result["verdict"] == "green", result
    return f"green — {body}"


def t_S5_negative_a_moved_contract_turns_the_instrument_red(tmp: str) -> str:
    """違反例: 逐語が 1 つ動いた本体は**赤**(棄権ではない)。同定できない file は棄権。"""
    body = check_body_contract.DEFAULT_BODY
    moved = os.path.join(tmp, "claude-moved.exe")
    needle = b'function wgr(){return CN()!=="local-agent"}'
    replacement = b'function wgr(){return CN()!=="local-agemt"}'   # 同じ長さ
    assert len(needle) == len(replacement)
    shutil.copyfile(body, moved)
    with open(moved, "r+b") as fh:
        data = fh.read()
        at = data.find(needle)
        assert at >= 0, "逐語が元の本体に無い"
        fh.seek(at)
        fh.write(replacement)
    red = check_body_contract.inspect(moved)
    assert red["verdict"] == "red", red
    assert red["missing"] == ["the-gate-fires-only-for-local-agent"], red

    other = os.path.join(tmp, "not-the-body.bin")
    with open(other, "wb") as fh:
        fh.write(b"hello\n" * 1000)
    abstain = check_body_contract.inspect(other)
    assert abstain["verdict"] == "abstain", abstain
    return (f"狙った理由で拒否 — 逐語が動いた版は red(欠け={red['missing']})/ "
            "本体でない file は abstain(3 値が弁別する)")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("t_")]

if __name__ == "__main__":
    failed = 0
    for test in TESTS:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                note = test(tmp)
                print(f"ok   {test.__name__[2:]}: {note}")
            except Exception:
                failed += 1
                print(f"FAIL {test.__name__[2:]}")
                traceback.print_exc()
    print("ALL OK" if not failed else f"{failed} 件 FAIL")
    sys.exit(1 if failed else 0)
