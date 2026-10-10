"""替え玉の codex app-server(本番の handler の検 — 本物の codex も上流も要らない)。

stdin の JSON-RPC の要求に、録った実物の行(tests/recorded/codex-0.162.1)の形で答える。thread とターンの id は、録った id を
この process が振った id へ差し替える。

- initialize → 録った答え / initialized → 何も返さない。
- thread/start → 新しい thread の id で録った答えと thread/started / thread/resume → 名指された id で同じ形の答え。
- turn/start → 新しいターンの id で答え、入力の文字で決めた筋書きを流す:
  - SLOW が在れば、答えの文字の途中(item/agentMessage/delta)を 0.05 秒ごとに出し続け、turn/interrupt を受けたら録った止めの終わり
    (状態 interrupted の turn/completed)を出す。
  - IMAGES_PHRASE が在れば、入力の画像の数を ``IMAGES <数>`` と答える(画像が turn/start の入力に載ったかを見る)。
  - SETTINGS_PHRASE が在れば、thread を開いた要求の model と config の圧縮の閾値と、turn/start の effort を
    ``SETTINGS effort=<値> compact=<値> model=<値>`` と答える(宣言が要求の行に載ったかを見る — 名乗らない欄は None)。
  - どれでもなければ、録った 1 つ目のターンの通知(turn/started 〜 turn/completed)をそのまま流す。
- turn/steer → SLOW のターンが走っていて expectedTurnId がそのターンなら受けて、足した文字を ``steered:<文字> `` の答えの文字の
  途中として出す(fake の handler と同じ規則)。走っていなければ JSON-RPC の誤りで断る。
- 引数(app-server --listen stdio://)は読まない。
"""

import json
import sys
import threading
from pathlib import Path

RECORDED = Path(__file__).resolve().parent.parent / "recorded" / "codex-0.162.1"
DELTA_PAUSE_SECONDS = 0.05
MAX_SLOW_PIECES = 400
IMAGES_PHRASE = "Count the attached images."
SETTINGS_PHRASE = "Tell the settings."
STEERED_PREFIX = "steered:"
# 走っていないターンへの turn/steer を断る JSON-RPC の誤りの code(invalid request)。
NOT_STEERABLE_CODE = -32600


def said_text(inputs: list[dict[str, object]]) -> str:
    """ターンの入力の列から文字の入力をつないだ文を取るため。"""
    return " ".join(str(item.get("text", "")) for item in inputs if item.get("type") == "text")


def image_count(inputs: list[dict[str, object]]) -> int:
    """ターンの入力の列の画像の数を数えるため。"""
    return sum(1 for item in inputs if item.get("type") == "image")


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
        self.opened_with: dict[str, object] = {}

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
            self.opened_with = dict(params)  # type: ignore[arg-type]  # 要求の形は handler の rpc.hy が作る
            self.write({"id": request_id, "result": self.renamed(answer_of(self.two_turns, 2), "")["result"]})
            started = next(m for m in self.two_turns if m.get("method") == "thread/started")
            self.write(self.renamed(started, ""))
        elif method == "thread/resume":
            self.thread_id = str(params["threadId"])  # type: ignore[index]  # 同上
            self.opened_with = dict(params)  # type: ignore[arg-type]  # 同上
            self.write({"id": request_id, "result": self.renamed(answer_of(self.two_turns, 2), "")["result"]})
        elif method == "turn/start":
            self.start_turn(request_id, params)  # type: ignore[arg-type]  # 同上
        elif method == "turn/steer":
            self.steer_turn(request_id, params)  # type: ignore[arg-type]  # 同上
        elif method == "turn/interrupt":
            self.interrupt_turn(request_id)

    def answer_turn(self, turn_id: str, text: str) -> None:
        """答えの全文 text を 1 つの途中の文字と全文で出して終えるターンの通知を流すため(録った形の最小 — 筋書きの答え)。"""
        where = {"threadId": self.thread_id, "turnId": turn_id}
        self.write({"method": "turn/started", "params": {"threadId": self.thread_id, "turn": {"id": turn_id, "status": "inProgress"}}})
        self.write({"method": "item/started", "params": {**where, "item": {"type": "agentMessage", "id": "msg_1", "text": ""}}})
        self.write({"method": "item/agentMessage/delta", "params": {**where, "itemId": "msg_1", "delta": text}})
        self.write({"method": "item/completed", "params": {**where, "item": {"type": "agentMessage", "id": "msg_1", "text": text}}})
        self.write({"method": "turn/completed",
                    "params": {"threadId": self.thread_id, "turn": {"id": turn_id, "status": "completed", "error": None}}})

    def settings_text(self, params: dict[str, object]) -> str:
        """thread を開いた要求と turn/start が名乗った宣言(effort・圧縮の閾値・model)を答えの文にするため。"""
        config = self.opened_with.get("config") or {}
        compact = config.get("model_auto_compact_token_limit") if isinstance(config, dict) else None
        return f"SETTINGS effort={params.get('effort')} compact={compact} model={self.opened_with.get('model')}"

    def start_turn(self, request_id: object, params: dict[str, object]) -> None:
        """ターンを始め、入力の文字で決めた筋書き(頭の註)を流すため。"""
        turn_id = self.fresh_id("turn")
        self.write({"id": request_id, "result": self.renamed(answer_of(self.two_turns, 3), turn_id)["result"]})
        inputs = list(params.get("input", []))  # type: ignore[call-overload]  # 要求の形は handler の rpc.hy が作る
        said = said_text(inputs)
        if IMAGES_PHRASE in said:
            self.answer_turn(turn_id, f"IMAGES {image_count(inputs)}")
            return
        if SETTINGS_PHRASE in said:
            self.answer_turn(turn_id, self.settings_text(params))
            return
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

    def steer_turn(self, request_id: object, params: dict[str, object]) -> None:
        """走っている SLOW のターンに足した文字を、頭に steered: を付けた答えの文字の途中として出すため(違うターンなら断る)。"""
        running = self.slow_thread is not None and self.slow_thread.is_alive()
        if not running or params.get("expectedTurnId") != self.slow_turn:
            self.write({"id": request_id, "error": {"code": NOT_STEERABLE_CODE, "message": "no active turn to steer"}})
            return
        self.write({"id": request_id, "result": {"turnId": self.slow_turn}})
        said = said_text(list(params.get("input", [])))  # type: ignore[call-overload]  # 要求の形は handler の rpc.hy が作る
        self.write({"method": "item/agentMessage/delta",
                    "params": {"threadId": self.thread_id, "turnId": self.slow_turn, "itemId": "msg_1",
                               "delta": f"{STEERED_PREFIX}{said} "}})

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
