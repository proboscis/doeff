"""MCP の引数の型の表 `MCP_JSON_TYPES`(doeff/mcp.py)を差し替えると、defmcp-tool の handler の引数の注記
(macros.hy の `_mcp-handler-param`)と、tool の server の値の検め(doeff_agents の mcp_server_loop の
`_tool-result-with-stack`)の両方が従う事の検(設計検証 lt-K8KC9HXRJBQ4C47YNMNTVKTT4Y の盲検 A・B・card ki-973b4e44d7bc)。

注記が約束する型と、server が handler へ渡す値は、同じ表の行から決まる。どちらかが表を写して持つと(例: server の
側に JSON の型 → Python の型の表を手で書く)、表を替えた時に写した側だけが古い判断のまま残る — 盲検 B では、手で
書いた表が正しい JSON の整数を number として断り、注記・macro・型検査の検は全部通っていた。
"""

import inspect
import sys
import threading
import types

import doeff_hy  # noqa: F401 — registers extensions
import hy
import pytest
from doeff_agents._handlers.mcp_server_loop import _tool_result_with_stack
from doeff_agents._mcp_server import McpToolRequest, McpToolServer

import doeff.mcp
from doeff import McpToolDef, run

# 差し替えた表: integer は文字を受けて str と注記し、表に無かった型 probe-type を足す(他の行は本物の表と同じ)
_PROBE_TABLE = (
    doeff.mcp.McpJsonType("string", "str", (str,)),
    doeff.mcp.McpJsonType("integer", "str", (str,)),
    doeff.mcp.McpJsonType("number", "float", (int, float), as_float=True),
    doeff.mcp.McpJsonType("boolean", "bool", (bool,)),
    doeff.mcp.McpJsonType("array", "list[object]", (list,)),
    doeff.mcp.McpJsonType("object", "dict[str, object]", (dict,)),
    doeff.mcp.McpJsonType("probe-type", "bytes", (str,)),
)

_COUNT_TOOL = """
(require doeff-hy.macros [defmcp-tool])
(defmcp-tool count-tool "count" [{:name "n" :type "integer" :description "n"}] n)
"""

# 本物の表に無い型の tool(差し替えた表の下でだけ定義できる)
_PROBE_TOOL = """
(require doeff-hy.macros [defmcp-tool])
(defmcp-tool probe-tool "probe" [{:name "p" :type "probe-type" :description "p"}] p)
"""


def _define(source: str) -> types.ModuleType:
    """Hy の source を新しい module に読み込む(展開はこの時に表を読む)。"""
    mod = types.ModuleType("mcp_json_type_table_subject")
    sys.modules["mcp_json_type_table_subject"] = mod
    for form in hy.read_many(source):
        hy.eval(form, mod.__dict__, module=mod)
    return mod


def _call(tool: McpToolDef, arguments: dict[str, object]) -> object:
    """本物の server の `_tool-result-with-stack` で tool を 1 回呼び、答え #("tool" ok 値) を返す。"""
    server = McpToolServer((tool,), port=0)
    try:
        request = McpToolRequest(
            tool_name=tool.name, arguments=arguments, event=threading.Event(), holder=[]
        )
        return run(_tool_result_with_stack(server, [], request))
    finally:
        server.server_close()


def _annotations(tool: McpToolDef) -> dict[str, object]:
    return {name: p.annotation for name, p in inspect.signature(tool.handler).parameters.items()}


@pytest.fixture
def probe_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """表 `MCP_JSON_TYPES` を差し替える(展開も server も、呼ぶたびに doeff.mcp の表を読む)。"""
    monkeypatch.setattr(doeff.mcp, "MCP_JSON_TYPES", _PROBE_TABLE)


def test_annotation_and_server_follow_the_real_table() -> None:
    """本物の表: integer の引数は int と注記し、server は整数を通して文字を断る。"""
    tool = _define(_COUNT_TOOL).count_tool
    assert _annotations(tool) == {"n": "int"}
    assert _call(tool, {"n": 3}) == ("tool", True, 3)
    assert _call(tool, {"n": "3"}) == ("tool", False, "argument n: expected integer, got str")


def test_annotation_and_server_follow_the_swapped_table(probe_table: None) -> None:
    """表の行を差し替えると、注記と server の検めが両方とも差し替えた行に従う。表に足した型も両方が受ける。"""
    count_tool = _define(_COUNT_TOOL).count_tool
    assert _annotations(count_tool) == {"n": "str"}
    assert _call(count_tool, {"n": "3"}) == ("tool", True, "3")
    assert _call(count_tool, {"n": 3}) == ("tool", False, "argument n: expected integer, got int")
    probe_tool = _define(_PROBE_TOOL).probe_tool
    assert _annotations(probe_tool) == {"p": "bytes"}
    assert _call(probe_tool, {"p": "x"}) == ("tool", True, "x")


def test_unknown_type_is_refused_by_both(probe_table: None) -> None:
    """表に無い型は、展開(注記を作れない)と schema の組み立て(server が検められない)の両方が断る。"""
    with pytest.raises(Exception, match="知らない型"):
        _define(
            "(require doeff-hy.macros [defmcp-tool])\n"
            '(defmcp-tool bad-tool "bad" [{:name "x" :type "null" :description "x"}] x)'
        )
    with pytest.raises(ValueError, match="is not one of"):
        doeff.mcp.McpParamSchema(name="x", type="null", description="x")
