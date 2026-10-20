---
title: "外部APIのテストで通信するのをやめる"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "テスト", "pytest"]
published: false
---

外部のAPIを呼ぶ処理は、テストでも通信します。この記事では、本物の応答を一度だけ記録し、テストは通信せずに記録から答えます。通信するテストには、よく次の悩みがあります。

- 呼ぶたびに通信を待つので、テストが遅くなります。
- 相手の障害や回数の制限で、コードに誤りが無くても落ちます。
- APIキーが要るので、CIに本物の鍵を置くことになります。

Pythonのライブラリdoeffで、この3つを解きます。記録に無い依頼は、通信せずに失敗させます。

doeffは、処理の中の「通信する」「待つ」といった操作を、外側の部品で差し替えられるライブラリです。連載が初めての方は[1本目の入門](https://zenn.dev/proboscis/articles/doeff-introduction)から、コードは[GitHub](https://github.com/proboscis/doeff)から読めます。まずは、通信の代わりに固定の応答を返すテストから始めます。

```python
from doeff import Pass, Resume, do, handler, run, with_handlers  # 処理の定義・ハンドラ・実行を使う。
from doeff_core_effects import HttpRequest, HttpResponse  # HTTPの依頼と応答の型。


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
```

`fetch_text`は「GETしたい」と頼むだけで、自分では通信しません。テストでは`fake_http`が依頼を受け、固定の応答を返します。

![通信の答え方を、外側のハンドラで選ぶ](/images/zenn-use-cases-v0/generated/replay-concept.png)
*処理本体は`HttpRequest`を頼むだけです。固定の応答・記録からの再生・本物の通信のどれで答えるかを、外側のハンドラが決めます。*

## 通信を、外側への依頼にする

コードに出てきた語を、先に整理します。

- `@do`を付けた関数は、呼んでもすぐには動きません。手順を表すProgramを返します。
- `yield HttpRequest("GET", url)`は、「このURLをGETしたい」という依頼を外側へ渡します。
- このような依頼を、エフェクト(effect)と呼びます。
- 依頼を受けて、どう答えるかを決める部品を、ハンドラ(handler)と呼びます。
- `with_handlers`は、ハンドラをProgramの外側に取り付けます。
- `run`は、ハンドラを付けたProgramを最後まで動かします。

`fake_http`がしていることは、次の3つだけです。

- `isinstance`で、届いた依頼がHTTPかどうかを見分けます。
- HTTPなら、`Resume(k, response)`で応答を渡し、止まっていた処理を再開します。
- HTTP以外の依頼は、`Pass`で外側のハンドラへ渡します。

`HttpResponse`は、本番で通信するハンドラが返すのと同じ型です。そのため`fetch_text`は、テストと本番の違いを知りません。

よくある手と比べると、差し替える場所が違います。

| 手 | 差し替える場所 |
| --- | --- |
| `unittest.mock.patch` | 処理が呼ぶ関数を、モジュールの中の名前で差し替えます |
| VCR.pyなどの記録ライブラリ | HTTPライブラリの内部で、やり取りを記録・再生します |
| doeffのハンドラ | 処理が出した依頼を、外側のハンドラが受け取ります |

doeffの処理は、どのHTTPライブラリで通信するかを知りません。差し替えのために、ライブラリの内部へ手を入れることもありません。

## 本物の応答を一度だけ記録し、次から再生する

固定の応答は手で書くので、本物のAPIの応答とずれていきます。そこで本物の応答を一度だけ記録し、次からは記録から答えます。記録には、doeffのメモ化の部品を使います。この記事では、記録を読み書きするハンドラの組を「記録の層」と呼びます。記録に無い時に応答を取ってくるハンドラは、「取得担当」と呼びます。

```python
import pytest  # 失敗を期待するテストに使う。
from doeff_core_effects.handlers import await_handler, slog_discard_handler  # 補助の依頼の受け手。
from doeff_core_effects.memo_handlers import (  # 記録の層を組み立てる部品。
    content_address,  # 既定の記録の鍵。依頼の全部の欄から作る。
    make_memo_rewriter,  # 記録にあれば記録から答え、無ければ外側へ依頼を出し直す。
    sqlite_memo_handler,  # 記録をSQLiteのファイルへ読み書きする。
)
from doeff_core_effects.scheduler import scheduled  # 保存の読み書きの待ちを進める。


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
```

`run_recorded`は、内側から順に3つのハンドラを付けます。3つ目の`fetcher`が、取得担当です。

| 順 | ハンドラ | 受け持つこと |
| --- | --- | --- |
| 1 | `make_memo_rewriter(HttpRequest)` | 記録にあれば、記録から答えます。無ければ依頼を外側へ出し直し、得た応答を記録します |
| 2 | `sqlite_memo_handler(database)` | 記録の読み書きを、SQLiteのファイルで行います |
| 3 | `fetcher` | 記録に無い依頼だけを受けます。固定の応答・失敗・本物の通信から選びます |

最後の2行は、補助の依頼を受ける部品です。SQLiteの読み書きは非同期で行うので、`await_handler`がその待ちを受けます。`slog_discard_handler`は、記録の層が出すログを捨てます。`scheduled`は、待っている処理の再開を受け持ちます。処理本体を`async def`にする必要はありません。

**この順番が要です。** 取得担当を記録の層より内側に置くと、記録を見る前に通信してしまいます。

`reject_http`は、再生だけのテストで使う取得担当です。HTTPの依頼が届いたら、通信せずに`LookupError`にします。記録の層が答えた依頼は、ここまで届きません。

テストは次のとおりです。

```python
def test_record_then_replay(tmp_path):  # tmp_pathは、pytestが用意する一時のディレクトリ。
    database = tmp_path / "http.sqlite"  # このテストの記録のファイル。
    url = "https://example.invalid/article"  # 実在しない、例のためのURL。
    assert run_recorded(fetch_text(url), database, fake_http) == "記録した本文"  # 1回目は取得し、記録する。
    assert run_recorded(fetch_text(url), database, reject_http) == "記録した本文"  # 2回目は記録から答える。
    with pytest.raises(LookupError):  # 記録に無いURLは、通信せずに失敗する。
        run_recorded(fetch_text(url + "/new"), database, reject_http)  # 取得担当まで届いて止まる。
```

- 1回目は`fake_http`が応答を返し、記録の層がそれを保存します。
- 2回目は`reject_http`を付けても通ります。取得担当まで、依頼が届いていない証拠です。
- 記録に無いURLは`reject_http`まで届き、`LookupError`になります。

![記録にあれば、取得担当まで進まない](/images/zenn-use-cases-v0/generated/replay-flow.png)
*記録の層は、まずSQLiteに記録があるかを尋ねます。記録があれば取得担当を通らず、無い時だけ取得担当が受けます。*

記録は、同じファイルを開けば別の`run`からも読めます。2回目はProgramとハンドラを作り直していますが、記録に当たります。別のプロセスから読んでも当たることを、2026年10月17日に確かめました。

## 記録する時だけ、本物のAPIへ通信する

本物の応答を記録する時は、取得担当を`http_production_handler()`に替えます。httpxで実際に通信する、doeffの標準の取得担当です。

```python
from doeff_core_effects.http_handlers import http_production_handler  # httpxで実際に通信する取得担当。

database = "tests/http.sqlite"  # リポジトリに入れる記録のファイル。
url = "https://api.example.com/articles/1"  # 記録したいAPIのURL。
# 記録が無い時だけ、本物のAPIへ通信して記録する。記録があれば通信しない。
text = run_recorded(fetch_text(url), database, http_production_handler())
```

- 記録が無ければ、1回だけ通信して応答を保存します。
- 記録があれば、通信しません。
- 記録のファイルは、テストと一緒にリポジトリへ入れます。
- CIのテストでは、取得担当を`reject_http`にします。

2026年10月17日に、この枠のURLを、同じ機械の中に立てたHTTPサーバーに替えて確かめました。1回目は実際に通信して、本文を記録しました。サーバーを止めた後の2回目は、`reject_http`のまま記録から答えました。外のサービスへは通信していません。

同じ日に、この記事のテストを含む完全な例の5つを、pytestで2回走らせました。2回とも5つ全部が通りました。pytestが測った関数の実行時間は、5つ合わせて約1.8秒と約2.9秒でした。同じ機械で、他の重い仕事も動いている時の値です。ライブラリを読み込む時間は、この数に含みません。

## APIキーを、記録の鍵に入れない

記録の層は、依頼から「記録の鍵」を作り、その鍵で記録を引きます。既定の鍵の`content_address`は、依頼の全部の欄から作ったハッシュです。ヘッダに載せたAPIキーも、鍵に入ります。

そのため、本物の鍵で記録してCIで偽の鍵を使うと、記録に当たりません。鍵の作り方を替えて、ヘッダを鍵から外します。

```python
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
```

- `method_and_url`は、メソッドとURLだけで鍵を作ります。
- 偽の鍵で呼んでも同じ鍵になるので、記録に当たります。
- 既定の鍵のままだと、偽の鍵の呼び出しは`LookupError`になります。これも完全な例で確かめています。

鍵から外した欄は、記録を引く時に区別されません。`params`や本文で答えが変わるAPIなら、それも鍵に入れます。

鍵はメソッドとURLか、ハッシュです。どちらでも、記録のファイルにAPIキーの文字は残りません。ただし、保存される応答のヘッダや本文には、個人の情報が入ることがあります。リポジトリへ入れる前に、記録の中身を確かめます。

## 記録の範囲と、今の実装の限界

- 記録の層は、依頼を型で選びます。`HttpRequest`なら、POSTも記録の対象です。再生してよいかは、アプリケーションで決めます。
- 400や500の応答も、値として返れば記録されます。後の`raise_for_status()`が失敗しても、記録は消えません。
- 記録の中身は、pickleで保存されます。信頼できない記録のファイルは、読み込みません。
- 既定の鍵は、依頼をpickleしたバイト列のハッシュです。doeffの版を上げた後も同じ鍵になるかは、確かめていません。
- 保存のハンドラを付け忘れると、記録の層は毎回取得へ進みます。記録が要るテストでは、2回目の再生まで確かめます。

1つのハンドラで記録と再生をする`http_fixture_handler`もあります。`mode="record"`では、本番の取得担当を内側で組み立てます。この道具もヘッダの値を鍵に入れ、鍵の作り方は替えられません。取得担当や鍵を自分で選びたい時は、この記事の組み方が向いています。

記録と再生だけなら、VCR.pyなどの記録ライブラリでもできます。doeffの記録の層は、依頼の型を選んで付けます。そのため、HTTP以外の依頼にも同じ組み方を使えます。

## まとめ

- 通信を`yield HttpRequest(...)`の依頼にすると、どう答えるかを外側で選べます。
- テストでは固定の応答のハンドラを付け、通信せずに確かめます。
- 本物の応答は、記録の層を付けて一度だけ記録し、次から再生します。
- 記録に無い依頼は`reject_http`で、通信せずに失敗させます。
- APIキーを記録の鍵から外せば、CIに本物の鍵を置かずに再生できます。

待ちを含む処理のテストは、3本目のテストで20秒待つのをやめる(公開予定)で扱いました。依存を外側から渡す話は、4本目の依存を引数で引き回すのをやめる(公開予定)にあります。次の7本目では、時刻・値段・注文を同じ形で差し替え、バックテストと本番で同じ処理を動かします。

[doeffとは？：メイン記事へ戻る](https://zenn.dev/proboscis/articles/doeff-introduction)

## 参考

この記事のコードは、2026年10月17日に、PyPIのdoeff 0.6.0で確かめました。Pythonは3.12と3.14で試しました。GitHubにあるdoeffのmain(commit `9adc4f8`)でも、同じく動きました。コードは上から順に、前のコードの続きとして動きます。本物のAPIへ通信する枠は、mainの版でだけ、同じ機械のサーバーに替えて確かめました。

試す時は、[完全な例](https://github.com/proboscis/doeff/blob/main/publications/zenn-use-cases-v0/examples/http_replay.py)を`http_replay.py`として保存し、次のコマンドで実行します。

```bash
uv run --with "doeff[http]==0.6.0" --with pytest pytest -q http_replay.py  # PyPIのdoeff 0.6.0を入れて、完全な例の5つのテストを走らせる。
```

- [doeff本体(この記事で確かめた版)](https://github.com/proboscis/doeff/tree/9adc4f8e657a552f5253d8a7b422363789c2608c)
- [HttpRequestとHttpResponseの定義](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-core-effects/doeff_core_effects/http_effects.hy)
- [記録の層(make_memo_rewriter)](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-core-effects/doeff_core_effects/memo_handlers.py)
- [SQLiteへの保存](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-core-effects/doeff_core_effects/storage/sqlite.py)
- [標準の取得担当とhttp_fixture_handler](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-core-effects/doeff_core_effects/_http_handlers_impl.hy)
- [unittest.mock(Python公式の説明)](https://docs.python.org/ja/3/library/unittest.mock.html)
- [VCR.py(公式の説明)](https://vcrpy.readthedocs.io/)
- 前の回:ライブラリのcommitの68%を、Claude Codeと書いた1年(公開予定)
- 次の回:バックテストと本番で処理を2つ書くのをやめる(公開予定)
