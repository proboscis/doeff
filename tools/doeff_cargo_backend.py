"""PEP 517 build entry for doeff's Rust packages -- one entry point for building and fetching Rust components.

There is a single entry (ADR-DOE-BUILD-001): uv sync, uv build, path dependencies from other repos, builds from a uv git checkout, and the worker's
`uv build --wheel` all go through it. Before building, the entry always looks up the "wheel store keyed by a hash of the source contents". If a wheel is
there, it is used without invoking cargo; otherwise the wheel is built, stored, and then used. The wheel hook (build_wheel) and the editable hook
(build_editable) both answer from the same wheel in the same store. The earlier editable hook ran maturin in a temporary target every time, rebuilding doeff-vm about 100 times a day in the daily verification (#3860).

editable (_editable_wheel): the build products of the stored wheel (the native extension module, and the build output that maturin's include puts in the wheel)
go into the venv's site-packages under `__editable__.<name>.native/` as the contents of the editable wheel (uv's RECORD owns them -- if they disappear,
uv reinstalls them, and uninstall removes them). Sources are still read from the working tree's maturin python-source, and the package search order is the venv's products dir ->
the working tree's dir (a small finder `__editable___<name>_finder` shipped in the wheel, plus the .pth that activates it). Why products are not placed inside the source tree
(daily run of 2026-10-07 04:00): a copy that deletes files absent from the tree's git listing (dotfiles' remote_check) removed the products, but uv's record
did not know whether they existed, so the next uv sync did not rebuild and imports failed (tests/test_editable_native_survives_tree_prune.py). The products dir is
searched first, so products that the earlier layout left in the source tree are never used.

wheel (_source_wheel): for packages with a maturin python-source, the wheel hook also takes only the build products, data, and dist-info from the stored wheel,
installs the python-source package files from the working tree, and rewrites RECORD (the same split as editable -- ADR-DOE-BUILD-001 R2b).
Reason: the key looks only at the files declared in tool.uv.cache-keys, so for a package that does not list python-source in its declaration
(doeff-effect-analyzer -- no declaration, default glob), a wheel of an earlier version with the same Rust was fetched with the old Python, and an
analyzer installed as a git dependency claimed the new version while carrying the old Python. The working tree's __pycache__ and .pyc files, and products that an earlier layout left in the tree, are not installed. A package
without python-source gets the stored wheel as is.

key (_wheel_slot): a sha256 of everything that affects the build -- the contents and relative paths of the tool.uv.cache-keys files (DEFAULT_SOURCE_GLOBS if undeclared), the maturin
version, the machine, the libc name and version (the wheels in the store are built with CI's glibc, so a machine with an older glibc must not take one and fail at import -- ADR-DOE-BUILD-001
R4 addendum 2), the Python version and ABI used to build, environment variables that change the build (RUSTFLAGS, CARGO_PROFILE_*, etc. -- #2969), and the build settings. It does not depend on file mtimes or the working
tree path -- even if the pin is bumped and the working tree is recreated, the same contents give the same key. uv also looks at the tool.uv.cache-keys files and then calls the entry, so this
declaration is the single definition of both "the set on which uv asks for a rebuild" and "the store key". The key is built without spawning child processes (ADR-DOE-BUILD-001 R4 addendum --
the rustc version is not part of the key. The key is needed before the store lookup, so a design that spawned `rustc -V` made an environment without rustc on PATH fail at key creation even though the store
held a wheel for the same source -- slot sync on zeus, 2026-10-07. What determines a wheel's contents is the source, the Python
ABI, the machine, the environment variables that change the build, and the build settings; the ABI exposed to Python is the C-API, so the rustc version does not leak outside the wheel).

store (_wheel_slot, _store_wheel): `<store>/<package>-<key>/<wheel>` plus a used marker `.used` (replaced on every use to advance the dir's mtime).
The store = env DOEFF_WHEEL_CACHE, else `$XDG_CACHE_HOME/doeff-cargo-wheels` (else under ~/.cache). The layout is the same as doeff-cluster's worker
wheel store (state/wheels/<package>-<key>/ and .used -- shared/core/native_wheel.py), and the daily verification shares it by passing the worker's /work/state/wheels as the
store -- the worker's cleanup (removing dirs unused for 7 days) applies as is. A stored wheel is checked against RECORD hashes before use;
if it is corrupt, one line naming it is printed, the wheel is removed, and it is rebuilt (corrupt wheels are never used silently).

Off-machine store (_remote_wheel, ADR-DOE-BUILD-001 R5 addendum, design docs/design/rust-wheel-distribution): when the local
store has nothing, before building, the entry fetches the asset `<package>-<key>.whl` of doeff's public GitHub release `rust-wheels` over unauthenticated HTTPS
(up to WHEEL_REMOTE_TIMEOUT_SECONDS seconds), verifies it with the RECORD hashes, reconstructs the real file name from the dist-info and the WHEEL Tag, and places it in the local
store. Only doeff's GitHub Actions (.github/workflows/rust-wheels.yml) writes to that store; the entry never writes to it. When fetching fails
(404, network failure, timeout, corrupt wheel), one line naming the cause is printed and the build proceeds as before. env DOEFF_WHEEL_REMOTE replaces the store's base, and an empty value
disables the store lookup (for checks of the local build path, and places with no network). uv's find-links and index are not used (uv does not use them for path, editable, or git dependencies).

report (_write_report): if env DOEFF_WHEEL_REPORT is set, one JSON line {"project": <package name>, "wheel": <path of the wheel in the
store>, "source": <origin: local, remote, built>, "built": <whether this call built it>} is appended to that file ("built" is the field read by
doeff-cluster's native_wheel.reported_built, so it stays). The doeff-cluster worker (the environment preparation and startup script) goes through this entry with `uv build --wheel
--out-dir` and installs the resulting wheel -- it keeps no key or store of its own. The report is used only to see whether a build happened (the reader is
reported_built in shared/core/native_wheel.py; for an entry version that writes no report, whether it built is UNREPORTED; #3860).

cargo target: only when building, it is placed in a per-build temporary dir outside the package dir and deleted once the wheel is made (#1493). Why not one shared
target: cargo judges freshness by source file mtimes and names products by their path relative to the workspace. When two working trees share one
target, the earlier tree's sources look older than the later build's products, so another tree's build is silently reused (measured with cargo 1.96.1)
-- and once that wheel is placed under a key, every build with the same key fetches it. When the user passes CARGO_TARGET_DIR, the build happens there and it is not deleted -- the way for a
session fixing Rust to use incremental builds (`CARGO_TARGET_DIR=<dir> uv sync`). In cargo,
the env CARGO_TARGET_DIR beats the config's build.target-dir (and CARGO_BUILD_TARGET_DIR), so the entry's temporary dir also beats those settings.
A build that uses the temporary dir also points the intermediate-artifact location CARGO_BUILD_BUILD_DIR at the same temporary dir. Even if the environment points that location at memory or similar (zeus's
worker), one build's intermediates are not left behind. When the user passes CARGO_TARGET_DIR, the environment's value is left as is.
The temporary dir's name includes the build process's pid (doeff-cargo-target-<pid>-<random chars>). A build stopped by SIGTERM or SIGKILL does no cleanup,
so the next build, at the entry, removes dirs whose owner process is gone.

The canonical source is tools/doeff_cargo_backend.py. Each package's doeff_cargo_backend.py is a symlink to this file (backend-path can only point inside the package's
dir -- PEP 517). A package whose build is not maturin alone (doeff-indexer -- bundles a CLI binary into the wheel) passes its own build
(Compile) to wheel_from_store and editable_from_store and goes through the same store. sdist does not consult the store (no Rust is built).
"""

