"""bytecode の準備の道具(worker/entry/code_prepare.hy)の取るだけの形(--fetch-only)の検。

新しい版の Hy の bytecode を手元の machine で焼かずに、機体の保存先と分け合う保存先(DOEFF_HY_CODE_SHARE・
ADR-DOE-BUILD-002 R8)から取ってくるだけで済ませ、取れない file が 1 つでも在る時と分け合う保存先が読めない時
(ShareUnusable)は焼かずに 1 行の訳を標準エラーへ出して決まった 0 でない終了 code で終わる事を確かめる。

確かめの層は 2 つ:
  1. 効果の層(prepare_trees(..., fetch_only=True)): BakeSources と RunProcess(bytecode_pool の起動)を数える答え手を
     被せ、全部在る木・取れない file が在る木・ShareUnusable の 3 件のどれでも BakeSources の呼び 0 回・bytecode_pool の
     起動 0 回である事を、報告の行の rebuilt=0 の字面だけでなく答え手の数えで確かめる。
  2. CLI の層(hy code_prepare.hy --fetch-only …): 本番と同じ引数の形で道具を起こし、終了 code・標準エラーの 1 行の訳・
     報告の行の rebuilt=0・.pyc の配置と import の値・sitecustomize による compile 0 回を確かめる。
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass, field
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from xml.sax.saxutils import escape

import hy
import pytest
from doeff_cluster import tree_files
from doeff_cluster._worker.core.code_plan import MARKER
from doeff_cluster._worker.entry import code_prepare as cp
from doeff_core_effects import RunProcess, Slog, os_file_handler, subprocess_handler
from doeff_time import SimClock, sim_time_handler

from doeff import K, Pass, Resume, do, handler, run, with_handlers

#: 準備の道具(本番と同じ script)。
TOOL = Path(__file__).resolve().parents[1] / "src" / "doeff_cluster" / "_worker" / "entry" / "code_prepare.hy"

_COMPILED_HOOK = """\
import os

_LOG = os.environ.get("BAKE_COMPILED_LOG")
if _LOG:
    import importlib.machinery as _machinery

    _original = _machinery.SourceFileLoader.source_to_code

    def source_to_code(self, data, path, *args, **kwargs):
        if isinstance(path, str) and path.endswith(".hy"):
            with open(_LOG, "a") as log:
                log.write(path + "\\n")
        return _original(self, data, path, *args, **kwargs)

    _machinery.SourceFileLoader.source_to_code = source_to_code
