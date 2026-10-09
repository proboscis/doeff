"""使い手の repo が読む doeff_claude_code の名の型の宣言の失敗ケース(#4257)。

使い手の repo は、この package の型の宣言の手書きの写し 6 file を自分の typings/ に持つ。pyright は写しを package の .pyi より先に
読むので、ここで名や欄が増えても、写しに足すまで使い手に見えない(2026-10-09 の日次で約 32 件の赤)。写しを消せない訳は 2 つ:

- doeff_hy.static_stub が作る .pyi の型が、使い手が読む所で写しより粗い: ToolCall.input・FakeReply.tool_input・
  ClaudeSessionSpec.settings が型引数の無い FrozenMap・FakeClaudeWorld の responder・respond・restarted が Incomplete・
  層 2 の effect の答えの型が型引数の無い EffectBase(pyright にはどれも Any か object に見え、取り違えが赤にならない)。
- package の入口の宣言(__init__.pyi)が無く、package そのものを import する使い手の行が strict で「型の宣言が無い」の赤になる。

→ .hy の注記に型を書いて .pyi を道具で作り直し、入口の宣言を置く。ここでは:
- 使い手が読む名ごとに、生成の .pyi が期待の型を持つ事を .pyi の構文の木で照らす(pyright を要しない)。期待の型は写しの宣言の型。
  写しより実装の方が細かい所は実装の型(答えの型 object → 答えの和など)、写しが実装と食い違う所も実装の型(StopReason に
  LIVE_LIMIT は無い — 生かす本数の上限では降ろさない・#4072 の E1b)。照らすのは期待に書いた欄だけ(実装が欄を足しても赤にしない)。
- package そのものを import して package の dir の一覧(__path__)を読む使い手の形に、strict の赤が出ない。
宣言と .hy の一致は tests/test_generated_stubs.py の一致の検が見る。
"""

import ast
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from doeff_hy.static_stub import strict_errors

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "doeff_claude_code"

#: fake の世界の返事の作り手(同期の responder・kleisli の respond)の型 — 写しと同じく呼びの形を問わない callable。
OPTIONAL_CALLABLE = "Callable[..., object] | None"


@dataclass(frozen=True)
class Declared:
    """使い手が読む class 1 つの期待の宣言: 基底・欄の注記・method の形・enum の member(名 → 値)。"""

    bases: tuple[str, ...] = ()
    fields: dict[str, str] = field(default_factory=dict[str, str])
    methods: dict[str, str] = field(default_factory=dict[str, str])
    members: dict[str, str] = field(default_factory=dict[str, str])