from __future__ import annotations

import base64
import csv
import fnmatch
import glob
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import shutil
import sys
import sysconfig
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

import maturin

TARGET_ENV = "CARGO_TARGET_DIR"
BUILD_DIR_ENV = "CARGO_BUILD_BUILD_DIR"
TEMP_PREFIX = "doeff-cargo-target-"
WHEEL_CACHE_ENV = "DOEFF_WHEEL_CACHE"
# Env that replaces the base of the off-machine store (an empty value skips the store lookup -- the store from the header note).
WHEEL_REMOTE_ENV = "DOEFF_WHEEL_REMOTE"
# Store default = the download URL of doeff's public release rust-wheels (read unauthenticated, no API -- the unauthenticated API is limited to 60 calls per hour).
WHEEL_REMOTE_DEFAULT = "https://github.com/proboscis/doeff/releases/download/rust-wheels"
# Seconds to wait when fetching one wheel from the store (after that, one line naming the cause is printed and the build runs locally).
WHEEL_REMOTE_TIMEOUT_SECONDS = 20
# Env naming the file to which a one-line JSON of the stored wheel and whether it was built is appended (the report from the header note -- nothing is written if unset).
WHEEL_REPORT_ENV = "DOEFF_WHEEL_REPORT"
# Used marker of a store dir (advances the dir's mtime -- the worker's cleanup picks dirs unused for 7 days by dir mtime).
USED_MARK = ".used"
# Default build-relevant files (globs from the package dir) for a package that declares no tool.uv.cache-keys files.
DEFAULT_SOURCE_GLOBS = ("pyproject.toml", "Cargo.toml", "Cargo.lock", "build.rs", "src/**/*")
# Environment variables that change the wheel contents (name and value go into the store key): rustc flags, cargo profile and build settings,
# maturin and PyO3 settings, and the flag that omits bundling doeff-indexer's CLI binary (so a build that bundles it never fetches a wheel built without it).
BUILD_ENV_NAMES = frozenset({"RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC", "MACOSX_DEPLOYMENT_TARGET", "DOEFF_INDEXER_SKIP_CLI_BUILD"})
BUILD_ENV_PREFIXES = ("CARGO_PROFILE_", "CARGO_BUILD_", "MATURIN_", "PYO3_")
# Names that match the prefixes above but only change where and how many jobs build, not the contents -- including them in the key would split the store per working tree or machine load
# and defeat sharing.
PLACE_ONLY_ENV_NAMES = frozenset({"CARGO_BUILD_TARGET_DIR", BUILD_DIR_ENV, "CARGO_BUILD_JOBS"})
# Suffixes of native extension module files (the build products that editable places in the venv's products dir).
NATIVE_SUFFIXES = (".so", ".pyd", ".dylib")

