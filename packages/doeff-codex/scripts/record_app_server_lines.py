"""codex の app-server の stdout の行を録る(tests/recorded/codex-<版>/ の作り方)。

版を固定した本物の codex の binary を、手元に立てた偽の上流(OpenAI の Responses API の SSE を返す HTTP server)につなぎ、JSON-RPC の
要求を stdin へ書いて、stdout の行をそのまま file に書く。口座も network の外も要らない — model の答えだけが偽物で、行の形は本物の
binary が出した物。録る筋書きと比べ:

- two-turns: 1 つの process で thread を始め、ターンを 2 回走らせる(答えの文字の途中・答えの全文・ターンの終わり)。
- interrupt: 遅い答えの途中で turn/interrupt を送る(状態 interrupted の終わり)。
- rate-limit: 上流が 429 で断る(誤りの通知と状態 failed の終わり)。
- usage-limit: 上流が口座の利用枠切れ(429 の usage_limit_reached と枠の header)で断る(codexErrorInfo usageLimitExceeded の誤りと
  枠の通知と状態 failed の終わり)。
- server-overloaded: 上流が提供側の混雑(503 の server_is_overloaded)で断り(codexErrorInfo serverOverloaded の誤りと状態 failed の
  終わり — 文「Selected model is at capacity. …」)、同じ thread の次のターンは答える(混雑の後の試し直しの形)。
- tool-calls: 上流が道具の呼びを順に返す(命令の成功・命令の失敗・apply_patch でのファイルの追加・web 検索)— 道具の item
  (commandExecution・fileChange・webSearch)の始まりと終わりの形。
- background-command: 1 つ目のターンで、短い待ち(yield_time_ms)で戻って後ろで走り続ける命令を呼び、2 つ目のターン(遅い答え)の
  間にその命令が終わる — 前のターンを名乗る命令の終わり(item/completed)が次のターンの間に届く形。
- other-tools: 上流がほかの道具を順に呼ぶ(録りの MCP の server の道具・thread に宣言した動的な道具・画像を見る道具・子の agent を
  始めて待つ)— 道具の item(mcpToolCall・dynamicToolCall・imageView・collabAgentToolCall)の始まりと終わりの形。この筋書きだけ
  CODEX_HOME に録りの MCP の server(record_mcp_server.py)を宣言し、initialize で試験の口(experimentalApi — thread/start の
  dynamicTools)を名乗り、codex からの動的な道具の呼び(item/tool/call)に答える。子の agent の thread の行(子のターンの始まりと
  終わり・答え)も同じ stdout に混ざる。
- code-mode-unavailable: code mode の host(codex-code-mode-host)の無い dir に置いた codex を、code mode だけで道具を呼ぶ設定
  (features.code_mode_only)で起こし、1 つの process でターンを 2 回走らせる(上流はどちらのターンも code mode の道具 exec を 1 回
  呼ぶ)— code mode が使えない警告(warning の通知)がどこに・何回出るかと、道具の呼びが何を返すかの形(card ki-86b1443b22b6)。
- code-mode-host-broken: 比べ — 起動できない host(在るが、起こすとすぐ code 1 で終わる)を隣に置いた codex で同じ 2 ターン(codex
  0.162.1 は host の file が在るかだけを見て警告を出すので、この形では警告が出ない事の実測)。
- exec-json: 比べ — 同じ答えを `codex exec --json` で走らせた行(文字の途中を出さない事の実測)。

使い方(版を上げた時に録り直す。筋書きの名を続けて書くと、その筋書きだけを録る — 既定は全部):

    uv run --no-project python -I packages/doeff-codex/scripts/record_app_server_lines.py <codex の binary> <書く dir> <作業の dir> [筋書き…]

作業の dir は /tmp の外に置く(codex は /tmp の下の CODEX_HOME に助けの binary を作らず、警告の行を stderr に出す)。
"""

import http.server
import json
import os
import queue
import shutil
import socketserver
import struct
import subprocess
import sys
import threading
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

JsonObject = dict[str, object]
Send = Callable[[JsonObject], None]
Wait = Callable[[Callable[[JsonObject], bool], float], JsonObject]

