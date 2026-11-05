"""Record docs/assets/videos/article-terminal-window.gif: the same fight of examples/effects_tour/spire.py, once at a
real terminal and once in a real window. Nothing on the two panes is drawn by this script: `python spire.py play` runs
in an xterm and `python spire.py window` runs in its tkinter window, both on an Xvfb screen, driven with real keys and
real clicks by xdotool. The script adds only the code lines, the captions and the mouse pointer around the captures,
and it stops if the two programs do not play the fight it worked out with `scripted`.

Needs Xvfb, xterm, xdotool and fonts (apt install xvfb xterm xdotool fonts-dejavu xfonts-100dpi).
Usage (from the repository root):
  uv run --no-project --with doeff==0.8.0 --with pillow python tools/docs-video/record/terminal_window.py
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from doeff import Resume, handler, run
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
TOUR = ROOT / "examples" / "effects_tour"
OUT = ROOT / "docs" / "assets" / "videos" / "article-terminal-window.gif"
sys.dont_write_bytecode = True
sys.path.insert(0, str(TOUR))

import spire  # noqa: E402

SEED = 39
PLAN = ("Strike", "Strike", "Defend", "Bash", "Strike", "Strike", "Strike")  # the hands of this seed allow these cards
TURN_1_MOVES = 3

BG = (17, 20, 26)
BAND = (27, 31, 39)
TEXT = (216, 222, 233)
DIM = (140, 149, 166)
SWAP = (255, 196, 84)
SCREEN = (800, 440)
TERMINAL_ROWS = 12
WINDOW_TOP = 180
HEADER = 30
CAPTION = 34
GAP = 8
STAGE = (SCREEN[0] + 2 * GAP, 2 * HEADER + SCREEN[1] + CAPTION + 2 * GAP)

TERMINAL_LINE = ("run(quiet_stats(", ("printed", True), "(dice(seed)(", ("human", True), "(battle())))))")
WINDOW_LINE = ("run(quiet_stats(", ("drawn", True), "(dice(seed)(", ("clicked", True), "(battle())))))")
UNCHANGED_LINE = ("run(quiet_stats(", ("printed", False), "(dice(seed)(", ("human", False), "(battle())))))")


def the_fight() -> tuple[list[int], list[str], tuple]:
    """Play PLAN without a screen: the hand positions to press, the lines of Show, and the result."""
    plan, moves, lines = list(PLAN), [], []

    @handler
    def follow(effect: spire.Choose, k):
        moves.append(effect.hand.index(plan.pop(0)))
        return (yield Resume(k, moves[-1]))

    @handler
    def keep(effect: spire.Show, k):
        lines.append(effect.text)
        return (yield Resume(k, None))

    result = run(spire.quiet_stats(keep(spire.dice(SEED)(follow(spire.battle())))))
    return moves, lines, result


def font(size: int, bold: bool = False):
    name = "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
    for folder in os.environ.get("RECORD_FONT_DIRS", "/usr/share/fonts/truetype/dejavu").split(os.pathsep):
        if (Path(folder) / name).exists():
            return ImageFont.truetype(str(Path(folder) / name), size)
    raise SystemExit(f"{name} not found (set RECORD_FONT_DIRS)")


class Screen:
    """An Xvfb screen read from its frame-buffer file, and xdotool on it."""

    def __init__(self, work: Path) -> None:
        self.display = ":" + str(70 + os.getpid() % 900)
        self.fb = work / "Xvfb_screen0"
        size = f"{SCREEN[0]}x{SCREEN[1]}x24"
        self.server = subprocess.Popen(
            ["Xvfb", self.display, "-screen", "0", size, "-fbdir", str(work), "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.env = {**os.environ, "DISPLAY": self.display}
        self.children: list[subprocess.Popen] = [self.server]
        deadline = time.time() + 20
        while not self.fb.exists():
            if time.time() > deadline:
                raise SystemExit("Xvfb did not start")
            time.sleep(0.1)
        time.sleep(1)

    def start(self, *command: str, **options) -> subprocess.Popen:
        child = subprocess.Popen(command, env=self.env, **options)
        self.children.append(child)
        return child

    def tool(self, *arguments) -> str:
        done = subprocess.run(["xdotool", *map(str, arguments)], env=self.env, capture_output=True, text=True, check=False)
        return done.stdout.strip()

    def find_window(self, title: str) -> str:
        """The window with this title. xdotool's own `search --name` misses windows on a bare X server."""
        deadline = time.time() + 30
        while True:
            for by in ("--class", "--name"):
                for window in self.tool("search", by, ".").split():
                    if self.tool("getwindowname", window) == title:
                        return window
            if time.time() > deadline:
                raise SystemExit(f"no window titled {title}")
            time.sleep(0.1)

    def grab(self) -> Image.Image:
        data = self.fb.read_bytes()
        header_size, *_ = struct.unpack(">I", data[:4])
        width, height = struct.unpack(">2I", data[16:24])
        line = struct.unpack(">I", data[48:52])[0]
        colors = struct.unpack(">I", data[76:80])[0]
        start = header_size + colors * 12
        return Image.frombuffer("RGB", (width, height), data[start : start + line * height], "raw", "BGRX", line, 1).copy()

    def settle(self, quiet: float = 0.2, limit: float = 8.0) -> None:
        """Wait until the screen has not changed for `quiet` seconds."""
        deadline, last, since = time.time() + limit, None, time.time()
        while time.time() < deadline:
            now = self.fb.read_bytes()
            if now != last:
                last, since = now, time.time()
            elif time.time() - since > quiet:
                return
            time.sleep(0.05)

    def close(self) -> None:
        for child in reversed(self.children):
            child.terminate()
        for child in self.children:
            child.wait(timeout=10)


