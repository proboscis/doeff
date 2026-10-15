"""替え玉の codex app-server(本番の handler の検 — 本物の codex も上流も要らない)。

stdin の JSON-RPC の要求に、録った実物の行(tests/recorded/codex-0.162.1)の形で答える。thread とターンの id は、録った id を
この process が振った id へ差し替える。

- initialize → 録った答え / initialized → 何も返さない。
- thread/start → 新しい thread の id で録った答えと thread/started / thread/resume → 名指された id で同じ形の答え。
- turn/start → 新しいターンの id で答え、入力の文字で決めた筋書きを流す:
  - SLOW が在れば、答えの文字の途中(item/agentMessage/delta)を 0.05 秒ごとに出し続け、turn/interrupt を受けたら録った止めの終わり
    (状態 interrupted の turn/completed)を出す。
  - IMAGES_PHRASE が在れば、入力の画像の数を ``IMAGES <数>`` と答える(画像が turn/start の入力に載ったかを見る)。
  - SETTINGS_PHRASE が在れば、thread を開いた要求の model と config の圧縮の閾値と、turn/start の effort と、この process を起こした
    argv の --disable の機能の名を ``SETTINGS effort=<値> compact=<値> model=<値> disabled=<名,…>`` と答える(宣言が要求の行と argv に
    載ったかを見る — 名乗らない欄は None・切る機能が無ければ disabled=)。
  - HOME_PHRASE が在れば、この process の CODEX_HOME の形を ``HOME dir=<権限> auth=<権限> account=<auth.json の口座の id>
    config=<link|file|none> sessions=<link|dir|none>`` と答える(借りた資格の家の形を子の process から見る — 資格の値は答えない)。
  - SPANNING の目印(BG-COMMAND・BG-WAIT)が在れば、2 つのターンにまたがる録り(後ろで走る命令の始まりのターンと、その命令が終わる
    次のターン)のうち、目印のターンの通知を流す。前のターンを名乗る遅れた行(命令の終わり)は、この process が前のターンに振った id へ
    差し替える。
  - REPLAYED の目印(USAGE-LIMIT・RATE-LIMIT・TOOL-CALLS・OTHER-TOOLS・CODE-MODE)が在れば、その目印で録ったターンの通知
    (turn/start の答えの後 〜 そのターンの turn/completed)をそのまま流す(利用枠切れ・429 の断りで失敗したターン・命令の成功と失敗と
    apply_patch と web 検索の道具の item を持つターン・MCP と動的な道具と画像を見る道具と子の agent の道具の item を持つターン・code
    mode の host を使えない codex の、文だけを返すターン — 子の agent の thread の行(子のターンの始まりと終わりを含む)も録った順の
    まま混ぜて流す)。
  - PREFACED の目印(CODE-MODE)が在れば、その目印で録った 1 つ目のターンの前の警告(thread を開いた答えと turn/start の答えの間の
    warning の通知 — code mode が使えない警告を含む)を、turn/start の答えより先に書く(録った順)。
  - どれでもなければ、録った 1 つ目のターンの通知(turn/started 〜 turn/completed)をそのまま流す。
- turn/steer → SLOW のターンが走っていて expectedTurnId がそのターンなら受けて、足した文字を ``steered:<文字> `` の答えの文字の
  途中として出す(fake の handler と同じ規則)。走っていなければ JSON-RPC の誤りで断る。
- 引数は --disable の機能の名だけを読む(SETTINGS の答え — app-server --listen stdio:// は読まない)。
"""

import json
import os
import sys
import threading
from pathlib import Path

RECORDED = Path(__file__).resolve().parent.parent / "recorded" / "codex-0.162.1"
DELTA_PAUSE_SECONDS = 0.05
MAX_SLOW_PIECES = 400
IMAGES_PHRASE = "Count the attached images."
SETTINGS_PHRASE = "Tell the settings."
HOME_PHRASE = "Tell the home."
STEERED_PREFIX = "steered:"
# 入力の目印 → 録った筋書きの名(その筋書きのターンの通知をそのまま流す)。
REPLAYED = {"USAGE-LIMIT": "usage-limit", "RATE-LIMIT": "rate-limit", "TOOL-CALLS": "tool-calls", "OTHER-TOOLS": "other-tools",
            "CODE-MODE": "code-mode-unavailable"}
# 入力の目印 → 2 つのターンにまたがる録りの名と、そのターンを始めた録りの要求の id(turn/start)。
SPANNING = {"BG-COMMAND": ("background-command", 3), "BG-WAIT": ("background-command", 4)}
# 入力の目印 → 録った筋書きの名(1 つ目のターンの前の警告を turn/start の答えより先に書く — そのターンの通知は REPLAYED が流す)。
PREFACED = {"CODE-MODE": "code-mode-unavailable"}
# 走っていないターンへの turn/steer を断る JSON-RPC の誤りの code(invalid request)。
NOT_STEERABLE_CODE = -32600