EXPECTED: dict[str, dict[str, Declared]] = {
    "values": {
        "ClaudeHome": Declared(fields={"config_dir": "str", "env": "FrozenMap[str]"}),
        "ClaudeSessionSpec": Declared(
            fields={
                "home": "ClaudeHome",
                "cwd": "str",
                "model": "str | None",
                "effort": "str | None",
                # CLI の settings の JSON を深く凍らせた写像。中の値は問わない(写しと同じ object — 型引数を書かない FrozenMap と同じ型を
                # 名指して書く)。
                "settings": "FrozenMap[object]",
                "mcp_servers": "FrozenMap[McpSse | McpStdio]",
                "permission": "BypassAll | AskHost | DenyUnlisted | HomeSettings",
                "autocompact": "AutocompactAuto | AutocompactTokens | None",
                "system_prompt_append": "str | None",
                "cold_resume_prompt": "str | None",
            }
        ),
        "TurnInput": Declared(fields={"text": "str", "ref": "str", "attachments": "tuple[ImageAttachment, ...]"}),
        "ClaudeTurn": Declared(fields={"session_id": "str", "turn_seq": "int"}),
        "FreshSession": Declared(fields={"session_id": "str"}),
        "ResumeSession": Declared(fields={"session_id": "str", "carry": "LinkFromHome | Rebuilt | None"}),
        "ForkSession": Declared(fields={"parent_session_id": "str", "carry": "LinkFromHome | Rebuilt | None"}),
    },
    "lines": {
        "ToolCall": Declared(fields={"id": "str", "name": "str", "input": "FrozenMap[FrozenJson]"}),
        "ToolAnswer": Declared(fields={"id": "str", "text": "str", "is_error": "bool", "non_text_kinds": "tuple[str, ...]"}),
        "ModelWindow": Declared(fields={"model": "str", "context_window": "int | None", "max_output_tokens": "int | None"}),
        "AccountRefusalHit": Declared(fields={"error": "str", "text": "str"}),
        "Usage": Declared(
            fields={
                "input_tokens": "int | None",
                "output_tokens": "int | None",
                "cache_creation_input_tokens": "int | None",
                "cache_read_input_tokens": "int | None",
                "cache_creation_5m_input_tokens": "int | None",
                "cache_creation_1h_input_tokens": "int | None",
                "web_search_requests": "int | None",
                "service_tier": "str | None",
            }
        ),
        "AccountLimitHit": Declared(fields={"window": "str | None", "resets_at": "int | None", "text": "str"}),
        "CompactTrigger": Declared(bases=("StrEnum",), members={"AUTO": "auto", "MANUAL": "manual"}),
        "CompactBoundary": Declared(
            fields={
                "trigger": "CompactTrigger",
                "pre_tokens": "int",
                "post_tokens": "int | None",
                "cumulative_dropped_tokens": "int | None",
                "duration_ms": "int | None",
            }
        ),
    },
    "faults": {
        "ClaudeDropProcess": Declared(bases=("EffectBase[bool]",), fields={"session_id": "str"}),
        "ClaudeForgetSession": Declared(bases=("EffectBase[bool]",), fields={"session_id": "str"}),
        # 写しに無い検の口 — 同じ file の層 2 の effect なので答えの型を同じ形で持つ。
        "ClaudeEmitOutsideTurn": Declared(bases=("EffectBase[bool]",), fields={"session_id": "str"}),
        "ClaudeLiveProcess": Declared(bases=("EffectBase[LiveProcess | NoLiveProcess]",), fields={"session_id": "str"}),
        "StopReason": Declared(
            bases=("StrEnum",),
            members={
                "SESSION_CLOSED": "session-closed",
                "LAUNCH_CHANGED": "launch-changed",
                "OUTSIDE_TURN_OUTPUT": "outside-turn-output",
                "INTERRUPT_SIGNAL": "interrupt-signal",
                "CREDENTIAL_FLOOR": "credential-floor",
            },
        ),
        "LiveProcess": Declared(fields={"launches": "int"}),
        "NoLiveProcess": Declared(fields={"launches": "int", "stopped_because": "StopReason | None"}),
    },
    "effects": {
        "ClaudeStartTurn": Declared(
            bases=("EffectBase[StartTurnOutcome]",),
            fields={"origin": "FreshSession | ResumeSession | ForkSession", "spec": "ClaudeSessionSpec", "input": "TurnInput"},
        ),
        # 本番の handler は添付の断り(AttachmentRefused)も答える(handler.hy の inject-input)。
        "ClaudeInjectInput": Declared(
            bases=("EffectBase[InputQueued | NoTurnInFlight | AttachmentRefused]",),
            fields={"turn": "ClaudeTurn", "input": "TurnInput"},
        ),
        "ClaudeInterruptTurn": Declared(bases=("EffectBase[InterruptRequested | NoTurnInFlight]",), fields={"turn": "ClaudeTurn"}),
        "ClaudeReadTurnEvents": Declared(
            bases=("EffectBase[TurnEventPage | UnknownTurn]",),
            fields={"turn": "ClaudeTurn", "after_seq": "int", "wait_up_to": "float"},
        ),
        "ClaudeAnswerPermission": Declared(
            bases=("EffectBase[Answered | NoSuchRequest]",),
            fields={"turn": "ClaudeTurn", "request_id": "str", "answer": "Allow | Deny"},
        ),
        "ClaudeCloseSession": Declared(
            bases=("EffectBase[SessionClosed | ProcessStillAlive]",), fields={"session_id": "str", "reason": "str"}
        ),
        "ClaudeSessionStatus": Declared(
            bases=("EffectBase[SessionStatus]",), fields={"home": "ClaudeHome", "cwd": "str", "session_id": "str"}
        ),
        "ClaudeExportSession": Declared(
            bases=("EffectBase[ExportSessionOutcome]",), fields={"home": "ClaudeHome", "cwd": "str", "session_id": "str"}
        ),
        "ClaudeWarmSession": Declared(
            bases=("EffectBase[WarmSessionOutcome]",),
            fields={"origin": "FreshSession | ResumeSession", "spec": "ClaudeSessionSpec"},
        ),
        "ClaudeLiveLimitExceeded": Declared(
            bases=("EffectBase[None]",), fields={"session_id": "str", "live": "int", "limit": "int", "warm": "bool"}
        ),
        "TurnStarted": Declared(fields={"turn": "ClaudeTurn", "session_id": "str"}),
        "SessionWarmed": Declared(fields={"session_id": "str"}),
        "SessionExported": Declared(fields={"jsonl_text": "str"}),
        "SessionNotFound": Declared(fields={"session_id": "str"}),
        "SessionIdInUse": Declared(fields={"session_id": "str"}),
        "TurnInFlight": Declared(fields={"turn": "ClaudeTurn"}),
        "CarryRefused": Declared(fields={"detail": "str"}),
        "LaunchFailed": Declared(fields={"exit_code": "int | None", "stderr_tail": "str"}),
    },
    "fake": {
        "StopHookRejection": Declared(fields={"answer": "str", "reason": "str"}),
        "FakeReply": Declared(
            fields={
                "text": "str",
                "tool_seconds": "float",
                "needs_permission": "bool",
                "fail": "str | None",
                "lose": "str | None",
                "lose_exit_code": "int | None",
                "lose_stderr": "str | None",
                "usage": "Usage",
                "cost_usd": "float | None",
                "lines": "int",
                "think_seconds": "float",
                "interrupt_receipt": "bool",
                "deltas": "int",
                "tool_input": "FrozenMap[FrozenJson]",
                "tool_output": "str",
                "tool_error": "bool",
                "last_call_usage": "Usage | None",
                "last_call_model": "str | None",
                "model_windows": "tuple[ModelWindow, ...]",
                "account_refusal": "AccountRefusalHit | None",
                "stop_hook_rejections": "tuple[StopHookRejection, ...]",
                "account_limit": "AccountLimitHit | None",
                "compactions": "tuple[CompactBoundary, ...]",
            }
        ),
        "FakeClaudeWorld": Declared(
            fields={"responder": OPTIONAL_CALLABLE, "respond": OPTIONAL_CALLABLE},
            methods={
                "__init__": (
                    f"def __init__(self, responder: {OPTIONAL_CALLABLE} = None, *, respond: {OPTIONAL_CALLABLE} = None,"
                    " live_limit: int | None = None) -> None: ..."
                ),
                "restarted": "def restarted(self) -> FakeClaudeWorld: ...",
            },
        ),
    },
}