APP_SERVER_ARGS = ("app-server", "--listen", "stdio://")
FAST_PIECES = ("Hel", "lo, ", "wor", "ld.")
SLOW_PIECES = tuple(f"slow-{index} " for index in range(40))
# 利用枠切れの断りが名乗る戻りの刻(epoch 秒)。録り直しても同じ値になるよう固定し、録る日より先の刻にする。
USAGE_RESETS_AT = 1893456000
USAGE_SECONDARY_RESETS_AT = 1893888000
WAIT_SECONDS = 60.0
# 道具の筋書きの目印(要求の入力のどこかに在れば、上流は道具の呼びを返す)と、呼びの順(返した道具の結果の数で次を選ぶ)。
TOOLS_MARK = "TOOL-CALLS"
TOOL_COMMANDS = (
    "echo tool-out",
    "ls no-such-file",
    "apply_patch <<'EOF'\n*** Begin Patch\n*** Add File: note.txt\n+hello\n*** End Patch\nEOF",
)
TOOLS_SEARCH = "doeff effects"
TOOLS_DONE = ("Tools ", "done.")
# 後ろで走る命令の筋書きの目印(1 つ目のターン = 命令を始めて答える・2 つ目のターン = 遅い答えの間に命令が終わる)と命令。
BACKGROUND_MARK = "BG-COMMAND"
BACKGROUND_WAIT_MARK = "BG-WAIT"
BACKGROUND_COMMAND = "sleep 1; echo late-1; sleep 1; echo late-2; sleep 1; echo late-3"
# ほかの道具の筋書きの目印と、子の agent への頼み(子の要求は目印を持たず、この頼みだけを持つ — 上流は答えの全文で答える)。
OTHER_TOOLS_MARK = "OTHER-TOOLS"
SUB_AGENT_MARK = "SUB-AGENT"
SUB_AGENT_TASK = f"{SUB_AGENT_MARK} say hi"
SUB_AGENT_DONE = ("Sub ", "done.")
OTHER_TOOLS_DONE = ("Others ", "done.")
# 録りの MCP の server の名(config.toml の mcp_servers の鍵 — codex は道具を namespace mcp__<名> の下に model へ渡す)と道具の呼び。
MCP_SERVER = "recorder"
MCP_SERVER_SCRIPT = Path(__file__).resolve().parent / "record_mcp_server.py"
MCP_CALL: JsonObject = {"text": "hi"}
# thread/start で宣言する動的な道具と呼び・codex からの呼び(item/tool/call)への答え。
DYNAMIC_TOOL: JsonObject = {"type": "function", "name": "lookup_ticket", "description": "Look up a ticket.",
                            "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}
DYNAMIC_CALL: JsonObject = {"id": "T-1"}
DYNAMIC_ANSWER: JsonObject = {"contentItems": [{"type": "inputText", "text": "ticket T-1: open"}], "success": True}
# 画像を見る道具が開く画像(作業の dir に書く 1 画素の PNG)と、子の agent を待つ上限(codex の下限 10 秒)。
IMAGE_NAME = "pixel.png"
WAIT_AGENT_MS = 10000
# 子の agent の道具の namespace(codex 0.162.1 の multi_agent が model へ渡す名)。
AGENT_NAMESPACE = "multi_agent_v1"
# code mode の筋書きの目印(上流は code mode の道具 exec を 1 回呼び、その結果の後に答えの全文で答える)と、exec に渡す JavaScript。
CODE_MODE_MARK = "CODE-MODE"
CODE_MODE_SOURCE = "text('hi')"
CODE_MODE_DONE = ("Code ", "done.")
# code mode の host の file の名(codex 0.162.1 は自分の実行 file と同じ dir で探す — codex-rs/install-context の code_mode_host_program)と、
# 起動できない host の中身(在るが、起こすとすぐ code 1 で終わる)。
CODE_MODE_HOST = "codex-code-mode-host"
BROKEN_HOST = "#!/bin/sh\nexit 1\n"
# 提供側の混雑の筋書きの目印(最後の入力に在れば、上流は 503 と error.code server_is_overloaded で断る — codex 0.162.1 の
# codex-api/src/api_bridge.rs が CodexErr::ServerOverloaded に読む形)と、断りの body の文(codex は自分の文で名乗るので、中身は偽物)。
OVERLOADED_MARK = "SERVER-OVERLOADED"
OVERLOADED_BODY = {"error": {"code": "server_is_overloaded", "message": "The server is overloaded."}}


def sse_event(stream: IO[bytes], event: JsonObject) -> None:
    """偽の上流が SSE の 1 件を書くため(codex は event と data の 2 行と空行で 1 件と読む)。"""
    stream.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
    stream.flush()


def answer_events(pieces: tuple[str, ...]) -> list[JsonObject]:
    """偽の上流の 1 つの答え(差分 pieces を順に流し、全文と使用量で閉じる)の SSE の列を作るため。"""
    message = {"type": "message", "role": "assistant", "id": "msg_1"}
    usage = {
        "input_tokens": 10,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 4,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 14,
    }
    deltas: list[JsonObject] = [
        {"type": "response.output_text.delta", "item_id": "msg_1", "output_index": 0, "content_index": 0, "delta": piece}
        for piece in pieces
    ]
    text = "".join(pieces)
    return [
        {"type": "response.created", "response": {"id": "resp_1"}},
        {"type": "response.output_item.added", "output_index": 0, "item": {**message, "content": []}},
        *deltas,
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {**message, "content": [{"type": "output_text", "text": text, "annotations": []}]},
        },
        {"type": "response.completed", "response": {"id": "resp_1", "usage": usage}},
    ]