def disabled_features(argv: list[str]) -> list[str]:
    """起こされた argv の --disable の後ろの機能の名を順に取るため(会話の宣言の切る機能が argv に載ったかを答える)。"""
    return [name for flag, name in zip(argv, argv[1:]) if flag == "--disable"]


def said_text(inputs: list[dict[str, object]]) -> str:
    """ターンの入力の列から文字の入力をつないだ文を取るため。"""
    return " ".join(str(item.get("text", "")) for item in inputs if item.get("type") == "text")


def image_count(inputs: list[dict[str, object]]) -> int:
    """ターンの入力の列の画像の数を数えるため。"""
    return sum(1 for item in inputs if item.get("type") == "image")


def entry_kind(path: Path) -> str:
    """家の中の 1 つの入り口が link か実物か無いかを名乗るため。"""
    if path.is_symlink():
        return "link"
    if path.is_dir():
        return "dir"
    return "file" if path.exists() else "none"


def home_text() -> str:
    """この process の CODEX_HOME の形(dir と auth.json の権限・口座の id・config.toml と sessions の入り口)を答えの文にするため。"""
    home = Path(os.environ.get("CODEX_HOME", ""))
    auth = home / "auth.json"
    account = json.loads(auth.read_text(encoding="utf-8"))["tokens"]["account_id"] if auth.exists() else None
    auth_mode = oct(auth.stat().st_mode & 0o777) if auth.exists() else "none"
    return (f"HOME dir={oct(home.stat().st_mode & 0o777)} auth={auth_mode} account={account} "
            f"config={entry_kind(home / 'config.toml')} sessions={entry_kind(home / 'sessions')}")


