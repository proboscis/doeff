"""headless backend の器 — 子 process(claude -p / codex app-server)の起動・stdin の書き手・
stdout の読み手・events file への追記・観測の束(agora-redesign #37・段 2 lane 2d)。

判断は持たない: 「何を stdin へ書くか・手番はいつ終わるか・停止の合図を出すか」は headless_protocol.py の
Dialogue(純粋)が効果の値で返し、ここはそれを運ぶだけ(書き手 thread へ積む・SIGINT を送る・process を
降ろす)。読み手 thread は stdout の 1 行ごとに (1) events file へ逐語で追記(1 行 1 event —
agentd が offset から読む実況の正本)、(2) Dialogue.on_line の答えの sends を書き手へ、(3) 手番の
終わり・会話の id・型付きの失敗を観測の束へ積む、(4) 答えが対話の終わり(``Step.close``)なら**その
行で** process を降ろし始める(retire = stdin に EOF → 猶予 → SIGTERM → 猶予 → SIGKILL の梯子を
自分の thread で — 呼び手を止めない)。monitor の拍(HeadlessPoll)が束を空にして HeadlessObservation
として受け取る。

retire(段 12 lane 12e・agora-redesign #517): claude の手番の終わり = process の終わり。読み手が result の
行を読んだその場で EOF を出すのは、CLI が result の後に自分の background task の完了で model を起こし
直す隙間(手番の外の tool_use・記録に載らない行動)を **拍の待ち(monitor の 1 秒)より前に**閉じるため。
EOF で降りない process は梯子が降ろす — 「手番の終わり = process の終わり」は EOF の作法に依らず器が守る。

読み手と書き手を分ける理由(dotfiles agentcli/headless.py の _StdinWriter と同じ): 読みの thread が
stdin へ書くと、server が stdin を読んでいない拍に stdout の読みまで止まる。

この module は sessionhost の substrate の内側(substrate_headless.hy から呼ばれる)で、host の
公開 RPC の語彙は 1 語も持たない。
"""

# pyright: strict
import contextlib
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import IO, Literal

from doeff_agents.sessionhost.attachment import TurnAttachment, TurnContent
from doeff_agents.sessionhost.headless_events import (
    FileEventStore,
    HeadlessEventAppend,
    HeadlessEventStore,
)
from doeff_agents.sessionhost.headless_protocol import (
    Dialogue,
    HeadlessObservation,
    JSONObject,
    TurnEnded,
    parse_record,
)

#: stdin を閉じた後・SIGTERM の後に process が自分で降りるのを待つ猶予(秒)。
EOF_GRACE_SECONDS = 5.0
TERM_GRACE_SECONDS = 5.0


class HeadlessProcessStillAliveError(RuntimeError):
    """降ろす段(EOF → SIGTERM → SIGKILL)を全部踏んでも、猶予の中で process が降りなかった
    (agora-redesign #547)。呼び手(cleanup)は片付いたと記帳せず、登記簿は所有を保つ — 次の cleanup が
    同じ process を降ろし直す。"""

    def __init__(self, name: str, pid: int) -> None:
        super().__init__(
            f"headless process pid {pid} of session {name} is still alive after "
            "stdin EOF, SIGTERM and SIGKILL — the registry keeps owning it so the next "
            "cleanup retries (it is not recorded as cleaned)"
        )
        self.name = name
        self.pid = pid


class _StdinWriter:
    """stdin へ書く thread 1 本。close() は積んだ行の後に EOF を出す(冪等)。"""

    def __init__(self, stdin: IO[str]) -> None:
        self._stdin = stdin
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="headless-stdin", daemon=True)
        self.broken = False
        self.closed = False

    def start(self) -> None:
        self._thread.start()

    def send(self, line: str) -> None:
        self._queue.put(line)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._queue.put(None)

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            try:
                self._stdin.write(item if item.endswith("\n") else item + "\n")
                self._stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                self.broken = True
                break
        with contextlib.suppress(BrokenPipeError, OSError, ValueError):
            self._stdin.close()


