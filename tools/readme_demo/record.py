"""README の頭の GIF(images/readme-demo.gif)を録り直す道具。

GIF は「`pip install doeff` → program の file と handler の file を見る → 同じ `digest` を 4 つの handler で走らせると、
handler が k(continuation)をどう扱うかで effect の意味と答えが変わる」を見せる — 値を答えて k を resume する canned・
全部の effect を見て次へ渡す trace・1 つ通した後に k を捨てて途中で止める budget(1)・本物の HTTP で答える live_world
(`python -m doeff demo` と同じ 4 つ)。台本は下の SCRIPT の 1 か所に在り、録る file は隣の digest.py と
handlers.py(GIF に映る file そのもの)。2 つは README の例と同じ書き方(定義の間の空行は 1 つ・註を揃える)で、それぞれ 1 画面に
収める。handlers.py は REPL で使う `handler` と `run` も import する — ruff の整形は当てない(`make lint-ruff` の範囲の外)。
走るかは tests/test_readme_demo.py が確かめる:

    uv run --no-project python tools/readme_demo/record.py                       # images/readme-demo.gif を書き直す
    uv run --no-project python tools/readme_demo/record.py --font-dir <dir>      # 等幅の字形が機体に無い時
    uv run --no-project python tools/readme_demo/record.py --cast demo.cast      # 中間の asciicast も残す

録り方:

1. repo の外の一時の dir に digest.py と handlers.py を写し、`uv venv --seed` で pip の入った新しい venv を作る。
2. その dir で bash を擬似端末(pty)の中に起こし、台本の命令を 1 字ずつ打ち込む。命令は本当に走る — `pip install doeff` は
   PyPI から入れ、`live_world` は https://example.com を本当に取りに行く。画面に出る物は bash と Python の出力そのもの。
3. 出力の刻を asciicast v2 に書く。打鍵の間と台本の「待つ」は決めた長さにし、命令の出力の間の待ち(pip の取り寄せなど)は
   縮める(asciinema の idle time limit と同じ扱い)。
4. agg(https://github.com/asciinema/agg)で GIF にする。agg と uv は PATH から探す。

bash と Python には、この機体の環境変数を渡さない(HOME は一時の dir・pip は設定の file を読まない)。画面に機体の path や
名が出ない。GIF の大きさの目安は 1 MB まで — 越えたら終了コード 1 で知らせる(GIF は書く)。

終了コード: 書けて目安に収まれば 0・目安を越えれば 1・道具が使えなければ(uv・agg が無い・命令が時間内に終わらない)2。
標準ライブラリだけを使う(workspace を同期せずに走る)。
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import pty
import random
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
from dataclasses import dataclass
from fcntl import ioctl
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# GIF に映る file — program の digest.py と、それを import する handler の handlers.py。
DEMO_FILES = (HERE / "digest.py", HERE / "handlers.py")
DEFAULT_OUT = ROOT / "images" / "readme-demo.gif"
SIZE_BUDGET_BYTES = 1_000_000

# 端末の大きさ — `cat handlers.py` の命令の行と file(29 行)と次の prompt が 1 画面に収まる高さ。
COLUMNS = 76
ROWS = 32
SHELL_PROMPT = r"\$ $"
PYTHON_PROMPT = r">>> $"


@dataclass(frozen=True)
class Type:
    """命令を 1 字ずつ打ち、Enter を押し、prompt が戻るまで出力を録る。

    gap_cap は出力の間の待ちの上限(秒)— pip の取り寄せのような長い待ちを縮める。
    """

    text: str
    prompt: str
    gap_cap: float = 0.3


@dataclass(frozen=True)
class Hold:
    """何も打たずに待つ(秒)— 見る人が画面を読む間。"""

    seconds: float


SCRIPT: tuple[Type | Hold, ...] = (
    Hold(0.5),
    Type("pip install doeff", SHELL_PROMPT, gap_cap=0.005),
    Hold(1.0),
    Type("cat digest.py", SHELL_PROMPT),
    Hold(2.0),
    Type("cat handlers.py", SHELL_PROMPT),
    Hold(3.0),
    Type("python -i handlers.py", PYTHON_PROMPT),
    Hold(0.5),
    Type("run(handler(canned)(digest(url)))", PYTHON_PROMPT),
    Hold(1.2),
    Type("run(handler(canned)(handler(trace)(digest(url))))", PYTHON_PROMPT),
    Hold(1.5),
    Type("run(handler(canned)(budget(1)(digest(url))))", PYTHON_PROMPT),
    Hold(1.5),
    Type("run(handler(canned)(handler(live_world)(digest(url))))", PYTHON_PROMPT),
)
# 最後の画面を見せる長さ(秒)— agg の --last-frame-duration。
LAST_FRAME_SECONDS = 4.0
# 1 字を打つ間(秒)と揺らぎ。揺らぎの乱数は種を決め、録り直しても同じ打ち方にする。
CHAR_SECONDS = 0.035
CHAR_JITTER = 0.4
SEED = 7
COMMAND_TIMEOUT_SECONDS = 300

ANSI = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>()][0-9A-Za-z]?"
)


class ToolError(Exception):
    """道具が使えない(終了コード 2)。"""


def plain(text: str) -> str:
    """端末の制御の列と CR を除いた字。prompt が戻ったかを読むのに使う。"""
    return ANSI.sub("", text).replace("\r", "")


class Recorder:
    """pty の中の bash へ打ち、出力を仮の時計の刻で録る。"""

    def __init__(self, fd: int) -> None:
        """録り始めの状態 — 仮の時計は 0 秒、録った出力は無し。"""
        self.fd = fd
        self.clock = 0.0
        self.events: tuple[tuple[float, str], ...] = ()
        self.since_enter = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.random = random.Random(SEED)

    def _read(self, timeout: float) -> str | None:
        """pty から届いた出力を字にする。timeout 秒の間 何も来なければ None・端末が閉じたら空の字。"""
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return None
        try:
            data = os.read(self.fd, 65536)
        except OSError:  # 子が閉じた pty は EIO を返す
            return ""
        return self.decoder.decode(data)

    def _record(self, text: str) -> None:
        """出力を今の刻で GIF の列に残し、prompt が戻ったかを読む分にも足す。"""
        if text:
            self.events = (*self.events, (round(self.clock, 3), text))  # noqa: DOEFF002 — 録画の状態を持つ 1 つの recorder の時計と入力の欄
            self.since_enter += text  # noqa: DOEFF002 — 録画の状態を持つ 1 つの recorder の時計と入力の欄

    def drain(self, quiet: float) -> None:
        """quiet 秒の間 何も来なくなるまで読み、今の刻で録る(打った字の echo など)。"""
        while (chunk := self._read(quiet)) is not None:
            if chunk == "":
                return
            self._record(chunk)

    def wait_for(self, prompt: str, gap_cap: float) -> None:
        """prompt が出力の末尾に戻るまで読む。出力の間の待ちは gap_cap 秒までに縮めて刻む。"""
        last = time.monotonic()
        deadline = last + COMMAND_TIMEOUT_SECONDS
        while not re.search(prompt, plain(self.since_enter)):
            chunk = self._read(0.05)
            now = time.monotonic()
            if chunk == "":
                raise ToolError(f"端末が閉じた。最後の出力:\n{plain(self.since_enter)[-2000:]}")
            if chunk is not None:
                self.clock += min(now - last, gap_cap)  # noqa: DOEFF002 — 録画の状態を持つ 1 つの recorder の時計と入力の欄
                last = now
                self._record(chunk)
            if now > deadline:
                raise ToolError(
                    f"{COMMAND_TIMEOUT_SECONDS} 秒待っても prompt が戻らない。最後の出力:\n"
                    f"{plain(self.since_enter)[-2000:]}"
                )
        self.drain(0.15)

    def type(self, step: Type) -> None:
        """人が打つ速さで命令を打ち、走らせて、答えが出終わるまでを録る。"""
        for char in step.text:
            os.write(self.fd, char.encode())
            self.clock += CHAR_SECONDS * (1 + CHAR_JITTER * (self.random.random() - 0.5))
            self.drain(0.02)
        self.clock += CHAR_SECONDS * 3
        self.since_enter = ""
        os.write(self.fd, b"\r")
        self.wait_for(step.prompt, step.gap_cap)

    def hold(self, step: Hold) -> None:
        """見る人が今の画面を読めるよう、何も打たずに仮の時計だけを進める。"""
        self.drain(0.05)
        self.clock += step.seconds


def make_world(work: Path, python: str) -> Path:
    """GIF に映る file を写し、pip の入った新しい venv を作る。venv の dir を返す。"""
    uv = shutil.which("uv")
    if uv is None:
        raise ToolError("uv が PATH に無い(https://docs.astral.sh/uv/)")
    for demo_file in DEMO_FILES:
        shutil.copy(demo_file, work / demo_file.name)
    venv = work / ".venv"
    made = subprocess.run(
        [uv, "venv", "--quiet", "--seed", "--python", python, str(venv)],
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
    )
    if made.returncode != 0:
        raise ToolError(f"uv venv が落ちた:\n{made.stderr}")
    return venv


def shell_env(work: Path, venv: Path) -> dict[str, str]:
    """録る bash の環境。この機体の環境変数は渡さない。"""
    return {
        "PATH": f"{venv / 'bin'}:/usr/bin:/bin",
        "VIRTUAL_ENV": str(venv),
        "HOME": str(work),
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "PS1": r"\[\e[1;32m\]$\[\e[0m\] ",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_PROGRESS_BAR": "off",
        "PIP_NO_CACHE_DIR": "1",
        "PIP_NO_INPUT": "1",
    }


def record(work: Path, venv: Path) -> tuple[tuple[float, str], ...]:
    """pty の中で台本を演じ、出力の刻の列を返す。"""
    bash = shutil.which("bash", path="/usr/bin:/bin")
    if bash is None:
        raise ToolError("bash が /usr/bin にも /bin にも無い")
    pid, fd = pty.fork()
    if pid == 0:  # 子: 決めた大きさの端末で bash を起こす
        os.chdir(work)
        os.execve(bash, ["bash", "--norc", "--noprofile", "-i"], shell_env(work, venv))
    try:
        ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
        os.kill(pid, signal.SIGWINCH)
        recorder = Recorder(fd)
        recorder.wait_for(SHELL_PROMPT, gap_cap=0.0)
        for step in SCRIPT:
            if isinstance(step, Type):
                recorder.type(step)
            else:
                recorder.hold(step)
        return recorder.events
    finally:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        os.close(fd)


def write_cast(events: tuple[tuple[float, str], ...], path: Path) -> None:
    """録った出力を agg が読む asciicast v2 にする(環境は端末の種類だけを書く)。"""
    header = {
        "version": 2,
        "width": COLUMNS,
        "height": ROWS,
        "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"},
    }
    lines = [json.dumps(header)] + [json.dumps([at, "o", text]) for at, text in events]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render(cast: Path, out: Path, agg: str, font_dirs: list[str]) -> None:
    """asciicast を README に載せる GIF にする。色・字の大きさ・画の数は 1 MB の目安に収める値。"""
    command = (
        agg,
        "--quiet",
        "--theme",
        "github-dark",
        "--font-size",
        "16",
        "--fps-cap",
        "10",
        # 刻は録る時に縮めてあるので、agg では縮めない(台本の Hold を保つ)
        "--idle-time-limit",
        "60",
        "--last-frame-duration",
        str(LAST_FRAME_SECONDS),
        *(arg for font_dir in font_dirs for arg in ("--font-dir", font_dir)),
        str(cast),
        str(out),
    )
    made = subprocess.run(command, capture_output=True, text=True, check=False)
    if made.returncode != 0:
        raise ToolError(f"agg が落ちた:\n{made.stderr}")


def main(argv: list[str]) -> int:
    """録って GIF を書き、大きさと長さを知らせる。終了コードは module の docstring のとおり。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="書く GIF(既定 images/readme-demo.gif)"
    )
    parser.add_argument("--cast", type=Path, help="中間の asciicast を残す先(既定は残さない)")
    parser.add_argument(
        "--python",
        default="3.14",
        help="録る venv の Python の版(既定 3.14 — REPL の prompt に色が付く)",
    )
    parser.add_argument("--agg", default="agg", help="agg の path(既定は PATH から)")
    parser.add_argument(
        "--font-dir", action="append", default=[], help="agg に足す字形の dir(何度でも書ける)"
    )
    args = parser.parse_args(argv)
    agg = shutil.which(args.agg)
    try:
        if agg is None:
            raise ToolError(
                f"agg が見つからない: {args.agg}(https://github.com/asciinema/agg/releases)"
            )
        with tempfile.TemporaryDirectory(prefix="doeff-demo-") as tmp:
            work = Path(tmp)
            venv = make_world(work, args.python)
            events = record(work, venv)
            cast = args.cast or work / "demo.cast"
            write_cast(events, cast)
            render(cast, args.out, agg, args.font_dir)
    except ToolError as error:
        print(f"録れない: {error}", file=sys.stderr)
        return 2
    size = args.out.stat().st_size
    length = events[-1][0] + LAST_FRAME_SECONDS
    print(f"{args.out}: {size / 1_000_000:.2f} MB・約 {length:.1f} 秒")
    if size > SIZE_BUDGET_BYTES:
        print(f"目安の {SIZE_BUDGET_BYTES / 1_000_000:.1f} MB を越えた", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
