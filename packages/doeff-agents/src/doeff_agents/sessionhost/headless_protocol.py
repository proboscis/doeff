"""headless backend の stdin / stdout の作法(純粋・I/O なし)— agora-redesign #37・段 2 lane 2d。

headless の session は tui の pane を持たない: agent は子 process で、prompt は stdin に書き、
実況は stdout の JSON 行(claude = ``claude -p --output-format stream-json``・codex =
``codex app-server`` の JSON-RPC)で読む。この module は「stdout の 1 行を読んだら次に何を stdin
へ書くか・手番はいつ終わったか・割り込みはどう伝えるか」の**判断**だけを持つ状態機械
(Dialogue)で、実際に書く・process を起こす・信号を送るのは headless_process.py の器の仕事。
検は同じ Dialogue を替え玉の書き手で回す(効果の値 = ``Step`` / ``TurnInput`` / ``Interrupt``)。

kind ごとの物理(argv は impls/headless_argv.hy・stdin の綴りはここ):
- claude: 1 手番 1 process。prompt は stdin の本文ちょうど(書いて閉じる)。手番の終わりは
  ``{"type":"result"}`` の行、会話の id は ``{"type":"system","subtype":"init","session_id"}``。
  割り込み = process へ SIGINT(stream-json の入力の口は使わない — 1 手番 1 process の作法)。
  次の手番は ``--resume <sid>`` の新しい process(argv は impls 側・器は同じ名で起こし直す)。
- codex: app-server の process を手番の間も生かす(温かい)。手番 = ``turn/start``、終わりは
  自分の thread と turn の ``turn/completed``、割り込み = ``turn/interrupt``。server → client の
  要求は方策の表(全面許可の範囲の 4 種だけ accept・他は断って手番を型付きの失敗にする —
  dotfiles agentcli/codex_app_server.py と同じ規則)。

「手番の途中か」の判断(verdict)も純関数 1 点(``turn_verdict``)。
"""

# pyright: strict
import json
from dataclasses import dataclass
from typing import Literal, TypeAlias

JSON: TypeAlias = "dict[str, JSON] | list[JSON] | str | int | float | bool | None"
JSONObject: TypeAlias = "dict[str, JSON]"

AgentKind = Literal["claude", "codex"]

# ------------------------------------------------------------------ 効果の値(Dialogue が返す形)


@dataclass(frozen=True)
class TurnEnded:
    """1 手番の終わり(kind の終端の行を読んだ)。ok = 走行器が成功と名乗った。"""

    ok: bool
    detail: str


@dataclass(frozen=True)
class Step:
    """stdout の 1 行を読んだ答え: stdin へ書く行・手番の終わり・見つけた会話の id・型付きの失敗。"""

    sends: tuple[str, ...] = ()
    ended: TurnEnded | None = None
    conversation: dict[str, str] | None = None
    failure: str | None = None


@dataclass(frozen=True)
class TurnInput:
    """次の手番の本文を stdin へどう書くか。close_stdin = 書いた後に EOF(claude の 1 手番 1 process)。"""

    sends: tuple[str, ...]
    close_stdin: bool


@dataclass(frozen=True)
class Interrupt:
    """割り込みの伝え方: stdin へ書く行(codex の turn/interrupt)か process への SIGINT(claude)。"""

    sends: tuple[str, ...] = ()
    signal: bool = False


def _dumps(value: JSONObject) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _object_at(value: JSON, key: str) -> JSONObject:
    inner = value.get(key) if isinstance(value, dict) else None
    return inner if isinstance(inner, dict) else {}


def _text_at(value: JSON, key: str) -> str:
    inner = value.get(key) if isinstance(value, dict) else None
    return inner if isinstance(inner, str) else ""


# ------------------------------------------------------------------ claude(stream-json・1 手番 1 process)


class ClaudeDialogue:
    """``claude -p --output-format stream-json`` の作法。状態 = 会話の id(init で知る)だけ。"""

    kind: AgentKind = "claude"
    #: 1 手番 1 process: 手番の終わりで process が降りる。次の手番は起こし直す。
    one_process_per_turn: bool = True

    def __init__(self) -> None:
        self.conversation: dict[str, str] | None = None

    def opening(self) -> tuple[str, ...]:
        return ()

    def turn(self, prompt: str) -> TurnInput:
        return TurnInput(sends=(prompt,), close_stdin=True)

    def on_line(self, record: JSONObject) -> Step:
        kind = record.get("type")
        if kind == "system" and record.get("subtype") == "init":
            session_id = _text_at(record, "session_id")
            if session_id:
                self.conversation = {"session_id": session_id}
                return Step(conversation=self.conversation)
            return Step()
        if kind == "result":
            is_error = record.get("is_error") is True
            subtype = _text_at(record, "subtype") or ("error" if is_error else "success")
            return Step(ended=TurnEnded(ok=not is_error, detail=subtype))
        return Step()

    def interrupt(self) -> Interrupt:
        return Interrupt(signal=True)


