"""replay記事の掲載コードを順に実行し、完全な例との一致と書き方の決まりの数を確かめる。"""

import ast
import importlib
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))  # 公開する本文を作る道具を、同じ数え方で使うため。
release = importlib.import_module("render_release")  # 同じdirのrender_release.py。

article = (SOURCE / "doeff-replay.md").read_text()
example = (SOURCE / "examples/http_replay.py").read_text()
body = re.sub(r"\A---\n.*?\n---\n", "", article, flags=re.S)
fences = re.findall(r"```(\w*)\n(.*?)```", body, re.S)
blocks = [code for lang, code in fences if lang == "python"]
assert len(blocks) == 5, len(blocks)


class _Article(BaseHTTPRequestHandler):
    """同じ機械の中だけで答える、確かめ用のHTTPサーバー。"""

    def do_GET(self):
        """本番の取得担当が実際に通信して受け取る、固定の本文を返すため。"""
        body = "サーバーの本文".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        """確かめの出力を、アクセスの記録で汚さないため。"""


def run_block(index: int, namespace: dict, code: str | None = None) -> None:
    """本文の「上のコードに続けて実行」に合わせ、補助importなしで順に実行する。"""
    source = blocks[index - 1] if code is None else code
    exec(compile(source, f"doeff-replay.md:block-{index}", "exec"), namespace)


namespace: dict = {}
with TemporaryDirectory(prefix="replay-check-") as directory:
    root = Path(directory)

    # 1枠目: 固定の応答で、通信せずに終わる。
    run_block(1, namespace)
    namespace["test_fetch_text_without_network"]()

    # 2〜3枠目: 記録して再生し、記録に無いURLは通信せずにLookupErrorになる。
    run_block(2, namespace)
    run_block(3, namespace)
    (root / "record").mkdir()
    namespace["test_record_then_replay"](root / "record")

    # 4枠目: 本番の取得担当。URLと記録のファイルだけを、同じ機械の中のサーバーと一時の場所に替える。
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Article)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    local_url = f"http://127.0.0.1:{server.server_port}/articles/1"
    local_db = str(root / "production.sqlite")
    production = blocks[3]
    assert '"https://api.example.com/articles/1"' in production
    assert '"tests/http.sqlite"' in production
    production = production.replace('"https://api.example.com/articles/1"', repr(local_url))
    production = production.replace('"tests/http.sqlite"', repr(local_db))
    try:
        run_block(4, namespace, production)
        assert namespace["text"] == "サーバーの本文", namespace["text"]
    finally:
        server.shutdown()
        server.server_close()
    replayed = namespace["run_recorded"](
        namespace["fetch_text"](local_url), local_db, namespace["reject_http"]
    )
    assert replayed == "サーバーの本文", replayed  # サーバーを止めた後も、記録から答える。

    # 5枠目: ヘッダの鍵を記録の鍵から外すと、偽の鍵でも記録に当たる。
    run_block(5, namespace)
    (root / "key").mkdir()
    namespace["test_replay_without_real_key"](root / "key")

    # 本文の「既定の鍵のままだと、偽の鍵の呼び出しはLookupErrorになります」を確かめる。
    default_db = root / "default.sqlite"
    url = "https://example.invalid/private"
    run_recorded = namespace["run_recorded"]
    fetch_private = namespace["fetch_private"]
    run_recorded(fetch_private(url, "本物の鍵"), default_db, namespace["fake_http"])
    try:
        run_recorded(fetch_private(url, "ダミー"), default_db, namespace["reject_http"])
    except LookupError:
        pass
    else:
        raise AssertionError("既定の鍵で、偽の鍵の呼び出しが記録に当たった")

    # 記録の鍵の値に、APIキーの文字が入らないことを、SQLiteのファイルで確かめる。
    import sqlite3

    for path in (root / "key/http.sqlite", default_db):
        # 全件を読む理由: 確かめで作った一時のファイルで行は1つ。全部の鍵にAPIキーが無いことを見るため。
        keys = [row[0] for row in sqlite3.connect(path).execute("SELECT key FROM cache")]
        assert keys, path
        assert not any("本物の鍵" in key or "Bearer" in key for key in keys), keys

    # 本文の「別のプロセスから読んでも当たる」を確かめる。上で作った記録を、別のPythonで再生する。
    import subprocess

    replay_script = f"""
import sys
sys.path.insert(0, {str(SOURCE / "examples")!r})
import http_replay as example
url = "https://example.invalid/article"
assert example.run_recorded(example.fetch_text(url), {str(root / "record/http.sqlite")!r}, example.reject_http) == "記録した本文"
private = example.fetch_private("https://example.invalid/private", "ダミー")
db = {str(root / "key/http.sqlite")!r}
assert example.run_recorded(private, db, example.reject_http, example.method_and_url) == "記録した本文"
print("別のプロセスで再生: OK")
"""
    other = subprocess.run(
        [sys.executable, "-c", replay_script], capture_output=True, text=True, timeout=300, check=False
    )
    assert other.returncode == 0, (other.stdout, other.stderr)
    assert "OK" in other.stdout, other.stdout