def tool_events(step: int) -> list[JsonObject]:
    """道具の筋書きの step 番目の答え(TOOL_COMMANDS の命令を exec_command で呼ぶ・尽きたら web 検索と答えの全文)の SSE の列を作るため。"""
    if step >= len(TOOL_COMMANDS):
        search: JsonObject = {"type": "web_search_call", "id": "ws_1", "status": "completed",
                              "action": {"type": "search", "query": TOOLS_SEARCH}}
        answer = answer_events(TOOLS_DONE)
        # 検索の始まりの item は検索の語(action)を持たない — 上流は語を検索の終わりの item で名乗る。
        return [
            answer[0],
            {"type": "response.output_item.added", "output_index": 0,
             "item": {"type": "web_search_call", "id": "ws_1", "status": "in_progress"}},
            {"type": "response.output_item.done", "output_index": 0, "item": search},
            *answer[1:],
        ]
    call: JsonObject = {"type": "function_call", "id": f"fc_{step}", "call_id": f"call_{step}", "name": "exec_command",
                        "arguments": json.dumps({"cmd": TOOL_COMMANDS[step]})}
    usage = {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 4,
             "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 14}
    return [
        {"type": "response.created", "response": {"id": f"resp_tool_{step}"}},
        {"type": "response.output_item.added", "output_index": 0, "item": {**call, "arguments": ""}},
        {"type": "response.output_item.done", "output_index": 0, "item": call},
        {"type": "response.completed", "response": {"id": f"resp_tool_{step}", "usage": usage}},
    ]


def background_events(step: int) -> list[JsonObject]:
    """後ろで走る命令の筋書きの 1 つ目のターンの答え(step 0 = 短い待ちで戻る命令を exec_command で呼ぶ・ほか = 答えの全文)の SSE の
    列を作るため。"""
    if step >= 1:
        return answer_events(("Started.",))
    call: JsonObject = {"type": "function_call", "id": "fc_bg", "call_id": "call_bg", "name": "exec_command",
                        "arguments": json.dumps({"cmd": BACKGROUND_COMMAND, "yield_time_ms": 250})}
    usage = {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 4,
             "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 14}
    return [
        {"type": "response.created", "response": {"id": "resp_bg_0"}},
        {"type": "response.output_item.added", "output_index": 0, "item": {**call, "arguments": ""}},
        {"type": "response.output_item.done", "output_index": 0, "item": call},
        {"type": "response.completed", "response": {"id": "resp_bg_0", "usage": usage}},
    ]


def call_events(step: int, name: str, arguments: JsonObject, namespace: str | None = None) -> list[JsonObject]:
    """ほかの道具の筋書きの 1 つの答え(道具 name を arguments で 1 回呼ぶ — namespace の下の道具は namespace を名乗る)の SSE の列を作るため。"""
    call: JsonObject = {"type": "function_call", "id": f"fc_other_{step}", "call_id": f"call_other_{step}", "name": name,
                        "arguments": json.dumps(arguments)}
    if namespace is not None:
        call["namespace"] = namespace
    usage = {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 4,
             "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 14}
    return [
        {"type": "response.created", "response": {"id": f"resp_other_{step}"}},
        {"type": "response.output_item.added", "output_index": 0, "item": {**call, "arguments": ""}},
        {"type": "response.output_item.done", "output_index": 0, "item": call},
        {"type": "response.completed", "response": {"id": f"resp_other_{step}", "usage": usage}},
    ]


def spawned_agent(body: JsonObject) -> str:
    """ほかの道具の筋書きで、子の agent を始めた呼びの結果(spawn_agent の答え — JSON の agent_id)から、待つ子の id を読むため。"""
    outputs = [item for item in body.get("input", []) if isinstance(item, dict) and item.get("type") == "function_call_output"]
    spawned = json.loads(str(outputs[-1]["output"]))
    return str(spawned["agent_id"])


def other_tool_events(step: int, body: JsonObject, image: str) -> list[JsonObject]:
    """ほかの道具の筋書きの step 番目の答え(MCP の道具・動的な道具・画像を見る道具・子の agent を始める・子を待つ・尽きたら答えの全文)の
    SSE の列を作るため。image = 画像を見る道具に渡す画像の path。"""
    if step == 0:
        return call_events(step, "echo", MCP_CALL, f"mcp__{MCP_SERVER}")
    if step == 1:
        return call_events(step, str(DYNAMIC_TOOL["name"]), DYNAMIC_CALL)
    if step == 2:
        return call_events(step, "view_image", {"path": image})
    if step == 3:
        return call_events(step, "spawn_agent", {"message": SUB_AGENT_TASK}, AGENT_NAMESPACE)
    if step == 4:
        return call_events(step, "wait_agent", {"targets": [spawned_agent(body)], "timeout_ms": WAIT_AGENT_MS}, AGENT_NAMESPACE)
    return answer_events(OTHER_TOOLS_DONE)


def code_mode_events(step: int) -> list[JsonObject]:
    """code mode の筋書きの 1 つのターンの step 番目の答え(step 0 = code mode の道具 exec を custom の道具の呼びで 1 回呼ぶ・ほか = 答えの
    全文)の SSE の列を作るため。"""
    if step >= 1:
        return answer_events(CODE_MODE_DONE)
    call: JsonObject = {"type": "custom_tool_call", "id": "ctc_cm", "call_id": "call_cm", "name": "exec", "input": CODE_MODE_SOURCE}
    usage = {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 4,
             "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 14}
    return [
        {"type": "response.created", "response": {"id": "resp_cm_0"}},
        {"type": "response.output_item.added", "output_index": 0, "item": {**call, "input": ""}},
        {"type": "response.output_item.done", "output_index": 0, "item": call},
        {"type": "response.completed", "response": {"id": "resp_cm_0", "usage": usage}},
    ]


def tool_step(body: JsonObject, output_type: str = "function_call_output") -> int:
    """道具の筋書きの何番目の呼びかを、要求の入力に返ってきた道具の結果(output_type の item — 既定は function_call_output)の数で数える
    ため。"""
    inputs = body.get("input", [])
    return sum(1 for item in inputs if isinstance(item, dict) and item.get("type") == output_type) \
        if isinstance(inputs, list) else 0


def code_mode_step(body: JsonObject) -> int:
    """code mode の筋書きの今のターンで何番目の呼びかを、最後の利用者の入力より後に返ってきた custom の道具の結果の数で数えるため
    (2 つ目のターンの要求は 1 つ目のターンの結果も持つ)。"""
    inputs = body.get("input", [])
    if not isinstance(inputs, list):
        return 0
    users = [index for index, item in enumerate(inputs) if isinstance(item, dict) and item.get("role") == "user"]
    after = inputs[users[-1] + 1:] if users else inputs
    return tool_step({"input": after}, "custom_tool_call_output")


class Upstream(http.server.BaseHTTPRequestHandler):
    """偽の上流: 要求の最後の入力に RATE-LIMIT が在れば 429、USAGE-LIMIT が在れば利用枠切れの 429、SLOW が在れば遅い答え、入力の
    どこかに TOOL-CALLS が在れば道具の筋書き、BG-COMMAND が在れば後ろで走る命令の筋書き、最後の入力に BG-WAIT が在れば遅い答え、
    OTHER-TOOLS が在ればほかの道具の筋書き、SUB-AGENT が在れば(子の agent の要求)答えの全文、ほかは 4 つの差分の答え。"""

    protocol_version = "HTTP/1.1"
    request_log: IO[str]
    # ほかの道具の筋書きで画像を見る道具に渡す画像の path(録る前に main が置く)。
    image_path: str = ""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - BaseHTTPRequestHandler の引数の名のまま
        """要求ごとの既定の log の行を stderr に出さないため(要求は request_log に JSON で残す)。"""
        return

    def do_POST(self) -> None:
        """codex の Responses API の呼び 1 つに、入力の目印で選んだ筋書きで答えるため。"""
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))) or b"{}")
        said = json.dumps(body.get("input", []), ensure_ascii=False)[-400:]
        self.request_log.write(json.dumps({"method": "POST", "path": self.path, "model": body.get("model"),
                                           "stream": body.get("stream")}) + "\n")
        self.request_log.flush()
        if "RATE-LIMIT" in said:
            refusal = b'{"error": {"type": "rate_limit_exceeded", "message": "Rate limit reached"}}'
            self.send_response(429)
            self.send_header("content-type", "application/json")
            self.send_header("retry-after", "120")
            self.send_header("content-length", str(len(refusal)))
            self.end_headers()
            self.wfile.write(refusal)
            return
        if "USAGE-LIMIT" in said:
            self.refuse_usage_limit()
            return
        # 混雑の目印は最後の入力の item だけで見る(同じ thread の次のターンの要求は、前のターンの入力を履歴に持つ)。
        if OVERLOADED_MARK in json.dumps(body.get("input", [])[-1:], ensure_ascii=False):
            refusal = json.dumps(OVERLOADED_BODY).encode()
            self.send_response(503)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(refusal)))
            self.end_headers()
            self.wfile.write(refusal)
            return
        slow = "SLOW" in said or BACKGROUND_WAIT_MARK in said
        tools = TOOLS_MARK in json.dumps(body.get("input", []), ensure_ascii=False)
        background = BACKGROUND_MARK in json.dumps(body.get("input", []), ensure_ascii=False)
        others = OTHER_TOOLS_MARK in json.dumps(body.get("input", []), ensure_ascii=False)
        sub_agent = SUB_AGENT_MARK in json.dumps(body.get("input", []), ensure_ascii=False)
        code_mode = CODE_MODE_MARK in json.dumps(body.get("input", []), ensure_ascii=False)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        # 長さを名乗らない event-stream は、流し終えたら接続を閉じて終わりを伝える(send_header が close_connection を立てる)。
        self.send_header("connection", "close")
        self.end_headers()
        # 2 つ目のターン(BG-WAIT)は遅い答え — その間に 1 つ目のターンの命令が終わる。
        # 子の agent の要求は頼み(SUB-AGENT)だけを持ち、親の要求は目印(OTHER-TOOLS)と頼みの両方を持つ — 親を先に見分ける。
        events = (answer_events(SLOW_PIECES) if BACKGROUND_WAIT_MARK in said
                  else code_mode_events(code_mode_step(body)) if code_mode
                  else other_tool_events(tool_step(body), body, self.image_path) if others
                  else answer_events(SUB_AGENT_DONE) if sub_agent
                  else background_events(tool_step(body)) if background
                  else tool_events(tool_step(body)) if tools else answer_events(SLOW_PIECES if slow else FAST_PIECES))
        try:
            for event in events:
                sse_event(self.wfile, event)
                if event["type"] == "response.output_text.delta":
                    time.sleep(0.25 if slow else 0.05)
        except (BrokenPipeError, ConnectionResetError):
            # 止めたターン(interrupt)では codex が先に接続を閉じる — 録りの筋書きどおりなので、残りを書かずに終える。
            return


    def refuse_usage_limit(self) -> None:
        """口座の利用枠が尽きた断り(429)を返すため。body の欄(type usage_limit_reached・plan_type・resets_at)と枠の header
        (x-codex-primary-* / secondary-*)の名は、codex 0.162.1 の binary が読む名(binary の文字列で確かめた)に合わせた — 中身の値は偽物。"""
        refusal = json.dumps({"error": {"type": "usage_limit_reached", "message": "The usage limit has been reached",
                                        "plan_type": "plus", "resets_at": USAGE_RESETS_AT}}).encode()
        self.send_response(429)
        self.send_header("content-type", "application/json")
        self.send_header("x-codex-primary-used-percent", "100")
        self.send_header("x-codex-primary-window-minutes", "300")
        self.send_header("x-codex-primary-reset-at", str(USAGE_RESETS_AT))
        self.send_header("x-codex-secondary-used-percent", "37")
        self.send_header("x-codex-secondary-window-minutes", "10080")
        self.send_header("x-codex-secondary-reset-at", str(USAGE_SECONDARY_RESETS_AT))
        self.send_header("content-length", str(len(refusal)))
        self.end_headers()
        self.wfile.write(refusal)


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """偽の上流を、codex の同時の呼び(止めの後の次の呼びなど)でも塞がずに答えさせるため。"""

    daemon_threads = True