# PEP 517 config_settings: "setting name -> string or list of strings" passed by the frontend. The entry does not read them and passes them to maturin.
ConfigSettings: TypeAlias = Mapping[str, str | list[str]]
# Function that builds a wheel when the store has none: (this build's cargo target, dir to write the wheel into, build settings) -> file name of the wheel written.
Compile: TypeAlias = Callable[[Path, str, "ConfigSettings | None"], str]
# Origin of a stored wheel: local = found in the local store; remote = fetched from the off-machine store; built = built by this call.
WheelSource: TypeAlias = Literal["local", "remote", "built"]


@dataclass(frozen=True)
class StoredWheel:
    """One stored wheel: path = the wheel file in the store; source = its origin."""

    path: Path
    source: WheelSource

    @property
    def built(self) -> bool:
        """Whether this call built it (it was in neither the local store nor the remote store, or it was corrupt)."""
        return self.source == "built"


@dataclass(frozen=True)
class WheelEntry:
    """One file of a wheel the entry writes (editable, or a wheel with python-source installed from the working tree): name = path inside the wheel; data = contents; mode = permission bits of the installed
    file (the execute bit for a CLI binary)."""

    name: str
    data: bytes
    mode: int


def _owner_pid(name: str) -> int | None:
    """Read the pid of the build that created a temporary target from its name (doeff-cargo-target-<pid>-<random chars>)."""
    match name.removeprefix(TEMP_PREFIX).split("-", 1):
        case [pid, _] if pid.isdigit():
            return int(pid)
        case _:
            # Not a name in this entry's format -- its owner is unknown, so it is not a cleanup candidate.
            return None


