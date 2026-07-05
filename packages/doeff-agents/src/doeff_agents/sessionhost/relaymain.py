"""report-result-mcp stdio relay(oracle main.rs:761-959).

stdio MCP server(initialize / ping / tools/list / tools/call)として振る舞い、
`report_result` tool call を agentd socket の `session.report_result` RPC へ
中継する。DB / lease / serve loop には一切触らない(oracle と同じ性質)。

STDLIB-ONLY 制約(レイテンシ物理): この relay は agent の唯一の result data
channel であり、report の着地は monitor の turn-end stability 窓(tick 2 回
≈ 数百 ms)より速くなければならない。Rust oracle の relay は spawn から
ms 桁で report を着地させる。relay が Hy runtime + doeff の import 連鎖
(~170ms)を払うと report が turn-end に敗け、golden path で solicitation を
1 回焼く(S1 conformance failure として実測)。よってこのモジュールは
stdlib 以外を import してはならず、hostmain の subcommand dispatch も
Hy host の import より先にここへ分岐する。
"""

import json
import socket
import sys
from typing import Any

MCP_PROTOCOL_VERSION = "2024-11-05"
REPORT_RESULT_MCP_SUBCOMMAND = "report-result-mcp"


def mcp_result(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def mcp_error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def mcp_tool_error(msg_id: Any, text: str) -> dict:
    """tool-level エラー(well-formed tools/call 応答 + isError — transport
    ではなく agent が扱う。oracle mcp_tool_error :961-972)。"""
    return mcp_result(
        msg_id,
        {"content": [{"type": "text", "text": f"Error: {text}"}], "isError": True},
    )


def report_result_tool_def() -> dict:
    return {
        "name": "report_result",
        "description": (
            "Report this session's final structured result to agentd. "
            "Call exactly once with your result as the `payload` "
            "argument. agentd validates it against the session's "
            "result schema and records it byte-faithfully; if it "
            "responds with a validation error, fix the payload and "
            "call again."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "payload": {
                    "type": "object",
                    "description": (
                        "The result object satisfying the session's result schema."
                    ),
                }
            },
            "required": ["payload"],
        },
    }


def relay_report_result(socket_path: str, session_id: str, payload: Any) -> Any:
    """payload を agentd socket の session.report_result RPC へ中継する
    (oracle relay_report_result :923-951)。ok=false は message ごと raise —
    呼び手(tool call handler)が MCP tool error へ写像する。"""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        try:
            client.connect(socket_path)
        except OSError as e:
            raise RuntimeError(f"connecting to agentd socket {socket_path}: {e}")
        request = {
            "id": 1,
            "method": "session.report_result",
            "params": {"session_id": session_id, "payload": payload},
        }
        with client.makefile("rw", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(request, separators=(",", ":"), ensure_ascii=False) + "\n"
            )
            stream.flush()
            line = stream.readline()
        response = json.loads(line.strip())
        if response.get("ok"):
            return response.get("result")
        raise RuntimeError(response.get("error") or "session.report_result failed")
    finally:
        client.close()


def handle_mcp_message(msg: Any, session_id: str, socket_path: str) -> dict | None:
    """MCP JSON-RPC 1 message の dispatch(oracle handle_mcp_message :817-856)。
    request には応答、notification(id 無し)には None。"""
    if not isinstance(msg, dict):
        return None
    method = msg.get("method", "")
    msg_id = msg.get("id")
    if method == "initialize":
        protocol = (msg.get("params") or {}).get("protocolVersion") or (
            MCP_PROTOCOL_VERSION
        )
        # boot レイテンシ予算のため import はここまで遅延(initialize は
        # report の critical path 上だが、metadata 読みは ~ms で済む)。
        import importlib.metadata

        try:
            version = importlib.metadata.version("doeff-agents")
        except Exception:
            version = "0"
        return mcp_result(
            msg_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "doeff-agentd-report-result",
                    "version": version,
                },
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return mcp_result(msg_id, {})
    if method == "tools/list":
        return mcp_result(msg_id, {"tools": [report_result_tool_def()]})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        if name != "report_result":
            return mcp_tool_error(msg_id, f"unknown tool: {name}")
        arguments = params.get("arguments") or {}
        if "payload" not in arguments:
            return mcp_tool_error(msg_id, "report_result requires a `payload` argument")
        try:
            relay_report_result(socket_path, session_id, arguments["payload"])
        except Exception as e:
            return mcp_tool_error(msg_id, str(e))
        return mcp_result(
            msg_id,
            {"content": [{"type": "text", "text": "result recorded"}], "isError": False},
        )
    if msg_id is None:
        return None
    return mcp_error(msg_id, -32601, f"method not found: {method}")


def run_report_result_mcp(args: list) -> None:
    """stdio MCP server 本体(oracle run_report_result_mcp :763-813)。壊れた
    行は無視(crash は agent の唯一の result channel を殺す)、EOF で終了。"""
    session_id = None
    socket_path = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--session":
            i += 1
            session_id = args[i] if i < len(args) else None
        elif arg == "--socket":
            i += 1
            socket_path = args[i] if i < len(args) else None
        else:
            raise ValueError(f"report-result-mcp: unknown argument: {arg}")
        i += 1
    if session_id is None:
        raise ValueError("report-result-mcp requires --session <id>")
    if socket_path is None:
        raise ValueError("report-result-mcp requires --socket <path>")
    while True:
        line = sys.stdin.readline()
        if line == "":
            break
        trimmed = line.strip()
        if trimmed == "":
            continue
        try:
            msg = json.loads(trimmed)
        except Exception:
            continue
        response = handle_mcp_message(msg, session_id, socket_path)
        if response is not None:
            sys.stdout.write(
                json.dumps(response, separators=(",", ":"), ensure_ascii=False) + "\n"
            )
            sys.stdout.flush()