def codex_home(work: Path, port: int, mcp_server: bool = False, code_mode_only: bool = False) -> Path:
    """録りの codex を偽の上流にだけつなぐため、口座の要らない provider を名指す config.toml を持つ CODEX_HOME を作る。mcp_server =
    録りの MCP の server を宣言するか / code_mode_only = 道具を code mode だけで呼ぶ設定(features.code_mode_only — gpt-6-astra の
    model の宣言と同じ道具の出し方)にするか(どちらも別の dir の家 — ほかの筋書きの行に知らせや設定を混ぜない)。"""
    home = work / ("codex-home-mcp" if mcp_server else "codex-home-code-mode" if code_mode_only else "codex-home")
    home.mkdir(parents=True, exist_ok=True)
    # MCP の server は録りと同じ python で起こす(-I — 作業の dir の file を読み込まない)。
    declared = (f'[mcp_servers.{MCP_SERVER}]\ncommand = {json.dumps(sys.executable)}\n'
                f'args = ["-I", {json.dumps(str(MCP_SERVER_SCRIPT))}]\n') if mcp_server else ""
    features = "[features]\ncode_mode_only = true\n" if code_mode_only else ""
    (home / "config.toml").write_text(
        'model = "gpt-mock"\nmodel_provider = "mock"\n' + features + '[model_providers.mock]\nname = "mock"\n'
        f'base_url = "http://127.0.0.1:{port}/v1"\nwire_api = "responses"\nrequires_openai_auth = false\n'
        "request_max_retries = 0\nstream_max_retries = 0\n" + declared,
        encoding="utf-8",
    )
    return home


