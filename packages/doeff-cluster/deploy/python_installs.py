"""uv の入れた Python の置き場(UV_PYTHON_INSTALL_DIR)で、同じ minor の新しい patch の版に追い越された古い版の dir を消し、その名を新しい版への
link に置き換える道具 — deploy/boot.sh が起動のたびに背後で呼ぶ($WORK_DIR/state/python)。uv の置き場の形だけを読むので、ほかの
uv の置き場(uv の既定の ~/.local/share/uv/python ほか)の書き手も、入れた後に同じ道具を呼べる。

訳: uv は Python の patch の版ごとに dir(例 cpython-3.14.7+freethreaded-linux-x86_64-gnu・1 本 約 0.1 GB)を作り、版が上がっても前の版の
dir を消さない。2026-11-08 の zeus の state/python には 9 本・約 1 GB、atlas には 4 本が並んでいた。日や数で消す決まりにはできない —
venv(pyvenv.cfg の home と bin/python の link)は patch の版の dir を直に指す事が多く(11-08 の zeus の boot の root と共有 checkout の
venv 117 本のうち 112 本が patch の dir を指す・85 本は追い越された 3.14.3t)、venv の置き場は作業木・job の root・uv の cache の環境ほか
数え切れない(/work の全体を探すと 110 秒でも終わらない)。消すだけでは、数え漏らした venv が壊れる。

決まり: 置き場の名 <実装>-<major>.<minor>.<patch>[<pre>][+<変種>]-<platform> を、実装・major・minor・変種・platform の組ごとに並べ、組の中で
版の最も新しい dir(本物の dir — link でない)を残す。組の中のそれより古い版は:
  dir      置き場の .temp の下へ rename して脇に置き、同じ名で残す版への相対の link を置き、錠を離してから脇の中身を消す。古い版を指す
           venv は、同じ minor の新しい patch の版で動き続ける(uv が minor の link — cpython-3.14-… — で venv に与える patch の上げと同じ
           扱い。patch の版の間で ABI は変わらない)。uv は link の名を入れた版と数えない(`uv python list` に出ない)。
  link     この道具が前に置いた link。残す版でない先を指していれば、残す版へ付け替える。
  使用中    この名前空間の走っている process の作業 dir・実行 file・引数の path・読み込んだ file が dir の下に在る — 消さない(走っている
           Python が後から読む標準の library を、別の patch の版に替えない)。/proc を読めない機体では何も消さない。
minor の link(cpython-3.14-…・cpython-3.14+freethreaded-…)と、名の形に合わない項目は触らない。

錠: uv が install の間に持つ置き場の錠(<置き場>/.lock の flock)を持ったまま判じて rename する — 並んで走る `uv python install` が
入れている途中の版を消さない(uv が入れ終えるまで待つ — 呼び手が背後で起こす)。前の回が消し終える前に止まった脇の dir
(.temp/doeff-retired-*)も消す。

動詞(答えは標準出力の JSON 1 行・訳は標準エラーの行):
  gc     上の決まりで置き換える。
  count  gc と同じ判じだけをする(何も変えない)。

標準の library だけを使う。process の path の読みは uv の cache の片付け(同じ dir の uv_cache.py の process_paths・used_under)と同じ物を使う。
"""

import argparse
import contextlib
import fcntl
import json
import os
import re
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uv_cache import process_paths, used_under  # noqa: E402

INSTALL_LOCK = ".lock"
TEMP = ".temp"
RETIRED_PREFIX = "doeff-retired-"
LINK_PREFIX = "doeff-link-"
# uv の置き場の版の dir の名(uv の PythonInstallationKey の綴り — 例 cpython-3.14.7+freethreaded-linux-x86_64-gnu・cpython-3.15.0rc2-linux-x86_64-gnu)。
NAME = re.compile(
    r"(?P<impl>[a-z]+)-(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:(?P<pre>a|b|rc)(?P<pre_n>\d+))?(?P<rest>(?:\+[a-z]+)?-.+)"
)
PRE_RANK = {"a": 0, "b": 1, "rc": 2, None: 3}


def say(line: str) -> None:
    print(f"python-installs: {line}", file=sys.stderr, flush=True)


@dataclass(frozen=True)
class Group:
    """版を比べる組: 実装・major・minor・変種と platform の綴り(例 +freethreaded-linux-x86_64-gnu)。"""

    impl: str
    major: int
    minor: int
    rest: str


@dataclass(frozen=True, order=True)
class Version:
    """組の中の版の順: patch・pre の段(a < b < rc < 正式)・pre の番号。"""

    patch: int
    pre_rank: int
    pre_number: int


@dataclass(frozen=True)
class Install:
    """置き場の項目 1 つ: link = 名が link(前の回がこの道具で置いた物)。"""

    path: Path
    group: Group
    version: Version
    link: bool


def install_of(path: Path) -> Install | None:
    """置き場の項目 1 つを版として読む(名の形に合わない・link でも dir でもない物は None)。"""
    found = NAME.fullmatch(path.name)
    if found is None:
        return None
    link = path.is_symlink()
    if not link and not path.is_dir():
        return None
    return Install(
        path=path,
        group=Group(found["impl"], int(found["major"]), int(found["minor"]), found["rest"]),
        version=Version(int(found["patch"]), PRE_RANK[found["pre"]], int(found["pre_n"] or 0)),
        link=link,
    )