def _alive(pid: int) -> bool:
    """Whether the process for the pid still exists (if not, that build was stopped and did no cleanup)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A process of a different user has the same pid.
        return True
    return True


def _sweep_orphans(root: Path) -> None:
    """Remove temporary targets left by stopped builds. Only dirs whose owner process is gone; builds that are running are not touched."""
    for path in root.glob(f"{TEMP_PREFIX}*"):
        match _owner_pid(path.name):
            case int(pid) if not _alive(pid):
                # If another build that reached the entry at the same time removed it first, do not look for the vanished files.
                with suppress(FileNotFoundError):
                    shutil.rmtree(path)
            case _:
                pass


@contextmanager
def cargo_target_dir() -> Iterator[Path]:
    """This build's cargo target. Without a user setting, create a temporary dir, pass it via env, and delete it on exit."""
    match os.environ.get(TARGET_ENV):
        case (None | "") as absent:
            root = Path(tempfile.gettempdir())
            _sweep_orphans(root)
            made = Path(tempfile.mkdtemp(prefix=f"{TEMP_PREFIX}{os.getpid()}-", dir=root))
            build_dir = os.environ.get(BUILD_DIR_ENV)
            os.environ[TARGET_ENV] = str(made)
            os.environ[BUILD_DIR_ENV] = str(made)
            try:
                yield made
            finally:
                match absent:
                    case None:
                        del os.environ[TARGET_ENV]
                    case str():
                        os.environ[TARGET_ENV] = absent
                match build_dir:
                    case None:
                        del os.environ[BUILD_DIR_ENV]
                    case str():
                        os.environ[BUILD_DIR_ENV] = build_dir
                shutil.rmtree(made)
        case str() as given:
            yield Path(given)


def _maturin_wheel(target: Path, wheel_directory: str, config_settings: ConfigSettings | None) -> str:
    """Build for packages built by maturin alone (the target was already passed via env by cargo_target_dir)."""
    return maturin.build_wheel(wheel_directory, config_settings, None)


def stored_wheel(package: Path, config_settings: ConfigSettings | None, compile_wheel: Compile) -> StoredWheel:
    """Look up the wheel for the package's source key in the local store; if absent (or corrupt), fetch it from the off-machine store; if absent there too, build and
    store it -- the core of the entry for building and fetching Rust components (header note). Answer = the wheel in the store. One line on stderr (reused / fetched / seconds to build)."""
    slot = _wheel_slot(package, config_settings)
    match _intact_wheel(slot):
        case Path() as found:
            _mark_used(slot)
            print(f"doeff_cargo_backend: reusing wheel for identical source (no build) -- {found.name} in {slot}", file=sys.stderr)
            return _write_report(package, StoredWheel(path=found, source="local"))
        case None:
            pass
    match _remote_wheel(slot):
        case Path() as fetched:
            return _write_report(package, StoredWheel(path=fetched, source="remote"))
        case None:
            pass
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="doeff-wheel-out-") as out:
        with cargo_target_dir() as target:
            name = compile_wheel(target, out, config_settings)
        stored = _store_wheel(slot, Path(out) / name)
    print(f"doeff_cargo_backend: wheel built ({time.monotonic() - started:.0f} s) -- {name} placed in {slot}", file=sys.stderr)
    return _write_report(package, StoredWheel(path=stored, source="built"))


def _remote_wheel(slot: Path) -> Path | None:
    """Fetch the key's wheel (`<store>/<slot name>.whl`) from the off-machine store, verify it with the RECORD hashes, then place it in the local store
    (the store from the header note). Answer = the wheel placed in the local store. When it cannot be fetched (store lookup disabled, 404, network failure, timeout, corrupt wheel),
    print one line naming the cause and return None (the caller builds) -- never build silently, never use a corrupt wheel silently."""
    base = os.environ.get(WHEEL_REMOTE_ENV, WHEEL_REMOTE_DEFAULT)
    if not base:
        return None
    url = f"{base.rstrip('/')}/{slot.name}.whl"
    with tempfile.TemporaryDirectory(prefix="doeff-wheel-remote-") as fetched:
        raw = Path(fetched) / f"{slot.name}.whl"
        try:
            with urllib.request.urlopen(url, timeout=WHEEL_REMOTE_TIMEOUT_SECONDS) as response:
                raw.write_bytes(response.read())
        except urllib.error.HTTPError as error:
            print(f"doeff_cargo_backend: remote store has no wheel with the same key (HTTP {error.code}) -- {url}; building locally", file=sys.stderr)
            return None
        except (urllib.error.URLError, OSError) as error:
            print(f"doeff_cargo_backend: cannot reach the remote store ({error}) -- {url}; building locally", file=sys.stderr)
            return None
        # Read the name only after knowing the wheel is intact (readable as a zip and RECORD matches).
        match _wheel_problem(raw):
            case str() as problem:
                print(f"doeff_cargo_backend: remote store wheel is corrupt ({problem}) -- {url}; not using it, building locally", file=sys.stderr)
                return None
            case None:
                pass
        match _wheel_file_name(raw):
            case str() as name:
                pass
            case None:
                print(f"doeff_cargo_backend: cannot read the name and tag of the remote store wheel -- {url}; not using it, building locally", file=sys.stderr)
                return None
        named = Path(fetched) / name
        raw.rename(named)
        placed = _store_wheel(slot, named)
    print(f"doeff_cargo_backend: using remote store wheel (no build) -- {name} from {url}", file=sys.stderr)
    return placed