# ------------------------------------------------------------------ codex(app-server・JSON-RPC・温かい process)

REQ_INITIALIZE = "initialize"
REQ_THREAD_START = "thread/start"
REQ_THREAD_RESUME = "thread/resume"
REQ_TURN_START = "turn/start"
REQ_TURN_INTERRUPT = "turn/interrupt"
NOTE_INITIALIZED = "initialized"
NOTE_TURN_STARTED = "turn/started"
NOTE_TURN_COMPLETED = "turn/completed"
CLIENT_INFO: dict[str, str] = {
    "name": "doeff-sessionhost",
    "version": "1",
    "title": "doeff sessionhost headless backend",
}
#: server → client の要求への答え方(方策の表は 1 つ)。accept は全面許可の範囲で意味の
#: 変わらない 4 種だけ。他(表に無い method も)は断り、手番は型付きの失敗 + turn/interrupt。
SERVER_REQUEST_ACCEPT: dict[str, JSONObject] = {
    "item/commandExecution/requestApproval": {"decision": "accept"},
    "item/fileChange/requestApproval": {"decision": "accept"},
    "execCommandApproval": {"decision": "approved"},
    "applyPatchApproval": {"decision": "approved"},
}
REFUSE_CODE = -32001
FAIL_UNSUPPORTED_SERVER_REQUEST = "unsupported-server-request"
FAIL_INITIALIZE = "initialize-failed"
FAIL_THREAD_START = "thread-start-failed"
FAIL_THREAD_RESUME = "thread-resume-failed"
FAIL_TURN_START = "turn-start-failed"
#: 全面許可の意味を app-server の params へ写す(dotfiles codex_shim.full_access_app_server と同値)。
THREAD_FULL_ACCESS: JSONObject = {"approvalPolicy": "never", "sandbox": "danger-full-access"}
TURN_FULL_ACCESS: JSONObject = {"sandboxPolicy": {"type": "dangerFullAccess"}}


@dataclass(frozen=True)
class CodexPlan:
    """thread を開く時の値(cwd・model・続きの thread の id)。effort は手番ごと。"""

    cwd: str
    model: str | None = None
    effort: str | None = None
    resume_thread_id: str | None = None


@dataclass
class _CodexState:
    thread_id: str = ""
    turn_id: str = ""
    thread_open: bool = False
    pending_prompt: str | None = None
    failure: str = ""
    interrupt_sent: bool = False
    counter: int = 0


