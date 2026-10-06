"""doeff の Rust の package の PEP 517 の build の口 — Rust の部品を組む・引く入口の 1 つ(agora-redesign #1493・#2364・#3860)。

入口は 1 つ(ADR-DOE-BUILD-001): uv sync・uv build・別の repo の path の依存・uv の git の checkout からの build・worker の
`uv build --wheel` のどれもこの口を通る。口は組む前に、必ず「source の中身の hash を鍵にした wheel の保存先」を引く。在れば cargo を
撃たずにそれを使い、無ければ組んで置いてから使う。wheel の hook(build_wheel)も editable の hook(build_editable)も同じ保存先の
同じ wheel から答える。以前の editable は毎回 maturin を一時の target で撃ち、日次の検証で doeff-vm を 1 日 約 100 度組み直していた(#3860)。

editable(_editable_wheel): 保存先の wheel の組み立ての成果物(native の拡張 module と、maturin の include で wheel に入れる組み立ての
出力)を、editable の wheel の中身として venv の site-packages の `__editable__.<名>.native/` に入れる(uv の RECORD が持つ — 消えれば
uv が入れ直し、uninstall でも消える)。source は作業木の maturin の python-source のまま読み、package の探し先は venv の成果物の dir →
作業木の dir の順(wheel に入れる小さな finder `__editable___<名>_finder` と、それを起こす .pth)。成果物を source の木の中に置かない
理由(2026-10-07 04:00 の日次): 木の git の名簿に無い file を消す写し(dotfiles の remote_check)が成果物を消しても、uv の記録は
その有無を知らず、次の uv sync が組み直さず import が落ちた(tests/test_editable_native_survives_tree_prune.py)。成果物の dir を先に
探すので、前の形が source の木に残した成果物は使われない。

鍵(_wheel_slot): 組みに効く物の sha256 — tool.uv.cache-keys の file(宣言が無ければ DEFAULT_SOURCE_GLOBS)の中身と相対 path・rustc と
maturin の版・機体・組む Python の版と ABI・組みを変える環境変数(RUSTFLAGS・CARGO_PROFILE_* など — #2969)・build の設定。file の時刻と
作業木の path には依らない — pin を上げて作業木を作り直しても、中身が同じなら同じ鍵。uv も tool.uv.cache-keys の file を見て口を呼ぶ
ので、この宣言が「uv が組み直しを問う集合」と「保存先の鍵」の 1 つの定義になる。

保存先(_wheel_slot・_store_wheel): `<置き場>/<package>-<鍵>/<wheel>` と使った印 `.used`(使うたびに置き換えて dir の時刻を進める)。
置き場 = env DOEFF_WHEEL_CACHE、無ければ `$XDG_CACHE_HOME/doeff-cargo-wheels`(無ければ ~/.cache の下)。並びは doeff-cluster の worker の
wheel の置き場(state/wheels/<package>-<鍵>/ と .used — shared/core/native_wheel.py)と同じで、日次の検証は worker の /work/state/wheels を
置き場に渡して共有する — worker の掃除(7 日使われない dir を消す)がそのまま効く。保存先の wheel は使う前に RECORD の hash で
確かめ、壊れていれば名指しの 1 行を出してその wheel を除き、組み直す(黙って壊れた物を使わない)。

報告(_write_report): env DOEFF_WHEEL_REPORT が在れば、その file に 1 行の JSON {"project": <package の名>, "wheel": <保存先の中の
wheel の path>, "built": <この呼び出しが組んだか>} を足す。doeff-cluster の worker(実行環境の準備と起動の script)は `uv build --wheel
--out-dir` でこの口を通り、出た wheel を入れる — 自前の鍵と置き場を持たない。報告は組んだかを見るためだけに使う(読み手は
shared/core/native_wheel.py の reported_built・報告を書かない版の口なら組んだかは UNREPORTED・#3860)。

cargo の target: 組む時だけ、package の dir の外の 1 回の build ごとの一時の dir に置き、wheel を作ったら消す(#1493)。1 つの共有の
target にしない理由: cargo は source の file の時刻で新旧を判定し、成果物を workspace の中の相対 path で名付ける。2 つの作業木が 1 つの
target を共有すると、先に作った作業木の source は後の build の成果物より古く見え、別の作業木の build を黙って使う(cargo 1.96.1 で
実測・agora-redesign #1472 の comment)— その wheel が鍵の下に置かれると、同じ鍵の build が全部それを引く。利用者が CARGO_TARGET_DIR を
渡した時はそこに組み、消さない — Rust を直すセッションが差分の build を使うための口(`CARGO_TARGET_DIR=<dir> uv sync`)。cargo では
env の CARGO_TARGET_DIR が config の build.target-dir(と CARGO_BUILD_TARGET_DIR)に勝つので、口の一時の dir はそれらの設定にも勝つ。
一時の dir の名には build の process の pid を入れる(doeff-cargo-target-<pid>-<乱字>)。SIGTERM・SIGKILL で止められた build は後始末を
しないので、次の build が入口で、持ち主の process が居ない dir を片づける。

正本は tools/doeff_cargo_backend.py。各 package の doeff_cargo_backend.py はこの file への symlink(backend-path は package の dir の中
しか指せない — PEP 517)。組み方が maturin だけでない package(doeff-indexer — CLI の binary を wheel に同梱する)は、自分の組み方
(Compile)を wheel_from_store・editable_from_store に渡し、同じ保存先を通る。sdist は引かない(Rust を組まない)。
"""