def recorded_messages(name: str) -> list[dict[str, object]]:
    """録った 1 本の stdout の行を JSON の object の列で読むため。"""
    return [json.loads(line) for line in (RECORDED / f"{name}.stdout.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def answer_of(messages: list[dict[str, object]], request_id: int) -> dict[str, object]:
    """録った行から、その id の要求への答えを引くため。"""
    return next(message for message in messages if message.get("id") == request_id and "result" in message)


class Stub:
    """替え玉の 1 つの process の状態(thread とターンの id・止めの合図)。要求をまたいで書き換える欄は名の頭に mut_ を付ける(DOEFF002)。"""

    def __init__(self) -> None:
        self.two_turns = recorded_messages("two-turns")
        self.interrupt = recorded_messages("interrupt")
        thread_answer = answer_of(self.two_turns, 2)
        turn_answer = answer_of(self.two_turns, 3)
        self.recorded_thread = str(thread_answer["result"]["thread"]["id"])  # type: ignore[index]  # 録った答えの形は検が確かめている
        self.recorded_turn = str(turn_answer["result"]["turn"]["id"])  # type: ignore[index]  # 同上
        self.mut_made = 0
        self.mut_thread_id = ""
        self.write_lock = threading.Lock()
        self.stop_slow = threading.Event()
        self.mut_slow_turn = ""
        self.mut_slow_thread: threading.Thread | None = None
        self.mut_opened_with: dict[str, object] = {}
        # 2 つのターンにまたがる録りの、録ったターンの id → この process が振ったターンの id。
        self.mut_spanned: dict[str, str] = {}

    def write(self, message: dict[str, object]) -> None:
        """1 行の message を stdout へ書くため(止めの thread と主の loop の両方から書くので lock の中で)。"""
        with self.write_lock:
            sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()

    def renamed(self, message: dict[str, object], turn_id: str) -> dict[str, object]:
        """録った行の thread とターンの id を、この process の id へ差し替えるため。"""
        text = json.dumps(message, ensure_ascii=False)
        return json.loads(text.replace(self.recorded_thread, self.mut_thread_id).replace(self.recorded_turn, turn_id))

    def fresh_id(self, kind: str) -> str:
        """thread とターンの id を振るため。"""
        self.mut_made += 1
        return f"stub-{kind}-{self.mut_made}"

    def spanning_turn(self, name: str, request_id: int, turn_id: str) -> list[dict[str, object]]:
        """2 つのターンにまたがる録り name のうち、要求 request_id で始めたターンの通知(その答えの後から最初の turn/completed まで)を
        取るため。thread の id と、この録りでこれまでに流したターン(前のターンを名乗る遅れた行を含む)の id を、この process の id へ
        差し替える。"""
        messages = recorded_messages(name)
        thread = str(answer_of(messages, 2)["result"]["thread"]["id"])  # type: ignore[index]  # 録った答えの形は検が確かめている
        recorded_turn = str(answer_of(messages, request_id)["result"]["turn"]["id"])  # type: ignore[index]  # 同上
        self.mut_spanned[recorded_turn] = turn_id
        start = messages.index(answer_of(messages, request_id)) + 1
        end = next(index for index, message in enumerate(messages) if index >= start and message.get("method") == "turn/completed")
        text = json.dumps(messages[start:end + 1], ensure_ascii=False).replace(thread, self.mut_thread_id)
        for recorded, mine in self.mut_spanned.items():
            text = text.replace(recorded, mine)
        return json.loads(text)

    def replayed_turn(self, name: str, turn_id: str) -> list[dict[str, object]]:
        """録った筋書き name の 1 つ目のターンの通知(turn/start の答えの後から、そのターンの turn/completed まで — 子の agent の
        ターンの終わりは数えない)を、その録りの thread とターンの id をこの process の id へ差し替えて取るため。"""
        messages = recorded_messages(name)
        thread = str(answer_of(messages, 2)["result"]["thread"]["id"])  # type: ignore[index]  # 録った答えの形は検が確かめている
        turn = str(answer_of(messages, 3)["result"]["turn"]["id"])  # type: ignore[index]  # 同上
        start = messages.index(answer_of(messages, 3)) + 1
        end = next(index for index, message in enumerate(messages)
                   if index >= start and message.get("method") == "turn/completed"
                   and message["params"]["turn"]["id"] == turn)  # type: ignore[index]  # 同上
        text = json.dumps(messages[start:end + 1], ensure_ascii=False)
        return json.loads(text.replace(thread, self.mut_thread_id).replace(turn, turn_id))

    def preface_of(self, name: str) -> list[dict[str, object]]:
        """録った筋書き name の、thread を開いた答え(id 2)と 1 つ目のターンの turn/start の答え(id 3)の間の警告(warning の通知)を、
        その録りの thread の id をこの process の id へ差し替えて取るため(codex はターンを始める時の警告を答えより先に出す)。"""
        messages = recorded_messages(name)
        thread = str(answer_of(messages, 2)["result"]["thread"]["id"])  # type: ignore[index]  # 録った答えの形は検が確かめている
        between = messages[messages.index(answer_of(messages, 2)) + 1:messages.index(answer_of(messages, 3))]
        text = json.dumps([message for message in between if message.get("method") == "warning"], ensure_ascii=False)
        return json.loads(text.replace(thread, self.mut_thread_id))

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
            self.mut_thread_id = self.fresh_id("thread")
            self.mut_opened_with = dict(params)  # type: ignore[arg-type]  # 要求の形は handler の rpc.hy が作る
            self.write({"id": request_id, "result": self.renamed(answer_of(self.two_turns, 2), "")["result"]})
            started = next(m for m in self.two_turns if m.get("method") == "thread/started")
            self.write(self.renamed(started, ""))
        elif method == "thread/resume":
            self.mut_thread_id = str(params["threadId"])  # type: ignore[index]  # 同上
            self.mut_opened_with = dict(params)  # type: ignore[arg-type]  # 同上
            self.write({"id": request_id, "result": self.renamed(answer_of(self.two_turns, 2), "")["result"]})
        elif method == "turn/start":
            self.start_turn(request_id, params)  # type: ignore[arg-type]  # 同上
        elif method == "turn/steer":
            self.steer_turn(request_id, params)  # type: ignore[arg-type]  # 同上
        elif method == "turn/interrupt":
            self.interrupt_turn(request_id)

    def answer_turn(self, turn_id: str, text: str) -> None:
        """答えの全文 text を 1 つの途中の文字と全文で出して終えるターンの通知を流すため(録った形の最小 — 筋書きの答え)。"""
        where = {"threadId": self.mut_thread_id, "turnId": turn_id}
        self.write({"method": "turn/started", "params": {"threadId": self.mut_thread_id, "turn": {"id": turn_id, "status": "inProgress"}}})
        self.write({"method": "item/started", "params": {**where, "item": {"type": "agentMessage", "id": "msg_1", "text": ""}}})
        self.write({"method": "item/agentMessage/delta", "params": {**where, "itemId": "msg_1", "delta": text}})
        self.write({"method": "item/completed", "params": {**where, "item": {"type": "agentMessage", "id": "msg_1", "text": text}}})
        self.write({"method": "turn/completed",
                    "params": {"threadId": self.mut_thread_id, "turn": {"id": turn_id, "status": "completed", "error": None}}})

    def settings_text(self, params: dict[str, object]) -> str:
        """thread を開いた要求と turn/start が名乗った宣言(effort・圧縮の閾値・model)と、argv の切る機能を答えの文にするため。"""
        config = self.mut_opened_with.get("config") or {}
        compact = config.get("model_auto_compact_token_limit") if isinstance(config, dict) else None
        disabled = ",".join(disabled_features(sys.argv))
        return f"SETTINGS effort={params.get('effort')} compact={compact} model={self.mut_opened_with.get('model')} disabled={disabled}"

    def start_turn(self, request_id: object, params: dict[str, object]) -> None:
        """ターンを始め、入力の文字で決めた筋書き(頭の註)を流すため。"""
        turn_id = self.fresh_id("turn")
        inputs = list(params.get("input", []))  # type: ignore[call-overload]  # 要求の形は handler の rpc.hy が作る
        said = said_text(inputs)
        prefaced = next((name for marker, name in PREFACED.items() if marker in said), None)
        for warning in self.preface_of(prefaced) if prefaced is not None else []:
            self.write(warning)
        self.write({"id": request_id, "result": self.renamed(answer_of(self.two_turns, 3), turn_id)["result"]})
        if IMAGES_PHRASE in said:
            self.answer_turn(turn_id, f"IMAGES {image_count(inputs)}")
            return
        if SETTINGS_PHRASE in said:
            self.answer_turn(turn_id, self.settings_text(params))
            return
        if HOME_PHRASE in said:
            self.answer_turn(turn_id, home_text())
            return
        spanning = next((recorded for marker, recorded in SPANNING.items() if marker in said), None)
        if spanning is not None:
            for notification in self.spanning_turn(spanning[0], spanning[1], turn_id):
                self.write(notification)
            return
        replayed = next((name for marker, name in REPLAYED.items() if marker in said), None)
        if replayed is not None:
            for notification in self.replayed_turn(replayed, turn_id):
                self.write(notification)
            return
        if "SLOW" not in said:
            for notification in self.first_turn_notifications():
                self.write(self.renamed(notification, turn_id))
            return
        self.mut_slow_turn = turn_id
        self.stop_slow.clear()
        self.mut_slow_thread = threading.Thread(target=self.emit_slow, args=(turn_id,), daemon=True)
        self.mut_slow_thread.start()

    def emit_slow(self, turn_id: str) -> None:
        """止めが来るまで、答えの文字の途中を間を置いて出し続けるため(止めの thread の入口)。"""
        self.write({"method": "turn/started", "params": {"threadId": self.mut_thread_id, "turn": {"id": turn_id, "status": "inProgress"}}})
        self.write({"method": "item/started", "params": {"threadId": self.mut_thread_id, "turnId": turn_id,
                                                          "item": {"type": "agentMessage", "id": "msg_1", "text": ""}}})
        for index in range(MAX_SLOW_PIECES):
            if self.stop_slow.wait(DELTA_PAUSE_SECONDS):
                return
            self.write({"method": "item/agentMessage/delta",
                        "params": {"threadId": self.mut_thread_id, "turnId": turn_id, "itemId": "msg_1", "delta": f"slow-{index} "}})

    def steer_turn(self, request_id: object, params: dict[str, object]) -> None:
        """走っている SLOW のターンに足した文字を、頭に steered: を付けた答えの文字の途中として出すため(違うターンなら断る)。"""
        running = self.mut_slow_thread is not None and self.mut_slow_thread.is_alive()
        if not running or params.get("expectedTurnId") != self.mut_slow_turn:
            self.write({"id": request_id, "error": {"code": NOT_STEERABLE_CODE, "message": "no active turn to steer"}})
            return
        self.write({"id": request_id, "result": {"turnId": self.mut_slow_turn}})
        said = said_text(list(params.get("input", [])))  # type: ignore[call-overload]  # 要求の形は handler の rpc.hy が作る
        self.write({"method": "item/agentMessage/delta",
                    "params": {"threadId": self.mut_thread_id, "turnId": self.mut_slow_turn, "itemId": "msg_1",
                               "delta": f"{STEERED_PREFIX}{said} "}})

    def interrupt_turn(self, request_id: object) -> None:
        """遅いターンを止め、録った止めの終わり(状態 interrupted)を出すため。"""
        self.stop_slow.set()
        if self.mut_slow_thread is not None:
            self.mut_slow_thread.join(5)
        self.write({"id": request_id, "result": {}})
        recorded = next(m for m in self.interrupt if m.get("method") == "turn/completed")
        params = recorded["params"]
        turn = params["turn"]  # type: ignore[index]  # 録った行の形は検 test_lines.hy が確かめている
        self.write({**recorded, "params": {**params, "threadId": self.mut_thread_id,  # type: ignore[dict-item]  # 同上
                                           "turn": {**turn, "id": self.mut_slow_turn, "rootTurnId": self.mut_slow_turn}}})


def main() -> None:
    """stdin の要求を 1 行ずつ読んで答えるため(stdin の EOF で終わる)。"""
    stub = Stub()
    for line in sys.stdin:
        if line.strip():
            stub.handle(json.loads(line))


if __name__ == "__main__":
    main()
