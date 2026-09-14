"""headless backend の stdin / stdout の作法(純粋・I/O なし)— agora-redesign #37・段 2 lane 2d。

headless の session は tui の pane を持たない: agent は子 process で、prompt は stdin に書き、
実況は stdout の JSON 行(claude = ``claude -p --output-format stream-json``・codex =
``codex app-server`` の JSON-RPC)で読む。この module は「stdout の 1 行を読んだら次に何を stdin
へ書くか・手番はいつ終わったか・割り込みはどう伝えるか」の**判断**だけを持つ状態機械
(Dialogue)で、実際に書く・process を起こす・信号を送るのは headless_process.py の器の仕事。
検は同じ Dialogue を替え玉の書き手で回す(効果の値 = ``Step`` / ``TurnInput`` / ``Interrupt``)。

kind ごとの物理(argv は impls/headless_argv.hy・stdin の綴りはここ):
- claude: ``--input-format stream-json`` の温かい process(段 8 lane 4x・agora-redesign #56 —
  実測 2026-09-13 = conformance/interrupt-physics.md)。prompt は stdin の user の行
  ``{"type":"user","message":{"role":"user","content":<本文>}}`` で、stdin は閉じない: 手番の
  終わり(``{"type":"result"}`` の行)の後も process は生き、次の user の行が次の手番になる
  (同じ session_id・init の行がもう 1 度出る)。**手番の途中に書いた user の行は、CLI が次の
  tool の境界で走っている手番に注入する**(assistant がその本文に反応してから result が出る・
  num_turns が増える)= 割り込みの本文(``inject``)。会話の id は
  ``{"type":"system","subtype":"init","session_id"}``。止める合図(withdraw)= process へ SIGINT
  (result を出さずに降りる)。process が降りた後の次の手番は ``--resume <sid>`` の新しい process
  (argv は impls 側・器は同じ名で起こし直す)。
  段 10 lane 10n(agora-redesign #93・実測 2026-09-14 = 同 md の追記): 注入の行に ``uuid``(呼び手の
  ref = ACP の Message の id・UUID の形でなくてよい)を付けると CLI が
  ``{"type":"command_lifecycle","command_uuid":…,"state":queued|started|completed|cancelled|discarded|refused}``
  でその行の運命を名乗る — **started = model がその本文を読む拍**(道具の境界で畳まれた・または次の手番として
  走り出した)。道具が長くて境界が来ない間は queued のまま。停止の合図(``escalate``)=
  ``{"type":"control_request","request_id":…,"request":{"subtype":"interrupt"}}`` で、走っている道具 / 生成を
  止める: 答え ``control_response`` の ``still_queued`` に注入の uuid が在れば、続く ``result``(is_error・
  error_during_execution)は「止めた段の終わり」であって host から見た手番の終わりではない(CLI が注入の行を
  同じ session の次の手番として即座に走らせる — codex の inject と同じ扱い)。``still_queued`` に無い注入は
  次の手番にならない(畳みの途中で abort された)ので手番の終わり(interrupted)を報告する。停止の合図を
  出していない ``result`` の時点で queued のままの注入(道具の無い生成の途中に書いた行 — 実測の第 1 走)も
  同じ: CLI が次の手番として走らせるので手番は続く。
- codex: app-server の process を手番の間も生かす(温かい)。手番 = ``turn/start``、終わりは
  自分の thread と turn の ``turn/completed``、止める合図 = ``turn/interrupt``。割り込みの本文
  (``inject``)= ``turn/interrupt`` を送り、interrupted の ``turn/completed`` を手番の終わりとして
  報告せずに同じ thread へ ``turn/start`` を積む(host から見て手番は 1 つのまま)。server →
  client の要求は方策の表(全面許可の範囲の 4 種だけ accept・他は断って手番を型付きの失敗に
  する — dotfiles agentcli/codex_app_server.py と同じ規則)。

「手番の途中か」の判断(verdict)も純関数 1 点(``turn_verdict``)。

host の再起動の後の復帰(段 10 lane 10h・agora-redesign #84・既知の形 = kubelet が node の再起動の後に
container の生死を観測して pod の状態を直す): registry(host の process に 1 つ)は host と共に消えるので、
行が手番の途中(awaiting)のまま残った session の backend は観測で決める — pid の存在(kill 0)と所有(この
host の registry が同じ pid の生きた process を持つ)。判断は ``recovery_verdict`` の 1 点: 手番の途中 ∧
backend が死んでいる → 終端(``backend-dead``)/ それ以外 → keep(idle の温かい行は process が降りていても
次の send が --resume で同じ session を起こし直す設計なので触らない)。
"""

