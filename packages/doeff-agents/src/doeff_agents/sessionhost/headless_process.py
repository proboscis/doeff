"""headless backend の器 — 子 process(claude -p / codex app-server)の起動・stdin の書き手・
stdout の読み手・events file への追記・観測の束(agora-redesign #37・段 2 lane 2d)。

判断は持たない: 「何を stdin へ書くか・手番はいつ終わるか」は headless_protocol.py の Dialogue
(純粋)が効果の値で返し、ここはそれを運ぶだけ(書き手 thread へ積む・SIGINT を送る・process を
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
from collections.abc import Mapping, Sequence
from typing import IO

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
    ) -> None:
        self.name = name
        self.argv = tuple(argv)
        self.events_path = events_path
        self.dialogue = dialogue
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

    def deliver(self, prompt: str) -> bool:
        """次の手番の本文を stdin へ。戻り = process が生きていて書ける状態だったか。"""
        if not self.alive() or self._writer.closed:
            return False
        plan = self.dialogue.turn(prompt)
        for line in plan.sends:
            self._writer.send(line)
        if plan.close_stdin:
            self._writer.close()
        return True

    def inject(self, text: str) -> bool:
        """割り込みの本文を走っている手番へ(段 8 lane 4x — stdin の行・判断は Dialogue.inject)。
        戻り = 器が受け取ったか(process が生きていて、走っている手番が在り、行を書いた)。
        手番の終わりを読んで monitor がまだ受け取っていない拍も「走っていない」(その本文は
        誰の手番でもない turn を起こしてはならない — 呼び手が queued へ倒す)。"""
        if not self.alive() or self._writer.closed:
            return False
        with self._lock:
            if self._ended:
                return False
        plan = self.dialogue.inject(text)
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
        self._writer.close()
        if self.alive():
            try:
                self._process.wait(timeout=EOF_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self._process.terminate()
                try:
                    self._process.wait(timeout=TERM_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=TERM_GRACE_SECONDS)
        self._writer.join(2.0)
        self._reader.join(2.0)

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
        finally:
            with contextlib.suppress(OSError):
                self._events.close()
            with contextlib.suppress(OSError):
                self._stderr.close()


class HeadlessRegistry:
    """session の名 → 生きている(か降りたばかりの)process。host の process に 1 つ。"""

    def __init__(self) -> None:
        self._processes: dict[str, HeadlessProcess] = {}
        self._lock = threading.Lock()

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
            process = HeadlessProcess(name, argv, cwd, env, events_path, dialogue)
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
