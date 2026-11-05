"""A small fake warm child for the contract test of the warm child, on the real side (tests/warm_contract_handlers.hy starts one per socket).

A script to reproduce, inside the check, only the promises kept by the real warm child (outside this package):
  * Receive a request at socket-path, read and answer the one line of the promise (reading and writing use the same functions as the real responder, warm-request-of and warm-answer-line)
  * mode = accept: fork a child A; A does setsid to become the head of a group, dup2s to the log, runs the entry (module) in a child B, and writes the end of B
    (a negative value if by signal) to the exit file by replacement, then ends. The warm child answers A's pid and start-ticks (read per machine type with proc-stat-of)
    (replacing env is the job of the real warm child, and this fake does not do it -- see the note of run_entry)
  * mode = refuse:<text> answers a refusal, and mode = silent does not answer and waits holding the connection (to see the requester's timeout)
The warm child does not wait for A's end (it discards SIGCHLD and reaps automatically), so a finished A is gone, and the requester reads it from the exit file.
"""

from __future__ import annotations

import os
import runpy
import signal
import socket
import sys

import hy  # noqa: F401  - to load the Hy modules of doeff_core_effects
from doeff import run
from doeff_core_effects import proc_stat_of, warm_answer_line, warm_request_of
from doeff_core_effects._os_warm_process import WarmRequestWire
from doeff_core_effects._process_stat import ProcStat
from doeff_core_effects import scheduled
from doeff_core_effects import WarmForked, WarmRefused
from doeff_hy._wire import Malformed


def start_ticks(pid: int) -> int:
    """To read the start time of the process pid -- the mark by which the requester tells apart a reused pid. It is read with proc-stat-of, the same per-machine-type read as the requester (os-warm-process-handler)
    (Linux = /proc, macOS = libproc -- reading /proc by itself cannot answer on macOS)."""
    seen: ProcStat | None = run(proc_stat_of(pid))
    if seen is None:
        raise ProcessLookupError(f"cannot read the state of the forked child {pid}")
    return seen.start_ticks


def run_entry(entry: str, args: tuple[str, ...], cwd: str) -> int:
    """Child B: to run the entry module with the given cwd in the same process and return the exit code.
    TERM is still blocked, inherited from A, so restore its handling to the default and then unblock -- a TERM that arrived earlier and was waiting also takes effect here.
    Replacing the child's environment variables with the request's env is the job of the real warm child (outside this package), and this fake does not replace them -- the contract test
    only checks that env is carried in the one line of the promise (the round-trip check) and that it does not appear in the answer."""
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
    os.chdir(cwd)
    sys.path.insert(0, cwd)
    sys.argv = [entry, *args]
    try:
        runpy.run_module(entry, run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return 0


def write_exit(exit_path: str, code: int) -> None:
    """Child A: to write the entry's exit code to the exit file by replacement (so the requester is never made to read a half-written content)."""
    partial = f"{exit_path}.partial"
    with open(partial, "w", encoding="utf-8") as f:
        f.write(f"{code}\n")
    os.replace(partial, exit_path)


def become_a(listener: socket.socket, connection: socket.socket, request: WarmRequestWire, ready: int) -> None:
    """Child A: to become the head of a group, direct output to the log, and write the end of child B to the exit file and end (it does not return).
    Block TERM (A does not die from a TERM sent to the group, and writes B's end). After setsid and the blocking are done, write 1 byte to the ready pipe --
    the warm child reads it and then answers the requester (so a signal that arrives before they are done is not missed with "no such process")."""
    listener.close()
    connection.close()
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    os.setsid()
    os.write(ready, b"r")
    os.close(ready)
    log = os.open(request.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(log, 1)
    os.dup2(log, 2)
    b = os.fork()
    if b == 0:
        os._exit(run_entry(request.entry, tuple(request.args), request.cwd))
    _, status = os.waitpid(b, 0)
    write_exit(request.exit_path, os.waitstatus_to_exitcode(status))
    os._exit(0)


def received_line(connection: socket.socket) -> bytes:
    """To receive one line of the request (up to the newline)."""
    received = b""
    while b"\n" not in received:
        chunk = connection.recv(4096)
        if not chunk:
            break
        received += chunk
    return received.split(b"\n", 1)[0]


def answer(listener: socket.socket, connection: socket.socket, mode: str) -> None:
    """To answer one request according to mode (accept starts child A)."""
    line = received_line(connection)
    if mode == "silent":
        # Do not answer, and wait for the requester to close the connection at its timeout (when closed, recv returns empty).
        while connection.recv(4096):
            pass
        return
    if mode.startswith("refuse:"):
        connection.sendall(run(scheduled(warm_answer_line(WarmRefused(detail=mode.removeprefix("refuse:"))))))
        return
    request = run(scheduled(warm_request_of(line)))
    if isinstance(request, Malformed):
        connection.sendall(run(scheduled(warm_answer_line(WarmRefused(detail="the shape of the request differs from the promise")))))
        return
    ready_read, ready_write = os.pipe()
    a = os.fork()
    if a == 0:
        os.close(ready_read)
        become_a(listener, connection, request, ready_write)
    os.close(ready_write)
    os.read(ready_read, 1)
    os.close(ready_read)
    connection.sendall(run(scheduled(warm_answer_line(WarmForked(pid=a, start_ticks=start_ticks(a))))))


def main() -> None:
    """To keep waiting for requests at socket-path (until the parent stops it)."""
    socket_path, mode = sys.argv[1], sys.argv[2]
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(socket_path)
        listener.listen(8)
        # The assembly side (warm_contract_handlers.hy) reads this one line and starts making requests (it does not wait by time).
        print("ready", flush=True)
        while True:
            connection, _ = listener.accept()
            with connection:
                answer(listener, connection, mode)


if __name__ == "__main__":
    main()