def installs_of(place: Path) -> list[Install]:
    try:
        entries = sorted(place.iterdir())
    except FileNotFoundError:
        return []
    return [found for found in map(install_of, entries) if found is not None]


@contextlib.contextmanager
def install_lock(place: Path) -> Iterator[None]:
    """uv の置き場の錠(<置き場>/.lock の flock — uv が install の間に持つ物)を持つ(uv が入れ終えるまで待つ)。"""
    fd = os.open(place / INSTALL_LOCK, os.O_WRONLY | os.O_CREAT, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def replace_superseded(place: Path, proc: Path, *, remove: bool) -> dict[str, object]:
    """組ごとに最も新しい版の dir を残し、古い版の dir を脇へ rename して残す版への link に置き換える(remove の時 — 置き場の錠を持って
    呼ぶ)。答え = 名ごとの判じ。"""
    installs = installs_of(place)
    paths = process_paths(proc)
    dirs = [found for found in installs if not found.link]
    newest = {
        group: max(
            (found for found in dirs if found.group == group), key=lambda found: found.version
        )
        for group in {found.group for found in dirs}
    }
    verdicts = [(found, _verdict(found, newest.get(found.group), paths)) for found in installs]
    if remove:
        for found, verdict in verdicts:
            if verdict in ("retired", "relinked"):
                _replace(place, found, newest[found.group])
    return {
        "installs": len(installs),
        "kept": sorted(found.path.name for found in newest.values()),
        **{
            verdict: sorted(found.path.name for found, got in verdicts if got == verdict)
            for verdict in ("retired", "relinked", "linked", "used")
        },
        "proc": paths is not None,
    }


def _verdict(found: Install, kept: Install | None, paths: list[str] | None) -> str | None:
    """項目 1 つの判じ(頭の註の決まり): None = 触らない(残す版・組に本物の dir が無い・新しい link・/proc を読めない)・linked = 残す版を
    指す link・relinked = ほかを指す link・used = 使用中の dir・retired = 置き換える dir。"""
    if kept is None or found.version >= kept.version or paths is None:
        return None
    if found.link:
        return "linked" if os.readlink(found.path) == kept.path.name else "relinked"
    if used_under(paths, found.path) or used_under(paths, found.path.resolve()):
        return "used"
    return "retired"


def _replace(place: Path, found: Install, kept: Install) -> None:
    """古い版の dir を .temp の下へ脇に置き(link は脇に置かない)、同じ名で残す版への link を置く。"""
    if not found.link:
        (place / TEMP).mkdir(exist_ok=True)
        found.path.rename(place / TEMP / f"{RETIRED_PREFIX}{found.path.name}.{os.getpid()}")
    _place_link(place, found.path.name, kept.path.name)


def _place_link(place: Path, name: str, target: str) -> None:
    """place/name に、同じ dir の target への相対の link を置く(.temp で作って rename — 名が無い間を作らない)。"""
    (place / TEMP).mkdir(exist_ok=True)
    staged = place / TEMP / f"{LINK_PREFIX}{name}.{os.getpid()}"
    with contextlib.suppress(FileNotFoundError):
        staged.unlink()
    staged.symlink_to(target)
    os.replace(staged, place / name)


def leftovers(place: Path) -> list[Path]:
    with contextlib.suppress(FileNotFoundError):
        return sorted(p for p in (place / TEMP).iterdir() if p.name.startswith(RETIRED_PREFIX))
    return []


def gc(place: Path, proc: Path, *, remove: bool) -> dict[str, object]:
    """判じて(remove なら)置き換え、脇の dir を消す。答えに file system の空きの前と後を載せる。置き場が無ければ何もしない。"""
    if not place.is_dir():
        say(f"{place} が無い — 何もしない")
        return {
            "installs": 0,
            "kept": [],
            "retired": [],
            "relinked": [],
            "linked": [],
            "used": [],
            "proc": True,
        }
    before = shutil.disk_usage(place).free
    with install_lock(place):
        result = replace_superseded(place, proc, remove=remove)
    if remove:
        for gone in leftovers(place):
            shutil.rmtree(gone, ignore_errors=True)
    if result["proc"] is False:
        say("/proc を読めない — 使っているかを判じられないので、何も変えない")
    result["freeBefore"] = before
    result["freeAfter"] = shutil.disk_usage(place).free
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python_installs.py")
    parser.add_argument("verb", choices=("gc", "count"))
    parser.add_argument(
        "--dir", type=Path, required=True, help="uv の Python の置き場(UV_PYTHON_INSTALL_DIR)"
    )
    parser.add_argument("--proc", type=Path, default=Path("/proc"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = gc(args.dir, args.proc, remove=args.verb == "gc")
    say(
        f"{args.dir}: 版 {result['installs']} 項目 — 残す {len(result['kept'])}・使用中 {len(result['used'])}・"
        f"{'link に置き換えた' if args.verb == 'gc' else '置き換えられる'} {len(result['retired'])}・"
        f"付け替え {len(result['relinked'])}"
    )
    print(json.dumps({"verb": args.verb, **result}, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