def placed_codex(codex: str, work: Path, host: str) -> str:
    """筋書きの codex の置き方を作るため: host = given(渡された binary のまま — 隣の host もそのまま)/ absent(host の無い dir に置いた
    binary)/ broken(起動できない host を隣に置いた binary)。codex は host を自分の実行 file の dir で探す(link を解いた実体の dir)ので、
    置き場には binary の実体を置く(同じ file system なら hard link・違えば写す)。"""
    if host == "given":
        return codex
    place = work / f"codex-host-{host}"
    place.mkdir(parents=True, exist_ok=True)
    placed = place / "codex"
    if not placed.exists():
        try:
            os.link(codex, placed)
        except OSError:
            shutil.copy2(codex, placed)
    beside = place / CODE_MODE_HOST
    if host == "broken":
        beside.write_text(BROKEN_HOST, encoding="utf-8")
        beside.chmod(0o755)
    elif beside.exists():
        beside.unlink()
    return str(placed)


def record_app_server(codex: str, out: Path, env: dict[str, str], cwd: Path, name: str,
                      script: Callable[[Send, Wait, Path], None]) -> None:
    """1 つの筋書きを app-server の 1 つの process で走らせ、書いた行と読んだ行を届いた順に file へ残すため。"""
    with (out / f"{name}.stderr").open("w", encoding="utf-8") as stderr:
        # argv は doeff_codex.rpc の app-server-argv と同じ形(前置き + app-server --listen stdio://)— 組み立てた argv を本物に当てる。
        process = subprocess.Popen([codex, *APP_SERVER_ARGS], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                   env=env, cwd=cwd, text=True, bufsize=1)
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("app-server の stdin / stdout を pipe で開いていない")
    stdin, stdout = process.stdin, process.stdout
    arrivals: queue.Queue[JsonObject] = queue.Queue()
    received = (out / f"{name}.stdout.jsonl").open("w", encoding="utf-8")
    sent = (out / f"{name}.stdin.jsonl").open("w", encoding="utf-8")

    def read() -> None:
        """stdout の行を届いたまま file へ書き、JSON の object なら待つ側へ渡すため(読みの thread)。"""
        for raw in stdout:
            received.write(raw)
            received.flush()
            message = json.loads(raw)
            if isinstance(message, dict):
                arrivals.put(message)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()

    def send(message: JsonObject) -> None:
        """要求を 1 行で stdin へ書き、書いた行も file に残すため。"""
        line = json.dumps(message)
        sent.write(line + "\n")
        sent.flush()
        stdin.write(line + "\n")
        stdin.flush()

    def wait(matches: Callable[[JsonObject], bool], timeout: float) -> JsonObject:
        """筋書きが次の手を打つ前に、条件に合う行が届くのを待つため(届いた順に読み、合わない行は読み流す)。"""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{name}: 待った行が {timeout} 秒の内に来なかった")
            message = arrivals.get(timeout=remaining)
            if matches(message):
                return message

    try:
        script(send, wait, cwd)
    finally:
        stdin.close()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(5)
        sent.close()
        received.close()
        print(name, "exit", process.returncode)