from __future__ import annotations

import base64
import csv
import fnmatch
import glob
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import tomllib
import zipfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

import maturin

TARGET_ENV = "CARGO_TARGET_DIR"
TEMP_PREFIX = "doeff-cargo-target-"
WHEEL_CACHE_ENV = "DOEFF_WHEEL_CACHE"
# 保存先の中の wheel と組んだかを 1 行の JSON で足す file を指す env(頭の註の報告 — 無ければ書かない)。
WHEEL_REPORT_ENV = "DOEFF_WHEEL_REPORT"
# 保存先の dir の使った印(dir の時刻を進める — worker の掃除は 7 日使われない dir を dir の時刻で選ぶ)。
USED_MARK = ".used"
# tool.uv.cache-keys の file を宣言しない package の、組みに効く file の既定(package の dir からの glob)。
DEFAULT_SOURCE_GLOBS = ("pyproject.toml", "Cargo.toml", "Cargo.lock", "build.rs", "src/**/*")
# wheel の中身を変える環境変数(置き場の鍵に名と値を入れる・agora-redesign #2969): rustc の旗・cargo の profile と build の設定・
# maturin と PyO3 の設定・doeff-indexer の CLI の binary を同梱しない旗(同梱しない wheel を同梱する build が引かないため)。
BUILD_ENV_NAMES = frozenset({"RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC", "MACOSX_DEPLOYMENT_TARGET", "DOEFF_INDEXER_SKIP_CLI_BUILD"})
BUILD_ENV_PREFIXES = ("CARGO_PROFILE_", "CARGO_BUILD_", "MATURIN_", "PYO3_")
# 上の接頭辞に当たるが、組む場所と並べる数だけを変えて中身を変えない名 — 鍵に入れると作業木や機体の混み方ごとに置き場が割れて
# 共有が効かなくなる。
PLACE_ONLY_ENV_NAMES = frozenset({"CARGO_BUILD_TARGET_DIR", "CARGO_BUILD_JOBS"})
# native の拡張 module の file の末尾(editable で venv の成果物の dir へ入れる組み立ての成果物)。
NATIVE_SUFFIXES = (".so", ".pyd", ".dylib")

# PEP 517 の config_settings: frontend が渡す「設定の名 → 文字列か文字列の list」。口は中を読まず maturin へ渡す。
ConfigSettings: TypeAlias = Mapping[str, str | list[str]]
# 保存先に無い時に wheel を組む関数: (この build の cargo の target, wheel を出す dir, build の設定) → 出した wheel の file の名。
Compile: TypeAlias = Callable[[Path, str, "ConfigSettings | None"], str]


