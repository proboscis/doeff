# The anti-patterns scene (docs/12-patterns.md, "Anti-Patterns"). Three runs:
#   1. bad_program calls time.sleep(5) and good_program yields Delay(5), both under sim_time_handler: the sleep holds
#      the wall clock for 5 s behind the handler's back, the Delay moves the virtual clock by 5 s at once.
#   2. The repository's semgrep rule doeff-no-sleep-in-programs on a copy of bad_program: it reports the sleep.
#   3. good_side_effects yields a WriteFile effect: one handler writes the file, another keeps the write as a record;
#      the program is the same under both.
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from doeff_core_effects import scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from doeff import EffectBase, Resume, do, handler, run

REPO = Path(__file__).resolve().parents[6]
START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)

BAD_PROGRAM = '''import time

from doeff import do


@do
def bad_program():
    # DON'T: blocking call
    time.sleep(5)  # Blocks entire runtime
    return "done"
'''


@do
def bad_program():
    # DON'T: blocking call
    time.sleep(5)  # Blocks entire runtime
    return "done"


@do
def good_program():
    # DO: yield the time effect; the installed time handler does the waiting
    yield Delay(5)
    return "done"


@do
def timed(program):
    """The program's answer and how far the handler's clock moved while it ran."""
    before = yield GetTime()
    value = yield program
    after = yield GetTime()
    return value, (after - before).total_seconds()


for program in [bad_program, good_program]:
    started = time.perf_counter()
    value, moved = run(scheduled(sim_time_handler(start_time=START)(timed(program()))))
    wall = time.perf_counter() - started
    print(f"{program.__name__} under sim_time_handler: {value!r}, virtual clock +{moved:.0f} s, wall clock {wall:.3f} s")


# The rule only looks at programs under the repository's source and example directories, so the copy sits under an
# examples/ directory of its own.
with tempfile.TemporaryDirectory() as root:
    (Path(root) / "examples").mkdir()
    (Path(root) / "examples" / "bad_program.py").write_text(BAD_PROGRAM)
    scan = subprocess.run(
        ["semgrep", "--config", str(REPO / ".semgrep.yaml"), "--metrics=off", "--quiet", "--json", "examples/bad_program.py"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    results = json.loads(scan.stdout)["results"]
    found = [(r["check_id"].rsplit(".", 1)[-1], r["start"]["line"], r["extra"]["severity"]) for r in results]
    print(f"semgrep on bad_program: exit {scan.returncode}, findings {found}")
    print("  message, first lines:")
    print("\n".join(f"    {line}" for line in results[0]["extra"]["message"].splitlines()[:3]))


@dataclass(frozen=True)
class WriteFile(EffectBase):
    path: str
    data: str


@do
def good_side_effects():
    # Do: model the side effect explicitly
    yield WriteFile(path="file.txt", data="data")
    return "done"


def to_disk(directory):
    """The handler that gives WriteFile its everyday meaning: the bytes go to a file under directory."""

    @handler
    def write_to_disk(effect: WriteFile, k):
        (Path(directory) / effect.path).write_text(effect.data)
        return (yield Resume(k, None))

    return write_to_disk


def to_record(writes):
    """The handler a test installs: each write is kept in the writes dict, and no file is touched."""

    @handler
    def record_writes(effect: WriteFile, k):
        writes[effect.path] = effect.data
        return (yield Resume(k, None))

    return record_writes


with tempfile.TemporaryDirectory() as directory:
    value = run(to_disk(directory)(good_side_effects()))
    print(f"good_side_effects under write_to_disk: {value!r}, files {sorted(p.name for p in Path(directory).iterdir())}")
    writes = {}
    value = run(to_record(writes)(good_side_effects()))
    print(f"good_side_effects under record_writes: {value!r}, writes {writes}, cwd has file.txt: {Path('file.txt').exists()}")
