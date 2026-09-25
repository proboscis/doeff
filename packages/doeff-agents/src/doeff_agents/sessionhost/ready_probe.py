"""``doeff-sessionhost ready --socket <path>`` — host の readiness を終了 code に写す probe の口。

今の pool の readinessProbe は socket の connect だけを測るので、store が書けない host(volume の満杯 —
実弾 2026-09-25)も緑になる。この口は host の ``daemon.status`` を 1 度問い、答えの ``ready`` を読む:
ready = true → 0 / false・答えない・読めない → 1(理由を stderr に 1 行)。Hy も agentd も import しない
(probe は 10 秒ごとに起きるので、import の代を払わない)。判断そのものは host 側
(store_health.readiness_of)の 1 点で、ここは写すだけ。
"""

# pyright: strict
import json
import socket
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

READY_SUBCOMMAND = "ready"
#: 答えを待つ上限(秒)。probe の timeoutSeconds より短く置く。
READY_PROBE_TIMEOUT_SECONDS = 5.0

_USAGE = "usage: doeff-sessionhost ready --socket <path>\n"


@dataclass(frozen=True)
class ReadyVerdict:
    """probe の答え: ready と、その理由の 1 文。"""

    ready: bool
    reason: str


def ready_verdict(answer: object) -> ReadyVerdict:
    """daemon.status の応答(JSON-RPC の 1 行を解いた値)→ ReadyVerdict。純関数。"""
    if not isinstance(answer, dict):
        return ReadyVerdict(False, "host answered something that is not an object")
    reply = cast("dict[str, object]", answer)
    error = reply.get("error")
    if error is not None:
        return ReadyVerdict(False, f"host answered an error: {error}")
    raw_result = reply.get("result")
    if not isinstance(raw_result, dict):
        return ReadyVerdict(False, "host answered without a result")
    result = cast("dict[str, object]", raw_result)
    ready = result.get("ready")
    if ready is True:
        return ReadyVerdict(True, "ready")
    if ready is False:
        reason = result.get("not_ready_reason")
        return ReadyVerdict(False, str(reason) if reason else "host says it is not ready")
    return ReadyVerdict(False, "host did not name its readiness (an older host?)")


def ask_status(socket_path: str, timeout: float) -> object:
    """host の socket へ daemon.status を 1 度問い、応答の 1 行を JSON として返す。"""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(socket_path)
        conn.sendall(b'{"id": 1, "method": "daemon.status"}\n')
        buffer = b""
        while not buffer.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            buffer += chunk
    return json.loads(buffer.decode("utf-8"))


def main(argv: Sequence[str]) -> int:
    args = list(argv)
    if any(arg in ("--help", "-h") for arg in args):
        sys.stdout.write(_USAGE)
        return 0
    if len(args) != 2 or args[0] != "--socket":
        sys.stderr.write(_USAGE)
        return 2
    try:
        answer = ask_status(args[1], READY_PROBE_TIMEOUT_SECONDS)
    except (OSError, ValueError) as error:
        sys.stderr.write(f"doeff-sessionhost ready: host did not answer: {error}\n")
        return 1
    verdict = ready_verdict(answer)
    if not verdict.ready:
        sys.stderr.write(f"doeff-sessionhost ready: not ready: {verdict.reason}\n")
        return 1
    return 0