@dataclass(frozen=True)
class StoredWheel:
    """保存先の wheel 1 つ: path = 保存先の中の wheel の file・built = この呼び出しが組んだ(保存先に無かったか壊れていた)。"""

    path: Path
    built: bool


@dataclass(frozen=True)
class WheelEntry:
    """editable の wheel に書く file 1 つ: name = wheel の中の path・data = 中身・mode = 入れた file の許可の bit(CLI の binary は実行の bit)。"""

    name: str
    data: bytes
    mode: int


def _owner_pid(name: str) -> int | None:
    """一時の target の名(doeff-cargo-target-<pid>-<乱字>)から、それを作った build の pid を読む。"""
    match name.removeprefix(TEMP_PREFIX).split("-", 1):
        case [pid, _] if pid.isdigit():
            return int(pid)
        case _:
            # この口の名の形ではない — 持ち主を言えないので片づけの対象にしない。
            return None


def _alive(pid: int) -> bool:
    """pid の process がまだ居るか(居なければ、その build は止められて後始末をしていない)。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 別の利用者の process が同じ pid で居る。
        return True
    return True


def _sweep_orphans(root: Path) -> None:
    """止められた build が残した一時の target を消す。持ち主の process が居ない dir だけで、走っている build には触らない。"""
    for path in root.glob(f"{TEMP_PREFIX}*"):
        match _owner_pid(path.name):
            case int(pid) if not _alive(pid):
                # 同時に入口に来た別の build が先に消していれば、消えた file は探さない。
                with suppress(FileNotFoundError):
                    shutil.rmtree(path)
            case _:
                pass


@contextmanager
def cargo_target_dir() -> Iterator[Path]:
    """この build の cargo の target。利用者の指定が無ければ一時の dir を作って env に渡し、抜ける時に消す。"""
    match os.environ.get(TARGET_ENV):
        case (None | "") as absent:
            root = Path(tempfile.gettempdir())
            _sweep_orphans(root)
            made = Path(tempfile.mkdtemp(prefix=f"{TEMP_PREFIX}{os.getpid()}-", dir=root))
            os.environ[TARGET_ENV] = str(made)
            try:
                yield made
            finally:
                match absent:
                    case None:
                        del os.environ[TARGET_ENV]
                    case str():
                        os.environ[TARGET_ENV] = absent
                shutil.rmtree(made)
        case str() as given:
            yield Path(given)


def _maturin_wheel(target: Path, wheel_directory: str, config_settings: ConfigSettings | None) -> str:
    """maturin だけで組む package の組み方(target は cargo_target_dir が env に渡してある)。"""
    return maturin.build_wheel(wheel_directory, config_settings, None)


def stored_wheel(package: Path, config_settings: ConfigSettings | None, compile_wheel: Compile) -> StoredWheel:
    """package の source の鍵の wheel を保存先から引き、無ければ(壊れていれば)組んで置くため — Rust の部品を組む・引く入口の本体
    (頭の註)。答え = 保存先の中の wheel。stderr に 1 行(使った / 組んだ秒)。"""
    slot = _wheel_slot(package, config_settings)
    match _intact_wheel(slot):
        case Path() as found:
            _mark_used(slot)
            print(f"doeff_cargo_backend: 同じ source の wheel を使う(組まない)— {found.name}・{slot}", file=sys.stderr)
            return _write_report(package, StoredWheel(path=found, built=False))
        case None:
            pass
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="doeff-wheel-out-") as out:
        with cargo_target_dir() as target:
            name = compile_wheel(target, out, config_settings)
        stored = _store_wheel(slot, Path(out) / name)
    print(f"doeff_cargo_backend: wheel を組んだ({time.monotonic() - started:.0f} 秒)— {name}・{slot} に置いた", file=sys.stderr)
    return _write_report(package, StoredWheel(path=stored, built=True))


def _write_report(package: Path, stored: StoredWheel) -> StoredWheel:
    """env DOEFF_WHEEL_REPORT の file に、保存先の中の wheel と組んだかを 1 行の JSON で足すため(頭の註の報告 — env が無ければ何も
    しない)。答え = stored(呼び手がそのまま返す)。"""
    match os.environ.get(WHEEL_REPORT_ENV):
        case str() as report if report:
            match _pyproject(package).get("project"):
                case {"name": str() as project}:
                    pass
                case _:
                    project = "unnamed"
            line = json.dumps({"project": project, "wheel": str(stored.path), "built": stored.built}, ensure_ascii=False)
            with open(report, "a", encoding="utf-8") as out:
                out.write(line + "\n")
        case _:
            pass
    return stored


def wheel_from_store(wheel_directory: str, config_settings: ConfigSettings | None, compile_wheel: Compile) -> str:
    """wheel の hook の本体: cwd の package の保存先の wheel を wheel_directory へ写し、その名を返すため。"""
    stored = stored_wheel(Path.cwd(), config_settings, compile_wheel)
    shutil.copy2(stored.path, Path(wheel_directory) / stored.path.name)
    return stored.path.name


def editable_from_store(wheel_directory: str, config_settings: ConfigSettings | None, compile_wheel: Compile) -> str:
    """editable の hook の本体: cwd の package の保存先の wheel から editable の wheel を作り、その名を返すため(頭の註)。
    maturin の python-source を持たない package(bin の binary・Python の source を持たない拡張)は、保存先の wheel そのものを渡す
    (作業木を指す物が無いので、editable も wheel と同じ中身)。"""
    package = Path.cwd()
    stored = stored_wheel(package, config_settings, compile_wheel)
    config = _maturin_config(package)
    match config.get("python-source"):
        case str() as source:
            return _editable_wheel(stored.path, package, (package / source).resolve(), _wheel_includes(config), Path(wheel_directory))
        case _:
            shutil.copy2(stored.path, Path(wheel_directory) / stored.path.name)
            return stored.path.name


def build_wheel(
    wheel_directory: str,
    config_settings: ConfigSettings | None = None,
    metadata_directory: str | None = None,
) -> str:
    """uv sync・uv build が wheel を求めた時に、保存先の同じ source の wheel を渡すため(無ければ組んで置く — 頭の註)。"""
    return wheel_from_store(wheel_directory, config_settings, _maturin_wheel)


def build_editable(
    wheel_directory: str,
    config_settings: ConfigSettings | None = None,
    metadata_directory: str | None = None,
) -> str:
    """uv の workspace の一員・editable の path の依存を入れる時も、保存先の wheel から答えるため(頭の註)。"""
    return editable_from_store(wheel_directory, config_settings, _maturin_wheel)


def _source_files(package: Path) -> list[Path]:
    """組みに効く file(package の tool.uv.cache-keys の file の glob か既定 — uv が組み直しを判じる集合と同じ)を相対 path の順で並べる。"""
    declared = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8")).get("tool", {}).get("uv", {}).get("cache-keys", [])
    globs = [key["file"] for key in declared if isinstance(key, dict) and isinstance(key.get("file"), str)] or list(DEFAULT_SOURCE_GLOBS)
    matched = {Path(hit) for pattern in globs for hit in glob.glob(str(package / pattern), recursive=True)}
    return sorted((path for path in matched if path.is_file()), key=lambda path: os.path.relpath(path, package))


def _tool_versions() -> str:
    """組みに効く道具の版(rustc と maturin)— 道具が替われば同じ source でも wheel を組み直すため。"""
    rustc = subprocess.run(["rustc", "-V"], capture_output=True, text=True, check=False).stdout.strip()
    return f"rustc={rustc} maturin={getattr(maturin, '__version__', '?')}"


def _build_environment() -> str:
    """組みを変える環境変数(BUILD_ENV_NAMES と BUILD_ENV_PREFIXES に当たる名、置き場だけの名を除く)の名と値を並べるため
    (agora-redesign #2969 — 同じ source でも RUSTFLAGS・CARGO_PROFILE_RELEASE_STRIP などを変えて組んだ wheel を、既定の build が
    引かないように)。値は鍵の hash に入るだけで、どこにも出さない。"""
    chosen = sorted(
        (name, value)
        for name, value in os.environ.items()
        if (name in BUILD_ENV_NAMES or name.startswith(BUILD_ENV_PREFIXES)) and name not in PLACE_ONLY_ENV_NAMES
    )
    return repr(chosen)


def _python_abi() -> str:
    """組む Python の版・ABI・機体(拡張 module の名の末尾 — 例 `.cpython-314t-x86_64-linux-gnu.so`)— free-threading の cp314t と
    cp313 のように ABI の違う環境で同じ source を組んでも、置き場の wheel を取り違えないため(agora-redesign #2969)。"""
    return f"{sys.implementation.cache_tag} {sysconfig.get_config_var('EXT_SUFFIX')}"


def _pyproject(package: Path) -> dict[str, object]:
    """package の pyproject.toml を読むため。"""
    return tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))