# pyright: strict
import json
import uuid
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
    """止める合図の伝え方: stdin へ書く行(codex の turn/interrupt)か process への SIGINT(claude)。"""

    sends: tuple[str, ...] = ()
    signal: bool = False


@dataclass(frozen=True)
class Injection:
    """割り込みの本文の伝え方(段 8 lane 4x): ``accepted`` = 走っている手番が在り本文を引き受けた
    (偽 = 器は受け取らなかった — 呼び手が queued へ倒す)。``sends`` = stdin へ書く行: claude =
    user の行(CLI が走っている手番に注入する)/ codex = turn/interrupt(次の turn/start は
    Dialogue が完了の通知で積む・既に止めていれば行は無く本文を継ぎ足すだけ)。``ref`` = 注入の行の
    名(段 10 lane 10n — claude は user の行の uuid に写し、CLI の command_lifecycle がこの綴りで
    運命を名乗る。codex は名を運ぶ欄が無い)。"""

    accepted: bool = False
    sends: tuple[str, ...] = ()
    ref: str = ""


@dataclass(frozen=True)
class Escalation:
    """停止の合図の伝え方(段 10 lane 10n): ``accepted`` = 走っている手番へ注入した行がまだ読まれて
    いない(queued)ので合図を出す / 偽 = 出す物が無い(手番が走っていない・queued の注入が無い・
    既に合図を出して答えを待っている・注入の段の無い器)。``sends`` = stdin へ書く行(claude =
    control_request interrupt)。``request_id`` = 答え(control_response)を結ぶ id。"""

    accepted: bool = False
    sends: tuple[str, ...] = ()
    request_id: str = ""


def _dumps(value: JSONObject) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _object_at(value: JSON, key: str) -> JSONObject:
    inner = value.get(key) if isinstance(value, dict) else None
    return inner if isinstance(inner, dict) else {}


def _text_at(value: JSON, key: str) -> str:
    inner = value.get(key) if isinstance(value, dict) else None
    return inner if isinstance(inner, str) else ""


# ------------------------------------------------------------------ claude(stream-json の入出力・温かい process)


def claude_user_line(text: str, ref: str = "") -> str:
    """``--input-format stream-json`` の stdin の 1 行 = user の message(実測の綴り)。``ref`` を
    付けると最上位の ``uuid`` に写す(CLI が command_lifecycle でこの綴りを名乗り返す — 段 10 lane 10n)。"""
    record: JSONObject = {"type": "user", "message": {"role": "user", "content": text}}
    if ref:
        record["uuid"] = ref
    return _dumps(record)


def claude_interrupt_request_line(request_id: str) -> str:
    """停止の合図の 1 行(実測 2026-09-14): ``control_request`` の subtype ``interrupt``。"""
    return _dumps(
        {"type": "control_request", "request_id": request_id, "request": {"subtype": "interrupt"}}
    )


#: 注入の行の運命(CLI の command_lifecycle の state の閉語彙・実測 2026-09-14)。queued = 命令の列に入った /
#: started = 手番に汲まれた(model が読む拍)/ 終端 = completed | cancelled | discarded | refused。
InjectionState = Literal["queued", "started", "completed", "cancelled", "discarded", "refused"]
INJECTION_TERMINAL_STATES: tuple[InjectionState, ...] = (
    "completed",
    "cancelled",
    "discarded",
    "refused",
)
#: 停止の合図で止めた段の終わり(手番の終わりとして報告する時の detail)。
INTERRUPTED_DETAIL = "interrupted"
#: CLI が system/init の capabilities で名乗る、注入の行の運命(command_lifecycle)を出す能力(実測 2.1.270)。名乗らない
#: CLI(旧い版)では運命が来ないので、注入は追わず(result で手番が終わる・停止の合図は出さない = 今日どおりの注入だけ)。
LIFECYCLE_CAPABILITY = "msg_lifecycle_v1"


