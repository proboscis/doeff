"""codex の app-server の stdout の行を録る(tests/recorded/codex-<版>/ の作り方)。

版を固定した本物の codex の binary を、手元に立てた偽の上流(OpenAI の Responses API の SSE を返す HTTP server)につなぎ、JSON-RPC の
要求を stdin へ書いて、stdout の行をそのまま file に書く。口座も network の外も要らない — model の答えだけが偽物で、行の形は本物の
binary が出した物。録る筋書きは 3 つと比べの 1 つ:

- two-turns: 1 つの process で thread を始め、ターンを 2 回走らせる(答えの文字の途中・答えの全文・ターンの終わり)。
- interrupt: 遅い答えの途中で turn/interrupt を送る(状態 interrupted の終わり)。
- rate-limit: 上流が 429 で断る(誤りの通知と状態 failed の終わり)。
- exec-json: 比べ — 同じ答えを `codex exec --json` で走らせた行(文字の途中を出さない事の実測)。

使い方(版を上げた時に録り直す):

    uv run --no-project python -I packages/doeff-codex/scripts/record_app_server_lines.py <codex の binary> <書く dir> <作業の dir>

作業の dir は /tmp の外に置く(codex は /tmp の下の CODEX_HOME に助けの binary を作らず、警告の行を stderr に出す)。
"""

import http.server
import json
import queue
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

JsonObject = dict[str, object]
Send = Callable[[JsonObject], None]
Wait = Callable[[Callable[[JsonObject], bool], float], JsonObject]

APP_SERVER_ARGS = ("app-server", "--listen", "stdio://")
FAST_PIECES = ("Hel", "lo, ", "wor", "ld.")
SLOW_PIECES = tuple(f"slow-{index} " for index in range(40))
WAIT_SECONDS = 60.0


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


class Upstream(http.server.BaseHTTPRequestHandler):
    """偽の上流: 要求の最後の入力に RATE-LIMIT が在れば 429、SLOW が在れば遅い答え、ほかは 4 つの差分の答え。"""

    protocol_version = "HTTP/1.1"
    request_log: IO[str]

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
        slow = "SLOW" in said
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        try:
            for event in answer_events(SLOW_PIECES if slow else FAST_PIECES):
                sse_event(self.wfile, event)
                if event["type"] == "response.output_text.delta":
                    time.sleep(0.25 if slow else 0.05)
        except (BrokenPipeError, ConnectionResetError):
            # 止めたターン(interrupt)では codex が先に接続を閉じる — 録りの筋書きどおりなので、残りを書かずに終える。
            return
        self.close_connection = True


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """偽の上流を、codex の同時の呼び(止めの後の次の呼びなど)でも塞がずに答えさせるため。"""

    daemon_threads = True


def codex_home(work: Path, port: int) -> Path:
    """録りの codex を偽の上流にだけつなぐため、口座の要らない provider を名指す config.toml を持つ CODEX_HOME を作る。"""
    home = work / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        'model = "gpt-mock"\nmodel_provider = "mock"\n[model_providers.mock]\nname = "mock"\n'
        f'base_url = "http://127.0.0.1:{port}/v1"\nwire_api = "responses"\nrequires_openai_auth = false\n'
        "request_max_retries = 0\nstream_max_retries = 0\n",
        encoding="utf-8",
    )
    return home


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


def start_thread(send: Send, wait: Wait, cwd: Path) -> str:
    """初期化から thread の始めまでを済ませ、ターンに使う thread の id を得るため。"""
    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"clientInfo": {"name": "doeff-codex-recorder", "version": "0"}}})
    wait(lambda message: message.get("id") == 1, WAIT_SECONDS)
    send({"jsonrpc": "2.0", "method": "initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "thread/start",
          "params": {"cwd": str(cwd), "approvalPolicy": "never", "sandbox": "read-only"}})
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


def main(codex: str, out: Path, work: Path) -> None:
    """偽の上流を立て、3 つの筋書きと exec の比べを録って、tests/recorded の 1 版分の file を作るため。"""
    out.mkdir(parents=True, exist_ok=True)
    with (out / "upstream-requests.jsonl").open("w", encoding="utf-8") as request_log:
        Upstream.request_log = request_log
        server = ThreadingServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            home = codex_home(work, server.server_address[1])
            env = {"PATH": "/usr/bin:/bin", "HOME": str(work), "CODEX_HOME": str(home)}
            cwd = work / "cwd"
            cwd.mkdir(parents=True, exist_ok=True)
            for name, script in (("two-turns", two_turns), ("interrupt", interrupt), ("rate-limit", rate_limit)):
                record_app_server(codex, out, env, cwd, name, script)
            compared = subprocess.run([codex, "exec", "--json", "--skip-git-repo-check", "say hello"], capture_output=True,
                                      text=True, env=env, cwd=cwd, timeout=120, stdin=subprocess.DEVNULL, check=True)
            (out / "exec-json.stdout.jsonl").write_text(compared.stdout, encoding="utf-8")
            print("exec-json exit", compared.returncode)
        finally:
            server.shutdown()


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
