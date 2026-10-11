"""録りの MCP の server(record_app_server_lines.py の筋書き other-tools が CODEX_HOME に宣言する)。

codex が MCP の道具を呼んだ item(mcpToolCall)の形を録るため、道具 echo を 1 つだけ持つ server を stdio(改行で区切った JSON-RPC)で
立てる。echo は引数 text を ``echo: <text>`` の文の block 1 つで返す。initialize・tools/list・tools/call のほかの要求は「無い method」の
誤りで答え、通知(id の無い行)には答えない。録り以外では使わない。
"""

import json
import sys

PROTOCOL_VERSION = "2025-06-18"
METHOD_NOT_FOUND = -32601
ECHO_TOOL = {"name": "echo", "description": "Echo the text back.",
             "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}


def answer(request: dict[str, object]) -> dict[str, object]:
    """要求 1 つへの JSON-RPC の答え(result か error)を作るため。"""
    match request:
        case {"method": "initialize", "params": {"protocolVersion": str(version)}}:
            result: object = {"protocolVersion": version, "capabilities": {"tools": {}},
                              "serverInfo": {"name": "doeff-codex-recorder", "version": "0"}}
        case {"method": "initialize"}:
            result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "doeff-codex-recorder", "version": "0"}}
        case {"method": "tools/list"}:
            result = {"tools": [ECHO_TOOL]}
        case {"method": "tools/call", "params": {"arguments": {"text": str(text)}}}:
            result = {"content": [{"type": "text", "text": f"echo: {text}"}], "isError": False}
        case _:
            return {"jsonrpc": "2.0", "id": request.get("id"),
                    "error": {"code": METHOD_NOT_FOUND, "message": f"no method {request.get('method')}"}}
    return {"jsonrpc": "2.0", "id": request.get("id"), "result": result}


def main() -> None:
    """stdin の要求を 1 行ずつ読んで答えるため(stdin の EOF で終わる — codex が process を降ろす)。"""
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        if isinstance(request, dict) and "id" in request:
            sys.stdout.write(json.dumps(answer(request)) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