def _maturin_config(package: Path) -> dict[str, object]:
    """package の [tool.maturin] の表を読むため(無ければ空)。"""
    match _pyproject(package).get("tool"):
        case {"maturin": dict() as config}:
            return config
        case _:
            return {}


def _wheel_slot(package: Path, config_settings: ConfigSettings | None) -> Path:
    """source の中身・道具の版・機体・組む Python の ABI・組みを変える環境変数・build の設定の hash で、wheel を置く dir を決めるため
    (file の時刻と作業木の path に依らない — 置き場だけを変える CARGO_TARGET_DIR なども入れない)。dir の名は <package>-<鍵>
    (作業木の dir の名は作業木ごとに違うので使わない・worker の wheel の置き場と同じ並び — 頭の註)。"""
    digest = hashlib.sha256()
    lines = (_tool_versions(), sys.platform, platform.machine(), _python_abi(), _build_environment(), repr(sorted((config_settings or {}).items())))
    for line in lines:
        digest.update(line.encode("utf-8") + b"\0")
    for path in _source_files(package):
        digest.update(os.path.relpath(path, package).encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    base = os.environ.get(WHEEL_CACHE_ENV) or str(Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "doeff-cargo-wheels")
    match _pyproject(package).get("project"):
        case {"name": str() as project}:
            pass
        case _:
            project = "unnamed"
    return Path(base) / f"{project}-{digest.hexdigest()[:32]}"


def _wheel_problem(wheel: Path) -> str | None:
    """保存先の wheel が壊れているか(zip として読めない・RECORD が無い・RECORD の file が無いか hash が違う)を 1 行で言うため
    (None = 壊れていない)。"""
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            records = sorted(name for name in names if name.endswith(".dist-info/RECORD"))
            match records:
                case [record]:
                    rows = list(csv.reader(io.StringIO(archive.read(record).decode("utf-8"))))
                case _:
                    return f"RECORD が {len(records)} 個(1 個のはず)"
            for row in rows:
                match row:
                    case [name, str() as hashed, _] if hashed:
                        if name not in names:
                            return f"RECORD の {name} が wheel に無い"
                        algorithm, _, expected = hashed.partition("=")
                        if _urlsafe_digest(algorithm, archive.read(name)) != expected:
                            return f"{name} の hash が RECORD と違う"
                    case _:
                        pass
    except (zipfile.BadZipFile, OSError, KeyError, ValueError, UnicodeDecodeError) as error:
        return f"wheel として読めない: {error}"
    return None


def _urlsafe_digest(algorithm: str, data: bytes) -> str:
    """wheel の RECORD の hash の綴り(urlsafe base64・末尾の = なし)。"""
    return base64.urlsafe_b64encode(hashlib.new(algorithm, data).digest()).rstrip(b"=").decode("ascii")


def _intact_wheel(slot: Path) -> Path | None:
    """保存先の dir の壊れていない wheel(名の順の先頭)を返すため。壊れた wheel は名指しの 1 行を出して除く(この口が置いた file
    だけ — 置き場の dir の中の *.whl)。無ければ None(組む)。"""
    for wheel in sorted(slot.glob("*.whl")):
        match _wheel_problem(wheel):
            case None:
                return wheel
            case str() as problem:
                print(f"doeff_cargo_backend: 保存先の wheel が壊れている({problem})— {wheel} を除いて組み直す", file=sys.stderr)
                wheel.unlink(missing_ok=True)
    return None


def _mark_used(slot: Path) -> None:
    """使った印を別名に書いてから置き換え、dir の時刻を進めるため(掃除が使っている wheel を消さない — worker の印と同じ置き方)。"""
    handle, staged = tempfile.mkstemp(dir=slot, prefix=".used.")
    os.close(handle)
    os.replace(staged, slot / USED_MARK)


def _store_wheel(slot: Path, built: Path) -> Path:
    """組んだ wheel を保存先へ写し、置いた path を返すため(一時の名で書いてから名を替える — 同じ鍵を同時に組んだ別の build と
    混ざらず、書きかけを読ませない)。"""
    slot.mkdir(parents=True, exist_ok=True)
    staged = slot / f".{built.name}.{os.getpid()}.part"
    shutil.copy2(built, staged)
    placed = slot / built.name
    os.replace(staged, placed)
    _mark_used(slot)
    return placed


def _wheel_includes(config: Mapping[str, object]) -> tuple[str, ...]:
    """[tool.maturin] の include のうち wheel に入る物の glob(package の dir から)— 組み立ての出力を wheel に同梱する宣言
    (doeff-indexer の CLI の binary)。editable はこれも venv の成果物の dir へ入れる。"""
    match config.get("include"):
        case list() as entries:
            return tuple(
                pattern
                for entry in entries
                for pattern in (
                    [entry]
                    if isinstance(entry, str)
                    else [entry["path"]]
                    if isinstance(entry, dict) and isinstance(entry.get("path"), str) and entry.get("format", "all") in ("wheel", "all")
                    else []
                )
            )
        case _:
            return ()


def _built_member(member: str, package: Path, source_root: Path, includes: tuple[str, ...]) -> bool:
    """wheel の file 1 つが組み立ての成果物(native の拡張 module か、include で wheel に入れた物)か — editable が venv の成果物の dir へ
    入れる物。dist-info・data の下と、source そのもの(.py など)は入れない(作業木の source が正しい)。"""
    if member.endswith("/") or member.split("/", 1)[0].endswith((".dist-info", ".data")):
        return False
    relative = os.path.relpath(source_root / member, package)
    return member.endswith(NATIVE_SUFFIXES) or any(fnmatch.fnmatch(relative, pattern) for pattern in includes)


# editable の wheel に入れる finder の module の中身({packages} = 答える package の名の組・{native} = 成果物の dir の名・{pth} = 作業木の
# source の根を 1 行目に持つ同じ wheel の .pth の名 — source の根は finder に書かず .pth から読む)。
# package の探し先を venv の成果物の dir → 作業木の source の dir の順にする(成果物の dir を先に探すので、前の形が source の木に残した
# 成果物は使われない)。
EDITABLE_FINDER = '''\
"""editable の入れの finder — doeff の build の口(tools/doeff_cargo_backend.py)が書く(agora-redesign #3860)。"""
import importlib.util
import os
import sys

SITE = os.path.dirname(os.path.abspath(__file__))
NATIVE = os.path.join(SITE, {native!r})
PACKAGES = {packages!r}
# 作業木の source の根は、同じ wheel の .pth の 1 行目だけが持つ(定義元を 1 つにする — .pth の行を別の checkout へ書き換える向け直しに
# finder も従う)。
with open(os.path.join(SITE, {pth!r}), encoding="utf-8") as _pth:
    SOURCE = _pth.readline().strip()


class EditableNativeFinder:
    """PACKAGES の package を、探し先 = 成果物の dir → 作業木の source の dir の package として答える。"""

    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        if fullname not in PACKAGES:
            return None
        source = os.path.join(SOURCE, fullname)
        return importlib.util.spec_from_file_location(
            fullname, os.path.join(source, "__init__.py"), submodule_search_locations=[os.path.join(NATIVE, fullname), source]
        )


sys.meta_path.insert(0, EditableNativeFinder)
'''


def _editable_wheel(stored: Path, package: Path, source_root: Path, includes: tuple[str, ...], wheel_directory: Path) -> str:
    """保存先の wheel から editable の wheel を作るため(頭の註の editable): 組み立ての成果物を `__editable__.<名>.native/` の下に、
    dist-info(RECORD を除く)と、source_root を指し finder を起こす .pth と、finder の module を入れた wheel を wheel_directory に書く。
    答え = 書いた wheel の名(保存先の wheel と同じ名 — 同じ tag)。"""
    with zipfile.ZipFile(stored) as archive:
        (dist_info,) = {name.split("/", 1)[0] for name in archive.namelist() if name.split("/", 1)[0].endswith(".dist-info")}
        distribution = dist_info.split("-", 1)[0]
        native = f"__editable__.{distribution}.native"
        products = tuple(
            WheelEntry(name=f"{native}/{info.filename}", data=archive.read(info), mode=(info.external_attr >> 16) & 0o777)
            for info in archive.infolist()
            if _built_member(info.filename, package, source_root, includes)
        )
        metadata = tuple(
            WheelEntry(name=name, data=archive.read(name), mode=0o644)
            for name in sorted(archive.namelist())
            if name.startswith(f"{dist_info}/") and not name.endswith("/") and name != f"{dist_info}/RECORD"
        )
    tops = sorted({entry.name.split("/")[1] for entry in products if entry.name.count("/") >= 2})
    packages = tuple(top for top in tops if (source_root / top / "__init__.py").is_file())
    finder = f"__editable___{distribution}_finder"
    loose = any(entry.name.count("/") == 1 for entry in products)  # package の外の成果物(根の拡張 module)は成果物の dir を路に足す
    pth = "".join((f"{source_root}\n", f"{native}\n" if loose else "", f"import {finder}\n" if packages else ""))
    entries = (
        *metadata,
        *products,
        WheelEntry(name=f"{finder}.py", data=EDITABLE_FINDER.format(native=native, packages=packages, pth=f"{distribution}.pth").encode("utf-8"), mode=0o644),
        WheelEntry(name=f"{distribution}.pth", data=pth.encode("utf-8"), mode=0o644),
    )
    text = io.StringIO()
    csv.writer(text, lineterminator="\n").writerows(
        (*((entry.name, f"sha256={_urlsafe_digest('sha256', entry.data)}", str(len(entry.data))) for entry in entries), (f"{dist_info}/RECORD", "", ""))
    )
    with zipfile.ZipFile(wheel_directory / stored.name, "w", compression=zipfile.ZIP_DEFLATED) as editable:
        for entry in entries:
            info = zipfile.ZipInfo(entry.name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (0o100000 | (entry.mode or 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            editable.writestr(info, entry.data)
        editable.writestr(f"{dist_info}/RECORD", text.getvalue())
    return stored.name


def build_sdist(sdist_directory: str, config_settings: ConfigSettings | None = None) -> str:
    """sdist を組む時も、maturin が cargo に作らせる target を作業木の外に置くため(Rust は組まないので保存先は引かない)。"""
    with cargo_target_dir():
        return maturin.build_sdist(sdist_directory, config_settings)


get_requires_for_build_wheel = maturin.get_requires_for_build_wheel
get_requires_for_build_editable = maturin.get_requires_for_build_editable
get_requires_for_build_sdist = maturin.get_requires_for_build_sdist
prepare_metadata_for_build_wheel = maturin.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = maturin.prepare_metadata_for_build_editable