#: 型の別名(使い手が答えの和として名指す物)。
EXPECTED_ALIASES: dict[str, dict[str, str]] = {
    "effects": {
        "StartTurnOutcome": (
            "TurnStarted | SessionNotFound | SessionIdInUse | TurnInFlight | CarryRefused | LaunchFailed | AttachmentRefused"
        ),
        "ExportSessionOutcome": "SessionExported | SessionNotFound",
        "WarmSessionOutcome": "SessionWarmed | SessionNotFound | SessionIdInUse | TurnInFlight | CarryRefused | LaunchFailed",
    },
}


def _type(text: str) -> str:
    """型の式の綴りを揃える(空白・括弧の書き方の違いで食い違いを数えないため)。"""
    return ast.unparse(ast.parse(text, mode="eval").body)


def _declared_module(module: str) -> ast.Module:
    return ast.parse((PACKAGE / f"{module}.pyi").read_text(encoding="utf-8"))


def _classes(module: str) -> dict[str, ast.ClassDef]:
    return {node.name: node for node in _declared_module(module).body if isinstance(node, ast.ClassDef)}


def _bases(node: ast.ClassDef) -> tuple[str, ...]:
    # defeffect の展開は基底を `_doeff_effect_base`(doeff の EffectBase の別名)と書く — 同じ型なので名を揃えて比べる。
    return tuple(ast.unparse(base).replace("_doeff_effect_base", "EffectBase") for base in node.bases)


