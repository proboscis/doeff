"""headless backend の器 — 子 process(claude -p / codex app-server)の起動・stdin の書き手・
stdout の読み手・events file への追記・観測の束(agora-redesign #37・段 2 lane 2d)。

判断は持たない: 「何を stdin へ書くか・手番はいつ終わるか・停止の合図を出すか」は headless_protocol.py の
Dialogue(純粋)が効果の値で返し、ここはそれを運ぶだけ(書き手 thread へ積む・SIGINT を送る・process を
降ろす)。読み手 thread は stdout の 1 行ごとに (1) events file へ逐語で追記(1 行 1 event —
agentd が offset から読む実況の正本)、(2) Dialogue.on_line の答えの sends を書き手へ、(3) 手番の
終わり・会話の id・型付きの失敗を観測の束へ積む。monitor の拍(HeadlessPoll)が束を空にして
HeadlessObservation として受け取る。

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
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import IO

from doeff_agents.sessionhost.attachment import TurnAttachment, TurnContent
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
STDERR_SUFFIX = ".stderr"


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
    ) -> None:
        self.name = name
        self.argv = tuple(argv)
        self.events_path = events_path
        self.dialogue = dialogue
        #: 手番の終わりの合図(段 12 lane 12b・agora-redesign #207 根 1): 読み手の thread が Dialogue.on_line で
        #: 手番の終わりを読んだ拍に、この名で呼ぶ(登記簿の待ち手 = host の monitor を起こす)。判断は増えない —
        #: 境界を決めるのは今日どおり Dialogue の 1 点で、ここはその答えを合図にするだけ。None = 合図なし。
        self._on_turn_ended = on_turn_ended
        directory = os.path.dirname(events_path)
        if directory:
            os.makedirs(directory, mode=0o700, exist_ok=True)
        self._events = open(events_path, "a", encoding="utf-8")  # noqa: SIM115 — 読み手 thread が閉じる
        self._stderr = open(events_path + STDERR_SUFFIX, "a", encoding="utf-8")  # noqa: SIM115
        self._lock = threading.Lock()
        self._records: list[JSONObject] = []
        self._ended: list[TurnEnded] = []
        self._conversation: dict[str, str] | None = None
        self._failure: str | None = None
        self._interrupted = False
        self._process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        stdin = self._process.stdin
        stdout = self._process.stdout
        if stdin is None or stdout is None:
            raise RuntimeError("headless process was spawned without stdin / stdout pipes")
        self._writer = _StdinWriter(stdin)
        self._writer.start()
        self._reader = threading.Thread(
            target=self._read, args=(stdout,), name="headless-stdout", daemon=True
        )
        self._reader.start()
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
        """stdin を閉じ、猶予の後に SIGTERM → SIGKILL。thread は最後に合流する。"""
        self.close_stdin()
        if self.alive():
            try:
                self._process.wait(timeout=EOF_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self.terminate()
                try:
                    self._process.wait(timeout=TERM_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    self.force_kill()
                    self._process.wait(timeout=TERM_GRACE_SECONDS)
        self.join_io(2.0)

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

    # -- 読み手 -----------------------------------------------------------------------

    def _read(self, stdout: IO[str]) -> None:
        try:
            for raw in stdout:
                self._events.write(raw if raw.endswith("\n") else raw + "\n")
                self._events.flush()
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
                # 手番の終わりは lock の外で合図する(待ち手は observe() で束を取りに来る — 束は先に置いてある)。
                if step.ended is not None and self._on_turn_ended is not None:
                    self._on_turn_ended(self.name)
        finally:
            with contextlib.suppress(OSError):
                self._events.close()
            with contextlib.suppress(OSError):
                self._stderr.close()


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

    def __init__(self) -> None:
        self._processes: dict[str, HeadlessProcess] = {}
        self._lock = threading.Lock()
        #: 手番の終わりの合図(段 12 lane 12b・agora-redesign #207 根 1): 登記した process の読み手が手番の終わりを
        #: 読むたびに 1 つ進む数と、それを待つ条件変数。host の monitor は拍の合間をこの待ちで過ごし(上限 =
        #: monitor の周期 — 周期は保険に退く)、手番が終わった拍に即座に観測して turn_ended_at を刻む。
        self._turn_ends = threading.Condition()
        self._turn_end_count = 0

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
            if existing is not None and existing.alive():
                raise RuntimeError(f"headless session already exists: {name}")
            process = HeadlessProcess(name, argv, cwd, env, events_path, dialogue, self._turn_ended)
            self._processes[name] = process
            return process

    def get(self, name: str) -> HeadlessProcess | None:
        with self._lock:
            return self._processes.get(name)

    def has_alive(self, name: str) -> bool:
        process = self.get(name)
        return process is not None and process.alive()

    def kill(self, name: str) -> bool:
        """名の process を降ろして忘れる。戻り = 登記が在ったか。"""
        with self._lock:
            process = self._processes.pop(name, None)
        if process is None:
            return False
        process.kill()
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
