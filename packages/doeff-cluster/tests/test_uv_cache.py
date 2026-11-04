"""uv の cache の用意と片付けの道具 deploy/uv_cache.py と、それを起動の準備の終わりに背後で起こす boot.sh の doeff_uv_cache_upkeep を守る検。

道具そのものを、検ごとの一時の dir に作った cache の形(uv の bucket の名だけを真似た dir と file)で実際に走らせる:
- 署名: SigV4 の答えが AWS の文書の例(GET Object・Range つき)の署名と一致する。
- seed: pack は錠・作りかけ・組みの一時の環境・`uv run --with` の環境・この道具の印を入れない。入れる時は cache に無い物だけを入れ、在る
  file を替えない。入れ終えたら印を書き、印が在れば 2 度目は入れない。HTTP の置き場からは Range の GET を並べて取り寄せ、片の ETag が
  HEAD と違えば入れない。
- gc: 消すのは、最後に使った刻が日数より前で — 展開済みの wheel は hardlink の数が 1 の物・venv と環境はどの process も使っていない物・
  一時の物。同じ回に venv を消して hardlink の数が 1 に戻った展開済みの wheel も消す。count は何も消さない。前の gc から間が無ければ消さない。
  予算を渡すと、cache だけが持つ byte(venv が hardlink で持つ中身を除き、cache の中で共に持つ中身は 1 度)が予算を越える分を、
  floor を過ぎた古い項目から消し、venv が hardlink か symlink で指す項目・process が読み込んだ項目は消さない。
- 置き場: s3://<bucket>/<key> の URL は Secret の鍵 endpoint(Secret が無ければ AWS_ENDPOINT_URL_S3)へ送り、upkeep は Secret の名
  だけを替えると別の置き場から入れ、戻すと元の置き場から入れる。endpoint が無ければ断る。http(s) の URL と file の path はそのまま。
- upkeep: 環境変数を読んで seed と gc を順に撃ち、seed が落ちても gc を撃つ。空きが下限を下回れば seed しない。refresh は名指した node でだけ撃つ。
- refresh: 空の cache に project ごとの取り込みを撃ち(venv は project の外)、組みの一時の環境を除いて pack し、lock の hash を
  metadata に載せて PUT する。置いてある写しの hash が同じか、置いてから間が無ければ組まない。取り込みが落ちれば PUT しない。
- boot.sh: doeff_uv_cache_upkeep は役 worker と prepare で、値が環境に在る時だけ道具を背後で起こし、標準出力に何も出さない。
"""

from __future__ import annotations

import datetime
import email.utils
import http.server
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
TOOL = PACKAGE / "deploy" / "uv_cache.py"
BOOT = PACKAGE / "deploy" / "boot.sh"
OLD = time.time() - 30 * 86400


def _load():
    spec = importlib.util.spec_from_file_location("uv_cache_under_test", TOOL)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


uv_cache = _load()


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _age(path: Path, when: float = OLD) -> None:
    """path とその下の全部の最後の変更と読みの刻を when にする(下から — dir の刻を後で書く)。"""
    for top, dirs, files in os.walk(path, topdown=False):
        for name in [*files, *dirs]:
            os.utime(Path(top, name), (when, when), follow_symlinks=False)
    os.utime(path, (when, when), follow_symlinks=False)


def _venv(path: Path) -> Path:
    _write(path / "pyvenv.cfg", "home = /usr\n")
    (path / "bin").mkdir(parents=True, exist_ok=True)
    (path / "lib" / "python3.14" / "site-packages").mkdir(parents=True, exist_ok=True)
    return path


# ── 署名 ──────────────────────────────────────────────────────────────────