def _fields(node: ast.ClassDef) -> dict[str, str]:
    return {
        statement.target.id: ast.unparse(statement.annotation)
        for statement in node.body
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
    }


def _signature(function: ast.FunctionDef) -> str:
    """method の形(名・引数と注記と既定値・答え)— 本体は比べない。"""
    shape = ast.FunctionDef(
        name=function.name,
        args=function.args,
        body=[ast.Expr(value=ast.Constant(value=...))],
        decorator_list=[],
        returns=function.returns,
        type_params=[],
    )
    return ast.unparse(ast.fix_missing_locations(shape))


def _methods(node: ast.ClassDef) -> dict[str, str]:
    return {statement.name: _signature(statement) for statement in node.body if isinstance(statement, ast.FunctionDef)}


def _members(node: ast.ClassDef) -> dict[str, object]:
    return {
        statement.targets[0].id: statement.value.value
        for statement in node.body
        if isinstance(statement, ast.Assign)
        and isinstance(statement.targets[0], ast.Name)
        and isinstance(statement.value, ast.Constant)
    }


def _expected_method(source: str) -> str:
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    return _signature(function)


def _mismatches(module: str, name: str, expected: Declared) -> list[str]:
    """期待の宣言と生成の .pyi の食い違いの一覧(空 = 使い手は期待の型で読める)。"""
    node = _classes(module).get(name)
    if node is None:
        return [f"{module}.{name} が宣言に無い"]
    bases = _bases(node)
    fields = _fields(node)
    methods = _methods(node)
    members = _members(node)
    wrong_bases = bool(expected.bases) and bases != tuple(_type(base) for base in expected.bases)
    wrong_members = bool(expected.members) and members != expected.members
    return [
        *([f"基底: 期待 {expected.bases} / 宣言 {bases}"] if wrong_bases else []),
        *(
            f"欄 {key}: 期待 {_type(kind)} / 宣言 {fields.get(key)}"
            for key, kind in expected.fields.items()
            if fields.get(key) != _type(kind)
        ),
        *(
            f"method {key}: 期待 {_expected_method(source)} / 宣言 {methods.get(key)}"
            for key, source in expected.methods.items()
            if methods.get(key) != _expected_method(source)
        ),
        *([f"member: 期待 {expected.members} / 宣言 {members}"] if wrong_members else []),
    ]


@pytest.mark.parametrize(
    ("module", "name"),
    [(module, name) for module, classes in EXPECTED.items() for name in classes],
    ids=[f"{module}.{name}" for module, classes in EXPECTED.items() for name in classes],
)
def test_the_generated_stub_declares_what_the_users_read(module: str, name: str) -> None:
    assert _mismatches(module, name, EXPECTED[module][name]) == []


@pytest.mark.parametrize(
    ("module", "name"),
    [(module, name) for module, aliases in EXPECTED_ALIASES.items() for name in aliases],
    ids=[f"{module}.{name}" for module, aliases in EXPECTED_ALIASES.items() for name in aliases],
)
def test_the_generated_stub_declares_the_answer_aliases(module: str, name: str) -> None:
    aliases = {
        statement.target.id: ast.unparse(statement.value)
        for statement in _declared_module(module).body
        if isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        and ast.unparse(statement.annotation) == "TypeAlias"
        and statement.value is not None
    }
    assert aliases.get(name) == _type(EXPECTED_ALIASES[module][name])


# 使い手の形: package そのものを import し、package の dir の一覧(__path__)から隣の file の path を作る(替え玉の CLI の script の path を作る使い手)。
PACKAGE_ITSELF = """\
(import doeff_claude_code)

(setv where (list doeff_claude_code.__path__))
"""


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")
def test_importing_the_package_itself_is_typed(tmp_path: Path) -> None:
    errors = strict_errors(tmp_path, PACKAGE_ITSELF)
    assert errors == (), errors