def _wheel_file_name(wheel: Path) -> str | None:
    """Reconstruct the wheel's real file name (`<name>-<version>-<python>-<abi>-<platform>.whl`) from the name of the inner `*.dist-info` dir and the Tag lines of WHEEL
    -- the store asset's name is the key (`<package>-<key>.whl`), so it carries no name. None if it cannot be read."""
    with zipfile.ZipFile(wheel) as archive:
        infos = sorted({name.split("/", 1)[0] for name in archive.namelist() if name.split("/", 1)[0].endswith(".dist-info")})
        match infos:
            case [dist_info]:
                text = archive.read(f"{dist_info}/WHEEL").decode("utf-8")
            case _:
                return None
    distribution, _, version = dist_info.removesuffix(".dist-info").rpartition("-")
    tags = [line.removeprefix("Tag:").strip().split("-") for line in text.splitlines() if line.startswith("Tag:")]
    if not distribution or not tags or any(len(tag) != 3 for tag in tags):
        return None
    parts = (".".join(dict.fromkeys(tag[index] for tag in tags)) for index in range(3))
    return f"{distribution}-{version}-{'-'.join(parts)}.whl"


def _write_report(package: Path, stored: StoredWheel) -> StoredWheel:
    """Append a one-line JSON of the stored wheel and whether it was built to the file named by env DOEFF_WHEEL_REPORT (the report from the header note -- does nothing
    if the env is unset). Answer = stored (the caller returns it as is)."""
    match os.environ.get(WHEEL_REPORT_ENV):
        case str() as report if report:
            match _pyproject(package).get("project"):
                case {"name": str() as project}:
                    pass
                case _:
                    project = "unnamed"
            line = json.dumps(
                {"project": project, "wheel": str(stored.path), "source": stored.source, "built": stored.built}, ensure_ascii=False
            )
            with open(report, "a", encoding="utf-8") as out:
                out.write(line + "\n")
        case _:
            pass
    return stored


def wheel_from_store(wheel_directory: str, config_settings: ConfigSettings | None, compile_wheel: Compile) -> str:
    """Core of the wheel hook: write a wheel into wheel_directory from the cwd package's stored wheel and return its name. For a package with a maturin
    python-source, install the python-source files from the working tree (the wheel from the header note); for a package without, copy the stored wheel."""
    package = Path.cwd()
    stored = stored_wheel(package, config_settings, compile_wheel)
    config = _maturin_config(package)
    match config.get("python-source"):
        case str() as source:
            return _source_wheel(stored.path, package, (package / source).resolve(), _wheel_includes(config), Path(wheel_directory))
        case _:
            shutil.copy2(stored.path, Path(wheel_directory) / stored.path.name)
            return stored.path.name


def editable_from_store(wheel_directory: str, config_settings: ConfigSettings | None, compile_wheel: Compile) -> str:
    """Core of the editable hook: build an editable wheel from the cwd package's stored wheel and return its name (header note).
    For a package without a maturin python-source (a bin binary, an extension with no Python source), hand over the stored wheel itself
    (nothing points at the working tree, so editable has the same contents as the wheel)."""
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
    """When uv sync or uv build asks for a wheel, answer from the same-source wheel in the store (build and store it if absent; python-source is installed from the working
    tree -- header note)."""
    return wheel_from_store(wheel_directory, config_settings, _maturin_wheel)


def build_editable(
    wheel_directory: str,
    config_settings: ConfigSettings | None = None,
    metadata_directory: str | None = None,
) -> str:
    """When installing a uv workspace member or an editable path dependency, also answer from the stored wheel (header note)."""
    return editable_from_store(wheel_directory, config_settings, _maturin_wheel)