def test_sigv4_matches_the_aws_get_object_example() -> None:
    """AWS の文書「Signature Calculations for the Authorization Header」の GET Object の例と同じ署名になる。"""
    headers = uv_cache.sigv4_headers(
        "GET",
        "https://examplebucket.s3.amazonaws.com/test.txt",
        "us-east-1",
        uv_cache.Credentials("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
        datetime.datetime(2013, 5, 24, tzinfo=datetime.UTC),
        payload_hash=uv_cache.EMPTY_SHA256,
        headers={"Range": "bytes=0-9"},
    )
    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


# ── pack と seed ──────────────────────────────────────────────────────────


@pytest.fixture
def source_cache(tmp_path: Path) -> Path:
    """置き場へ上げる元の cache: 展開済みの wheel 2 つ・索引 2 つと、pack が入れない物。"""
    cache = tmp_path / "source"
    _write(cache / "archive-v0" / "AAA" / "pkg_a" / "__init__.py", "a = 1\n")
    _write(cache / "archive-v0" / "BBB" / "pkg_b" / "__init__.py", "b = 2\n")
    _write(cache / "wheels-v6" / "pypi" / "pkg-a" / "a.http", "seed-a\n")
    _write(cache / "wheels-v6" / "pypi" / "pkg-b" / "b.http", "seed-b\n")
    _write(cache / "CACHEDIR.TAG", "Signature: 8a477f597d28d172789f06886806bc55\n")
    _write(cache / ".lock", "")
    _write(cache / ".tmpXYZ" / "half", "x")
    _write(cache / "builds-v0" / ".tmp1" / "x", "x")
    _write(cache / "environments-v2" / "env" / "pyvenv.cfg", "x")
    _write(cache / "interpreter-v4" / "i.msgpack", "x")
    _write(cache / uv_cache.GC_MARK, "{}")
    return cache


def test_pack_leaves_out_locks_partial_and_node_local_buckets(
    source_cache: Path, tmp_path: Path
) -> None:
    packed = uv_cache.pack(source_cache, tmp_path / "seed.tar.zst")
    assert packed["members"] == ["archive-v0", "CACHEDIR.TAG", "wheels-v6"]


def test_seed_adds_only_missing_entries_and_marks_the_cache(
    source_cache: Path, tmp_path: Path
) -> None:
    packed = tmp_path / "seed.tar.zst"
    uv_cache.pack(source_cache, packed)
    target = tmp_path / "target"
    # 走っている uv が書いた索引と展開済みの wheel(seed の物と同じ名)— 替えない。
    _write(target / "wheels-v6" / "pypi" / "pkg-a" / "a.http", "node-a\n")
    _write(target / "archive-v0" / "AAA" / "pkg_a" / "__init__.py", "node = 1\n")

    first = uv_cache.seed(target, str(packed), None, "us-east-1", 10.0, force=False)

    assert first["seeded"] is True
    assert (target / "wheels-v6" / "pypi" / "pkg-a" / "a.http").read_text() == "node-a\n"
    assert (target / "archive-v0" / "AAA" / "pkg_a" / "__init__.py").read_text() == "node = 1\n"
    assert (target / "wheels-v6" / "pypi" / "pkg-b" / "b.http").read_text() == "seed-b\n"
    assert (target / "archive-v0" / "BBB" / "pkg_b" / "__init__.py").read_text() == "b = 2\n"
    assert not list(target.glob(uv_cache.SEED_STAGE + "*"))
    assert (target / uv_cache.SEED_MARK).exists()

    second = uv_cache.seed(target, str(packed), None, "us-east-1", 10.0, force=False)
    assert second == {"verb": "seed", "seeded": False, "reason": "marked"}


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """HEAD と Range の GET だけを答える object の置き場の代わり(etag を差し替えると片の ETag が HEAD と食い違う)。"""

    body = b""
    etag = '"v1"'
    part_etag = '"v1"'
    ranges: tuple[str, ...] = ()
    # 署名の access key の id(Authorization の Credential=<id>/…)を GET ごとに並べる。
    keys: tuple[str, ...] = ()
    lock = threading.Lock()

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.send_header("ETag", self.etag)
        self.end_headers()

    def do_GET(self) -> None:
        found = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
        assert found is not None
        start, end = int(found[1]), int(found[2])
        signed = re.search(r"Credential=([^/]+)/", self.headers.get("Authorization", ""))
        with self.lock:
            type(self).ranges = (*type(self).ranges, self.headers["Range"])
            type(self).keys = (*type(self).keys, signed[1] if signed else "")
        chunk = self.body[start : end + 1]
        self.send_response(206)
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("ETag", self.part_etag)
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, *args: object) -> None:
        return None


@pytest.fixture
def store(source_cache: Path, tmp_path: Path) -> Iterator[str]:
    packed = tmp_path / "seed.tar.zst"
    uv_cache.pack(source_cache, packed)
    _RangeHandler.body = packed.read_bytes()
    _RangeHandler.etag = _RangeHandler.part_etag = '"v1"'
    _RangeHandler.ranges = ()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/bucket/seed.tar.zst"
    finally:
        server.shutdown()
        server.server_close()


def test_seed_over_http_fetches_parallel_ranges(store: str, tmp_path: Path) -> None:
    target = tmp_path / "target"
    result = uv_cache.seed(target, store, None, "us-east-1", 10.0, force=False, workers=3, part=97)
    assert result["seeded"] is True
    assert result["etag"] == "v1"
    assert len(_RangeHandler.ranges) == -(-len(_RangeHandler.body) // 97)
    assert (target / "archive-v0" / "BBB" / "pkg_b" / "__init__.py").read_text() == "b = 2\n"


def test_seed_over_http_refuses_parts_of_another_object(store: str, tmp_path: Path) -> None:
    _RangeHandler.part_etag = '"v2"'
    target = tmp_path / "target"
    with pytest.raises(SystemExit, match="ETag"):
        uv_cache.seed(target, store, None, "us-east-1", 10.0, force=False, workers=2, part=97)
    assert not (target / uv_cache.SEED_MARK).exists()
    assert not list(target.glob(uv_cache.SEED_STAGE + "*"))
    assert not (target / "archive-v0").exists()


# ── gc ────────────────────────────────────────────────────────────────────


@pytest.fixture
def busy(tmp_path: Path) -> Iterator[Path]:
    """作業 dir が busy の dir の下に在る process(gc はその下の venv と環境を使っていると見る)。"""
    place = tmp_path / "busy"
    place.mkdir()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=place)
    try:
        yield place
    finally:
        child.kill()
        child.wait()


def _cache_for_gc(tmp_path: Path, busy: Path) -> tuple[Path, Path]:
    cache = tmp_path / "cache"
    worktrees = tmp_path / "worktrees"
    _write(cache / "archive-v0" / "OLD" / "m.py", "old\n")
    young = _write(cache / "archive-v0" / "YOUNG" / "m.py", "young\n")
    shared = _write(cache / "archive-v0" / "SHARED" / "m.py", "shared\n")
    freed = _write(cache / "archive-v0" / "FREED" / "m.py", "freed\n")
    _write(cache / ".tmpOLD" / "x", "x")
    _venv(cache / "environments-v2" / "idle")
    _venv(cache / "environments-v2" / "inuse")
    # 使っている作業木の venv(shared を hardlink で指す)・使っていない古い venv(freed を指す)・新しい venv。
    used = _venv(busy / ".venv")
    os.link(shared, used / "lib" / "python3.14" / "site-packages" / "m.py")
    idle = _venv(worktrees / "wt-idle" / ".venv")
    os.link(freed, idle / "lib" / "python3.14" / "site-packages" / "m.py")
    _venv(worktrees / "wt-young" / ".venv")
    for path in [
        cache / "archive-v0" / "OLD",
        cache / "archive-v0" / "SHARED",
        cache / "archive-v0" / "FREED",
        cache / ".tmpOLD",
        cache / "environments-v2" / "idle",
        cache / "environments-v2" / "inuse",
        used,
        idle,
    ]:
        _age(path)
    os.utime(young.parent, None)
    return cache, worktrees