def json_path(message: JsonObject, *keys: str) -> object:
    """答えの行の入れ子の欄を辿るため(辿れなければ、録りが崩れた事を止めて知らせる)。"""
    value: object = message
    for key in keys:
        if not isinstance(value, dict):
            raise ValueError(f"{keys} を辿れない: {message}")
        value = value[key]
    return value


def start_thread(send: Send, wait: Wait, cwd: Path, sandbox: str = "read-only",
                 dynamic_tools: tuple[JsonObject, ...] = ()) -> str:
    """初期化から thread の始めまでを済ませ、ターンに使う thread の id を得るため(sandbox = thread の sandbox の綴り・dynamic_tools =
    thread に宣言する動的な道具 — 在れば initialize で試験の口 experimentalApi を名乗る)。"""
    capabilities = {"capabilities": {"experimentalApi": True}} if dynamic_tools else {}
    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"clientInfo": {"name": "doeff-codex-recorder", "version": "0"}, **capabilities}})
    wait(lambda message: message.get("id") == 1, WAIT_SECONDS)
    send({"jsonrpc": "2.0", "method": "initialized"})
    declared = {"dynamicTools": list(dynamic_tools)} if dynamic_tools else {}
    send({"jsonrpc": "2.0", "id": 2, "method": "thread/start",
          "params": {"cwd": str(cwd), "approvalPolicy": "never", "sandbox": sandbox, **declared}})
    answer = wait(lambda message: message.get("id") == 2, WAIT_SECONDS)
    return str(json_path(answer, "result", "thread", "id"))


def start_turn(send: Send, wait: Wait, request_id: int, thread_id: str, text: str) -> str:
    """ターンを 1 つ始め、止めと終わりの待ちに使うターンの id を得るため。"""
    send({"jsonrpc": "2.0", "id": request_id, "method": "turn/start",
          "params": {"threadId": thread_id, "input": [{"type": "text", "text": text}]}})
    answer = wait(lambda message: message.get("id") == request_id, WAIT_SECONDS)
    return str(json_path(answer, "result", "turn", "id"))


