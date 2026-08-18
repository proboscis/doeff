"""doeff-adr が外部 process と接する唯一の層。

外部 process(semgrep / pytest)の応答時間は検査の意味に属さない — 機体の事情である。
その事情が検査の完了そのものを人質に取らないよう、待ちの上限はこの層の構造として
常に付く。起動地点ごとの任意引数にすると必ず書き忘れが起きる(2026-08-18 実測:
package 全域の外部 process 起動 2 箇所すべてが timeout なし)。

打ち切りは「結果なし」ではなく「走らなかった」— 呼び出し側が findings なし・検査通過
と黙読できない専用の例外へ変換して返す。
"""

import math
import os
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path

# 既定 600 秒 — 高負荷帯(2026-08-18 host-load 実測: load1 220〜266 = 12〜15/CPU、
# disk0 45,000 tps・723 MB/s の帯で `ls` 1 発が 120 秒で完走しない)で正当に遅れた
# scan / collect を打ち切らず、かつ hang を人手なしで有限時間に落とせる幅。
DEFAULT_EXTERNAL_PROCESS_TIMEOUT_SECONDS: float = 600.0
EXTERNAL_PROCESS_TIMEOUT_ENV = "DOEFF_ADR_EXTERNAL_PROCESS_TIMEOUT_SECONDS"


class ExternalProcessTimeoutError(RuntimeError):
    """外部 process が上限まで返らなかった。

    「結果が空だった」ではなく「結果が存在しない」— 検査の材料としては
    走らなかった run と同じ扱いをしなければならない。
    """

    def __init__(
        self,
        command: Sequence[str],
        timeout_seconds: float,
        *,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.command: list[str] = list(command)
        self.timeout_seconds = timeout_seconds
        self.stdout = stdout
        self.stderr = stderr
        detail = f"; partial stderr:\n{stderr[-2000:]}" if stderr else ""
        super().__init__(
            f"external process did not return within {timeout_seconds:g}s and was killed — "
            f"no verdict was produced (this is not a passing check); "
            f"command: {self.command_line}; "
            f"raise {EXTERNAL_PROCESS_TIMEOUT_ENV} if this bound is too tight{detail}"
        )

    @property
    def command_line(self) -> str:
        return shlex.join(self.command)


def external_process_timeout_seconds() -> float:
    """待ちの上限(秒)。環境変数で上書きでき、読めない値は黙って無期限に落とさない。"""
    raw = os.environ.get(EXTERNAL_PROCESS_TIMEOUT_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_EXTERNAL_PROCESS_TIMEOUT_SECONDS
    try:
        seconds = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{EXTERNAL_PROCESS_TIMEOUT_ENV}={raw!r} is not a number of seconds"
        ) from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(
            f"{EXTERNAL_PROCESS_TIMEOUT_ENV}={raw!r} must be a finite positive number of "
            f"seconds — doeff-adr does not run external processes without a bound"
        )
    return seconds


def run_external_process(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """外部 process を有限の待ちで起動する。

    `timeout_seconds` は上限の「省略」ではなく上書き — 省略時は
    `external_process_timeout_seconds()` の値が必ず適用される。
    """
    if timeout_seconds is None:
        bound = external_process_timeout_seconds()
    elif timeout_seconds <= 0:
        raise ValueError(
            f"timeout_seconds must be positive, got {timeout_seconds!r} — "
            f"doeff-adr does not run external processes without a bound"
        )
    else:
        bound = timeout_seconds
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=bound,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExternalProcessTimeoutError(
            command,
            bound,
            stdout=_as_text(exc.stdout),
            stderr=_as_text(exc.stderr),
        ) from exc


def _as_text(partial: str | bytes | None) -> str:
    """打ち切り時の部分出力 — POSIX と Windows で bytes/str が割れるため両方を受ける。"""
    if partial is None:
        return ""
    if isinstance(partial, bytes):
        return partial.decode("utf-8", errors="replace")
    return partial