"""

_HY_SOURCES = ("mac/macros.hy", "use/user.hy", "imp/importer.hy", "other/plain.hy")
_CHECK_COMPILES = frozenset({"mac/macros.hy"})

_IMPORT = """\
import sys
sys.path.insert(0, sys.argv[1])
import hy
sys.pycache_prefix = None
import use.user
print(use.user.value)
"""

_BUCKET = "code"
_KEY_ID = "test-key"


def _write_four(root: Path, salt: str, *, macro_add: int = 1, importer: int = 2, plain: int = 3) -> None:
    """macro の定義元・使う側・import だけする側・無関係の 4 package を置く。"""
    files = {
        "mac/__init__.py": f"# {salt}\n",
        "mac/macros.hy": f'; {salt}\n(defmacro answer [] (+ 1 {macro_add}))\n(setv TAG "mac")\n',
        "use/__init__.py": f"# {salt}\n",
        "use/user.hy": f"; {salt}\n(require mac.macros [answer])\n(setv value (answer))\n",
        "imp/__init__.py": f"# {salt}\n",
        "imp/importer.hy": f"; {salt}\n(import mac.macros)\n(setv value {importer})\n",
        "other/__init__.py": f"# {salt}\n",
        "other/plain.hy": f"; {salt}\n(setv value {plain})\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


# --- 模擬の S3 ----------------------------------------------------------------------------------


@dataclass
class _Bucket:
    """模擬の S3 の中身と障害の注入(fail_after_lists = N 回目の LIST 以降を HTTP 500 にする)。"""

    objects: dict[str, tuple[bytes, float]] = field(default_factory=dict)
    requests: tuple[tuple[str, str], ...] = ()
    fail_after_lists: int | None = None
    list_count: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass(frozen=True)
class _Target:
    name: str
    query: dict[str, list[str]]


def _handler(bucket: _Bucket) -> type[BaseHTTPRequestHandler]:
    class S3(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — 基の名のまま上書きする
            return

        def _answer(self, code: int, body: bytes = b"") -> None:
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _target(self) -> _Target | None:
            if f"Credential={_KEY_ID}/" not in self.headers.get("Authorization", ""):
                self._answer(403)
                return None
            parts = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parts.path)
            if path != f"/{_BUCKET}" and not path.startswith(f"/{_BUCKET}/"):
                self._answer(404)
                return None
            return _Target(path[len(f"/{_BUCKET}/"):], urllib.parse.parse_qs(parts.query))

        def do_GET(self) -> None:
            target = self._target()
            if target is None:
                return
            name, query = target.name, target.query
            if not name:
                prefix = query.get("prefix", [""])[0]
                with bucket.lock:
                    bucket.list_count += 1
                    bucket.requests = (*bucket.requests, ("LIST", prefix))
                    if bucket.fail_after_lists is not None and bucket.list_count > bucket.fail_after_lists:
                        self._answer(500)
                        return
                    found = sorted((n, at) for n, (_, at) in bucket.objects.items() if n.startswith(prefix))
                limit = int(query.get("max-keys", ["1000"])[0])
                rows = "".join(
                    f"<Contents><Key>{escape(n)}</Key><LastModified>"
                    f"{time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at))}.{int(at * 1000) % 1000:03d}Z</LastModified></Contents>"
                    for n, at in found[:limit]
                )
                body = (
                    '<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                    f"<Name>{_BUCKET}</Name>{rows}</ListBucketResult>"
                ).encode()
                self._answer(200, body)
                return
            with bucket.lock:
                bucket.requests = (*bucket.requests, ("GET", name))
                found = bucket.objects.get(name)
            if found is None:
                self._answer(404)
            else:
                self._answer(200, found[0])

        def do_PUT(self) -> None:
            target = self._target()
            if target is None:
                return
            name = target.name
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            with bucket.lock:
                bucket.requests = (*bucket.requests, ("PUT", name))
                bucket.objects[name] = (data, time.time())
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.send_header("Date", formatdate(usegmt=True))
            self.end_headers()

    return S3


@dataclass(frozen=True)
class _Share:
    url: str
    bucket: _Bucket


@pytest.fixture
def share() -> Iterator[_Share]:
    bucket = _Bucket()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(bucket))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _Share(f"http://127.0.0.1:{server.server_address[1]}/{_BUCKET}/bytecode", bucket)
    finally:
        server.shutdown()
        server.server_close()


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# --- 道具を起こす台 -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Rig:
    hook: Path
    prefix: Path
    keys: Path
    wrong_keys: Path


@dataclass(frozen=True)
class _Run:
    returncode: int
    compiled: list[str]
    counts: dict[str, int]
    stderr: str


def _write_share_file(dest: Path, share_url: str, keys: Path) -> Path:
    dest.write_text(f"{share_url}\n{keys.read_text()}")
    return dest


def _tool(
    tree: Path,
    store: Path,
    rig: _Rig,
    compiled: Path,
    share_url: str | None,
    keys: Path | None,
    *,
    write: bool = False,
    fetch_only: bool = False,
    extra_args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    shared: list[str] = []
    if share_url is not None:
        named = _write_share_file(compiled.parent / f"{compiled.stem}.share", share_url, keys or rig.keys)
        shared = [f"DOEFF_HY_CODE_SHARE={named}"]
    mode = ["--fetch-only"] if fetch_only else []
    return subprocess.run(
        [
            "env", "-u", "DOEFF_HY_CODE_SHARE",
            *(["-u", "PYTHONDONTWRITEBYTECODE"] if write else ["PYTHONDONTWRITEBYTECODE=1"]),
            f"PYTHONPYCACHEPREFIX={rig.prefix}", f"DOEFF_HY_CODE_STORE={store}",
            f"PYTHONPATH={rig.hook}", f"BAKE_COMPILED_LOG={compiled}", *shared,
            sys.executable, "-m", "hy", str(TOOL), "--revision", "r", "--tree", str(tree), "--roots", ".", "--jobs", "1",
            *mode, *extra_args,
        ],
        capture_output=True, text=True, cwd=tree, timeout=180, check=False,
    )


def _counts(report: str, tree: Path) -> dict[str, int]:
    for line in report.splitlines():
        if f"tree={tree} " in line:
            return {k: int(v) for k, v in (part.split("=", 1) for part in line.split() if "=" in part) if v.isdigit()}
    return {}


def _run_tool(
    tree: Path,
    store: Path,
    rig: _Rig,
    share_url: str | None = None,
    keys: Path | None = None,
    *,
    fetch_only: bool = False,
    extra_args: tuple[str, ...] = (),
) -> _Run:
    compiled = tree.parent / f"{tree.name}.compiled.log"
    completed = _tool(tree, store, rig, compiled, share_url, keys, write=False, fetch_only=fetch_only, extra_args=extra_args)
    lines = compiled.read_text().split() if compiled.exists() else []
    names = sorted(str(Path(line).relative_to(tree)) for line in lines if Path(line).is_relative_to(tree))
    return _Run(completed.returncode, names, _counts(completed.stderr, tree), completed.stderr)


def _import_value(tree: Path, store: Path, rig: _Rig) -> int:
    completed = subprocess.run(
        ["env", f"PYTHONPYCACHEPREFIX={rig.prefix}", "PYTHONDONTWRITEBYTECODE=1", f"DOEFF_HY_CODE_STORE={store}",
         sys.executable, "-c", _IMPORT, str(tree)],
        capture_output=True, text=True, cwd=tree, timeout=180, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return int(completed.stdout.split()[-1])


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> _Rig:
    base = tmp_path_factory.mktemp("rig")
    hook, prefix, warm = base / "hook", base / "pyc", base / "warm"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(_COMPILED_HOOK)
    keys, wrong = base / "keys", base / "wrong-keys"
    keys.write_text(f"{_KEY_ID}\nsecret\n")
    wrong.write_text("other-key\nsecret\n")
    made = _Rig(hook, prefix, keys, wrong)
    _write_four(warm, "warm")
    completed = _tool(warm, base / "store", made, base / "warm.compiled.log", None, None, write=True)
    assert completed.returncode == 0, completed.stderr
    return made


def _seed_via_normal_bake(tmp_path: Path, rig: _Rig, store: Path, share_url: str | None) -> None:
    """焼く形で 1 回焼いて保存先(と分け合う保存先)を満たす。"""
    seed = tmp_path / "seed" / "app"
    _write_four(seed, tmp_path.name)
    baked = _run_tool(seed, store, rig, share_url, fetch_only=False)
    assert baked.returncode == 0, baked.stderr
    assert baked.compiled == sorted(_HY_SOURCES), baked.compiled


# --- 効果の層: BakeSources 0 回・bytecode_pool 起動 0 回を答え手が数える -------------------------


@dataclass
class _CallLog:
    """取るだけの形の実行で、焼きの効果(BakeSources)と子 process(RunProcess / bytecode_pool)と報告の行を数える記録。"""

    bake_calls: int = 0
    process_calls: int = 0
    pool_launches: int = 0
    notes: list[str] = field(default_factory=list)


class _OutcomeView(Protocol):
    named: str
    stored: int
    rebuilt: int
    reused: int
    failed: int
    fetched: int
    problem: str | None


def _run_prepare_fetch_only(
    tree: Path,
    store: Path,
    share_file: Path | None,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[tuple[_OutcomeView, ...], _CallLog]:
    """prepare_trees(..., fetch_only=True) を本物の store_fetcher と、BakeSources・RunProcess を数える答え手で回す。"""
    for mod in ("mac", "mac.macros", "use", "use.user", "imp", "imp.importer", "other", "other.plain"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    if share_file is None:
        monkeypatch.delenv("DOEFF_HY_CODE_SHARE", raising=False)
    else:
        monkeypatch.setenv("DOEFF_HY_CODE_SHARE", str(share_file))

    log = _CallLog()

    @handler
    def counting_bake(effect: object, k: K) -> object:
        if isinstance(effect, cp.BakeSources):
            log.bake_calls += 1
            return (yield Resume(k, cp.plan.BakeAnswer(failed=(), stored=(), reused=(), unstored=())))
        yield Pass(effect, k)

    @handler
    def counting_process(effect: object, k: K) -> object:
        if isinstance(effect, RunProcess):
            log.process_calls += 1
            if any("bytecode_pool" in str(part) for part in effect.argv):
                log.pool_launches += 1
            out = yield effect
            return (yield Resume(k, out))
        yield Pass(effect, k)

    @handler
    def collect_slog(effect: object, k: K) -> object:
        if isinstance(effect, Slog):
            log.notes.append(str(effect.msg))
            return (yield Resume(k, None))
        yield Pass(effect, k)

    @do
    def program() -> object:
        shaped = yield cp.plan.tree_arguments((str(tree),), (".",))
        assert isinstance(shaped, tuple), shaped
        trees = yield cp.bake_trees(shaped)
        return (yield cp.prepare_trees(trees, "rev", 1, (), str(store), hy.__version__, fetch_only=True))

    result = run(
        with_handlers(
            [
                sim_time_handler(clock=SimClock()),
                collect_slog,
                os_file_handler,
                subprocess_handler,
                counting_process,
                tree_files,
                counting_bake,
                cp.store_entries,
                cp.closure_reads,
                cp.progress_marks(None),
                cp.store_fetcher(None),
            ],
            program(),
        )
    )
    assert isinstance(result, tuple), result
    outcomes: tuple[_OutcomeView, ...] = result
    return outcomes, log


def test_a_effect_fetch_only_all_present_calls_zero_bakes_and_zero_pool_launches(
    tmp_path: Path, rig: _Rig, share: _Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    """分け合う保存先に全部在る木で、BakeSources 0 回・bytecode_pool 起動 0 回・rebuilt=0・.pyc 配置。"""
    _seed_via_normal_bake(tmp_path, rig, tmp_path / "seed-store", share.url)
    tree, store = tmp_path / "target" / "app", tmp_path / "empty-store"
    _write_four(tree, tmp_path.name)
    share_file = _write_share_file(tmp_path / "share.cfg", share.url, rig.keys)

    outcomes, log = _run_prepare_fetch_only(tree, store, share_file, monkeypatch)

    assert log.bake_calls == 0, f"BakeSources was called {log.bake_calls} times"
    assert log.pool_launches == 0, log.pool_launches
    assert log.process_calls == 0, log.process_calls
    assert len(outcomes) == 1, outcomes
    outcome = outcomes[0]
    assert outcome.problem is None, outcome
    assert outcome.rebuilt == 0, outcome
    assert outcome.failed == 0, outcome
    assert outcome.fetched >= len(_HY_SOURCES), outcome
    assert (tree / MARKER).exists()
    assert any(f"tree={tree} " in line and "rebuilt=0" in line for line in log.notes), log.notes
    assert any("compile_s=" in line and "rebuilt=0" in line for line in log.notes), log.notes


def test_b_effect_fetch_only_missing_file_calls_zero_bakes_and_zero_pool_launches(
    tmp_path: Path, rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取れない file が在る木で、BakeSources 0 回・bytecode_pool 起動 0 回・数と最初の file の 1 行。"""
    store = tmp_path / "store"
    _seed_via_normal_bake(tmp_path, rig, store, None)
    tree = tmp_path / "target" / "app"
    _write_four(tree, tmp_path.name, importer=99, plain=88)

    outcomes, log = _run_prepare_fetch_only(tree, store, None, monkeypatch)

    assert log.bake_calls == 0, f"BakeSources was called {log.bake_calls} times"
    assert log.pool_launches == 0, log.pool_launches
    assert log.process_calls == 0, log.process_calls
    assert len(outcomes) == 1, outcomes
    outcome = outcomes[0]
    assert outcome.problem is not None, outcome
    assert "\n" not in outcome.problem, outcome
    assert "2" in outcome.problem, outcome.problem
    assert "imp/importer.hy" in outcome.problem, outcome.problem
    assert outcome.rebuilt == 0, outcome
    assert not (tree / MARKER).exists()