class HeadlessProcess:
    """1 つの headless の agent の process(1 session に 1 つ・温かい — 手番の間も生きる)。"""

    def __init__(
        self,
        name: str,
        argv: Sequence[str],
        cwd: str,
        env: Mapping[str, str],
        events_path: str,
        dialogue: Dialogue,
        on_turn_ended: Callable[[str], None] | None = None,
        events: HeadlessEventStore | None = None,
    ) -> None:
        self.name = name
        self.argv = tuple(argv)
        self.events_path = events_path
        self.dialogue = dialogue
        #: 手番の終わりの合図(段 12 lane 12b・agora-redesign #207 根 1): 読み手の thread が Dialogue.on_line で
        #: 手番の終わりを読んだ拍に、この名で呼ぶ(登記簿の待ち手 = host の monitor を起こす)。判断は増えない —
        #: 境界を決めるのは今日どおり Dialogue の 1 点で、ここはその答えを合図にするだけ。None = 合図なし。
        self._on_turn_ended = on_turn_ended
        #: 出来事の置き場の handler(headless_events — 本番の pod = 送り待ちの表・Mac = file・検 = memory)。
        #: events_path は置き場の名(locator)で、file の置き場だけがそれを path として読む。
        self._store: HeadlessEventStore = events if events is not None else FileEventStore()
        self._store.open_stream(events_path)
        self._lock = threading.Lock()
        self._records: list[JSONObject] = []
        self._ended: list[TurnEnded] = []
        self._conversation: dict[str, str] | None = None
        self._failure: str | None = None
        self._interrupted = False
        #: retire(対話の終わりで降ろし始めた)— 梯子の thread は 1 本だけ(冪等)。
        self._retire_lock = threading.Lock()
        self._retiring: threading.Thread | None = None
        self._process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        stdin = self._process.stdin
        stdout = self._process.stdout
        stderr = self._process.stderr
        if stdin is None or stdout is None or stderr is None:
            raise RuntimeError("headless process was spawned without stdin / stdout / stderr pipes")
        self._writer = _StdinWriter(stdin)
        self._writer.start()
        self._reader = threading.Thread(
            target=self._read, args=(stdout,), name="headless-stdout", daemon=True
        )
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._read_stderr, args=(stderr,), name="headless-stderr", daemon=True
        )
        self._stderr_reader.start()
        for line in dialogue.opening():
            self._writer.send(line)

    @property
    def pid(self) -> int:
        return self._process.pid

    def alive(self) -> bool:
        return self._process.poll() is None

    def exit_code(self) -> int | None:
        return self._process.poll()

    # -- 運ぶ(Dialogue の効果の値 → I/O) -------------------------------------------

    def deliver(self, prompt: str, attachments: tuple[TurnAttachment, ...] = ()) -> bool:
        """次の手番の本文(と添付)を stdin へ。戻り = process が生きていて書ける状態だったか。
        綴りは Dialogue が組む(段 10 lane 10o の追補・法 012 R21)— ここは運ぶだけ。"""
        if not self.alive() or self._writer.closed:
            return False
        self._store.begin_turn(self.events_path)
        plan = self.dialogue.turn(TurnContent(text=prompt, attachments=attachments))
        for line in plan.sends:
            self._writer.send(line)
        if plan.close_stdin:
            self._writer.close()
        return True

    def inject(
        self, text: str, ref: str = "", attachments: tuple[TurnAttachment, ...] = ()
    ) -> bool:
        """割り込みの本文を走っている手番へ(段 8 lane 4x — stdin の行・判断は Dialogue.inject)。
        戻り = 器が受け取ったか(process が生きていて、走っている手番が在り、行を書いた)。
        手番の終わりを読んで monitor がまだ受け取っていない拍も「走っていない」(その本文は
        誰の手番でもない turn を起こしてはならない — 呼び手が queued へ倒す)。``ref`` = 行の名
        (段 10 lane 10n — claude の uuid・CLI の command_lifecycle がこの綴りで運命を名乗る)。"""
        if not self.alive() or self._writer.closed:
            return False
        with self._lock:
            if self._ended:
                return False
        plan = self.dialogue.inject(TurnContent(text=text, attachments=attachments), ref)
        if not plan.accepted:
            return False
        for line in plan.sends:
            self._writer.send(line)
        return True

    def escalate(self) -> bool:
        """停止の合図(段 10 lane 10n — 判断は Dialogue.escalate): 走っている手番に読まれていない注入が
        在れば control_request interrupt を stdin へ。戻り = 合図を出したか(出す物が無ければ偽 —
        手番が走っていない・queued の注入が無い・既に出して答え待ち・注入の段の無い器)。"""
        if not self.alive() or self._writer.closed:
            return False
        with self._lock:
            if self._ended:
                return False
        plan = self.dialogue.escalate()
        if not plan.accepted:
            return False
        for line in plan.sends:
            self._writer.send(line)
        return True

    def interrupt(self) -> bool:
        """走っている手番を止める合図(stdin の行か SIGINT)。戻り = 合図を出せたか。"""
        if not self.alive():
            return False
        plan = self.dialogue.interrupt()
        for line in plan.sends:
            if not self._writer.closed:
                self._writer.send(line)
        if plan.signal:
            try:
                self._process.send_signal(signal.SIGINT)
            except OSError:
                return False
        signalled = bool(plan.sends) or plan.signal
        if signalled:
            with self._lock:
                self._interrupted = True
        return signalled

    def peek_records(self) -> tuple[JSONObject, ...]:
        """前の拍から読んだ stdout の行を**空にせずに**覗く(検の待ち条件・診断 — 観測の拍は observe)。"""
        with self._lock:
            return tuple(self._records)

    def observe(self) -> HeadlessObservation:
        """前の拍から読んだ事実を束ごと渡す(渡した後の束は空)。"""
        with self._lock:
            records = tuple(self._records)
            ended = tuple(self._ended)
            conversation = self._conversation
            failure = self._failure
            interrupted = self._interrupted
            self._records = []
            self._ended = []
            self._failure = None
            if ended:
                # 手番が終わった: 次の手番の合図は改めて出す
                self._interrupted = False
        alive = self.alive()
        return HeadlessObservation(
            alive=alive,
            exit_code=self.exit_code(),
            records=records,
            ended=ended,
            conversation=conversation,
            failure=failure,
            accepts_turn=alive
            and not self.dialogue.one_process_per_turn
            and not self._writer.closed,
            interrupted=interrupted,
        )

    def kill(self) -> None:
        """stdin を閉じ、猶予の後に SIGTERM → SIGKILL。thread は最後に合流する。全部の段を踏んでも猶予の中で
        降りなかった process は HeadlessProcessStillAliveError で型付きに断る(降りたと偽らない —
        agora-redesign #547)。"""
        self.close_stdin()
        if not self._escort_down():
            raise HeadlessProcessStillAliveError(self.name, self.pid)
        self.join_io(2.0)

    def _escort_down(self) -> bool:
        """降りるまで付き添う梯子(呼び手の thread で・有界): EOF の猶予 → SIGTERM → 猶予 → SIGKILL → 猶予。
        戻り = 降りたか(偽 = 全部の段を踏んでも生きている)。"""
        if self._went_down(EOF_GRACE_SECONDS):
            return True
        self.terminate()
        if self._went_down(TERM_GRACE_SECONDS):
            return True
        self.force_kill()
        return self._went_down(TERM_GRACE_SECONDS)

    # -- retire(対話の終わり = process の終わり・段 12 lane 12e・agora-redesign #517) --------------

    @property
    def retired(self) -> bool:
        """対話の終わりで降ろし始めた(stdin は閉じ、梯子が付き添っている / 付き添い終えた)。"""
        return self._retiring is not None

    def retire(self) -> None:
        """対話の終わりで process を降ろし始める(冪等・呼び手を止めない): stdin に EOF を出し、梯子
        (EOF の猶予 → SIGTERM → 猶予 → SIGKILL)を自分の thread で回す。読み手は process が降りるまで
        stdout を読み続ける(降り際の行も events file に残る)。梯子を踏み切っても降りない process は
        registry.kill / kill_all(cleanup)が HeadlessProcessStillAliveError で型付きに名乗る(ここでは投げない —
        読み手の thread に持ち主は居ない)。"""
        with self._retire_lock:
            if self._retiring is not None:
                return
            self._retiring = threading.Thread(
                target=self._escort_down, name=f"headless-retire-{self.name}", daemon=True
            )
            self.close_stdin()
            self._retiring.start()

    def join_retire(self, timeout: float) -> None:
        """梯子の thread に合流する(検・置き換えの前の待ち)。"""
        with self._retire_lock:
            escort = self._retiring
        if escort is not None:
            escort.join(timeout)

    def _went_down(self, grace: float) -> bool:
        """猶予の中で process が降りたか(既に降りていれば待たずに真)。"""
        try:
            self._process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            return False
        return True

    # -- 降ろす段(kill と kill_all が共有する 1 段ずつの動詞) ---------------------------

    def close_stdin(self) -> None:
        """stdin に EOF を出す(積んだ行の後・冪等)— 温かい claude はこれで降りる。"""
        self._writer.close()

    def terminate(self) -> None:
        """SIGTERM(降りていなければ)。"""
        if self.alive():
            with contextlib.suppress(OSError):
                self._process.terminate()

    def force_kill(self) -> None:
        """SIGKILL(降りていなければ)。"""
        if self.alive():
            with contextlib.suppress(OSError):
                self._process.kill()

    def join_io(self, timeout: float) -> None:
        """書き手と読み手の thread に合流する(降りた後)。"""
        self._writer.join(timeout)
        self._reader.join(timeout)
        self._stderr_reader.join(timeout)

    # -- 読み手 -----------------------------------------------------------------------

    def _append(self, stream: Literal["stdout", "stderr"], raw: str) -> None:
        """出来事の 1 行を置き場へ(handler の失敗は log して読みを止めない — 止めると子の pipe が詰まる)。"""
        try:
            self._store.append(HeadlessEventAppend(self.events_path, stream, raw))
        except Exception as error:
            sys.stderr.write(f"doeff-sessionhost headless {self.name}: events append failed: {error}\n")

    def _read_stderr(self, stderr: IO[str]) -> None:
        for raw in stderr:
            self._append("stderr", raw)

    def _read(self, stdout: IO[str]) -> None:
        try:
            for raw in stdout:
                self._append("stdout", raw)
                record = parse_record(raw)
                if record is None:
                    continue
                step = self.dialogue.on_line(record)
                for line in step.sends:
                    if not self._writer.closed:
                        self._writer.send(line)
                with self._lock:
                    self._records.append(record)
                    if step.ended is not None:
                        self._ended.append(step.ended)
                    if step.conversation is not None:
                        self._conversation = dict(step.conversation)
                    if step.failure is not None and self._failure is None:
                        self._failure = step.failure
                # 対話の終わり(claude の手番の終わり)= この行で process を降ろし始める(段 12 lane 12e・#517)。
                if step.close:
                    self.retire()
                # 手番の終わりは lock の外で合図する(待ち手は observe() で束を取りに来る — 束は先に置いてある)。
                if step.ended is not None and self._on_turn_ended is not None:
                    self._on_turn_ended(self.name)
        finally:
            with contextlib.suppress(OSError):
                stdout.close()