def turn_ended(turn_id: str) -> Callable[[JsonObject], bool]:
    """そのターンの終わり(turn/completed)の行を見分けるため。"""
    return lambda message: message.get("method") == "turn/completed" and json_path(message, "params", "turn", "id") == turn_id


def two_turns(send: Send, wait: Wait, cwd: Path) -> None:
    """1 つの process がターンをまたいで生き、どのターンも答えの途中を出す事を録るため。"""
    thread_id = start_thread(send, wait, cwd)
    for request_id, text in ((3, "say hello"), (4, "say hello again")):
        wait(turn_ended(start_turn(send, wait, request_id, thread_id, text)), WAIT_SECONDS)


def interrupt(send: Send, wait: Wait, cwd: Path) -> None:
    """答えの途中で止めたターンの終わりの形を録るため。"""
    thread_id = start_thread(send, wait, cwd)
    turn_id = start_turn(send, wait, 3, thread_id, "SLOW please")
    wait(lambda message: message.get("method") == "item/agentMessage/delta", WAIT_SECONDS)
    send({"jsonrpc": "2.0", "id": 4, "method": "turn/interrupt", "params": {"threadId": thread_id, "turnId": turn_id}})
    wait(turn_ended(turn_id), WAIT_SECONDS)


def rate_limit(send: Send, wait: Wait, cwd: Path) -> None:
    """上流が枠切れ(429)で断ったターンの誤りと終わりの形を録るため。"""
    thread_id = start_thread(send, wait, cwd)
    wait(turn_ended(start_turn(send, wait, 3, thread_id, "RATE-LIMIT please")), WAIT_SECONDS * 1.5)


def usage_limit(send: Send, wait: Wait, cwd: Path) -> None:
    """上流が口座の利用枠切れで断ったターンの誤り・枠の通知・終わりの形を録るため。"""
    thread_id = start_thread(send, wait, cwd)
    wait(turn_ended(start_turn(send, wait, 3, thread_id, "USAGE-LIMIT please")), WAIT_SECONDS * 1.5)


def server_overloaded(send: Send, wait: Wait, cwd: Path) -> None:
    """上流が混雑(503・server_is_overloaded)で断ったターンの誤りと終わりの形と、同じ process・同じ thread の次のターンが続けられる
    事を録るため(混雑の断りの後の試し直しは同じ thread の次のターン)。"""
    thread_id = start_thread(send, wait, cwd)
    wait(turn_ended(start_turn(send, wait, 3, thread_id, f"{OVERLOADED_MARK} please")), WAIT_SECONDS * 1.5)
    wait(turn_ended(start_turn(send, wait, 4, thread_id, "hello again")), WAIT_SECONDS)


def tool_calls(send: Send, wait: Wait, cwd: Path) -> None:
    """道具を呼ぶターン(命令の成功・失敗・apply_patch・web 検索の後に答え)の道具の item の形を録るため。命令が本当に走り、ファイルを
    書けるように、sandbox は会話の担当の既定と同じ danger-full-access(機体の sandbox の有無で行が変わらない)。"""
    thread_id = start_thread(send, wait, cwd, "danger-full-access")
    wait(turn_ended(start_turn(send, wait, 3, thread_id, f"{TOOLS_MARK} please")), WAIT_SECONDS * 2)


def background_command(send: Send, wait: Wait, cwd: Path) -> None:
    """後ろで走る命令を始めたターンの後に、遅い答えのターンを走らせ、前のターンを名乗る命令の終わりが次のターンの間に届く形を録るため。"""
    thread_id = start_thread(send, wait, cwd, "danger-full-access")
    wait(turn_ended(start_turn(send, wait, 3, thread_id, f"{BACKGROUND_MARK} please")), WAIT_SECONDS * 2)
    wait(turn_ended(start_turn(send, wait, 4, thread_id, f"{BACKGROUND_WAIT_MARK} please")), WAIT_SECONDS * 2)


def answering_tool_calls(send: Send, matches: Callable[[JsonObject], bool]) -> Callable[[JsonObject], bool]:
    """待つ間に届いた codex からの動的な道具の呼び(item/tool/call)に録りの答えを返しながら、matches に合う行を見分けるため。"""
    def check(message: JsonObject) -> bool:
        if message.get("method") == "item/tool/call" and "id" in message:
            send({"jsonrpc": "2.0", "id": message["id"], "result": DYNAMIC_ANSWER})
        return matches(message)
    return check


