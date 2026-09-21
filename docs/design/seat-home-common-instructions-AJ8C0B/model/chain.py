"""提案した連鎖の最小の実行モデル(設計段の実験用・本実装ではない)。

責務の割りは design.md §5 のとおりに写してある。反例を**実際に試す**ための器で、
doeff 本体の code ではない(本実装は Hy の sessionhost 側)。

  dotfiles-canon      … 正本の file(この器では tmp の下の実 file)
  host-declaration    … 宿の宣言(dict)
  join_judgment       … 宣言の読みと門(純関数・path の「形」しか見ない)
  agentd_effects      … env 鍵の綴りと AgentdSettings
  launch_readout      … 起動の拍ごとの読み(env → params)
  claude_home_installer … 家の綴りと据え付け + --settings の組み立て
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass

# --------------------------------------------------------------------------
# agentd_effects — env 鍵の綴りの定義点(ここ 1 つ)
# --------------------------------------------------------------------------
CLAUDE_SETTINGS_FILE_ENV = "DOEFF_AGENTD_CLAUDE_SETTINGS_FILE"
CLAUDE_MEMORY_FILE_ENV = "DOEFF_AGENTD_CLAUDE_MEMORY_FILE"
CLAUDE_SKILLS_DIR_ENV = "DOEFF_AGENTD_CLAUDE_SKILLS_DIR"


@dataclass(frozen=True)
class CarriedSource:
    """席へ運ぶ正本 1 種の**綴りの定義点**(R5 — 盲検後の S2 の実測で畳んだ)。

    欄を 1 つ足す操作が名簿を 4 枚触らせる形は 3 度壊れている(doeff commit d8472e1a)。
    宣言の鍵・env の鍵・params の欄・家の中の名・名乗りの語を **1 つの行**に持ち、
    join / launch / 据え付けはこの名簿を**回る**だけにする。
    """

    key: str            # 宣言の鍵 [agentd].<key>
    env: str            # env の鍵(agentd → 席)
    param: str          # 起動の params の欄
    kind: str           # "file-text"(中身を運ぶ)/ "dir-link"(先を運ぶ)
    home_name: str      # 家の中の名 <CLAUDE_CONFIG_DIR>/<home_name>
    label: str          # 名乗りと node の label の語


CARRIED_INSTRUCTION_SOURCES = (
    CarriedSource(key="claude_memory_file", env=CLAUDE_MEMORY_FILE_ENV,
                  param="claude_memory_text", kind="file-text",
                  home_name="CLAUDE.md", label="memory"),
    CarriedSource(key="claude_skills_dir", env=CLAUDE_SKILLS_DIR_ENV,
                  param="claude_skills_dir", kind="dir-link",
                  home_name="skills", label="skills"),
)

CARRIED_KINDS = frozenset({"file-text", "dir-link"})


def absent_event_of(source: CarriedSource) -> str:
    """名乗りの語も名簿から**導く**(手で 2 度綴らない)。"""
    noun = "file" if source.kind == "file-text" else "dir"
    return f"seat-{source.label}-{noun}-absent"


@dataclass(frozen=True)
class AgentdSettings:
    claude_settings_file: str = ""
    claude_memory_file: str = ""
    claude_skills_dir: str = ""


# --------------------------------------------------------------------------
# join_judgment — 宣言の読みと門(純関数。file を読まない・家を知らない)
# --------------------------------------------------------------------------
AGENTD_BASE_KEYS = frozenset({
    "server", "token_file", "node_name", "state_dir", "backend", "session_hooks",
    "claude_settings_file",
    "ownership", "ownership_proof", "capacity", "places", "work_roots", "revision", "build",
})
# R5: 運ぶ物の鍵は名簿から**導く**。名簿に無い鍵は参加が断る(fail-closed)。
AGENTD_KEYS = AGENTD_BASE_KEYS | {s.key for s in CARRIED_INSTRUCTION_SOURCES}


def declared_values_of(declaration: dict) -> dict:
    """宣言に無い鍵は断る(fail-closed — 今日の doeff と同じ形)。"""
    unknown = sorted(set(declaration) - AGENTD_KEYS)
    if unknown:
        raise ValueError(f"[agentd] に宣言に無い鍵: {', '.join(unknown)}")
    return dict(declaration)


def _path_of(word: str | None, where: str) -> str | None:
    """path の**形**だけを見る(cwd 相対は断る)。存在は見ない = 不在は非致命。"""
    word = (word or "").strip()
    if not word:
        return None
    if not (word.startswith("/") or word == "~" or word.startswith("~/")):
        raise ValueError(f"{where} は絶対 path か ~/… であること(cwd 相対は断る): {word!r}")
    return word


def claude_memory_file_of(text: str | None) -> str | None:
    return _path_of(text, "[agentd].claude_memory_file")


def claude_skills_dir_of(text: str | None) -> str | None:
    return _path_of(text, "[agentd].claude_skills_dir")


def join_env_of(declaration: dict, home: str) -> dict:
    """R5: 運ぶ物は名簿を**回る**。1 種増やしても、この関数は 1 行も変わらない。"""
    values = declared_values_of(declaration)
    env: dict[str, str] = {}
    pairs = [("claude_settings_file", CLAUDE_SETTINGS_FILE_ENV)]
    pairs += [(s.key, s.env) for s in CARRIED_INSTRUCTION_SOURCES]
    for key, env_name in pairs:
        word = _path_of(values.get(key), f"[agentd].{key}")
        if word is None:
            continue
        env[env_name] = word.replace("~", home, 1) if word.startswith("~") else word
    return env


# --------------------------------------------------------------------------
# launch_readout — 起動の拍ごとの読み(env → params)。memoize しない。
# --------------------------------------------------------------------------
def instruction_sources(env: dict, agent_type: str, log: list[str],
                        trace: list[tuple[str, str]] | None = None) -> dict:
    """名指しが無い = 欄を出さない。名指しが在って file が無い = 欄を出さず 1 行名乗る。"""
    if agent_type != "claude":
        return {}
    params: dict[str, str] = {}
    for source in CARRIED_INSTRUCTION_SOURCES:      # R5: 名簿を回る
        path = env.get(source.env, "").strip()
        if not path:
            continue
        if source.kind == "file-text":
            if trace is not None:
                trace.append(("read", path))
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as fh:
                    params[source.param] = fh.read()
                continue
        elif source.kind == "dir-link":
            if trace is not None:
                trace.append(("stat", path))
            if os.path.isdir(path):
                params[source.param] = path
                continue
        else:
            raise RuntimeError(f"運び方 {source.kind!r} を知らない: {source.key}")
        log.append(f"{absent_event_of(source)} {source.env}={path}")
    return params


# --------------------------------------------------------------------------
# claude_home_installer — 家の綴り・据え付け・--settings の組み立て
# --------------------------------------------------------------------------
CLAUDE_USER_MEMORY_FILE = "CLAUDE.md"
CLAUDE_USER_SKILLS_DIR = "skills"
CLAUDE_MD_EXCLUDES_SETTING = "claudeMdExcludes"
CLAUDE_AUTO_MEMORY_DIR_SETTING = "autoMemoryDirectory"
CLAUDE_DISABLE_ALL_HOOKS_SETTING = "disableAllHooks"
CLAUDE_SETTINGS_OWNED_KEYS = frozenset({
    CLAUDE_DISABLE_ALL_HOOKS_SETTING,
    CLAUDE_AUTO_MEMORY_DIR_SETTING,
    CLAUDE_MD_EXCLUDES_SETTING,
})


def _write_atomic(path: str, text: str, tmp_suffix: str = ".agentd-tmp") -> None:
    """R2(盲検 A-2 の修正): tmp は**書き手ごとに一意**。共有の家へ 2 席が同拍で書くと、
    `path + suffix` の固定 tmp では os.replace が FileNotFoundError で競う(実測 123/200)。"""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                               prefix=os.path.basename(path) + tmp_suffix + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def install_into_home(config_dir: str, params: dict, log: list[str]) -> None:
    """家へ据える。運ばれてきた物を実体化するだけ — 中身は判断しない。

    R5: 据え付けも名簿を回る。運び方は 2 種(`file-text` / `dir-link`)で、
    種を増やさない限り 1 種足しても**この関数は 1 行も変わらない**。
    """
    os.makedirs(config_dir, exist_ok=True)
    for source in CARRIED_INSTRUCTION_SOURCES:
        carried = params.get(source.param)
        if not isinstance(carried, str):
            continue
        path = os.path.join(config_dir, source.home_name)
        if source.kind == "file-text":
            # ⚠ 実体 file(symlink/hardlink は本体が user 層で落とす — design.md §2.3)
            _write_atomic(path, carried)
            log.append(f"seat-instructions-installed {source.label}={len(carried.encode())}")
        elif source.kind == "dir-link":
            outcome = fs_ensure_symlink(carried, path)
            # R1(盲検 A-1 の修正): 計器は**結末**を名乗る。意図した先だけを印字すると、
            # 起きなかった張り替えを log が肯定する(FsLinkArtifact の target-conflict の実測)。
            if outcome != "unchanged":
                log.append(
                    f"seat-instructions-installed {source.label}={outcome} target={carried}")
            if outcome == "occupied-by-real-entity":
                raise RuntimeError(
                    f"{path} is a real file/dir where the seat's {source.label} symlink "
                    "belongs (erosion guard) — refusing to overwrite")
        else:
            raise RuntimeError(f"運び方 {source.kind!r} を知らない: {source.key}")


def fs_ensure_symlink(target: str, link: str) -> str:
    """R1: 家の symlink を**正本へ向け直す**動詞(substrate に足す effect の模型)。

    既存の `FsLinkArtifact` は「target が別実体なら触らず `target-conflict`」で、
    正本を移した日に**黙って**張り替わらない(実測 = counterexamples/repro_A_real_substrate.py)。
    置換してよいかの判断は呼び手の持ち物なので、呼び手ごとの effect を持つ。
    結末は 3 値: `unchanged`(既に正しい)/ `linked`(張った・張り替えた)/
    `occupied-by-real-entity`(実体が居る — 黙って置換しない)。
    """
    if os.path.islink(link):
        if os.readlink(link) == target:
            return "unchanged"
        os.unlink(link)
        os.symlink(target, link)
        return "linked"
    if os.path.exists(link):
        return "occupied-by-real-entity"
    os.symlink(target, link)
    return "linked"


def md_excludes_of(home: str | None) -> list[str]:
    """二重読みを落とす 1 本を席の $HOME から導く(宣言に書かせない・glob を使わない)。"""
    if not home:
        return []
    return [os.path.join(home, ".claude", CLAUDE_USER_MEMORY_FILE)]


def build_settings(params: dict, home: str | None, session_hooks: str) -> dict:
    settings: dict = {}
    if session_hooks != "inherit":
        settings[CLAUDE_DISABLE_ALL_HOOKS_SETTING] = True
    memory_dir = params.get("memory_dir")
    if isinstance(memory_dir, str) and memory_dir.strip():
        settings[CLAUDE_AUTO_MEMORY_DIR_SETTING] = memory_dir
    excludes = md_excludes_of(home)
    if excludes:
        settings[CLAUDE_MD_EXCLUDES_SETTING] = excludes
    declared = params.get("claude_settings")
    if declared:
        if not isinstance(declared, dict):
            raise RuntimeError("claude_settings は JSON の object であること")
        if CLAUDE_DISABLE_ALL_HOOKS_SETTING in settings:
            raise RuntimeError("claude_settings は session_hooks=inherit の手番にだけ合流する")
        for key, value in declared.items():
            if key in settings or key in CLAUDE_SETTINGS_OWNED_KEYS:
                raise RuntimeError(f"claude_settings の鍵 {key!r} は doeff が置く鍵と衝突する")
            settings[key] = value
    return settings


def build_argv(params: dict, home: str | None, session_hooks: str) -> list[str]:
    args = ["claude", "--dangerously-skip-permissions"]
    settings = build_settings(params, home, session_hooks)
    if settings:
        args += ["--settings", json.dumps(settings, separators=(",", ":"))]
    return args


# --------------------------------------------------------------------------
# 連鎖の 1 拍(起動の拍)
# --------------------------------------------------------------------------
def launch_beat(declaration: dict, home: str, config_dir: str, agent_type: str = "claude",
                session_hooks: str = "inherit", extra_params: dict | None = None,
                trace: list[tuple[str, str]] | None = None):
    log: list[str] = []
    env = join_env_of(declaration, home)
    params = dict(extra_params or {})
    params.update(instruction_sources(env, agent_type, log, trace))
    install_into_home(config_dir, params, log)
    argv = build_argv(params, home, session_hooks)
    return argv, log