def _source_files(package: Path) -> list[Path]:
    """List the build-relevant files (globs of the package's tool.uv.cache-keys files, or the default -- the same set uv uses to decide on rebuilds) in relative-path order."""
    declared = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8")).get("tool", {}).get("uv", {}).get("cache-keys", [])
    globs = [key["file"] for key in declared if isinstance(key, dict) and isinstance(key.get("file"), str)] or list(DEFAULT_SOURCE_GLOBS)
    matched = {Path(hit) for pattern in globs for hit in glob.glob(str(package / pattern), recursive=True)}
    return sorted((path for path in matched if path.is_file()), key=lambda path: os.path.relpath(path, package))


def _tool_versions() -> str:
    """Versions of the tools that affect the build (maturin -- read from the distribution metadata without spawning child processes) -- so that when maturin changes, the wheel is rebuilt even for the same source.
    The rustc version is not included (the key in the header note; ADR-DOE-BUILD-001 R4 addendum)."""
    return f"maturin={importlib.metadata.version('maturin')}"


def _build_environment() -> str:
    """List the names and values of environment variables that change the build (names matching BUILD_ENV_NAMES and BUILD_ENV_PREFIXES, excluding place-only names)
    (so that a default build never fetches a wheel built with different RUSTFLAGS, CARGO_PROFILE_RELEASE_STRIP, etc. for the same source).
    Values go only into the key's hash and are never shown anywhere."""
    chosen = sorted(
        (name, value)
        for name, value in os.environ.items()
        if (name in BUILD_ENV_NAMES or name.startswith(BUILD_ENV_PREFIXES)) and name not in PLACE_ONLY_ENV_NAMES
    )
    return repr(chosen)


def _python_abi() -> str:
    """Python version, ABI, and machine used to build (the tail of an extension module name -- e.g. `.cpython-314t-x86_64-linux-gnu.so`) -- so that building the same source in environments with different ABIs, such as free-threading cp314t and
    cp313, never mixes up the wheels in the store."""
    return f"{sys.implementation.cache_tag} {sysconfig.get_config_var('EXT_SUFFIX')}"


def _pyproject(package: Path) -> dict[str, object]:
    """Read the package's pyproject.toml."""
    return tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))


def _maturin_config(package: Path) -> dict[str, object]:
    """Read the package's [tool.maturin] table (empty if absent)."""
    match _pyproject(package).get("tool"):
        case {"maturin": dict() as config}:
            return config
        case _:
            return {}


def _wheel_slot(package: Path, config_settings: ConfigSettings | None) -> Path:
    """Decide the dir for the wheel from a hash of the source contents, tool versions, machine, Python ABI, environment variables that change the build, and build settings
    (independent of file mtimes and the working tree path -- CARGO_TARGET_DIR and the like, which only change the location, are not included). The dir name is <package>-<key>
    (the working tree's dir name differs per working tree so it is not used; same layout as the worker's wheel store -- header note)."""
    digest = hashlib.sha256()
    lines = (
        _tool_versions(), sys.platform, platform.machine(), repr(platform.libc_ver()), _python_abi(), _build_environment(),
        repr(sorted((config_settings or {}).items())),
    )
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
    """Say in one line whether a stored wheel is corrupt (unreadable as a zip, no RECORD, a RECORD file missing, or a hash mismatch)
    (None = not corrupt)."""
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            records = sorted(name for name in names if name.endswith(".dist-info/RECORD"))
            match records:
                case [record]:
                    rows = list(csv.reader(io.StringIO(archive.read(record).decode("utf-8"))))
                case _:
                    return f"RECORD count is {len(records)} (expected 1)"
            for row in rows:
                match row:
                    case [name, str() as hashed, _] if hashed:
                        if name not in names:
                            return f"{name} listed in RECORD is not in the wheel"
                        algorithm, _, expected = hashed.partition("=")
                        if _urlsafe_digest(algorithm, archive.read(name)) != expected:
                            return f"hash of {name} differs from RECORD"
                    case _:
                        pass
    except (zipfile.BadZipFile, OSError, KeyError, ValueError, UnicodeDecodeError) as error:
        return f"unreadable as a wheel: {error}"
    return None


def _urlsafe_digest(algorithm: str, data: bytes) -> str:
    """Spelling of a wheel RECORD hash (urlsafe base64, no trailing =)."""
    return base64.urlsafe_b64encode(hashlib.new(algorithm, data).digest()).rstrip(b"=").decode("ascii")


