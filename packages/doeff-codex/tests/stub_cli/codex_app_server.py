"""替え玉の codex app-server(本番の handler の検 — 本物の codex も上流も要らない)。

stdin の JSON-RPC の要求に、録った実物の行(tests/recorded/codex-0.162.1)の形で答える。thread とターンの id は、録った id を
この process が振った id へ差し替える。

- initialize → 録った答え / initialized → 何も返さない。
- thread/start → 新しい thread の id で録った答えと thread/started / thread/resume → 名指された id で同じ形の答え。
- turn/start → 新しいターンの id で答え、入力に SLOW が無ければ、録った 1 つ目のターンの通知(turn/started 〜 turn/completed)を
  そのまま流す。SLOW が在れば、答えの文字の途中(item/agentMessage/delta)を 0.05 秒ごとに出し続け、turn/interrupt を受けたら
  録った止めの終わり(状態 interrupted の turn/completed)を出す。
- 引数(app-server --listen stdio://)は読まない。
"""

import json
import sys
import threading
from pathlib import Path

RECORDED = Path(__file__).resolve().parent.parent / "recorded" / "codex-0.162.1"
DELTA_PAUSE_SECONDS = 0.05
MAX_SLOW_PIECES = 400


def recorded_messages(name: str) -> list[dict[str, object]]:
    """録った 1 本の stdout の行を JSON の object の列で読むため。"""
    return [json.loads(line) for line in (RECORDED / f"{name}.stdout.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def answer_of(messages: list[dict[str, object]], request_id: int) -> dict[str, object]:
    """録った行から、その id の要求への答えを引くため。"""
    return next(message for message in messages if message.get("id") == request_id and "result" in message)


class Stub:
    """替え玉の 1 つの process の状態(thread とターンの id・止めの合図)。"""

    def __init__(self) -> None:
        self.two_turns = recorded_messages("two-turns")
        self.interrupt = recorded_messages("interrupt")
        thread_answer = answer_of(self.two_turns, 2)
        turn_answer = answer_of(self.two_turns, 3)
        self.recorded_thread = str(thread_answer["result"]["thread"]["id"])  # type: ignore[index]  # 録った答えの形は検が確かめている
        self.recorded_turn = str(turn_answer["result"]["turn"]["id"])  # type: ignore[index]  # 同上
        self.made = 0
        self.thread_id = ""
        self.write_lock = threading.Lock()
        self.stop_slow = threading.Event()
        self.slow_turn = ""
        self.slow_thread: threading.Thread | None = None

    def write(self, message: dict[str, object]) -> None:
        """1 行の message を stdout へ書くため(止めの thread と主の loop の両方から書くので lock の中で)。"""
        with self.write_lock:
            sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()

    def renamed(self, message: dict[str, object], turn_id: str) -> dict[str, object]:
        """録った行の thread とターンの id を、この process の id へ差し替えるため。"""
        text = json.dumps(message, ensure_ascii=False)
        return json.loads(text.replace(self.recorded_thread, self.thread_id).replace(self.recorded_turn, turn_id))

    def fresh_id(self, kind: str) -> str:
        """thread とターンの id を振るため。"""
        self.made += 1
        return f"stub-{kind}-{self.made}"

    def first_turn_notifications(self) -> list[dict[str, object]]:
        """録った 1 つ目のターンの通知(turn/start の答えの後から最初の turn/completed まで)を取るため。"""
        start = self.two_turns.index(answer_of(self.two_turns, 3)) + 1
        end = next(index for index, message in enumerate(self.two_turns)
                   if index >= start and message.get("method") == "turn/completed")
        return self.two_turns[start:end + 1]

    def handle(self, message: dict[str, object]) -> None:
        """要求 1 つに答えるため。"""
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            self.write({"id": request_id, "result": answer_of(self.two_turns, 1)["result"]})
        elif method == "thread/start":
            self.thread_id = self.fresh_id("thread")
            self.write({"id": request_id, "result": self.renamed(answer_of(self.two_turns, 2), "")["result"]})
            started = next(m for m in self.two_turns if m.get("method") == "thread/started")
            self.write(self.renamed(started, ""))
        elif method == "thread/resume":
            self.thread_id = str(params["threadId"])  # type: ignore[index]  # 要求の形は handler の rpc.hy が作る
            self.write({"id": request_id, "result": self.renamed(answer_of(self.two_turns, 2), "")["result"]})
        elif method == "turn/start":
            self.start_turn(request_id, params)  # type: ignore[arg-type]  # 同上
        elif method == "turn/interrupt":
            self.interrupt_turn(request_id)

    def start_turn(self, request_id: object, params: dict[str, object]) -> None:
        """ターンを始め、入力に SLOW が無ければ録った 1 ターンを流し、在れば止めまで途中の文字を出し続けるため。"""
        turn_id = self.fresh_id("turn")
        self.write({"id": request_id, "result": self.renamed(answer_of(self.two_turns, 3), turn_id)["result"]})
        said = json.dumps(params.get("input", []))
        if "SLOW" not in said:
            for notification in self.first_turn_notifications():
                self.write(self.renamed(notification, turn_id))
            return
        self.slow_turn = turn_id
        self.stop_slow.clear()
        self.slow_thread = threading.Thread(target=self.emit_slow, args=(turn_id,), daemon=True)
        self.slow_thread.start()

    def emit_slow(self, turn_id: str) -> None:
        """止めが来るまで、答えの文字の途中を間を置いて出し続けるため(止めの thread の入口)。"""
        self.write({"method": "turn/started", "params": {"threadId": self.thread_id, "turn": {"id": turn_id, "status": "inProgress"}}})
        self.write({"method": "item/started", "params": {"threadId": self.thread_id, "turnId": turn_id,
                                                          "item": {"type": "agentMessage", "id": "msg_1", "text": ""}}})
        for index in range(MAX_SLOW_PIECES):
            if self.stop_slow.wait(DELTA_PAUSE_SECONDS):
                return
            self.write({"method": "item/agentMessage/delta",
                        "params": {"threadId": self.thread_id, "turnId": turn_id, "itemId": "msg_1", "delta": f"slow-{index} "}})

    def interrupt_turn(self, request_id: object) -> None:
        """遅いターンを止め、録った止めの終わり(状態 interrupted)を出すため。"""
        self.stop_slow.set()
        if self.slow_thread is not None:
            self.slow_thread.join(5)
        self.write({"id": request_id, "result": {}})
        recorded = next(m for m in self.interrupt if m.get("method") == "turn/completed")
        params = recorded["params"]
        turn = params["turn"]  # type: ignore[index]  # 録った行の形は検 test_lines.hy が確かめている
        self.write({**recorded, "params": {**params, "threadId": self.thread_id,  # type: ignore[dict-item]  # 同上
                                           "turn": {**turn, "id": self.slow_turn, "rootTurnId": self.slow_turn}}})


def main() -> None:
    """stdin の要求を 1 行ずつ読んで答えるため(stdin の EOF で終わる)。"""
    stub = Stub()
    for line in sys.stdin:
        if line.strip():
            stub.handle(json.loads(line))


if __name__ == "__main__":
    main()
