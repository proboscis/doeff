"""外部APIのテストを、通信せずに回す。記事 doeff-replay.md の掲載コードの完全な例。

pytestで集めても、直接実行してもよい。外のサービスへは通信しない。
本番の取得担当(http_production_handler)は、同じ機械の中に立てたHTTPサーバーで確かめる。
"""

import threading  # 確かめ用のHTTPサーバーを、別のスレッドで動かす。
from http.server import (  # 同じ機械の中だけで答える、確かめ用のHTTPサーバーを作る。
    BaseHTTPRequestHandler,  # GETへの答え方を書く、基底のクラス。
    ThreadingHTTPServer,  # 答えを別のスレッドで返すサーバー。
)
from pathlib import Path  # 一時のディレクトリの中の場所を組み立てる。
from tempfile import TemporaryDirectory  # pytestを使わない時の、tmp_pathの代わり。

import pytest  # 失敗を期待するテストに使う。
from doeff_core_effects import HttpRequest, HttpResponse  # HTTPの依頼と応答の型。
from doeff_core_effects.handlers import await_handler, slog_discard_handler  # 補助の依頼の受け手。
from doeff_core_effects.http_handlers import (  # 本番で使う取得担当。
    http_production_handler,  # httpxで実際に通信する取得担当。
)
from doeff_core_effects.memo_handlers import (  # 記録の層を組み立てる部品。
    content_address,  # 既定の記録の鍵。依頼の全部の欄から作る。
    make_memo_rewriter,  # 記録にあれば記録から答え、無ければ外側へ依頼を出し直す。
    sqlite_memo_handler,  # 記録をSQLiteのファイルへ読み書きする。
)
from doeff_core_effects.scheduler import scheduled  # 保存の読み書きの待ちを進める。

from doeff import (  # 処理の定義・ハンドラ・実行を使う。
    Pass,  # 担当外の依頼を、外側のハンドラへ渡す。
    Resume,  # 止まった処理へ値を渡して、再開する。
    do,  # 関数を、yieldで依頼を出す処理(Program)にする。
    handler,  # 関数を、Programに取り付けられるハンドラにする。
    run,  # ハンドラを付けたProgramを、最後まで動かす。
    with_handlers,  # ハンドラを、Programの外側に取り付ける。
)


@do  # 呼んだだけでは動かない処理(Program)にする。
def fetch_text(url: str):  # URLの本文を返す。どう通信するかは知らない。
    response = yield HttpRequest("GET", url)  # 「GETしたい」と外側へ依頼する。
    response.raise_for_status()  # 400以上の応答なら、例外にする。
    return response.text  # 本文の文字列を返す。