def _intact_wheel(slot: Path) -> Path | None:
    """Return the first intact wheel (in name order) in the store dir. For a corrupt wheel, print one line naming it and remove it (only files this entry
    placed -- *.whl in the store dir). None if there is none (build)."""
    for wheel in sorted(slot.glob("*.whl")):
        match _wheel_problem(wheel):
            case None:
                return wheel
            case str() as problem:
                print(f"doeff_cargo_backend: stored wheel is corrupt ({problem}) -- removing {wheel} and rebuilding", file=sys.stderr)
                wheel.unlink(missing_ok=True)
    return None


def _mark_used(slot: Path) -> None:
    """Write the used marker under another name, then replace, advancing the dir's mtime (so cleanup never removes a wheel in use -- same placement as the worker's marker)."""
    handle, staged = tempfile.mkstemp(dir=slot, prefix=".used.")
    os.close(handle)
    os.replace(staged, slot / USED_MARK)


def _store_wheel(slot: Path, built: Path) -> Path:
    """Copy the built wheel into the store and return the path it was placed at (write under a temporary name, then rename -- so it does not mix with another build
    that built the same key at the same time, and a half-written file is never read)."""
    slot.mkdir(parents=True, exist_ok=True)
    staged = slot / f".{built.name}.{os.getpid()}.part"
    shutil.copy2(built, staged)
    placed = slot / built.name
    os.replace(staged, placed)
    _mark_used(slot)
    return placed


def _wheel_includes(config: Mapping[str, object]) -> tuple[str, ...]:
    """Globs (from the package dir) of the [tool.maturin] include entries that go into the wheel -- a declaration that bundles build output into the wheel
    (doeff-indexer's CLI binary). Editable also places these in the venv's products dir."""
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
    """Whether a file of the wheel is a build product (a native extension module, or something put into the wheel by include) -- what editable places in the venv's products dir.
    Files under dist-info and data, and the sources themselves (.py etc.), are not included (the working tree's sources are the correct ones)."""
    if member.endswith("/") or member.split("/", 1)[0].endswith((".dist-info", ".data")):
        return False
    relative = os.path.relpath(source_root / member, package)
    return member.endswith(NATIVE_SUFFIXES) or any(fnmatch.fnmatch(relative, pattern) for pattern in includes)