class CodexDialogue:
    """``codex app-server --listen stdio://`` の作法(schema v2)。process は手番の間も生きる。"""

    kind: AgentKind = "codex"
    one_process_per_turn: bool = False

    def __init__(self, plan: CodexPlan) -> None:
        self.plan = plan
        self.state = _CodexState(thread_id=plan.resume_thread_id or "")
        self.conversation: dict[str, str] | None = (
            {"session_id": plan.resume_thread_id} if plan.resume_thread_id else None
        )

    # -- 封筒 ---------------------------------------------------------------------

    def _request(self, method: str, params: JSONObject) -> str:
        self.state.counter += 1
        return _dumps(
            {
                "jsonrpc": "2.0",
                "id": f"{method}#{self.state.counter}",
                "method": method,
                "params": params,
            }
        )

    @staticmethod
    def _notify(method: str, params: JSONObject) -> str:
        return _dumps({"jsonrpc": "2.0", "method": method, "params": params})

    @staticmethod
    def _reply(rid: JSON, result: JSONObject) -> str:
        return _dumps({"jsonrpc": "2.0", "id": rid, "result": result})

    @staticmethod
    def _refuse(rid: JSON, message: str) -> str:
        return _dumps(
            {"jsonrpc": "2.0", "id": rid, "error": {"code": REFUSE_CODE, "message": message}}
        )

    def _thread_params(self) -> JSONObject:
        params: JSONObject = dict(THREAD_FULL_ACCESS)
        params["cwd"] = self.plan.cwd
        if self.plan.model:
            params["model"] = self.plan.model
        return params

    def _turn_start(self, prompt: str) -> str:
        params: JSONObject = {
            "threadId": self.state.thread_id,
            "input": [{"type": "text", "text": prompt}],
        }
        params.update(TURN_FULL_ACCESS)
        if self.plan.effort:
            params["effort"] = self.plan.effort
        self.state.turn_id = ""
        self.state.interrupt_sent = False
        return self._request(REQ_TURN_START, params)

    def _owns(self, params: JSONObject) -> bool:
        """通知 / 要求が自分の thread と turn のものか(id が在って違う時だけ「別の実行」)。"""
        thread = _text_at(params, "threadId")
        if thread and self.state.thread_id and thread != self.state.thread_id:
            return False
        turn_id = _text_at(_object_at(params, "turn"), "id") or _text_at(params, "turnId")
        return not (turn_id and self.state.turn_id and turn_id != self.state.turn_id)

    # -- 効果 ----------------------------------------------------------------------

    def opening(self) -> tuple[str, ...]:
        return (self._request(REQ_INITIALIZE, {"clientInfo": dict(CLIENT_INFO)}),)

    def turn(self, prompt: str) -> TurnInput:
        if self.state.thread_open:
            return TurnInput(sends=(self._turn_start(prompt),), close_stdin=False)
        self.state.pending_prompt = prompt
        return TurnInput(sends=(), close_stdin=False)

    def interrupt(self) -> Interrupt:
        if self.state.turn_id and self.state.thread_id and not self.state.interrupt_sent:
            self.state.interrupt_sent = True
            return Interrupt(
                sends=(
                    self._request(
                        REQ_TURN_INTERRUPT,
                        {"threadId": self.state.thread_id, "turnId": self.state.turn_id},
                    ),
                )
            )
        return Interrupt()

    def on_line(self, record: JSONObject) -> Step:
        method = record.get("method")
        rid = record.get("id")
        params = _object_at(record, "params")
        if isinstance(method, str) and rid is not None:
            return self._on_server_request(rid, method, params)
        if isinstance(method, str):
            return self._on_notification(method, params)
        if rid is not None and ("result" in record or "error" in record):
            return self._on_response(str(rid), record)
        return Step()

    def _fail(self, reason: str) -> Step:
        if not self.state.failure:
            self.state.failure = reason
        return Step(failure=self.state.failure)

    def _on_response(self, rid: str, record: JSONObject) -> Step:
        method = rid.split("#", 1)[0]
        error = record.get("error")
        result = _object_at(record, "result")
        if method == REQ_INITIALIZE:
            return self._on_initialize_reply(error)
        if method in (REQ_THREAD_START, REQ_THREAD_RESUME):
            return self._on_thread_reply(method, error, result)
        if method == REQ_TURN_START:
            if error is not None:
                return self._fail(f"{FAIL_TURN_START}: {_text_at(error, 'message')}")
            started = _text_at(_object_at(result, "turn"), "id")
            if started and not self.state.turn_id:
                self.state.turn_id = started
        return Step()

    def _on_initialize_reply(self, error: JSON) -> Step:
        if error is not None:
            return self._fail(f"{FAIL_INITIALIZE}: {_text_at(error, 'message')}")
        if self.plan.resume_thread_id:
            opener = self._request(
                REQ_THREAD_RESUME,
                dict(self._thread_params(), threadId=self.plan.resume_thread_id),
            )
        else:
            opener = self._request(REQ_THREAD_START, self._thread_params())
        return Step(sends=(self._notify(NOTE_INITIALIZED, {}), opener))

    def _on_thread_reply(self, method: str, error: JSON, result: JSONObject) -> Step:
        if error is not None:
            reason = FAIL_THREAD_RESUME if method == REQ_THREAD_RESUME else FAIL_THREAD_START
            return self._fail(f"{reason}: {_text_at(error, 'message')}")
        opened = _text_at(_object_at(result, "thread"), "id")
        if opened and self.plan.resume_thread_id and opened != self.plan.resume_thread_id:
            return self._fail(
                f"{FAIL_THREAD_RESUME}: opened {opened} instead of {self.plan.resume_thread_id}"
            )
        if opened:
            self.state.thread_id = opened
        if not self.state.thread_id:
            return self._fail(f"{FAIL_THREAD_START}: response carried no thread id")
        self.state.thread_open = True
        self.conversation = {"session_id": self.state.thread_id}
        sends: tuple[str, ...] = ()
        if self.state.pending_prompt is not None:
            prompt = self.state.pending_prompt
            self.state.pending_prompt = None
            sends = (self._turn_start(prompt),)
        return Step(sends=sends, conversation=self.conversation)

    def _on_notification(self, method: str, params: JSONObject) -> Step:
        if not self._owns(params):
            return Step()
        if method == NOTE_TURN_STARTED:
            started = _text_at(_object_at(params, "turn"), "id")
            if started and not self.state.turn_id:
                self.state.turn_id = started
            return Step()
        if method == NOTE_TURN_COMPLETED:
            turn = _object_at(params, "turn")
            status = _text_at(turn, "status")
            self.state.turn_id = ""
            if self.state.failure:
                failure, self.state.failure = self.state.failure, ""
                return Step(ended=TurnEnded(ok=False, detail=failure))
            if status == "completed":
                return Step(ended=TurnEnded(ok=True, detail=status))
            message = _text_at(_object_at(turn, "error"), "message")
            return Step(ended=TurnEnded(ok=False, detail=message or f"turn-{status or 'unknown'}"))
        return Step()

    def _on_server_request(self, rid: JSON, method: str, params: JSONObject) -> Step:
        accept = SERVER_REQUEST_ACCEPT.get(method)
        if accept is not None:
            return Step(sends=(self._reply(rid, dict(accept)),))
        reason = f"{FAIL_UNSUPPORTED_SERVER_REQUEST}:{method}"
        refusal = self._refuse(
            rid, f"{reason}: the sessionhost headless backend cannot answer this request"
        )
        if not self._owns(params):
            return Step(sends=(refusal,))
        if not self.state.failure:
            self.state.failure = reason
        plan = self.interrupt()
        return Step(sends=(refusal, *plan.sends), failure=self.state.failure)