def test_c_effect_fetch_only_share_unusable_calls_zero_bakes_and_zero_pool_launches(
    tmp_path: Path, rig: _Rig, share: _Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    """分け合う保存先が ShareUnusable(初期確認の後に HTTP 500)でも BakeSources 0 回・bytecode_pool 起動 0 回。"""
    share.bucket.fail_after_lists = 1
    tree, store = tmp_path / "target" / "app", tmp_path / "empty-store"
    _write_four(tree, tmp_path.name)
    share_file = _write_share_file(tmp_path / "share.cfg", share.url, rig.keys)

    outcomes, log = _run_prepare_fetch_only(tree, store, share_file, monkeypatch)

    assert log.bake_calls == 0, f"BakeSources was called {log.bake_calls} times"
    assert log.pool_launches == 0, log.pool_launches
    assert log.process_calls == 0, log.process_calls
    assert len(outcomes) == 1, outcomes
    outcome = outcomes[0]
    assert outcome.problem is not None, outcome
    assert "\n" not in outcome.problem, outcome
    assert "HTTP 500" in outcome.problem, outcome.problem
    assert outcome.rebuilt == 0, outcome
    assert not (tree / MARKER).exists()


# --- CLI の層: 終了 code・標準エラーの 1 行の訳・.pyc の配置 ------------------------------------


def test_d_cli_fetch_only_from_local_store_exits_0_with_rebuilt_0(tmp_path: Path, rig: _Rig) -> None:
    """機体の保存先に全部在る木は --fetch-only で焼かずに .pyc を置いて 0 で終わり rebuilt=0。"""
    store = tmp_path / "store"
    _seed_via_normal_bake(tmp_path, rig, store, None)
    tree = tmp_path / "target" / "app"
    _write_four(tree, tmp_path.name)

    fetched = _run_tool(tree, store, rig, None, fetch_only=True)

    assert fetched.returncode == 0, fetched.stderr
    assert fetched.compiled == [], fetched.compiled
    assert fetched.counts.get("rebuilt") == 0, fetched.counts
    assert fetched.counts.get("failed") == 0, fetched.counts
    assert fetched.counts.get("stored", 0) >= len(_HY_SOURCES), fetched.counts
    assert "rebuilt=0" in next(line for line in fetched.stderr.splitlines() if "compile_s=" in line)
    assert (tree / MARKER).exists()
    assert _import_value(tree, store, rig) == 2


def test_e_cli_fetch_only_from_shared_store_exits_0_with_rebuilt_0(tmp_path: Path, rig: _Rig, share: _Share) -> None:
    """分け合う保存先に全部在る木は空の保存先からでも --fetch-only で .pyc を置いて 0 で終わり rebuilt=0。"""
    _seed_via_normal_bake(tmp_path, rig, tmp_path / "seed-store", share.url)
    tree, store = tmp_path / "target" / "app", tmp_path / "empty-store"
    _write_four(tree, tmp_path.name)

    fetched = _run_tool(tree, store, rig, share.url, fetch_only=True)

    assert fetched.returncode == 0, fetched.stderr
    assert set(fetched.compiled) <= _CHECK_COMPILES, fetched.compiled
    assert fetched.counts.get("rebuilt") == 0, fetched.counts
    assert fetched.counts.get("failed") == 0, fetched.counts
    assert fetched.counts.get("fetched", 0) >= len(_HY_SOURCES), fetched.counts
    assert "rebuilt=0" in next(line for line in fetched.stderr.splitlines() if "compile_s=" in line)
    assert (tree / MARKER).exists()
    assert _import_value(tree, store, rig) == 2


def test_f_cli_fetch_only_missing_files_exits_fixed_code_with_one_line_reason(tmp_path: Path, rig: _Rig) -> None:
    """取れない file が在る木では焼かずに取れなかった数と最初の file を 1 行で stderr に出し、決まった 0 でない code。"""
    store = tmp_path / "store"
    _seed_via_normal_bake(tmp_path, rig, store, None)
    tree = tmp_path / "target" / "app"
    _write_four(tree, tmp_path.name, importer=99, plain=88)

    fetched = _run_tool(tree, store, rig, None, fetch_only=True)

    assert cp.FETCH_ONLY_EXIT_CODE != 0
    assert fetched.returncode == cp.FETCH_ONLY_EXIT_CODE, (fetched.returncode, fetched.stderr)
    assert fetched.compiled == [], fetched.compiled
    reason_lines = [line for line in fetched.stderr.splitlines() if line.startswith("fetch-only:")]
    assert len(reason_lines) == 1, fetched.stderr
    assert "2" in reason_lines[0], reason_lines[0]
    assert "imp/importer.hy" in reason_lines[0], reason_lines[0]
    assert not (tree / MARKER).exists()


def test_g_cli_fetch_only_share_unusable_exits_same_code_with_one_line_reason(
    tmp_path: Path, rig: _Rig, share: _Share
) -> None:
    """分け合う保存先が届かない・403・途中で HTTP 500(ShareUnusable)の時も焼かずに 1 行の訳と同じ終了 code。"""
    cases = [
        ("unreachable", f"http://127.0.0.1:{_closed_port()}/{_BUCKET}/bytecode", rig.keys, None, "cannot reach"),
        ("forbidden", share.url, rig.wrong_keys, None, "HTTP 403"),
        ("mid-fetch-500", share.url, rig.keys, 1, "HTTP 500"),
    ]
    for name, url, keys, fail_after, expected_token in cases:
        share.bucket.fail_after_lists = fail_after
        share.bucket.list_count = 0
        tree, store = tmp_path / name / "app", tmp_path / name / "empty-store"
        _write_four(tree, f"{tmp_path.name}-{name}")

        fetched = _run_tool(tree, store, rig, url, keys, fetch_only=True)

        assert fetched.returncode == cp.FETCH_ONLY_EXIT_CODE, (name, fetched.returncode, fetched.stderr)
        assert fetched.compiled == [], (name, fetched.compiled)
        reason_lines = [line for line in fetched.stderr.splitlines() if line.startswith("fetch-only:")]
        assert len(reason_lines) == 1, (name, fetched.stderr)
        assert expected_token in reason_lines[0], (name, reason_lines[0])
        assert not (tree / MARKER).exists(), name


def test_h_cli_fetch_only_supports_pycache_prefix_and_entries(tmp_path: Path, rig: _Rig, share: _Share) -> None:
    """--pycache-prefix と --entries の組み合わせ(宣言の道具の呼び方)でも焼かずに置き場へ .pyc と完成の印を置く。"""
    _seed_via_normal_bake(tmp_path, rig, tmp_path / "seed-store", share.url)
    tree, store, pycache = tmp_path / "target" / "app", tmp_path / "empty-store", tmp_path / "root-pycache"
    _write_four(tree, tmp_path.name)

    fetched = _run_tool(
        tree, store, rig, share.url, fetch_only=True,
        extra_args=("--pycache-prefix", str(pycache), "--entries", "use.user"),
    )

    assert fetched.returncode == 0, fetched.stderr
    assert set(fetched.compiled) <= _CHECK_COMPILES, fetched.compiled
    assert fetched.counts.get("rebuilt") == 0, fetched.counts
    assert fetched.counts.get("failed") == 0, fetched.counts
    assert not list(tree.rglob("*.pyc")), "木の中に .pyc を書いていない事"
    assert list(pycache.rglob("*.pyc")), "置き場に .pyc が置かれている事"
    records = pycache / str(tree).lstrip("/")
    assert (records / MARKER).exists(), "置き場の記録の dir に完成の印が在る事"