@handler  # この関数を、Programに取り付けられるハンドラにする。
@do  # ハンドラ自身もyieldで、再開や委譲を指示する。
def fake_http(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
    if not isinstance(effect, HttpRequest):  # HTTP以外の依頼は受け持たない。
        return (yield Pass(effect, k))  # 外側のハンドラへ、そのまま渡す。
    body = "記録した本文"  # 通信の代わりに返す本文。
    # 状態200・ヘッダなし・本体・本文・URL・かかった秒数の順に渡す。
    response = HttpResponse(200, {}, body.encode(), body, effect.url, 0.0)
    return (yield Resume(k, response))  # 応答を渡して、止まった処理を再開する。


def test_fetch_text_without_network():  # pytestが集めるテスト関数。
    program = with_handlers([fake_http], fetch_text("https://example.invalid/a"))  # 外側に付ける。
    assert run(program) == "記録した本文"  # 通信せずに、すぐ終わる。


@handler  # 記録に無い依頼だけが、ここまで届く。
@do  # 依頼を見分けて、失敗させるか外へ渡す。
def reject_http(effect, k):  # 再生だけのテストで、取得担当の位置に置く。
    if isinstance(effect, HttpRequest):  # 記録に無いHTTPの依頼が届いた。
        raise LookupError(f"記録に無い依頼: {effect.method} {effect.url}")  # 通信せずに失敗させる。
    return (yield Pass(effect, k))  # HTTP以外の依頼は、外側へ渡す。


def run_recorded(program, database, fetcher, key_fn=content_address):  # 記録の層を付けて動かす。
    program = make_memo_rewriter(HttpRequest, key_fn=key_fn)(program)  # 記録にあれば、記録から答える。
    program = sqlite_memo_handler(database)(program)  # 記録をSQLiteのファイルへ読み書きする。
    program = fetcher(program)  # 記録に無い依頼だけを、取得担当が受ける。
    program = await_handler()(slog_discard_handler(program))  # 保存の待ちと、記録のログを受ける。
    return run(scheduled(program))  # 待ちを進めながら、最後まで動かす。


def test_record_then_replay(tmp_path):  # tmp_pathは、pytestが用意する一時のディレクトリ。
    database = tmp_path / "http.sqlite"  # このテストの記録のファイル。
    url = "https://example.invalid/article"  # 実在しない、例のためのURL。
    assert run_recorded(fetch_text(url), database, fake_http) == "記録した本文"  # 1回目は取得し、記録する。
    assert run_recorded(fetch_text(url), database, reject_http) == "記録した本文"  # 2回目は記録から答える。
    with pytest.raises(LookupError):  # 記録に無いURLは、通信せずに失敗する。
        run_recorded(fetch_text(url + "/new"), database, reject_http)  # 取得担当まで届いて止まる。


@do  # APIキーをヘッダに付けて、本文を取る。
def fetch_private(url: str, api_key: str):  # 鍵の要るAPIを呼ぶ処理。
    headers = {"Authorization": f"Bearer {api_key}"}  # 鍵をヘッダに載せる。
    response = yield HttpRequest("GET", url, headers=headers)  # 鍵つきのGETを依頼する。
    response.raise_for_status()  # 400以上の応答なら、例外にする。
    return response.text  # 本文を返す。


def method_and_url(effect) -> str:  # 記録の鍵を、メソッドとURLだけで作る。
    return f"{effect.method} {effect.url}"  # ヘッダのAPIキーは、鍵に入れない。


def test_replay_without_real_key(tmp_path):  # CIに本物の鍵を置かずに再生する。
    database = tmp_path / "http.sqlite"  # このテストの記録のファイル。
    url = "https://example.invalid/private"  # 例のためのURL。
    run_recorded(fetch_private(url, "本物の鍵"), database, fake_http, method_and_url)  # 鍵つきで記録する。
    replay = fetch_private(url, "ダミー")  # CIでは、偽の鍵で同じURLを呼ぶ。
    assert run_recorded(replay, database, reject_http, method_and_url) == "記録した本文"  # 記録に当たる。


def test_default_key_misses_with_other_key(tmp_path):  # 既定の鍵では、鍵が違うと記録に当たらない。
    database = tmp_path / "http.sqlite"  # このテストの記録のファイル。
    url = "https://example.invalid/private"  # 例のためのURL。
    run_recorded(fetch_private(url, "本物の鍵"), database, fake_http)  # 既定の鍵で記録する。
    with pytest.raises(LookupError):  # ヘッダの鍵も既定の鍵に入るので、記録に当たらない。
        run_recorded(fetch_private(url, "ダミー"), database, reject_http)  # 取得担当まで届いて止まる。


class _Article(BaseHTTPRequestHandler):  # 同じ機械の中だけで答える、確かめ用のHTTPサーバー。
    def do_GET(self):  # GETに、固定の本文を返す。
        body = "サーバーの本文".encode()  # 返す本文をUTF-8にする。
        self.send_response(200)  # 状態200を返す。
        self.send_header("Content-Type", "text/plain; charset=utf-8")  # 本文の文字コードを伝える。
        self.send_header("Content-Length", str(len(body)))  # 本文の長さを伝える。
        self.end_headers()  # ヘッダを閉じる。
        self.wfile.write(body)  # 本文を書く。

    def log_message(self, *args):  # 確かめの出力を汚さないよう、アクセスの記録を出さない。
        return None  # 何も書かない。


def test_record_with_production_handler(tmp_path):  # 本番の取得担当で記録し、通信せずに再生する。
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Article)  # 空いている番号で、localhostだけに立てる。
    thread = threading.Thread(target=server.serve_forever, daemon=True)  # 別のスレッドで答えさせる。
    thread.start()  # サーバーを動かし始める。
    url = f"http://127.0.0.1:{server.server_port}/article"  # 同じ機械の中のURL。
    database = tmp_path / "http.sqlite"  # このテストの記録のファイル。
    try:  # 確かめが失敗しても、サーバーは必ず止める。
        # 記録が無い時だけ、本物のAPIへ通信して記録する。記録があれば通信しない。
        text = run_recorded(fetch_text(url), database, http_production_handler())
        assert text == "サーバーの本文"  # 1回目は、実際に通信して取った本文。
    finally:  # 次の再生は、サーバーを止めてから行う。
        server.shutdown()  # 受け付けを止める。
        server.server_close()  # 番号を放す。
    assert run_recorded(fetch_text(url), database, reject_http) == "サーバーの本文"  # 止めた後も記録で答える。


TESTS_WITH_TMP_PATH = (  # tmp_pathを受けるテスト。pytestを使わない時に、順に走らせる。
    test_record_then_replay,  # 記録して再生し、記録に無いURLは失敗する。
    test_replay_without_real_key,  # 鍵を外した記録の鍵で、偽の鍵でも再生できる。
    test_default_key_misses_with_other_key,  # 既定の鍵では、鍵が違うと当たらない。
    test_record_with_production_handler,  # 本番の取得担当で記録し、止めた後に再生する。
)


def verify() -> None:  # pytestを使わずに、全部のテストを順に走らせる。
    test_fetch_text_without_network()  # 固定の応答で、通信せずに終わる。
    with TemporaryDirectory() as directory:  # テストごとの記録のファイルを、一時の場所へ置く。
        root = Path(directory)  # 一時のディレクトリ。
        for index, test in enumerate(TESTS_WITH_TMP_PATH):  # 別々のディレクトリで走らせる。
            path = root / str(index)  # このテストだけのディレクトリ。
            path.mkdir()  # ディレクトリを作る。
            test(path)  # tmp_pathの代わりに渡す。


if __name__ == "__main__":  # 直接実行した時だけ、確かめを始める。
    verify()  # 5つのテストを走らせる。失敗すれば例外で止まる。
    print("固定の応答・記録と再生・記録に無い依頼の拒否・鍵を外した記録の鍵・本番の取得担当: OK")  # 全部通った時だけ表示する。