Dialogue: TypeAlias = "ClaudeDialogue | CodexDialogue"


# ------------------------------------------------------------------ 観測と手番の判断(純関数 1 点)


@dataclass(frozen=True)
class HeadlessObservation:
    """器(headless_process)が monitor の拍に返す観測: process の生死と、前の拍から読んだ事実。"""

    alive: bool
    exit_code: int | None
    #: 前の拍から読んだ stdout の行(JSON として読めた dict)。
    records: tuple[JSONObject, ...] = ()
    #: 前の拍から読んだ手番の終わり(複数なら最後が今の手番)。
    ended: tuple[TurnEnded, ...] = ()
    conversation: dict[str, str] | None = None
    failure: str | None = None
    #: 次の手番を同じ process で受けられるか(codex = 生きていれば真・claude = 偽)。
    accepts_turn: bool = False
    #: この手番に割り込みの合図(SIGINT / turn/interrupt)を出したか — claude は SIGINT で
    #: result を出さずに降りるので、その死は失敗ではなく「止めた手番の終わり」。
    interrupted: bool = False


VerdictKind = Literal["running", "turn-ended", "failed", "gone", "idle"]


@dataclass(frozen=True)
class Verdict:
    kind: VerdictKind
    ok: bool = True
    detail: str = ""


def turn_verdict(observation: HeadlessObservation | None, in_flight: bool) -> Verdict:
    """headless の行の次の 1 手(閉語彙 VerdictKind)— 器の観測と「手番の途中か」から:
    器に process が登記されていない → 手番の途中なら gone(host が process を失った)、
    そうでなければ idle / 手番の終わりを読んだ → turn-ended(最後の 1 つ)/ 型付きの失敗 →
    failed / 手番の途中に process が降りた(終わりを読まずに)→ 割り込みの合図を出していれば
    turn-ended(interrupted)、出していなければ failed / 手番の途中 → running / それ以外 →
    idle(温かい・何もしない)。"""
    if observation is None:
        return (
            Verdict("gone", ok=False, detail="no headless process is registered for this session")
            if in_flight
            else Verdict("idle")
        )
    if observation.ended:
        last = observation.ended[-1]
        return Verdict("turn-ended", ok=last.ok, detail=last.detail)
    if observation.failure:
        return Verdict("failed", ok=False, detail=observation.failure)
    if not in_flight:
        return Verdict("idle")
    if observation.alive:
        return Verdict("running")
    return _exit_verdict(observation)


def _exit_verdict(observation: HeadlessObservation) -> Verdict:
    """手番の途中に process が降りた: 割り込みの合図の後なら止めた手番の終わり、そうでなければ失敗。"""
    if observation.interrupted:
        return Verdict("turn-ended", ok=False, detail="interrupted")
    return Verdict(
        "failed",
        ok=False,
        detail=f"process exited with code {observation.exit_code} before the turn ended",
    )


def parse_record(line: str) -> JSONObject | None:
    """stdout の 1 行 → JSON の object(壊れた行・object でない行は None — 発明しない)。"""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        value: JSON = json.loads(stripped)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
