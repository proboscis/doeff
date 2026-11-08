"""bytecode の準備の道具(worker/entry/code_prepare.hy)は、機体の保存先に合う code の無い物を、焼く前に分け合う保存先(S3 の口の bucket —
doeff-hy の code_store の share-from-env・ADR-DOE-BUILD-002 R8)から取り寄せ、焼いた物はすぐ分け合う保存先へ置く。

直す前は保存先が機体ごとに閉じていて、同じ root の同じ module を全部の機体がそれぞれ焼いた(2026-11-07 に k3s-0・k3s-2・k3s-3・eos が
同じ root に同じ stored=162 rebuilt=245 を出し、それぞれ 166〜979 秒かけて焼いた)。並びが 1 本の小さい機体で計画の全部(約 3150 本)を
焼き直すと約 2190 秒になり、配備の回の温めの段の 1800 秒を越えた。

機体 = 別々の機体の保存先の dir。分け合う保存先 = この test の process の中の模擬の S3(path の形の ListObjectsV2・GET・PUT だけを答え、
署名の鍵の id が違えば 403)。道具は本番の準備と同じ引数の形で起こし、焼いた物は道具の外(compile の合流点を包む sitecustomize)から数える
(台と観測の作りは test_code_prepare_content_store.py と同じ)。
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
from xml.sax.saxutils import escape

import pytest

#: 準備の道具(本番の準備の処理ステージが起こす script と同じ file)。
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

#: 道具が焼かなくても compile されうる物: macro の定義元。使う側の code の macro の記録を照らす guard は定義元の code を読み、機体の保存先に
#: その entry がまだ無ければ(取り寄せる順が使う側より後)定義元を 1 度 compile する — 道具の焼きではない(報告の rebuilt に数えない・
#: 同じ事は機体の保存先だけの準備でも起きる — test_code_prepare_content_store.py の test_c の註・#4172)。
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


def _write_four(root: Path, salt: str, *, macro_add: int = 1) -> None:
    """macro の定義元・使う側・import だけする側・無関係の 4 package を置く(salt = 件ごとに中身を変える註の行)。"""
    files = {
        "mac/__init__.py": f"# {salt}\n",
        "mac/macros.hy": f"; {salt}\n(defmacro answer [] (+ 1 {macro_add}))\n(setv TAG \"mac\")\n",
        "use/__init__.py": f"# {salt}\n",
        "use/user.hy": f"; {salt}\n(require mac.macros [answer])\n(setv value (answer))\n",
        "imp/__init__.py": f"# {salt}\n",
        "imp/importer.hy": f"; {salt}\n(import mac.macros)\n(setv value 2)\n",
        "other/__init__.py": f"# {salt}\n",
        "other/plain.hy": f"; {salt}\n(setv value 3)\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


# --- 模擬の S3 ----------------------------------------------------------------------------------


@dataclass
class _Bucket:
    """模擬の S3 の中身: objects = object の名 → #(中身 置いた刻)・requests = 受けた要求の #(method object の名か一覧の prefix) の列。"""

    objects: dict[str, tuple[bytes, float]] = field(default_factory=dict)
    requests: tuple[tuple[str, str], ...] = ()
    lock: threading.Lock = field(default_factory=threading.Lock)

    def codes(self) -> dict[str, bytes]:
        """置かれた code の entry(名 → 中身)。"""
        with self.lock:
            return {name: data for name, (data, _) in self.objects.items() if name.endswith(".code")}