def test_gc_removes_only_old_unshared_and_unused_units(
    tmp_path: Path, busy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache, worktrees = _cache_for_gc(tmp_path, busy)
    real = uv_cache.process_paths()
    assert real is not None
    assert str(busy) in real
    monkeypatch.setattr(
        uv_cache,
        "process_paths",
        lambda _proc=None: sorted([*real, str(cache / "environments-v2" / "inuse")]),
    )

    result = uv_cache.gc(cache, [worktrees, busy], 7.0, 3, dry_run=False, every_seconds=None)

    left = sorted(p.name for p in (cache / "archive-v0").iterdir())
    assert left == ["SHARED", "YOUNG"]
    assert sorted(p.name for p in (cache / "environments-v2").iterdir()) == ["inuse"]
    assert not (cache / ".tmpOLD").exists()
    assert not (worktrees / "wt-idle" / ".venv").exists()
    assert (worktrees / "wt-idle").is_dir()
    assert (worktrees / "wt-young" / ".venv").is_dir()
    assert (busy / ".venv").is_dir()
    assert result["removed"] == {"venvs": 1, "environments": 1, "temporaries": 1, "archives": 2}
    assert result["before"]["archives"]["all"]["items"] == 4
    assert result["after"]["archives"]["all"]["items"] == 2
    assert not list(cache.glob(uv_cache.GC_TRASH + "*"))
    assert (cache / uv_cache.GC_MARK).exists()


def test_count_removes_nothing(tmp_path: Path, busy: Path) -> None:
    cache, worktrees = _cache_for_gc(tmp_path, busy)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    result = uv_cache.gc(cache, [worktrees], 7.0, 3, dry_run=True, every_seconds=None)
    # 足すのは錠の file だけ(同じ cache の gc と並ばない)。
    assert sorted(str(p) for p in tmp_path.rglob("*") if p.name != uv_cache.GC_LOCK) == before
    assert (
        result["before"]["archives"]["doomed"]["items"] == 1
    )  # OLD だけ — FREED は venv がまだ指す
    assert result["before"]["venvs"]["doomed"]["items"] == 1


def test_gc_waits_out_the_interval(tmp_path: Path, busy: Path) -> None:
    cache, worktrees = _cache_for_gc(tmp_path, busy)
    assert uv_cache.gc(cache, [worktrees], 7.0, 3, dry_run=False, every_seconds=3600)["ran"] is True
    assert uv_cache.gc(cache, [worktrees], 7.0, 3, dry_run=False, every_seconds=3600) == {
        "verb": "gc",
        "ran": False,
        "reason": "recent",
    }


def _symlinked_venv(venv: Path, entry: Path, *, relative: bool = False) -> Path:
    """UV_LINK_MODE=symlink の形の venv: site-packages の file が、展開済みの wheel の項目の中の file への symlink。"""
    _venv(venv)
    link = venv / "lib" / "python3.14" / "site-packages" / "m.py"
    target = entry / "m.py"
    link.symlink_to(os.path.relpath(target, link.parent) if relative else target)
    return venv


def test_gc_keeps_entries_that_kept_symlink_venvs_point_at(tmp_path: Path) -> None:
    """symlink の venv は項目の hardlink の数を増やさない。残る venv(新しい・--links の置き場の物)と残る環境が symlink で指す項目は
    消さず、消す venv だけが指していた項目は同じ回に消す(会話の .venv は UV_LINK_MODE=symlink で作る)。"""
    cache = tmp_path / "cache"
    entries = {
        name: _write(cache / "archive-v0" / name / "m.py", f"{name}\n").parent
        for name in ("YOUNGV", "HELD", "ENV", "IDLEV")
    }
    worktrees, held = tmp_path / "worktrees", tmp_path / "volumes"
    _symlinked_venv(worktrees / "wt-young" / ".venv", entries["YOUNGV"])
    idle = _symlinked_venv(worktrees / "wt-idle" / ".venv", entries["IDLEV"])
    holder = _symlinked_venv(held / "c-1" / ".venv", entries["HELD"], relative=True)
    environment = _symlinked_venv(cache / "environments-v2" / "young-env", entries["ENV"])
    for path in [*entries.values(), idle, holder]:
        _age(path)
    os.utime(environment, None)

    result = uv_cache.gc(
        cache, [worktrees], 7.0, 3, dry_run=False, every_seconds=None, link_roots=[held]
    )

    assert sorted(p.name for p in (cache / "archive-v0").iterdir()) == ["ENV", "HELD", "YOUNGV"]
    assert not idle.exists()
    assert holder.is_dir()
    assert result["removed"] == {"venvs": 1, "environments": 0, "temporaries": 0, "archives": 1}
    assert (
        worktrees / "wt-young" / ".venv" / "lib" / "python3.14" / "site-packages" / "m.py"
    ).read_text() == "YOUNGV\n"


def test_gc_keeps_entries_a_running_process_has_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """symlink の venv の拡張 module は、読み込んだ process の maps に項目の中の path で出る — その項目は消さない。"""
    cache = tmp_path / "cache"
    loaded = _write(cache / "archive-v0" / "LOADED" / "ext.so", "elf\n")
    _write(cache / "archive-v0" / "OLD" / "m.py", "old\n")
    _age(cache / "archive-v0")
    monkeypatch.setattr(uv_cache, "process_paths", lambda _proc=None: [str(loaded)])

    uv_cache.gc(cache, [], 7.0, 3, dry_run=False, every_seconds=None)

    assert sorted(p.name for p in (cache / "archive-v0").iterdir()) == ["LOADED"]


DAY = 86400


def _cache_for_size_budget(tmp_path: Path) -> tuple[Path, Path]:
    """予算の消しの cache: 期限(7 日)の内で floor(1 日)を過ぎた項目と、守る項目。

    OLDEST(5 日)・OLDER(4 日)・OLD(3 日)は cache だけが持つ展開済みの wheel。ENVLINK(3 日)は環境 env(3 日)と同じ中身を
    hardlink で持つ(cache の中だけの共有)。PINNED(6 日)は新しい作業木の venv が hardlink で指す 4 MiB。SYMLINKED(6 日)は
    新しい作業木の venv が symlink で指す。LOADED(6 日)は走っている process が読み込んだ。YOUNG は floor の内。
    """
    cache = tmp_path / "cache"
    worktrees = tmp_path / "worktrees"
    archive = cache / "archive-v0"
    for name in ("OLDEST", "OLDER", "OLD", "SYMLINKED", "LOADED", "YOUNG"):
        _write(archive / name / "m.py", name[0] * 65536)
    shared = _write(archive / "ENVLINK" / "m.py", "e" * 65536)
    env = _venv(cache / "environments-v2" / "env")
    os.link(shared, env / "lib" / "python3.14" / "site-packages" / "m.py")
    pinned = _write(archive / "PINNED" / "big.bin", "p" * (4 << 20))
    venv = _venv(worktrees / "wt-young" / ".venv")
    os.link(pinned, venv / "lib" / "python3.14" / "site-packages" / "big.bin")
    (venv / "lib" / "python3.14" / "site-packages" / "s.py").symlink_to(
        archive / "SYMLINKED" / "m.py"
    )
    now = time.time()
    for name, days in [
        ("OLDEST", 5),
        ("OLDER", 4),
        ("OLD", 3),
        ("ENVLINK", 3),
        ("PINNED", 6),
        ("SYMLINKED", 6),
        ("LOADED", 6),
    ]:
        _age(archive / name, now - days * DAY)
    _age(env, now - 3 * DAY)
    return cache, worktrees


def _size_budget_gc(cache: Path, worktrees: Path, size_budget: int) -> dict:
    return uv_cache.gc(
        cache,
        [worktrees],
        7.0,
        3,
        dry_run=False,
        every_seconds=None,
        size_budget=size_budget,
        floor_days=1.0,
    )


@pytest.fixture
def loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ext = tmp_path / "cache" / "archive-v0" / "LOADED" / "m.py"
    monkeypatch.setattr(uv_cache, "process_paths", lambda _proc=None: [str(ext)])


def test_gc_size_budget_counts_only_bytes_the_cache_alone_holds(
    tmp_path: Path, loaded: None
) -> None:
    """venv が hardlink で持つ PINNED の 4 MiB は数えず、展開済みの wheel と環境が共に持つ中身は 1 度だけ数える。予算の内なら消さない。"""
    cache, worktrees = _cache_for_size_budget(tmp_path)

    result = _size_budget_gc(cache, worktrees, 1 << 40)

    size_budget = result["size-budget"]
    assert size_budget["usage-before"] < 4 << 20
    assert 7 * 65536 <= size_budget["usage-before"] < 7 * 65536 + 64 * 4096
    assert size_budget["removed"] == {"archives": 0, "environments": 0, "temporaries": 0}
    assert size_budget["freed"] == 0
    assert size_budget["within"] is True
    assert size_budget["floor-days"] == 1.0
    assert len(list((cache / "archive-v0").iterdir())) == 8
    assert json.loads((cache / uv_cache.GC_MARK).read_text())["size-budget"] == size_budget


def test_gc_size_budget_removes_the_oldest_first_until_within(tmp_path: Path, loaded: None) -> None:
    """和が予算を 1 byte 越えるだけなら、最も古い消せる項目(OLDEST — 消せない PINNED・SYMLINKED・LOADED はより古い)だけを消す。"""
    cache, worktrees = _cache_for_size_budget(tmp_path)
    usage = _size_budget_gc(cache, worktrees, 1 << 40)["size-budget"]["usage-before"]

    size_budget = _size_budget_gc(cache, worktrees, usage - 1)["size-budget"]

    assert not (cache / "archive-v0" / "OLDEST").exists()
    assert sorted(p.name for p in (cache / "archive-v0").iterdir()) == [
        "ENVLINK",
        "LOADED",
        "OLD",
        "OLDER",
        "PINNED",
        "SYMLINKED",
        "YOUNG",
    ]
    assert size_budget["removed"] == {"archives": 1, "environments": 0, "temporaries": 0}
    assert size_budget["freed"] == usage - size_budget["usage-after"] > 0
    assert size_budget["within"] is True


def test_gc_size_budget_never_removes_held_or_young_units(tmp_path: Path, loaded: None) -> None:
    """予算 0 でも、venv が hardlink か symlink で指す項目・走っている process が読み込んだ項目・floor の内の項目は消さない。
    展開済みの wheel と環境が共に持つ中身は、両方を消して戻った byte に数える。"""
    cache, worktrees = _cache_for_size_budget(tmp_path)

    result = _size_budget_gc(cache, worktrees, 0)

    size_budget = result["size-budget"]
    assert sorted(p.name for p in (cache / "archive-v0").iterdir()) == [
        "LOADED",
        "PINNED",
        "SYMLINKED",
        "YOUNG",
    ]
    assert not list((cache / "environments-v2").iterdir())
    assert size_budget["removed"] == {"archives": 4, "environments": 1, "temporaries": 0}
    assert size_budget["freed"] >= 4 * 65536
    assert size_budget["within"] is False
    venv = worktrees / "wt-young" / ".venv" / "lib" / "python3.14" / "site-packages"
    assert (venv / "s.py").read_text() == "S" * 65536
    assert (venv / "big.bin").stat().st_size == 4 << 20


def test_gc_without_a_size_budget_only_expires(tmp_path: Path, loaded: None) -> None:
    cache, worktrees = _cache_for_size_budget(tmp_path)
    result = uv_cache.gc(cache, [worktrees], 7.0, 3, dry_run=False, every_seconds=None)
    assert "size_budget" not in result
    assert len(list((cache / "archive-v0").iterdir())) == 8


def test_gc_keeps_venvs_when_processes_are_unreadable(tmp_path: Path, busy: Path) -> None:
    cache, worktrees = _cache_for_gc(tmp_path, busy)
    uv_cache.gc(
        cache, [worktrees], 7.0, 3, dry_run=False, every_seconds=None, proc=tmp_path / "no-proc"
    )
    assert (worktrees / "wt-idle" / ".venv").is_dir()
    assert sorted(p.name for p in (cache / "environments-v2").iterdir()) == ["idle", "inuse"]


# ── upkeep ────────────────────────────────────────────────────────────────


def test_upkeep_reads_the_environment() -> None:
    settings = uv_cache.UpkeepSettings.from_env(
        {
            "DOEFF_UV_CACHE_SEED_URL": "http://store/b/seed.tar.zst",
            "DOEFF_UV_CACHE_SEED_SECRET": "ns/name",
            "DOEFF_UV_CACHE_SEED_MIN_FREE_GIB": "2",
            "DOEFF_UV_CACHE_GC_DAYS": "3",
            "DOEFF_UV_CACHE_GC_VENVS": "/a:/b",
            "DOEFF_UV_CACHE_GC_EVERY_SECONDS": "60",
            "DOEFF_UV_CACHE_GC_LINK_ROOTS": "/c",
        }
    )
    assert settings == uv_cache.UpkeepSettings(
        "http://store/b/seed.tar.zst",
        "ns/name",
        2 << 30,
        3.0,
        (Path("/a"), Path("/b")),
        60.0,
        (Path("/c"),),
    )
    assert uv_cache.UpkeepSettings.from_env({}) == uv_cache.UpkeepSettings()
    capped = uv_cache.UpkeepSettings.from_env(
        {"DOEFF_UV_CACHE_GC_SIZE_BUDGET_GIB": "100", "DOEFF_UV_CACHE_GC_FLOOR_DAYS": "2"}
    )
    assert (capped.gc_size_budget, capped.gc_floor_days) == (100 << 30, 2.0)
    assert (settings.gc_size_budget, settings.gc_floor_days) == (None, 1.0)


def test_upkeep_seeds_then_collects(source_cache: Path, tmp_path: Path) -> None:
    packed = tmp_path / "seed.tar.zst"
    uv_cache.pack(source_cache, packed)
    target = tmp_path / "target"
    result = uv_cache.upkeep(
        target, uv_cache.UpkeepSettings(seed_url=str(packed), seed_min_free=0, gc_days=7.0)
    )
    assert result["seed"]["seeded"] is True
    assert result["gc"]["ran"] is True


def test_upkeep_skips_seeding_below_the_free_floor_and_still_collects(
    source_cache: Path, tmp_path: Path
) -> None:
    packed = tmp_path / "seed.tar.zst"
    uv_cache.pack(source_cache, packed)
    target = tmp_path / "target"
    result = uv_cache.upkeep(
        target, uv_cache.UpkeepSettings(seed_url=str(packed), seed_min_free=1 << 60, gc_days=7.0)
    )
    assert result["seed"] == {"seeded": False, "reason": "low-free"}
    assert result["gc"]["ran"] is True
    assert not (target / "archive-v0").exists()


def test_upkeep_collects_when_seeding_fails(tmp_path: Path) -> None:
    target = tmp_path / "target"
    result = uv_cache.upkeep(
        target,
        uv_cache.UpkeepSettings(
            seed_url=str(tmp_path / "missing.tar.zst"), seed_min_free=0, gc_days=7.0
        ),
    )
    assert result["seed"]["reason"] == "failed"
    assert result["gc"]["ran"] is True


def test_upkeep_refreshes_only_on_the_named_node() -> None:
    env = {
        "DOEFF_UV_CACHE_SEED_URL": "http://store/b/seed.tar.zst",
        "DOEFF_UV_CACHE_SEED_REFRESH_NODE": "n1",
        "DOEFF_UV_CACHE_SEED_REFRESH_PROJECTS": "/p/a:/p/b",
        "DOEFF_UV_CACHE_SEED_REFRESH_EVERY_SECONDS": "60",
        "UV": "/bin/uv",
    }
    settings = uv_cache.UpkeepSettings.from_env({**env, "NODE_NAME": "n1"})
    assert settings.refresh_projects == (Path("/p/a"), Path("/p/b"))
    assert settings.refresh_every_seconds == 60.0
    assert settings.uv == "/bin/uv"
    assert settings.refreshes
    assert not uv_cache.UpkeepSettings.from_env({**env, "NODE_NAME": "n2"}).refreshes
    assert not uv_cache.UpkeepSettings.from_env(env).refreshes
    assert not uv_cache.UpkeepSettings.from_env({}).refreshes


# ── 置き場(s3:// の URL と Secret の endpoint)────────────────────────────────


def _secrets(monkeypatch: pytest.MonkeyPatch, stores: dict[str, dict[str, str]]) -> None:
    """read_secret(k8s の API server の読み)の代わり: ref ごとの鍵と値のうち、問われた鍵だけを返す。"""

    def read(ref: str, keys: tuple[str, ...], sa_dir: Path = uv_cache.SA_DIR) -> dict[str, str]:
        return {key: value for key, value in stores[ref].items() if key in keys}

    monkeypatch.setattr(uv_cache, "read_secret", read)


def _store_secret(key: str, endpoint: str) -> dict[str, str]:
    return {"access-key-id": key, "secret-access-key": f"{key}-secret", "endpoint": endpoint}


def test_an_s3_url_goes_to_the_endpoint_of_the_named_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secrets(
        monkeypatch,
        {
            "ns/garage": _store_secret("g", "http://garage:3900"),
            "ns/nemo": _store_secret("n", "http://nemo:9000/"),
        },
    )
    url = "s3://uv-cache-seed/seed.tar.zst"
    assert uv_cache.resolve_store(url, "ns/garage") == uv_cache.Target(
        "http://garage:3900/uv-cache-seed/seed.tar.zst", uv_cache.Credentials("g", "g-secret")
    )
    assert uv_cache.resolve_store(url, "ns/nemo") == uv_cache.Target(
        "http://nemo:9000/uv-cache-seed/seed.tar.zst", uv_cache.Credentials("n", "n-secret")
    )
    assert uv_cache.resolve_store("http://other/b/k", "ns/nemo") == uv_cache.Target(
        "http://other/b/k", uv_cache.Credentials("n", "n-secret")
    )
    assert uv_cache.resolve_store("/srv/seed.tar.zst", "ns/nemo") == uv_cache.Target(
        "/srv/seed.tar.zst", None
    )


def test_an_s3_url_without_an_endpoint_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _secrets(monkeypatch, {"ns/bare": {"access-key-id": "a", "secret-access-key": "b"}})
    for name in (
        "AWS_ENDPOINT_URL_S3",
        "AWS_ENDPOINT_URL",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit, match="endpoint"):
        uv_cache.resolve_store("s3://b/k", "ns/bare")
    with pytest.raises(SystemExit, match="AWS_ENDPOINT_URL_S3"):
        uv_cache.resolve_store("s3://b/k", None)
    with pytest.raises(SystemExit, match="s3://<bucket>/<key>"):
        uv_cache.resolve_store("s3://b/", "ns/bare")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://fallback:1")
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "http://env:9000")
    assert uv_cache.resolve_store("s3://b/k", None) == uv_cache.Target("http://env:9000/b/k", None)