def pid_exists(pid: int) -> bool:
    """pid の process が在るか(kill 0 — 送らない)。他人の process(EPERM)も在る。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class HeadlessRegistry:
    """session の名 → 生きている(か降りたばかりの)process。host の process に 1 つ。"""

    def __init__(self, events: HeadlessEventStore | None = None) -> None:
        self._processes: dict[str, HeadlessProcess] = {}
        self._lock = threading.Lock()
        #: 出来事の置き場の handler(既定 = file — Mac の当面の形)。host の composition root が
        #: ``use_event_store`` で差し替える(pod = 送り待ちの表・検 = memory)。
        self._mut_events: HeadlessEventStore = events if events is not None else FileEventStore()
        #: 手番の終わりの合図(段 12 lane 12b・agora-redesign #207 根 1): 登記した process の読み手が手番の終わりを
        #: 読むたびに 1 つ進む数と、それを待つ条件変数。host の monitor は拍の合間をこの待ちで過ごし(上限 =
        #: monitor の周期 — 周期は保険に退く)、手番が終わった拍に即座に観測して turn_ended_at を刻む。
        self._turn_ends = threading.Condition()
        self._turn_end_count = 0

    @property
    def event_store(self) -> HeadlessEventStore:
        return self._mut_events

    def use_event_store(self, store: HeadlessEventStore) -> None:
        """出来事の置き場の handler を差し替える(起動時・process を 1 つも起こす前に 1 度)。"""
        self._mut_events = store

    def _turn_ended(self, name: str) -> None:
        """process の読み手からの合図(名は診断のため — 待ち手は名を選ばず、拍を 1 回起こす)。"""
        with self._turn_ends:
            self._turn_end_count += 1
            self._turn_ends.notify_all()

    def wait_turn_end(self, seen: int, timeout: float) -> int:
        """手番の終わりの数が ``seen`` を越えるまで待つ(上限 ``timeout`` 秒・0 = 待たずに今の数)。戻り = 今の数
        (呼び手が次の ``seen`` にする)。待っている間に終わった手番は数に残るので、拍の途中で終わった手番の合図が
        落ちることは無い(次の待ちが即座に返る)。"""
        with self._turn_ends:
            self._turn_ends.wait_for(lambda: self._turn_end_count > seen, timeout=max(0.0, timeout))
            return self._turn_end_count

    def spawn(
        self,
        name: str,
        argv: Sequence[str],
        cwd: str,
        env: Mapping[str, str],
        events_path: str,
        dialogue: Dialogue,
    ) -> HeadlessProcess:
        """名で起こす。同じ名の生きた process が在れば拒む(tmux の duplicate session と同じ)。
        降りた process(SIGINT で止めた claude・idle で退いた器)は置き換える。"""
        with self._lock:
            existing = self._processes.get(name)
            if existing is not None and existing.alive() and existing.retired:
                # 対話の終わりで降り始めた process が梯子の途中(EOF から数秒の内に次の手番が来た):
                # 付き添い終えてから置き換える(有界 — 梯子の猶予の和)。同じ名に生きた process は 1 つ。
                existing.join_retire(EOF_GRACE_SECONDS + 2 * TERM_GRACE_SECONDS + 1.0)
                existing.join_io(1.0)
            if existing is not None and existing.alive():
                raise RuntimeError(f"headless session already exists: {name}")
            process = HeadlessProcess(
                name, argv, cwd, env, events_path, dialogue, self._turn_ended, self._mut_events
            )
            self._processes[name] = process
            return process

    def get(self, name: str) -> HeadlessProcess | None:
        with self._lock:
            return self._processes.get(name)

    def has_alive(self, name: str) -> bool:
        process = self.get(name)
        return process is not None and process.alive()

    def kill(self, name: str) -> bool:
        """名の process を降ろして忘れる。戻り = 登記が在ったか。

        忘れるのは**降りたのを確かめた後**(agora-redesign #547): 登記簿は process の唯一の持ち主で、降ろす前に
        外すと、猶予の中で降りなかった process(HeadlessProcessStillAliveError)の持ち主が居なくなる — 次の cleanup は
        「登記なし」と読んで片付いたと記帳し、生きた process が残る。断りは素通しで、登記は残る(次の cleanup が
        同じ process を降ろし直す)。降ろしている間に同じ名で起こし直された登記(spawn は降りた process を
        置き換える)は外さない。"""
        with self._lock:
            process = self._processes.get(name)
        if process is None:
            return False
        process.kill()
        with self._lock:
            if self._processes.get(name) is process:
                del self._processes[name]
        return True

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._processes))

    def kill_all(self) -> int:
        """登記の全 process を段ごとに並列で降ろして忘れる(host の停止 — 段 10 lane 10h 便 2): 全部の stdin を
        閉じ → EOF の猶予を一緒に待ち → 生き残りに SIGTERM → 猶予 → SIGKILL。1 つずつ kill() すると猶予が
        process の数だけ直列に積み、launchd の ExitTimeOut(既定 20 s)を越える。戻り = 降ろした登記の数。"""
        with self._lock:
            processes = list(self._processes.values())
            self._processes = {}
        for process in processes:
            process.close_stdin()
        _wait_all(processes, EOF_GRACE_SECONDS)
        for process in processes:
            process.terminate()
        _wait_all(processes, TERM_GRACE_SECONDS)
        for process in processes:
            process.force_kill()
        _wait_all(processes, TERM_GRACE_SECONDS)
        for process in processes:
            process.join_io(1.0)
        return len(processes)


def _wait_all(processes: Sequence[HeadlessProcess], grace: float) -> None:
    """全部が降りるか猶予が尽きるまで待つ(並列の猶予 — 1 つずつ wait しない)。"""
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(process.alive() for process in processes):
        time.sleep(0.05)