_INJECTION_STATES: dict[str, InjectionState] = {
    "queued": "queued",
    "started": "started",
    "completed": "completed",
    "cancelled": "cancelled",
    "discarded": "discarded",
    "refused": "refused",
}


def injection_state_of(value: JSON) -> InjectionState | None:
    """command_lifecycle の state の語 → 閉語彙(語彙の外は None — 発明しない)。"""
    return _INJECTION_STATES.get(value) if isinstance(value, str) else None


class ClaudeDialogue:
    """``claude -p --input-format stream-json --output-format stream-json`` の作法。状態 = 会話の
    id(init で知る)と「手番の途中か」(user の行を書いてから result を読むまで)、注入した行の運命
    (ref → InjectionState・段 10 lane 10n)と出した停止の合図(request_id と答えの still_queued)。"""

    kind: AgentKind = "claude"
    #: 温かい process(段 8 lane 4x): result の後も process は生きて次の user の行を待つ。
    one_process_per_turn: bool = False

    def __init__(self) -> None:
        self.conversation: dict[str, str] | None = None
        self.in_flight: bool = False
        #: この手番に注入した行の運命(ref → state)。手番の終わりを報告した時に空にする。
        self.injections: dict[str, InjectionState] = {}
        #: 出した停止の合図の request_id(None = 出していない / 答えの後の result を読んだ)。
        self.escalation: str | None = None
        #: 停止の合図の答え: 次の手番として生き残る注入の ref(None = まだ答えを読んでいない)。
        self.still_queued: tuple[str, ...] | None = None
        #: CLI の手番が開いているか(user の行 / init から result まで — 停止で閉じた段の後、注入の行が
        #: 次の手番として走り出すまでの隙間を見分ける)。
        self.cli_turn_open: bool = False
        #: CLI が注入の行の運命(command_lifecycle)を名乗るか(init の capabilities に LIFECYCLE_CAPABILITY)。名乗らない
        #: CLI では注入を追わない(追うと queued のままの注入が result を飲み続けて手番が終わらない)。
        self.lifecycle: bool = False

    def opening(self) -> tuple[str, ...]:
        return ()

    def turn(self, prompt: str) -> TurnInput:
        self.in_flight = True
        self.cli_turn_open = True
        return TurnInput(sends=(claude_user_line(prompt),), close_stdin=False)

    def queued_injections(self) -> tuple[str, ...]:
        """まだ model に読まれていない(queued の)注入の ref(注入した順)。"""
        return tuple(ref for ref, state in self.injections.items() if state == "queued")

    def inject(self, text: str, ref: str = "") -> Injection:
        """走っている手番へ本文を注入する: 同じ user の行(CLI が次の tool の境界で読む)。手番が
        走っていなければ書かない(書くと新しい手番になる — 誰の job でもない手番を起こさない)。
        ``ref`` は行の uuid(無ければ鋳造)— CLI の command_lifecycle がこの綴りで運命を名乗る。"""
        if not self.in_flight:
            return Injection()
        name = ref or str(uuid.uuid4())
        if self.lifecycle:
            self.injections[name] = "queued"
        return Injection(accepted=True, sends=(claude_user_line(text, name),), ref=name)

    def escalate(self) -> Escalation:
        """停止の合図(段 10 lane 10n): 走っている手番に queued のままの注入が在り、まだ合図を出して
        いなければ control_request interrupt を書く。答え(control_response)の still_queued と続く
        result の扱いは on_line。"""
        if not self.in_flight or self.escalation is not None or not self.queued_injections():
            return Escalation()
        request_id = str(uuid.uuid4())
        self.escalation = request_id
        self.still_queued = None
        return Escalation(
            accepted=True, sends=(claude_interrupt_request_line(request_id),), request_id=request_id
        )

    def _end(self, ok: bool, detail: str) -> Step:
        self.in_flight = False
        self.cli_turn_open = False
        self.injections = {}
        self.escalation = None
        self.still_queued = None
        return Step(ended=TurnEnded(ok=ok, detail=detail))

    def on_line(self, record: JSONObject) -> Step:
        kind = record.get("type")
        if kind == "system" and record.get("subtype") == "init":
            self.cli_turn_open = True
            capabilities = record.get("capabilities")
            if isinstance(capabilities, list):
                self.lifecycle = LIFECYCLE_CAPABILITY in capabilities
            session_id = _text_at(record, "session_id")
            if session_id:
                self.conversation = {"session_id": session_id}
                return Step(conversation=self.conversation)
            return Step()
        if kind == "command_lifecycle":
            return self._on_lifecycle(record)
        if kind == "control_response":
            return self._on_control_response(record)
        if kind == "result":
            return self._on_result(record)
        return Step()

    def _on_lifecycle(self, record: JSONObject) -> Step:
        """注入した行の運命を写す。queued の注入を待って手番を続けていた(result を飲んだ)後に、その注入が
        走らずに終わった(cancelled / discarded / refused)なら、残りの queued も無ければ手番の終わり。"""
        ref = _text_at(record, "command_uuid")
        state = injection_state_of(record.get("state"))
        if not ref or state is None or ref not in self.injections:
            return Step()
        self.injections[ref] = state
        if (
            self.in_flight
            and not self.cli_turn_open
            and state in INJECTION_TERMINAL_STATES
            and state != "completed"
            and not self.queued_injections()
        ):
            return self._end(False, f"interrupt-{state}")
        return Step()

    def _on_control_response(self, record: JSONObject) -> Step:
        """停止の合図の答え: success なら still_queued(次の手番として走る注入の ref)を覚える。
        error なら合図は効かなかった — 出していない状態に戻す(呼び手が撃ち直せる)。"""
        response = _object_at(record, "response")
        if self.escalation is None or _text_at(response, "request_id") != self.escalation:
            return Step()
        if _text_at(response, "subtype") != "success":
            self.escalation = None
            self.still_queued = None
            return Step()
        payload = _object_at(response, "response")
        raw = payload.get("still_queued")
        self.still_queued = tuple(
            item for item in (raw if isinstance(raw, list) else []) if isinstance(item, str)
        )
        return Step()

    def _on_result(self, record: JSONObject) -> Step:
        """result の行: queued のままの注入(停止の合図の後は still_queued に名指されたもの)が在れば CLI が
        次の手番として走らせるので手番は続く(飲む)。無ければ手番の終わり。"""
        self.cli_turn_open = False
        queued = self.queued_injections()
        is_error = record.get("is_error") is True
        subtype = _text_at(record, "subtype") or ("error" if is_error else "success")
        if self.escalation is not None:
            survivors = (
                tuple(ref for ref in queued if ref in self.still_queued)
                if self.still_queued is not None
                else queued
            )
            self.escalation = None
            self.still_queued = None
            if survivors:
                return Step()
            return self._end(False, INTERRUPTED_DETAIL)
        if queued:
            return Step()
        return self._end(not is_error, subtype)

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
    #: 段 8 lane 4x: 割り込みの本文 — turn/interrupt を送った後、interrupted の turn/completed で
    #: 同じ thread へ turn/start する本文(host から見て手番は 1 つのまま)。
    pending_injection: str | None = None
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

    def _turn_interrupt(self) -> str:
        return self._request(
            REQ_TURN_INTERRUPT,
            {"threadId": self.state.thread_id, "turnId": self.state.turn_id},
        )

    def interrupt(self) -> Interrupt:
        if self.state.turn_id and self.state.thread_id and not self.state.interrupt_sent:
            self.state.interrupt_sent = True
            return Interrupt(sends=(self._turn_interrupt(),))
        return Interrupt()

    def inject(self, text: str, ref: str = "") -> Injection:
        """割り込みの本文(段 8 lane 4x): 走っている turn を turn/interrupt で止め、その完了の
        通知(interrupted)で同じ thread へ本文の turn/start を積む — host から見て手番は
        1 つのまま。走っている turn が無ければ受け取らない(sends が空)。``ref`` は運ぶ欄が無い
        (app-server の turn/start に名は無い)— 注入の段が無いので読んだ証拠は次の turn/started。"""
        if not (self.state.turn_id and self.state.thread_id):
            return Injection()
        if self.state.pending_injection is not None:
            # 前の割り込みの turn/start がまだ積まれていない: 本文を継ぎ足す(順は保つ)。
            self.state.pending_injection = self.state.pending_injection + "\n\n" + text
            return Injection(accepted=True, ref=ref)
        self.state.pending_injection = text
        if self.state.interrupt_sent:
            return Injection(accepted=True, ref=ref)
        self.state.interrupt_sent = True
        return Injection(accepted=True, sends=(self._turn_interrupt(),), ref=ref)

    def escalate(self) -> Escalation:
        """codex に注入の段は無い(inject が turn/interrupt で即座に止めて渡す — 能力 stop)。
        停止の合図は出す物が無い。"""
        return Escalation()

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
                self.state.pending_injection = None
                return Step(ended=TurnEnded(ok=False, detail=failure))
            if self.state.pending_injection is not None:
                # 段 8 lane 4x: 止めたのは割り込みの本文を渡すため — 手番の終わりではない。
                # 同じ thread へ本文の turn を積む(turn_id は応答 / turn/started で知る)。
                injected = self.state.pending_injection
                self.state.pending_injection = None
                return Step(sends=(self._turn_start(injected),))
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
    #: 次の手番を同じ process で受けられるか(生きていて stdin が開いていれば真 — claude も
    #: 段 8 lane 4x から温かい process)。
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