# Contents of the finder module put in the editable wheel ({packages} = the set of package names it answers for; {native} = the name of the products dir; {pth} = the name of the same wheel's .pth,
# which holds the working tree's source root on its first line -- the source root is not written in the finder but read from the .pth).
# The package search order is the venv's products dir -> the working tree's source dir (the products dir is searched first, so products that an earlier layout left in the source tree
# are never used).
EDITABLE_FINDER = '''\
"""Finder for the editable install -- written by doeff's build entry (tools/doeff_cargo_backend.py)."""
import importlib.util
import os
import sys

SITE = os.path.dirname(os.path.abspath(__file__))
NATIVE = os.path.join(SITE, {native!r})
PACKAGES = {packages!r}
# The working tree's source root is held only by the first line of the same wheel's .pth (one definition -- the finder also follows a redirect that rewrites the .pth line to another
# checkout).
with open(os.path.join(SITE, {pth!r}), encoding="utf-8") as _pth:
    SOURCE = _pth.readline().strip()


class EditableNativeFinder:
    """Answer for the packages in PACKAGES, as packages whose search path = products dir -> working tree's source dir."""

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
    """Build an editable wheel from the stored wheel (editable in the header note): write into wheel_directory a wheel holding the build products under `__editable__.<name>.native/`,
    the dist-info (excluding RECORD), a .pth that points at source_root and activates the finder, and the finder module.
    Answer = the name of the wheel written (the same name as the stored wheel -- the same tag)."""
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
    loose = any(entry.name.count("/") == 1 for entry in products)  # products outside any package (a root extension module) add the products dir to the path
    pth = "".join((f"{source_root}\n", f"{native}\n" if loose else "", f"import {finder}\n" if packages else ""))
    entries = (
        *metadata,
        *products,
        WheelEntry(name=f"{finder}.py", data=EDITABLE_FINDER.format(native=native, packages=packages, pth=f"{distribution}.pth").encode("utf-8"), mode=0o644),
        WheelEntry(name=f"{distribution}.pth", data=pth.encode("utf-8"), mode=0o644),
    )
    _write_wheel(wheel_directory / stored.name, dist_info, entries)
    return stored.name


def _source_wheel(stored: Path, package: Path, source_root: Path, includes: tuple[str, ...], wheel_directory: Path) -> str:
    """Build a wheel from the stored wheel and the working tree's python-source (wheel in the header note): from the stored wheel take only the build products, data, and
    dist-info (excluding RECORD), reinstall the files of the python packages (and root modules) the stored wheel holds from the working tree's source_root,
    and write the wheel with a rewritten RECORD into wheel_directory. Answer = the name of the wheel written (the same name as the stored wheel -- the same tag)."""
    with zipfile.ZipFile(stored) as archive:
        (dist_info,) = {name.split("/", 1)[0] for name in archive.namelist() if name.split("/", 1)[0].endswith(".dist-info")}
        members = tuple(
            WheelEntry(name=info.filename, data=archive.read(info), mode=(info.external_attr >> 16) & 0o777)
            for info in archive.infolist()
            if not info.filename.endswith("/") and info.filename != f"{dist_info}/RECORD"
        )
    kept = tuple(entry for entry in members if not _source_member(entry.name, package, source_root, includes))
    packages = frozenset(
        top for entry in members if "/" in entry.name and not (top := entry.name.split("/", 1)[0]).endswith((".dist-info", ".data"))
    )
    modules = frozenset(entry.name for entry in members if "/" not in entry.name and _source_member(entry.name, package, source_root, includes))
    sources = tuple(
        WheelEntry(name=relative, data=path.read_bytes(), mode=path.stat().st_mode & 0o777)
        for path in _python_source_files(source_root, packages | modules)
        if not _built_member(relative := path.relative_to(source_root).as_posix(), package, source_root, includes)
    )
    _write_wheel(wheel_directory / stored.name, dist_info, (*sources, *kept))
    return stored.name


def _source_member(member: str, package: Path, source_root: Path, includes: tuple[str, ...]) -> bool:
    """Whether a wheel file is python-source source (neither under dist-info or data nor a build product -- what is reinstalled from the working tree)."""
    return not member.split("/", 1)[0].endswith((".dist-info", ".data")) and not _built_member(member, package, source_root, includes)


def _python_source_files(source_root: Path, tops: frozenset[str]) -> list[Path]:
    """List, in wheel-internal path order, the files under the package dirs named by tops and the root-module files of the working tree's python-source.
    Files under __pycache__ and .pyc and .pyo are not included (they do not exist in the building working tree -- the user's Python recreates them). Entries of tops
    that are absent from the working tree are not included (modules removed in that version)."""
    found = (
        hit
        for top in tops
        for hit in ((source_root / top).rglob("*") if (source_root / top).is_dir() else [source_root / top])
        if hit.is_file()
        and "__pycache__" not in hit.relative_to(source_root).parts
        and hit.suffix not in (".pyc", ".pyo")
    )
    return sorted(found, key=lambda path: path.relative_to(source_root).as_posix())


def _write_wheel(path: Path, dist_info: str, entries: tuple[WheelEntry, ...]) -> None:
    """Write to path a wheel holding entries and a RECORD (under dist_info, last) listing those files' hashes (timestamps are fixed -- the same contents give
    the same zip)."""
    text = io.StringIO()
    csv.writer(text, lineterminator="\n").writerows(
        (*((entry.name, f"sha256={_urlsafe_digest('sha256', entry.data)}", str(len(entry.data))) for entry in entries), (f"{dist_info}/RECORD", "", ""))
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        for entry in entries:
            info = zipfile.ZipInfo(entry.name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (0o100000 | (entry.mode or 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            wheel.writestr(info, entry.data)
        wheel.writestr(f"{dist_info}/RECORD", text.getvalue())


def build_sdist(sdist_directory: str, config_settings: ConfigSettings | None = None) -> str:
    """When building an sdist too, keep the target that maturin has cargo create outside the working tree (no Rust is built, so the store is not consulted)."""
    with cargo_target_dir():
        return maturin.build_sdist(sdist_directory, config_settings)


get_requires_for_build_wheel = maturin.get_requires_for_build_wheel
get_requires_for_build_editable = maturin.get_requires_for_build_editable
get_requires_for_build_sdist = maturin.get_requires_for_build_sdist
prepare_metadata_for_build_wheel = maturin.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = maturin.prepare_metadata_for_build_editable