class Film:
    """The frames of the gif: a capture of the screen, with the code lines, a caption and a pointer around it."""

    def __init__(self, screen: Screen) -> None:
        self.screen = screen
        self.frames: list[tuple[Image.Image, int]] = []
        self.pointer: tuple[int, int] | None = None
        self.terminal_line = TERMINAL_LINE
        self.window_line: tuple | None = None
        self.window_open = False
        self.terminal_command = "python spire.py play 39"
        self.window_command = ""
        self.caption = ""
        self.code = font(14)
        self.code_bold = font(14, True)
        self.words = font(15, True)

    def _header(self, draw: ImageDraw.ImageDraw, y: int, parts: tuple | None, command: str) -> None:
        draw.rectangle((0, y, STAGE[0], y + HEADER - 1), fill=BAND)
        x = GAP + 4
        for part in parts or ():
            text, swapped = (part, False) if isinstance(part, str) else part
            typeface = self.code_bold if swapped else self.code
            draw.text((x, y + 7), text, font=typeface, fill=SWAP if swapped else TEXT)
            x += draw.textlength(text, font=typeface)
        draw.text((STAGE[0] - GAP - 4, y + 7), command, font=self.code, fill=DIM, anchor="ra")

    def snap(self, seconds: float, clicked: bool = False) -> None:
        self.screen.settle()
        capture = self.screen.grab()
        frame = Image.new("RGB", STAGE, BG)
        draw = ImageDraw.Draw(frame)
        self._header(draw, 0, self.terminal_line, self.terminal_command)
        frame.paste(capture.crop((0, 0, SCREEN[0], WINDOW_TOP)), (GAP, HEADER))
        self._header(draw, HEADER + WINDOW_TOP + GAP, self.window_line, self.window_command)
        low = 2 * HEADER + WINDOW_TOP + GAP
        if self.window_open:
            frame.paste(capture.crop((0, WINDOW_TOP, SCREEN[0], SCREEN[1])), (GAP, low))
        if self.pointer is not None:
            x, y = self.pointer[0] + GAP, self.pointer[1] - WINDOW_TOP + low
            if clicked:
                draw.ellipse((x - 16, y - 16, x + 16, y + 16), outline=SWAP, width=3)
            arrow = [(x, y), (x, y + 18), (x + 5, y + 14), (x + 9, y + 22), (x + 12, y + 20), (x + 8, y + 13), (x + 14, y + 13)]
            draw.polygon(arrow, fill=(255, 255, 255), outline=(0, 0, 0))
        draw.text((STAGE[0] / 2, STAGE[1] - CAPTION / 2 - 2), self.caption, font=self.words, fill=TEXT, anchor="mm")
        self.frames.append((frame, int(seconds * 1000)))

    def save(self, path: Path) -> None:
        # One palette for the whole film, taken from a montage of frames, so the flat colours stay the same.
        sample = self.frames[:: max(1, len(self.frames) // 9)][:9]
        montage = Image.new("RGB", (STAGE[0], STAGE[1] * len(sample)))
        for row, (frame, _) in enumerate(sample):
            montage.paste(frame, (0, row * STAGE[1]))
        palette = montage.quantize(colors=96, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        images = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame, _ in self.frames]
        images[0].save(
            path,
            save_all=True,
            append_images=images[1:],
            duration=[seconds for _, seconds in self.frames],
            loop=0,
            optimize=False,
            disposal=1,
        )


def card_centre(move: int) -> tuple[int, int]:
    return 16 + 126 * move + 58, WINDOW_TOP + 130


def type_move(film: Film, move: int, hold: float) -> None:
    film.screen.tool("key", str(move))
    film.snap(0.35)
    film.screen.tool("key", "Return")
    film.snap(hold)


def click_move(film: Film, move: int, hold: float) -> None:
    target = card_centre(move)
    start = film.pointer or (target[0], SCREEN[1] - 10)
    for step in range(1, 6):
        film.pointer = tuple(start[i] + (target[i] - start[i]) * step // 5 for i in range(2))
        film.screen.tool("mousemove", *film.pointer)
        film.snap(0.05)
    film.snap(0.25, clicked=True)
    film.screen.tool("click", 1)
    film.snap(hold)


def record(work: Path) -> None:
    moves, lines, result = the_fight()
    session = work / "session.log"
    window_out = work / "window.out"
    screen = Screen(work)
    film = Film(screen)
    try:
        # `python` in the terminal is this interpreter: the one that has doeff.
        interpreter = Path(sys.executable).parent
        screen.env.update(PATH=f"{interpreter}{os.pathsep}{os.environ['PATH']}", PYTHONDONTWRITEBYTECODE="1")
        shell = ["script", "-qf", str(session), "-c", "PS1='$ ' bash --norc --noprofile"]
        screen.start(
            "xterm", "-fa", "DejaVu Sans Mono", "-fs", "9", "-geometry", f"100x{TERMINAL_ROWS}+0+0", "-b", "0",
            "-bg", "#1b1f27", "-fg", "#d8dee9", "-T", "terminal", "-e", "env", "-u", "LD_LIBRARY_PATH", *shell,
            cwd=TOUR,
        )  # fmt: skip
        terminal = screen.find_window("terminal")
        deadline = time.time() + 30
        while screen.tool("getwindowfocus") != terminal or "$ " not in (session.read_text() if session.exists() else ""):
            if time.time() > deadline:
                raise SystemExit("the terminal did not take the keyboard")
            screen.tool("windowfocus", terminal)
            time.sleep(0.2)
        screen.settle()

        film.caption = "At a terminal: printed answers Show, human answers Choose."
        film.snap(1.2)
        command = film.terminal_command
        for end in range(3, len(command) + 3, 3):
            screen.tool("type", "--delay", "10", command[end - 3 : end])
            film.snap(0.07)
        screen.tool("key", "Return")
        deadline = time.time() + 20
        while "play which?" not in (session.read_text() if session.exists() else ""):
            if time.time() > deadline:
                raise SystemExit("the terminal fight did not start")
            time.sleep(0.1)
        film.snap(1.4)
        for move in moves[:TURN_1_MOVES]:
            type_move(film, move, 0.9)
        film.snap(0.8)

        film.caption = "Now swap two handlers. battle does not change."
        film.window_line = UNCHANGED_LINE
        film.snap(1.3)
        film.window_line = WINDOW_LINE
        film.window_command = "python spire.py window 39"
        film.snap(2.4)

        with window_out.open("w") as out:
            screen.start("python", "spire.py", "window", str(SEED), cwd=TOUR, stdout=out, stderr=subprocess.STDOUT)
            screen.tool("windowmove", screen.find_window("spire"), 0, WINDOW_TOP)
            screen.tool("windowfocus", terminal)
            film.caption = "In a window: drawn answers Show, clicked answers Choose. The same seed."
            film.window_open = True
            film.pointer = (700, SCREEN[1] - 20)
            film.snap(1.8)
            for move in moves[:TURN_1_MOVES]:
                click_move(film, move, 0.9)
            film.snap(1.0)

            film.caption = "The same fight, move by move."
            for move in moves[TURN_1_MOVES:]:
                type_move(film, move, 0.3)
                click_move(film, move, 0.9)
            film.caption = f"The same fight, the same result: {result}. Only two handlers changed."
            film.snap(4.0)
    finally:
        screen.close()

    typed = session.read_text().replace("\r", "")
    position = 0
    for line in lines:
        position = typed.find(line, position)
        if position < 0:
            raise SystemExit(f"the terminal did not print {line!r}:\n{typed}")
    if f"-> {result}" not in typed or f"-> {result}" not in window_out.read_text():
        raise SystemExit(f"a fight did not end with {result}:\n{typed}\n{window_out.read_text()}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    film.save(OUT)
    print(f"{OUT}: {len(film.frames)} frames, {sum(s for _, s in film.frames) / 1000:.1f} s, {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="spire-record-") as place:
        record(Path(place))