def _handler(bucket: _Bucket) -> type[BaseHTTPRequestHandler]:
    """bucket を答える HTTP の答え手(path の形の /<bucket>/<object> の GET・PUT と /<bucket>?list-type=2 の一覧)を作るため。"""

    class S3(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — 基の名のまま上書きする
            return

        def _answer(self, code: int, body: bytes = b"") -> None:
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _target(self) -> tuple[str, dict[str, list[str]]] | None:
            """署名の鍵の id を検め(違えば 403 を答えて None)、object の名と query を返す。"""
            if f"Credential={_KEY_ID}/" not in self.headers.get("Authorization", ""):
                self._answer(403)
                return None
            parts = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parts.path)
            if path != f"/{_BUCKET}" and not path.startswith(f"/{_BUCKET}/"):
                self._answer(404)
                return None
            return path[len(f"/{_BUCKET}/"):], urllib.parse.parse_qs(parts.query)

        def do_GET(self) -> None:
            target = self._target()
            if target is None:
                return
            name, query = target
            if not name:
                prefix = query.get("prefix", [""])[0]
                with bucket.lock:
                    bucket.requests = (*bucket.requests, ("LIST", prefix))
                    found = sorted((n, at) for n, (_, at) in bucket.objects.items() if n.startswith(prefix))
                limit = int(query.get("max-keys", ["1000"])[0])
                rows = "".join(
                    f"<Contents><Key>{escape(n)}</Key><LastModified>"
                    f"{time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at))}.{int(at * 1000) % 1000:03d}Z</LastModified></Contents>"
                    for n, at in found[:limit])
                body = ('<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                        f"<Name>{_BUCKET}</Name>{rows}</ListBucketResult>").encode()
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
            name, _ = target
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
    """1 件の模擬の S3: url = 道具へ渡す DOEFF_HY_CODE_SHARE の値・bucket = 中身と受けた要求。"""

    url: str
    bucket: _Bucket


@pytest.fixture
def share() -> Iterator[_Share]:
    """件ごとに空の模擬の S3 を起こす(件の終わりに止める)。"""
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
    """誰も待っていない port(届かない分け合う保存先の代役)。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# --- 道具を起こす ------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Rig:
    """件をまたいで使う物: hook = compile を数える sitecustomize の dir・prefix = 道具自身の import の .pyc の dir・keys = 鍵の file 2 つ
    (正しい鍵・違う鍵)。"""

    hook: Path
    prefix: Path
    keys: Path
    wrong_keys: Path


@dataclass(frozen=True)
class _Bake:
    """1 回の焼きの観測: compiled = compile した木の中の Hy の source(名の順)・counts = 木の報告の行の数・stderr = 道具の stderr。"""

    compiled: list[str]
    counts: dict[str, int]
    stderr: str


def _tool(tree: Path, store: Path, rig: _Rig, compiled: Path, share_url: str | None, keys: Path | None, *, write: bool) -> subprocess.CompletedProcess[str]:
    """本番の準備と同じ引数の形で道具を起こす(分け合う保存先は環境変数で名指す — 本番の worker の環境と同じ口)。"""
    shared = ([f"DOEFF_HY_CODE_SHARE={share_url}"] if share_url else []) + ([f"DOEFF_HY_CODE_SHARE_KEYS={keys}"] if keys else [])
    return subprocess.run(
        [
            "env", "-u", "DOEFF_HY_CODE_SHARE", "-u", "DOEFF_HY_CODE_SHARE_KEYS",
            *(["-u", "PYTHONDONTWRITEBYTECODE"] if write else ["PYTHONDONTWRITEBYTECODE=1"]),
            f"PYTHONPYCACHEPREFIX={rig.prefix}", f"DOEFF_HY_CODE_STORE={store}",
            f"PYTHONPATH={rig.hook}", f"BAKE_COMPILED_LOG={compiled}", *shared,
            sys.executable, "-m", "hy", str(TOOL), "--revision", "r", "--tree", str(tree), "--roots", ".", "--jobs", "1",
        ],
        capture_output=True, text=True, cwd=tree, timeout=180, check=False,
    )


def _bake(tree: Path, store: Path, rig: _Rig, share_url: str | None, keys: Path | None = None) -> _Bake:
    """機体 1 台(store = その機体の保存先)で木を焼き、compile した木の中の Hy の source と報告の行の数を返す。"""
    compiled = tree.parent / f"{tree.name}.compiled.log"
    completed = _tool(tree, store, rig, compiled, share_url, rig.keys if keys is None else keys, write=False)
    assert completed.returncode == 0, completed.stderr
    lines = compiled.read_text().split() if compiled.exists() else []
    names = sorted(str(Path(line).relative_to(tree)) for line in lines if Path(line).is_relative_to(tree))
    counts: dict[str, int] = {}
    for line in completed.stderr.splitlines():
        if f"tree={tree} " in line:
            counts = {k: int(v) for k, v in (part.split("=", 1) for part in line.split() if "=" in part) if v.isdigit()}
    return _Bake(names, counts, completed.stderr)


def _import_value(tree: Path, store: Path, rig: _Rig) -> int:
    """用意した木を子の job と同じく import し、use.user の値を読む(保存先から書いた code が今の macro の展開であることを確かめる)。"""
    completed = subprocess.run(
        ["env", f"PYTHONPYCACHEPREFIX={rig.prefix}", "PYTHONDONTWRITEBYTECODE=1", f"DOEFF_HY_CODE_STORE={store}",
         sys.executable, "-c", _IMPORT, str(tree)],
        capture_output=True, text=True, cwd=tree, timeout=180, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return int(completed.stdout.split()[-1])


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> _Rig:
    """hook と鍵の file を置き、道具を 1 度だけ .pyc を書く設定で起こして、道具自身の import の .pyc を prefix の dir に作る。"""
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


def _first_node(tmp_path: Path, rig: _Rig, share: _Share, *, macro_add: int = 1) -> None:
    """並びの多い機体の代役: 空の機体の保存先で木を焼き、焼いた物を分け合う保存先へ置く。"""
    tree = tmp_path / "node-a" / "app"
    _write_four(tree, tmp_path.name, macro_add=macro_add)
    baked = _bake(tree, tmp_path / "store-a", rig, share.url)
    assert baked.compiled == sorted(_HY_SOURCES), baked.compiled
    assert len(share.bucket.codes()) >= len(_HY_SOURCES), sorted(share.bucket.codes())


# --- 受入 1: ほかの機体が焼いた物を取り寄せて焼かない ----------------------------------------------------


def test_a_small_node_fetches_what_another_node_baked_and_bakes_nothing(tmp_path: Path, rig: _Rig, share: _Share) -> None:
    """受入 1: ほかの機体(A)が焼いて分け合う保存先へ置いた entry(同じ鍵・同じ macro の記録)を、空の保存先の機体(B — 別の dir の木)は
    取り寄せて .pyc を置き、自分では 1 本も焼かない。報告の行は fetched に数え、rebuilt は 0。取り寄せた entry は B の機体の保存先にも
    手元の写しとして残り、次の準備は分け合う保存先へ問わずに機体の保存先から書く。直す前は B が 4 本を全部焼いた。"""
    _first_node(tmp_path, rig, share)
    tree, store = tmp_path / "node-b" / "app", tmp_path / "store-b"
    _write_four(tree, tmp_path.name)
    baked = _bake(tree, store, rig, share.url)
    assert set(baked.compiled) <= _CHECK_COMPILES, baked.compiled
    assert baked.counts.get("rebuilt") == 0 and baked.counts.get("fetched", 0) >= len(_HY_SOURCES), baked.counts
    assert "fetched=" in next(line for line in baked.stderr.splitlines() if "compile_s=" in line)
    assert _import_value(tree, store, rig) == 2
    asked = len(share.bucket.requests)
    again_tree = tmp_path / "node-b" / "app-2"
    _write_four(again_tree, tmp_path.name)
    again = _bake(again_tree, store, rig, share.url)
    assert again.compiled == [] and again.counts.get("fetched") == 0 and again.counts.get("stored", 0) >= len(_HY_SOURCES), again.counts
    assert [r for r in share.bucket.requests[asked:] if r != ("LIST", "bytecode/")] == [], "手元の写しが在るのに取り寄せた"


def test_b_only_the_modules_whose_macros_changed_are_baked(tmp_path: Path, rig: _Rig, share: _Share) -> None:
    """受入 2(macro の記録が合わない): A が焼いた entry のうち、B の macro と記録の合わない物(macro の定義元と、それを使う側)は使わずに
    B が焼き、合う物(import だけする側・無関係)は取り寄せる。B が import した値は B の macro の展開。"""
    _first_node(tmp_path, rig, share, macro_add=1)
    tree, store = tmp_path / "node-b" / "app", tmp_path / "store-b"
    _write_four(tree, tmp_path.name, macro_add=5)
    baked = _bake(tree, store, rig, share.url)
    assert baked.compiled == ["mac/macros.hy", "use/user.hy"], baked.compiled
    assert baked.counts.get("fetched", 0) >= 2 and baked.counts.get("failed") == 0, baked.counts
    assert _import_value(tree, store, rig) == 6


# --- 受入 2: 分け合う保存先が使えない時は機体の中で焼いて落ちない --------------------------------------------


def test_c_an_unreachable_share_is_named_and_the_node_bakes_by_itself(tmp_path: Path, rig: _Rig) -> None:
    """受入 2(届かない): 分け合う保存先が届かなければ、名乗って機体の中で全部を焼き、準備は通る(届かない保存先へ 1 本ごとに待たない —
    焼く前の 1 回の問いで使わないと決める)。"""
    tree, store = tmp_path / "node-b" / "app", tmp_path / "store-b"
    _write_four(tree, tmp_path.name)
    started = time.monotonic()
    baked = _bake(tree, store, rig, f"http://127.0.0.1:{_closed_port()}/{_BUCKET}/bytecode")
    assert baked.compiled == sorted(_HY_SOURCES), baked.compiled
    assert "分け合う保存先" in baked.stderr and "使えない" in baked.stderr, baked.stderr
    assert baked.counts.get("failed") == 0 and baked.counts.get("fetched") == 0, baked.counts
    assert time.monotonic() - started < 120
    assert _import_value(tree, store, rig) == 2


def test_d_a_share_refusing_the_key_is_named_and_the_node_bakes_by_itself(tmp_path: Path, rig: _Rig, share: _Share) -> None:
    """受入 2(鍵を断る): 分け合う保存先が鍵を断れば(403)、名乗って機体の中で焼き、分け合う保存先へは置かない。"""
    tree, store = tmp_path / "node-b" / "app", tmp_path / "store-b"
    _write_four(tree, tmp_path.name)
    baked = _bake(tree, store, rig, share.url, rig.wrong_keys)
    assert baked.compiled == sorted(_HY_SOURCES), baked.compiled
    assert "HTTP 403" in baked.stderr, baked.stderr
    assert share.bucket.codes() == {}


def test_e_missing_entries_are_baked_and_put_for_the_next_node(tmp_path: Path, rig: _Rig, share: _Share) -> None:
    """受入 2(欠けている): 分け合う保存先に entry が無ければ機体の中で焼き、焼いた物を置く — 次の機体(C)は取り寄せて焼かない。"""
    tree, store = tmp_path / "node-b" / "app", tmp_path / "store-b"
    _write_four(tree, tmp_path.name)
    baked = _bake(tree, store, rig, share.url)
    assert baked.compiled == sorted(_HY_SOURCES) and baked.counts.get("fetched") == 0, (baked.compiled, baked.counts)
    third = tmp_path / "node-c" / "app"
    _write_four(third, tmp_path.name)
    fetched = _bake(third, tmp_path / "store-c", rig, share.url)
    assert set(fetched.compiled) <= _CHECK_COMPILES and fetched.counts.get("rebuilt") == 0, (fetched.compiled, fetched.counts)


def test_f_broken_shared_entries_are_named_baked_and_replaced(tmp_path: Path, rig: _Rig, share: _Share) -> None:
    """受入 2(壊れている): code として読めない entry と、中身が名(macro の記録の指紋)の code でない entry は使わず、名乗って機体の中で
    焼き、焼いた物で置き直す — 次の機体(C)は置き直した entry を取り寄せて焼かない。"""
    _first_node(tmp_path, rig, share)
    names = sorted(share.bucket.codes())
    with share.bucket.lock:
        for index, name in enumerate(names):
            data, at = share.bucket.objects[name]
            other = share.bucket.objects[names[(index + 1) % len(names)]][0]
            share.bucket.objects[name] = (b"\x00broken" if index % 2 == 0 else other, at)
    tree, store = tmp_path / "node-b" / "app", tmp_path / "store-b"
    _write_four(tree, tmp_path.name)
    baked = _bake(tree, store, rig, share.url)
    assert baked.compiled == sorted(_HY_SOURCES), baked.compiled
    assert "an entry in the shared store is broken (cannot be read by marshal" in baked.stderr, baked.stderr
    assert "is not the code named" in baked.stderr, baked.stderr
    assert _import_value(tree, store, rig) == 2
    third = tmp_path / "node-c" / "app"
    _write_four(third, tmp_path.name)
    fetched = _bake(third, tmp_path / "store-c", rig, share.url)
    assert set(fetched.compiled) <= _CHECK_COMPILES and fetched.counts.get("rebuilt") == 0, (fetched.compiled, fetched.counts)


def test_g_without_a_named_share_nothing_changes(tmp_path: Path, rig: _Rig) -> None:
    """名指しが無ければ今までどおり(分け合う保存先の行を出さず、機体の保存先だけで焼く)。"""
    tree, store = tmp_path / "node-b" / "app", tmp_path / "store-b"
    _write_four(tree, tmp_path.name)
    baked = _bake(tree, store, rig, None)
    assert baked.compiled == sorted(_HY_SOURCES), baked.compiled
    assert "分け合う保存先" not in baked.stderr, baked.stderr