def mcp_server_settled(message: JsonObject) -> bool:
    """録りの MCP の server が立ち上がったか落ちたかの知らせを見分けるため(ターンの前に待つ — 立つ前の呼びは道具が無い)。"""
    return (message.get("method") == "mcpServer/startupStatus/updated" and json_path(message, "params", "name") == MCP_SERVER
            and json_path(message, "params", "status") in ("ready", "failed"))


def png_pixel() -> bytes:
    """画像を見る道具が開く 1 画素の PNG の中身を作るため。"""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")


def other_tools(send: Send, wait: Wait, cwd: Path) -> None:
    """ほかの道具を呼ぶターン(MCP の道具・動的な道具・画像を見る道具・子の agent を始めて待つ — その後に答え)の道具の item の形を録る
    ため。子の agent の thread の行も同じ stdout に混ざる。"""
    (cwd / IMAGE_NAME).write_bytes(png_pixel())
    thread_id = start_thread(send, wait, cwd, "danger-full-access", (DYNAMIC_TOOL,))
    wait(mcp_server_settled, WAIT_SECONDS)
    turn_id = start_turn(send, wait, 3, thread_id, f"{OTHER_TOOLS_MARK} please")
    wait(answering_tool_calls(send, turn_ended(turn_id)), WAIT_SECONDS * 2)


def code_mode_turns(send: Send, wait: Wait, cwd: Path) -> None:
    """code mode だけで道具を呼ぶ設定の 1 つの process でターンを 2 回走らせ(どちらも上流が exec を 1 回呼ぶ)、code mode が使えない
    警告がどのターンの・どの行の前後に出るか、2 つ目のターンにも出るかと、exec の呼びが何を返すかを録るため。"""
    thread_id = start_thread(send, wait, cwd, "danger-full-access")
    for request_id, text in ((3, f"{CODE_MODE_MARK} please"), (4, f"{CODE_MODE_MARK} again")):
        wait(turn_ended(start_turn(send, wait, request_id, thread_id, text)), WAIT_SECONDS)


@dataclass(frozen=True)
class Scenario:
    """録る筋書き 1 つ: name = 録った file の名の頭 / script = 要求を書いて行を待つ手順 / mcp_server = CODEX_HOME に録りの MCP の
    server を宣言するか / code_mode_only = 道具を code mode だけで呼ぶ設定にするか / host = code mode の host の置き方(placed_codex)。"""

    name: str
    script: Callable[[Send, Wait, Path], None]
    mcp_server: bool = False
    code_mode_only: bool = False
    host: str = "given"


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("two-turns", two_turns), Scenario("interrupt", interrupt), Scenario("rate-limit", rate_limit),
    Scenario("usage-limit", usage_limit), Scenario("server-overloaded", server_overloaded), Scenario("tool-calls", tool_calls),
    Scenario("background-command", background_command),
    Scenario("other-tools", other_tools, mcp_server=True),
    Scenario("code-mode-unavailable", code_mode_turns, code_mode_only=True, host="absent"),
    Scenario("code-mode-host-broken", code_mode_turns, code_mode_only=True, host="broken"))


def main(codex: str, out: Path, work: Path, chosen: tuple[str, ...]) -> None:
    """偽の上流を立て、筋書き(chosen が空なら全部と exec の比べ)を録って、tests/recorded の 1 版分の file を作るため。"""
    out.mkdir(parents=True, exist_ok=True)
    unknown = set(chosen) - {scenario.name for scenario in SCENARIOS}
    if unknown:
        raise ValueError(f"筋書きの名が無い: {sorted(unknown)}")
    # 筋書きを選んで録る時は、前に録った要求の記録を消さずに後ろへ足す。
    with (out / "upstream-requests.jsonl").open("a" if chosen else "w", encoding="utf-8") as request_log:
        Upstream.request_log = request_log
        server = ThreadingServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            port = server.server_address[1]
            env = {"PATH": "/usr/bin:/bin", "HOME": str(work), "CODEX_HOME": str(codex_home(work, port))}
            cwd = work / "cwd"
            cwd.mkdir(parents=True, exist_ok=True)
            Upstream.image_path = str(cwd / IMAGE_NAME)
            for scenario in SCENARIOS:
                if not chosen or scenario.name in chosen:
                    home = codex_home(work, port, scenario.mcp_server, scenario.code_mode_only)
                    binary = placed_codex(codex, work, scenario.host)
                    record_app_server(binary, out, {**env, "CODEX_HOME": str(home)}, cwd, scenario.name, scenario.script)
            if chosen:
                return
            compared = subprocess.run([codex, "exec", "--json", "--skip-git-repo-check", "say hello"], capture_output=True,
                                      text=True, env=env, cwd=cwd, timeout=120, stdin=subprocess.DEVNULL, check=True)
            (out / "exec-json.stdout.jsonl").write_text(compared.stdout, encoding="utf-8")
            print("exec-json exit", compared.returncode)
        finally:
            server.shutdown()


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), tuple(sys.argv[4:]))