@pytest.fixture
def two_stores(source_cache: Path, tmp_path: Path) -> Iterator[dict[str, type[_RangeHandler]]]:
    """同じ object を持つ 2 つの置き場(endpoint ごとの Range の GET の数え — 置き場ごとに別の handler の型)。"""
    packed = tmp_path / "seed.tar.zst"
    uv_cache.pack(source_cache, packed)
    body = packed.read_bytes()
    # 型の値は全部ここで決める(親の _RangeHandler の値は前の検が替えたまま残る)。
    fields = {"body": body, "etag": '"v1"', "part_etag": '"v1"', "ranges": (), "keys": ()}
    servers = [
        http.server.ThreadingHTTPServer(("127.0.0.1", 0), type(name, (_RangeHandler,), fields))
        for name in ("garage", "nemo")
    ]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield {
            f"http://127.0.0.1:{server.server_address[1]}": server.RequestHandlerClass
            for server in servers
        }
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def test_upkeep_seeds_from_the_store_its_secret_names_and_switches_back(
    two_stores: dict[str, type[_RangeHandler]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (garage, from_garage), (nemo, from_nemo) = two_stores.items()
    _secrets(
        monkeypatch, {"ns/garage": _store_secret("g", garage), "ns/nemo": _store_secret("n", nemo)}
    )
    env = {
        "DOEFF_UV_CACHE_SEED_URL": "s3://uv-cache-seed/seed.tar.zst",
        "DOEFF_UV_CACHE_SEED_MIN_FREE_GIB": "0",
    }
    for step, (secret, endpoint, key) in enumerate(
        [("ns/garage", garage, "g"), ("ns/nemo", nemo, "n"), ("ns/garage", garage, "g")]
    ):
        settings = uv_cache.UpkeepSettings.from_env({**env, "DOEFF_UV_CACHE_SEED_SECRET": secret})
        target = tmp_path / f"cache-{step}"
        before = {handler: len(handler.keys) for handler in (from_garage, from_nemo)}
        seeded = uv_cache.upkeep(target, settings, part=97)["seed"]
        assert seeded["seeded"] is True
        assert seeded["source"] == f"{endpoint}/uv-cache-seed/seed.tar.zst"
        assert (target / "archive-v0" / "AAA" / "pkg_a" / "__init__.py").read_text() == "a = 1\n"
        used, unused = (from_garage, from_nemo) if endpoint == garage else (from_nemo, from_garage)
        assert set(used.keys[before[used] :]) == {key}
        assert len(unused.keys) == before[unused]


# ── refresh ───────────────────────────────────────────────────────────────


class _StoreHandler(http.server.BaseHTTPRequestHandler):
    """HEAD と PUT だけを答える object の置き場の代わり(object 1 つ — 本体・metadata・置いた刻)。"""

    body: bytes | None = None
    meta = ""
    modified = 0.0
    puts = 0
    # 先頭から順に PUT の答えを替える: HTTP の状態の数か "drop"(本体を読まずに接続を切る)。空になれば 200 で置く。
    failures: ClassVar[list[int | str]] = []

    def do_HEAD(self) -> None:
        if self.body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.send_header("Last-Modified", email.utils.formatdate(self.modified, usegmt=True))
        if self.meta:
            self.send_header(uv_cache.LOCKS_META, self.meta)
        self.end_headers()

    def do_PUT(self) -> None:
        cls = type(self)
        cls.puts += 1
        if cls.failures:
            failure = cls.failures.pop(0)
            if failure == "drop":
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(int(failure))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        cls.body = self.rfile.read(int(self.headers["Content-Length"]))
        cls.meta = self.headers.get(uv_cache.LOCKS_META, "")
        cls.modified = time.time()
        self.send_response(200)
        self.send_header("ETag", '"put"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: object) -> None:
        return None


@pytest.fixture
def seed_store() -> Iterator[str]:
    _StoreHandler.body, _StoreHandler.meta, _StoreHandler.modified, _StoreHandler.puts = (
        None,
        "",
        0.0,
        0,
    )
    _StoreHandler.failures = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StoreHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/bucket/seed.tar.zst"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def fake_uv(tmp_path: Path) -> Path:
    """`uv sync … --project P --cache-dir C` の代わり: C に P の名の展開済みの項目を 1 つ書き、venv の置き場を P の外の
    UV_PROJECT_ENVIRONMENT に作る(撃たれた引数を C の calls へ足す)。"""
    script = _write(
        tmp_path / "bin" / "uv",
        "#!/bin/sh\n"
        'while [ $# -gt 0 ]; do case "$1" in --project) p=$2; shift;; --cache-dir) c=$2; shift;; esac; shift; done\n'
        'mkdir -p "$c/archive-v0/$(basename "$p")" "$UV_PROJECT_ENVIRONMENT"\n'
        'echo ok > "$c/archive-v0/$(basename "$p")/mod.py"\n'
        'mkdir -p "$c/builds-v0/x"\n',
    )
    script.chmod(0o755)
    return script


def _projects(tmp_path: Path) -> list[Path]:
    return [
        _write(tmp_path / "projects" / name / "uv.lock", f"lock {name}\n").parent
        for name in ("alpha", "beta")
    ]


def _refresh(cache: Path, projects: list[Path], url: str, uv: Path, **kw: object) -> dict:
    return uv_cache.refresh(
        cache,
        projects,
        url,
        None,
        "us-east-1",
        10.0,
        every_seconds=kw.get("every_seconds", 3600.0),
        min_free=0,
        force=bool(kw.get("force", False)),
        uv=str(uv),
    )


def test_locks_digest_follows_the_lock_content_and_order(tmp_path: Path) -> None:
    alpha, beta = _projects(tmp_path)
    first = uv_cache.locks_digest([alpha, beta])
    assert first == uv_cache.locks_digest([alpha, beta])
    assert first != uv_cache.locks_digest([beta, alpha])
    _write(beta / "uv.lock", "lock beta 2\n")
    assert first != uv_cache.locks_digest([alpha, beta])


def test_refresh_builds_an_empty_cache_from_the_locks_and_puts_it_with_the_hash(
    seed_store: str, fake_uv: Path, tmp_path: Path
) -> None:
    projects = _projects(tmp_path)
    cache = tmp_path / "cache"
    result = _refresh(cache, projects, seed_store, fake_uv)
    assert result["refreshed"] is True
    assert _StoreHandler.meta == uv_cache.locks_digest(projects) == result["locks"]
    assert [s["project"] for s in result["synced"]] == [str(p) for p in projects]
    unpacked = tmp_path / "unpacked"
    packed = _write(tmp_path / "got.tar.zst", "")
    packed.write_bytes(_StoreHandler.body or b"")
    uv_cache.seed(unpacked, str(packed), None, "us-east-1", 10.0, force=False)
    assert sorted(p.name for p in (unpacked / "archive-v0").iterdir()) == ["alpha", "beta"]
    assert not (unpacked / "builds-v0").exists()
    assert not list(cache.glob(uv_cache.REFRESH_BUILD + "*"))
    assert not any((p / ".venv").exists() for p in projects)


def test_refresh_skips_a_current_or_recent_copy_unless_forced(
    seed_store: str, fake_uv: Path, tmp_path: Path
) -> None:
    projects = _projects(tmp_path)
    cache = tmp_path / "cache"
    _refresh(cache, projects, seed_store, fake_uv)
    assert _refresh(cache, projects, seed_store, fake_uv)["reason"] == "current"
    _write(projects[0] / "uv.lock", "lock alpha 2\n")
    assert _refresh(cache, projects, seed_store, fake_uv)["reason"] == "recent"
    assert _StoreHandler.puts == 1
    _StoreHandler.modified = time.time() - 7200
    assert _refresh(cache, projects, seed_store, fake_uv)["refreshed"] is True
    assert _refresh(cache, projects, seed_store, fake_uv, force=True)["refreshed"] is True
    assert _StoreHandler.puts == 3


def test_refresh_gives_uv_a_tmpdir_under_the_build_dir(
    seed_store: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """組みの gcc が書く一時 file は、埋まっているかもしれない外の TMPDIR でなく build の dir に書く(終わりに一緒に消える)。"""
    outer = tmp_path / "outer-tmp"
    outer.mkdir()
    monkeypatch.setenv("TMPDIR", str(outer))
    seen = tmp_path / "tmpdirs"
    uv = _write(
        tmp_path / "bin" / "uv",
        "#!/bin/sh\n"
        'while [ $# -gt 0 ]; do case "$1" in --project) p=$2; shift;; --cache-dir) c=$2; shift;; esac; shift; done\n'
        'mkdir -p "$c/archive-v0/$(basename "$p")"\n'
        'echo ok > "$c/archive-v0/$(basename "$p")/mod.py"\n'
        f': > "$TMPDIR/cc-probe" && echo "$TMPDIR" >> {seen}\n',
    )
    uv.chmod(0o755)
    cache = tmp_path / "cache"
    assert _refresh(cache, _projects(tmp_path), seed_store, uv)["refreshed"] is True
    tmpdirs = [Path(line) for line in seen.read_text().splitlines()]
    assert len(tmpdirs) == 2
    assert all(t.relative_to(cache).parts[0].startswith(uv_cache.REFRESH_BUILD) for t in tmpdirs)
    assert not list(outer.iterdir())
    assert not list(cache.glob(uv_cache.REFRESH_BUILD + "*"))
    packed = _write(tmp_path / "got.tar.zst", "")
    packed.write_bytes(_StoreHandler.body or b"")
    unpacked = tmp_path / "unpacked"
    uv_cache.seed(unpacked, str(packed), None, "us-east-1", 10.0, force=False)
    assert not list(unpacked.rglob("cc-probe"))


def test_refresh_does_not_put_when_a_sync_fails(seed_store: str, tmp_path: Path) -> None:
    failing = _write(tmp_path / "bin" / "uv", "#!/bin/sh\nexit 2\n")
    failing.chmod(0o755)
    cache = tmp_path / "cache"
    with pytest.raises(subprocess.CalledProcessError):
        _refresh(cache, _projects(tmp_path), seed_store, failing)
    assert _StoreHandler.puts == 0
    assert not list(cache.glob(uv_cache.REFRESH_BUILD + "*"))


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """PUT の試し直しの待ちを眠らずに控える(待ちの並びは PUT_RETRY_DELAYS の頭の 3 つ)。"""
    slept: list[float] = []
    monkeypatch.setattr(uv_cache, "PUT_RETRY_DELAYS", (30.0, 60.0, 120.0))
    monkeypatch.setattr(uv_cache.time, "sleep", slept.append)
    return slept


def test_put_retry_delays_are_five_waits_within_sixteen_minutes() -> None:
    assert len(uv_cache.PUT_RETRY_DELAYS) == 5
    assert sum(uv_cache.PUT_RETRY_DELAYS) <= 16 * 60


@pytest.mark.parametrize("failures", [[503, 503], [500, "drop"], ["drop"]])
def test_put_retries_server_errors_and_dropped_connections(
    seed_store: str, waits: list[float], tmp_path: Path, failures: list[int | str]
) -> None:
    _StoreHandler.failures = list(failures)
    packed = _write(tmp_path / "seed.tar.zst", "packed")
    put = uv_cache.http_put_file(
        seed_store, packed, None, "us-east-1", 10.0, {uv_cache.LOCKS_META: "h"}
    )
    assert put == {"etag": "put", "attempts": len(failures) + 1}
    assert waits == [30.0, 60.0][: len(failures)]
    assert _StoreHandler.body == b"packed"
    assert _StoreHandler.meta == "h"


def test_put_gives_up_after_the_last_wait(
    seed_store: str, waits: list[float], tmp_path: Path
) -> None:
    _StoreHandler.failures = [503] * 4
    with pytest.raises(SystemExit, match=r"HTTP 503.*4 回目で止めた"):
        uv_cache.http_put_file(seed_store, _write(tmp_path / "s", "x"), None, "us-east-1", 10.0)
    assert waits == [30.0, 60.0, 120.0]
    assert _StoreHandler.body is None


def test_put_does_not_retry_a_refusal(seed_store: str, waits: list[float], tmp_path: Path) -> None:
    _StoreHandler.failures = [403]
    with pytest.raises(SystemExit, match=r"HTTP 403.*1 回目で止めた"):
        uv_cache.http_put_file(seed_store, _write(tmp_path / "s", "x"), None, "us-east-1", 10.0)
    assert waits == []
    assert _StoreHandler.puts == 1


def test_refresh_keeps_the_build_through_a_failed_put(
    seed_store: str, fake_uv: Path, waits: list[float], tmp_path: Path
) -> None:
    _StoreHandler.failures = [503]
    projects = _projects(tmp_path)
    result = _refresh(tmp_path / "cache", projects, seed_store, fake_uv)
    assert result["refreshed"] is True
    assert result["attempts"] == 2
    assert _StoreHandler.meta == uv_cache.locks_digest(projects)
    assert waits == [30.0]


# ── boot.sh ───────────────────────────────────────────────────────────────


def _upkeep_function() -> str:
    text = BOOT.read_text()
    found = re.search(r"^doeff_uv_cache_upkeep\(\) \{\n.*?^\}\n", text, re.S | re.M)
    assert found is not None, "boot.sh に doeff_uv_cache_upkeep が無い"
    assert "\n  doeff_uv_cache_upkeep\n" in text, (
        "doeff_prepare が doeff_uv_cache_upkeep を呼ばない"
    )
    return found[0]


@pytest.mark.parametrize(
    ("role", "env", "called"),
    [
        ("worker", {"DOEFF_UV_CACHE_SEED_URL": "http://store/b/seed.tar.zst"}, True),
        ("prepare", {"DOEFF_UV_CACHE_GC_DAYS": "7"}, True),
        ("drain", {"DOEFF_UV_CACHE_GC_DAYS": "7"}, False),
        ("coordinator", {"DOEFF_UV_CACHE_GC_DAYS": "7"}, False),
        ("worker", {}, False),
    ],
)
def test_boot_starts_upkeep_in_the_background_only_when_asked(
    tmp_path: Path, role: str, env: dict[str, str], called: bool
) -> None:
    root = tmp_path / "root"
    _write(root / "packages" / "doeff-cluster" / "deploy" / "uv_cache.py", "")
    python = _write(root / ".venv" / "bin" / "python", "#!/bin/sh\n")
    python.chmod(0o755)
    bin_dir = tmp_path / "bin"
    record = tmp_path / "uv-args"
    uv = _write(bin_dir / "uv", f'#!/bin/sh\nprintf "%s\\n" "$@" > {record}\n')
    uv.chmod(0o755)
    cache = tmp_path / "cache"
    script = _upkeep_function() + "doeff_uv_cache_upkeep\nwait\n"
    done = subprocess.run(
        ["sh", "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "role": role,
            "root": str(root),
            "DOEFF_UV_CACHE_DIR": str(cache),
            **env,
        },
    )
    assert done.stdout == ""
    if called:
        args = record.read_text().splitlines()
        assert args[args.index("--python") + 1] == str(python)
        assert args[-3:] == ["upkeep", "--cache", str(cache)]
        assert "--no-cache" in args
    else:
        assert not record.exists()