# ------------------------------------------------------------------ backend の生死と再起動の後の復帰(純関数 1 点)


@dataclass(frozen=True)
class BackendLiveness:
    """器の backend(headless の子 process)の生死の観測(段 10 lane 10h): ``exists`` = pid が在る(kill 0)、
    ``owned`` = この host の registry がその名で同じ pid の生きた process を持つ(stdin / stdout の pipe を
    握っているのはこの host)。host の再起動の後は registry が空なので owned は必ず偽 — 親を失った process は
    在っても器としては使えない(pipe の読み手が居ない)。"""

    pid: int | None
    exists: bool
    owned: bool


def backend_alive(liveness: BackendLiveness) -> bool:
    """backend が生きている = pid が在り ∧ この host が所有する。"""
    return liveness.exists and liveness.owned


RecoveryKind = Literal["keep", "backend-dead"]


@dataclass(frozen=True)
class RecoveryVerdict:
    kind: RecoveryKind
    detail: str = ""


def recovery_verdict(
    status_terminal: bool, in_flight: bool, liveness: BackendLiveness
) -> RecoveryVerdict:
    """host の起動時の復帰の 1 行の判断(閉語彙 RecoveryKind)— 行の事実と backend の観測から:
    終端の行 → keep / 手番の途中でない(idle の温かい行 — 次の send が --resume で同じ session を起こし直す)
    → keep / 手番の途中 ∧ backend が生きて所有 → keep(起こした process が在る)/ 手番の途中 ∧ backend が
    死んでいる(pid が無い・この host の所有でない)→ backend-dead(呼び手が status を終端に倒し、cause と
    event を刻む)。detail は cause の reason に載る観測の文(pid と在否・所有)。"""
    if status_terminal or not in_flight:
        return RecoveryVerdict("keep")
    if backend_alive(liveness):
        return RecoveryVerdict("keep")
    pid = "none" if liveness.pid is None else str(liveness.pid)
    fact = (
        "not running"
        if not liveness.exists
        else "running but not owned by this host (its stdio pipes died with the previous host)"
    )
    return RecoveryVerdict(
        "backend-dead",
        detail=f"backend process dead: headless process pid {pid} is {fact} while the turn was in flight",
    )


StopKind = Literal["keep", "turn-cut"]


def stop_verdict(status_terminal: bool, in_flight: bool) -> StopKind:
    """host の停止(TERM)の前の 1 行の判断(段 10 lane 10h 便 2): headless の子 process は host と共に降りるので、
    手番の途中(awaiting)の非終端の行は turn-cut(呼び手が stopped + cause cancelled(理由 = host の停止)にして
    黙って残さない)/ 終端の行・idle の温かい行は keep(行は触らない — 次の host の send が --resume で同じ session を
    起こし直す。process は行に依らず全部降ろす)。"""
    if status_terminal or not in_flight:
        return "keep"
    return "turn-cut"


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