def code_lines(code: str) -> tuple[str, ...]:
    """importの文を除いた実質行を、字下げを外して返す。完全な例と見比べるため。"""
    imports = {
        number
        for node in ast.parse(code).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for number in range(node.lineno, node.end_lineno + 1)
    }
    return tuple(
        line.strip()
        for number, line in enumerate(code.splitlines(), 1)
        if line.strip() and number not in imports
    )


# 4枠目は記事のためにURLと記録のファイルを書いた構成例なので、完全な例とは見比べない。
example_lines = {line.strip() for line in example.splitlines()}
for index, block in enumerate(blocks, 1):
    if index == 4:
        continue
    missing = [line for line in code_lines(block) if line not in example_lines]
    assert not missing, (f"block-{index}", missing)

# 書き方の決まりのうち、数で測れる項目。数え方は公開する本文を作るrender_release.pyに揃える。
released = release.release_text(article, release.read_rows((SOURCE / "publication-plan.md").read_text()))
measured = release.measure(released)
assert 3000 <= measured.body_chars <= 6000, measured.body_chars
assert measured.code_blocks <= 6, measured.code_blocks
assert 2 <= measured.images <= 4, measured.images
assert not measured.long_sentences, measured.long_sentences
assert not measured.plain_endings, measured.plain_endings
# 形: 悩み → 短いコード → 全体の図 → 本文。全体の図は、最初のコードの直後で最初の節より前に置く。
first_code_end = body.index("```", body.index("```python") + 3)
assert body.index("![") > first_code_end, "全体の図は冒頭の短いコードの後に置く"
assert body.index("![") < body.index("\n## "), "全体の図は最初の節より前に置く"
headings = re.findall(r"^## (.+)$", body, re.M)
assert headings[-2:] == ["まとめ", "参考"], headings
assert "published: false" in article
# 社外に出せない名前と、鍵やトークンの形。
forbidden = re.compile(
    r"agora|zeus|atlas|cisco|/home/|/Users/|sk-[A-Za-z0-9]|ghp_|ANTHROPIC_API_KEY|機体|繋ぎ口|答え手|名札",
    re.I,
)
assert not forbidden.search(article), forbidden.findall(article)
assert not forbidden.search(example), forbidden.findall(example)

print(
    f"OK: replay記事 Python {len(blocks)}枠を実行(4枠目はlocalhostのサーバーで記録・停止後に再生)・"
    f"完全な例と一致・既定の鍵は偽の鍵で外れる・記録の鍵にAPIキーの文字が無い・別のプロセスで再生 / "
    f"本文{measured.body_chars}字・枠{measured.code_blocks}・図{measured.images}・"
    f"60字を越える文{len(measured.long_sentences)}・です・ます以外{len(measured.plain_endings)}"
)
