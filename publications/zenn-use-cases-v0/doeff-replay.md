---
title: "外部APIのテストで通信するのをやめる"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "algebraiceffects", "テスト"]
published: false
---

ハンドラには、エフェクトが出ていく所と、その答えが戻る所が見えます。だから、その2つを取っておけます。取っておいた物から答えるハンドラの下で、同じプログラムをもう一度走らせます。するとHTTP APIの答えが、依頼を1つも送らずに返ります。

doeffで、Pythonの代数的エフェクト(algebraic effects)を紹介します。[1本目の入門](doeff-main.md)では、仕組みを見ました。関数はエフェクト、つまり操作に名前を付けた値を`yield`します。ハンドラはそれを、関数の残りである`k`と一緒に受け取ります。そして、関数をどう進めるかを決めます。3本目の[テストで20秒待つのをやめる](doeff-time.md)では、時計をハンドラにしました。この記事では、答えを取っておきます。コードは[GitHub](https://github.com/proboscis/doeff)から読めます。

プログラムが外の世界に触れる道は、`yield`するエフェクトだけだとします。エフェクトは1つずつハンドラへ出ていき、答えが戻ります。依頼と答えをすべて書き留めれば、やり取りの全部が手に入ります。書き留めた物から同じ依頼に答えれば、プログラムは同じように走ります。向こう側に誰もいなくてもです。Temporalのような持続するワークフローの基盤は、出来事の履歴を再生します。そうして、落ちたワークフローを立て直します。ここでは同じ考えが、数行のハンドラに収まります。

asyncioでコードを書く人なら、coroutineの奥の`await client.get(url)`と比べてください。そのcoroutineは、依頼を自分で送ります。だから記録するには、クライアントの中へ手を入れることになります。エフェクトでは、依頼はプログラムから出ていく値です。記録の係は、その出口に置くハンドラの1つにすぎません。

## プログラムが頼み、ハンドラが答える

`latest_releases`は、GitHubのAPIに、リポジトリごとの最新のリリースを尋ねます。

```python
# releases.py(抜粋)
import json  # APIの答えの本文を、JSONとして読む。
from collections import Counter  # 送った依頼を、URLごとに数える。

from doeff import Resume, ResumeThrow, do, handler, run  # 再開・例外での再開・定義・ハンドラ・実行を使う。
from doeff_core_effects import HttpRequest, HttpResponse  # HTTPの依頼のエフェクトと、その答えの型。

GITHUB = "https://api.github.com"  # GitHubのAPIの根のURL。


@do  # yieldのたびに止まるプログラムにする。
def latest_releases(repos: list[str], token: str, api: str = GITHUB):  # 尋ねるだけで、自分では送らない。
    tags = {}  # リポジトリごとの最新のタグを集める。
    for repo in repos:  # リポジトリを1つずつ尋ねる。
        response = yield HttpRequest(  # 依頼を出して止まる。答え方はハンドラが決める。
            "GET",  # メソッド。
            f"{api}/repos/{repo}/releases/latest",  # 最新のリリースのURL。
            headers={"Authorization": f"Bearer {token}"},  # トークンをヘッダに載せる。
        )
        response.raise_for_status()  # 400以上の答えなら、例外にする。
        tags[repo] = json.loads(response.text)["tag_name"]  # 本文のJSONから、タグの名を取る。
    return tags  # リポジトリからタグへの辞書を返す。
```

`HttpRequest`は、doeffのパッケージのエフェクトです。`HttpResponse`は、そのハンドラが返す答えです。`latest_releases`は、何も送りません。依頼を`yield`し、何が起きるかは`HttpRequest`のハンドラが決めます。本番では、httpxで送る`http_production_handler()`がそのハンドラです。最後の節で、手元のサーバーに向けて走らせます。それまでは`github`が、APIと同じ形で辞書から答えます。そして、届いた依頼を1つずつ数えます。

```python
# releases.py(抜粋)
# GitHubのAPIと同じ形で、辞書から答える。だからこのファイルは通信せずに走る。
# live.pyは、同じ依頼をHTTPで送る。
TAGS = {"acme/server": "v2.4.1", "acme/client": "v1.9.0"}  # リポジトリごとの最新のタグ。
sent: Counter[str] = Counter()  # URLごとに、送った依頼の数。


@handler  # HttpRequestを受けるハンドラにする。
def github(effect: HttpRequest, k):  # ここに届いた依頼は、送った物として数える。
    sent[effect.url] += 1  # このURLへの依頼を1つ数える。
    repo = effect.url.removeprefix(f"{GITHUB}/repos/").removesuffix("/releases/latest")  # URLからリポジトリの名を取る。
    body = json.dumps({"tag_name": TAGS[repo]})  # APIと同じ形の本文を作る。
    return (yield Resume(k, HttpResponse(200, {}, body.encode(), body, effect.url, 0.0)))  # 状態200の答えでkを再開する。
```

## 記録の係は、エフェクトをもう一度出す

記録の係は、プログラムと、答えるハンドラの間に座ります。

```python
# releases.py(抜粋)
def recording(tape: dict):  # 依頼を1つずつ外へ出し、その答えを取っておく。
    @handler  # HttpRequestを受けるハンドラにする。
    def record(effect: HttpRequest, k):  # 依頼と答えの組を、tapeに書く。
        answer = yield effect  # 同じ依頼を出し直す。1つ外のハンドラが答える。
        tape[(effect.method, effect.url)] = answer  # メソッドとURLの組で、答えを書き留める。
        return (yield Resume(k, answer))  # 同じ答えで、kを再開する。

    return record  # 作ったハンドラを返す。
```

ハンドラの中の`yield effect`は、受け取ったエフェクトをもう一度出します。答えるのは、1つ外のハンドラです。だから`record`には、依頼が出ていく所と、答えが戻る所が見えます。`record`はその組を取っておき、同じ答えで`k`を再開します。`latest_releases`は、記録の係が間に無い時と同じ物を受け取ります。そして`latest_releases`の中には、記録されている印が何もありません。

![recordは、出ていく依頼と戻る答えを両方見る](/images/zenn-use-cases-v0/generated/replay-concept.png)
*recordは依頼を外のgithubへ出し直し、答えをtapeに書いてから、同じ答えでkを再開します。*

tapeは、依頼をメソッドとURLで引きます。欄が同じ`HttpRequest`の値どうしでも、等しくはなりません。値ではなく、同一性で比べるからです。そこで記録の係が、どの2つの依頼を同じと見るかを決めます。この係は、ヘッダを鍵から外しました。この選び方は、ヘッダのトークンが効いてくる後の節で、また出てきます。

## 再生の係は、tapeから答える

```python
# releases.py(抜粋)
def replaying(tape: dict):  # tapeから答え、外へは決して出さない。
    @handler  # HttpRequestを受けるハンドラにする。
    def replay(effect: HttpRequest, k):  # 記録した答えを返す。
        key = (effect.method, effect.url)  # 記録の係と同じ鍵を作る。
        if key not in tape:  # tapeに無い依頼なら、
            error = LookupError(f"not recorded: {effect.method} {effect.url}")  # 記録に無い事を言う例外を作る。
            return (yield ResumeThrow(k, error))  # プログラムのyieldの所で、例外を起こす。
        return (yield Resume(k, tape[key]))  # 記録した答えで、kを再開する。

    return replay  # 作ったハンドラを返す。


@do  # プログラムを包むプログラムにする。
def reported(program):  # ハンドラが投げ込んだ例外を、呼ぶ側のtryで受ける。
    try:  # 包んだプログラムを走らせる。
        return (yield program)  # 包んだプログラムの結果を、そのまま返す。
    except LookupError as error:  # 記録に無い依頼で止まったら、
        return f"LookupError: {error}"  # 例外の型と文を返す。
```

`replay`は、エフェクトを決して外へ出しません。tapeに在る依頼には、記録した答えが返ります。tapeに無い依頼には、`ResumeThrow(k, error)`が返ります。`LookupError`は、`latest_releases`の中の`yield`の所で起きます。そしてほかの例外と同じく上へ伝わり、ここでは`reported`の`try`に届きます。

一度記録し、同じプログラムを再生の係だけの下で走らせます。

```python
print(run(github(recording(tape)(latest_releases(REPOS, "secret")))))  # githubの内側に記録の係を付けて走らせる。
print(run(replaying(tape)(latest_releases(REPOS, "secret"))))  # 同じプログラムを、再生の係だけで走らせる。
```

```text
{'acme/server': 'v2.4.1', 'acme/client': 'v1.9.0'}
sent: 2
```

```text
{'acme/server': 'v2.4.1', 'acme/client': 'v1.9.0'}
sent: 0
```

同じ関数で同じ答えが返り、2回目には依頼が1つもありません。2回目は、再生の係の外に`HttpRequest`のハンドラがありません。だから再生が、うっかり通信に届く事はありません。仮に`replay`が依頼を外へ渡せば、doeffは`UnhandledEffect`で走りを止めます。記録していないリポジトリは、その`yield`の所で止まります。

```python
print(run(replaying(tape)(reported(latest_releases(["acme/docs"], "secret")))))  # 記録していないリポジトリを尋ねる。
```

```text
LookupError: not recorded: GET https://api.github.com/repos/acme/docs/releases/latest
```

## tapeをファイルに置く

辞書は、プロセスが生きている間しか残りません。doeffのパッケージには、記録の係と再生の係を1つにしたハンドラがあります。後ろに保存先を持つ`make_memo_rewriter`です。

```python
# on_disk.py(抜粋)
def recorded(program, database, network, key=content_address):  # 保存の層を付けて、最後まで走らせる。
    program = make_memo_rewriter(HttpRequest, key_fn=key)(program)  # 在れば保存から答え、無ければ外へ出して残す。
    program = sqlite_memo_handler(database)(program)  # 保存: MemoExists・MemoGet・MemoPutにSQLiteのファイルで答える。
    program = network(program)  # 保存に無い依頼だけが、ここまで届く。
    return run(scheduled(await_handler()(slog_discard_handler(program))))  # 補助のハンドラを付けて走らせる。


@handler  # HttpRequestを受けるハンドラにする。
def offline(effect: HttpRequest, k):  # 通信の位置に置く。保存に無い依頼は失敗し、何も送らない。
    raise LookupError(f"not recorded: {effect.method} {effect.url}")  # 記録に無い事を言う例外を起こす。
```

内側から順に見ます。

| ハンドラ | すること |
|---|---|
| `make_memo_rewriter(HttpRequest, key_fn=key)` | `HttpRequest`ごとに鍵を作り、`MemoExists(key)`を`yield`します。在れば`MemoGet(key)`を`yield`し、保存した答えで`k`を再開します。無ければ`record`と同じく、依頼を外へ出します。そして`MemoPut(key, answer)`を`yield`し、`k`を再開します |
| `sqlite_memo_handler(database)` | `MemoExists`・`MemoGet`・`MemoPut`に、SQLiteのファイルで答えます |
| `network` | 保存に無い依頼だけを受けます。ここでは`github`、本番では`http_production_handler()`、CIでは`offline`です |
| `slog_discard_handler`・`await_handler`・`scheduled` | `slog_discard_handler`は、`make_memo_rewriter`のログの行を捨てます。残りの2つは、保存の層が`yield`する`Await`を走らせます。ファイルの読み書きを、worker threadで行うためです |

![保存に在れば、通信の位置まで届かない](/images/zenn-use-cases-v0/generated/replay-flow.png)
*make_memo_rewriterは、まず保存に在るかを尋ねます。在れば保存から答え、無い時だけ依頼を外へ出して答えを残します。*

保存も、エフェクトです。`make_memo_rewriter`は、SQLiteについて何も知りません。`MemoGet`と`MemoPut`を`yield`し、行き先は1つ外のハンドラが決めます。`sqlite_memo_handler(database)`の所は、ほかの保存のハンドラでも構いません。`in_memory_memo_handler()`を置けば、tapeはメモリに残ります。`make_memo_rewriter`は、そのままで同じように働きます。

`offline`は、通信の位置に座ります。ファイルが答える依頼は、そこまで届きません。ほかの依頼は、`LookupError`を起こします。例外は前と同じくプログラムの`yield`に届き、何も送られません。

```python
print(recorded(latest_releases(REPOS, "secret"), tape, github))  # 1回目: ファイルは空なので、githubが答える。
print(recorded(latest_releases(REPOS, "secret"), tape, offline))  # 2回目: 通信の位置にofflineを置いて走らせる。
```

```text
{'acme/server': 'v2.4.1', 'acme/client': 'v1.9.0'}
sent: 2
```

```text
{'acme/server': 'v2.4.1', 'acme/client': 'v1.9.0'}
sent: 0
```

2回目は、保存のハンドラまで新しく作った組で走ります。同じなのは、ファイルだけです。別のプロセスのテストやCIも、同じようにそのファイルから再生します。

## 2つの依頼を、何で同じと見るか

今度は、別のトークンで再生します。本物のトークンを持たない、CIと同じ形です。

```python
print(recorded(reported(latest_releases(REPOS, "ci-dummy")), tape, offline))  # CIのように、別のトークンで再生する。
```

```text
LookupError: not recorded: GET https://api.github.com/repos/acme/server/releases/latest
```

既定の鍵の`content_address`は、依頼の全部のハッシュです。メソッド・URL・ヘッダ・params・本文・時間の上限が入ります。トークンはヘッダに在るので、別のトークンの依頼は別の依頼です。だから、保存に当たりません。上で手で書いたtapeは、メソッドとURLで引きました。同じ鍵を、`make_memo_rewriter`に渡します。

```python
# on_disk.py(抜粋)
def method_and_url(effect: HttpRequest) -> str:  # ヘッダを外した鍵。だからトークンも入らない。
    return f"{effect.method} {effect.url}"  # メソッドとURLだけで鍵を作る。
```

```python
recorded(latest_releases(REPOS, "secret"), keyed, github, method_and_url)  # メソッドとURLの鍵で記録する。
print(recorded(latest_releases(REPOS, "ci-dummy"), keyed, offline, method_and_url))  # 別のトークンで再生する。
```

```text
{'acme/server': 'v2.4.1', 'acme/client': 'v1.9.0'}
```

鍵は、APIごとに選びます。鍵から外した物は、再生の時に区別されません。答えが`params`や本文で変わるなら、それも鍵に入れます。どちらの鍵でも、ファイルにトークンは残りません。鍵は`GET https://...`の文字列かハッシュで、保存する答えは応答だからです。ただし、応答は丸ごと保存されます。そのヘッダや本文には、個人の情報が入ることがあります。commitする前に、ファイルを読んで確かめます。

## I/O:動いているサーバーで記録し、止めて再生する

ここまでは、`github`が辞書から答えてきました。`live.py`は、GitHubのAPIと同じ形で答えるHTTPサーバーを手元に立てます。`http_production_handler()`で記録し、サーバーを止め、ファイルから再生します。

```python
# live.py(抜粋)
if __name__ == "__main__":  # ファイルとして走らせた時だけ動く。
    server = ThreadingHTTPServer(("127.0.0.1", 0), Releases)  # 手元に、空いた番号のHTTPサーバーを立てる。
    threading.Thread(target=server.serve_forever, daemon=True).start()  # 別のスレッドで答えさせる。
    api = f"http://127.0.0.1:{server.server_port}"  # 手元のサーバーのURL。
    with tempfile.TemporaryDirectory() as directory:  # 記録のファイルを、一時のdirに置く。
        tape = Path(directory) / "releases.sqlite"  # 記録のSQLiteのファイル。
        print("-- live: httpx sends each request, and the file keeps each answer")  # 区切りの見出し。
        print(recorded(latest_releases(REPOS, "secret", api), tape, http_production_handler()))  # httpxで送り、記録する。
        print(f"served: {served.total()}")  # サーバーが答えた数。
        server.shutdown()  # サーバーを止める。
        server.server_close()  # サーバーの口を閉じる。
        print("-- stopped: the server is gone, and the same file answers")  # 区切りの見出し。
        print(recorded(latest_releases(REPOS, "secret", api), tape, offline))  # 同じファイルが答える。
```

```text
{'acme/server': 'v2.4.1', 'acme/client': 'v1.9.0'}
served: 2
```

```text
{'acme/server': 'v2.4.1', 'acme/client': 'v1.9.0'}
```

1回目は、HTTPで通信します。`http_production_handler()`は、`httpx.AsyncClient`で依頼を送ります。その待ちは、`Await`を通します。サーバーは2回答えます。2回目は話す相手のサーバーが無く、ファイルが答えます。`latest_releases`は前と同じ関数で、`api`を手元のサーバーへ向けただけです。どこにも`async`がありません。その訳は、9本目の[async/awaitがあるのに、なぜyieldで書くのか](doeff-coroutines.md)で扱います。自分のリポジトリでは、`http_production_handler()`で一度記録し、ファイルをcommitします。CIでは、その所に`offline`を置きます。トークンは要りません。

VCR.pyや`unittest.mock`を知っている人もいるでしょう。VCR.pyは、requestsやhttpxのようなHTTPクライアントにpatchを当てます。そうして、やり取りをカセットに記録します。`unittest.mock`では、テストがクライアントを名前で差し替えます。どちらも、クライアントの所で働きます。VCR.pyはpatchを当てるライブラリを知り、mockは差し替える名前を知っています。ここの記録の係は、エフェクトの所で働きます。どのクライアントが依頼を送るかを知りません。そして同じハンドラが、プログラムが`yield`するどのエフェクトも記録します。[testing_without_mocks/record_replay.py](https://github.com/proboscis/doeff/blob/main/examples/testing_without_mocks/record_replay.py)は、言語モデルの答えを記録します。docsの章[Why Effects?](https://github.com/proboscis/doeff/blob/main/docs/20-why-effects-over-di.md#6-keep-the-answers-record-once-replay)は、1つの記録の係で`Chat`と`Draw`を記録します。

## 限界

- 保存する答えは、pickleで残します。信頼できるファイルだけを開きます。pickleを読むと、コードが走ることがあるからです。
- 既定の鍵は、pickleした依頼のハッシュです。doeffやPythonの版をまたいで同じままかは、約束されていません。`method_and_url`のような自分の鍵は、自分で決めるただの文字列です。
- 保存のハンドラを付けないと、`make_memo_rewriter`はどの依頼も保存に無いと見ます。そして毎回、黙って外へ出します。通信の位置に`offline`を置いた再生なら、これに気付けます。保存に無い依頼が、例外を起こすからです。
- `make_memo_rewriter`は、型で選んで記録します。POSTも含めて、どの`HttpRequest`も記録します。記録したPOSTは、再生で送り直しません。たいていは、それがテストの望む形です。いつも外へ出したい依頼があるなら、`record`のような係を書きます。そして、その依頼を`Pass`で外へ渡します。
- 再生で分かるのは、記録した答えをコードがどう扱うかです。APIが今もそう答えるかは分かりません。APIが変わったら、記録し直します。

## 試す

```bash
pip install "doeff[http]"   # CPython 3.11から3.14で入る。http extraで、HttpRequestとhttpxが入る。
python releases.py  # 記録・再生と、記録に無いリポジトリを走らせる。
python on_disk.py  # ファイルに置いた記録と、トークンを外した鍵を走らせる。
python live.py  # 手元のサーバーで記録し、止めてから再生する。
```

この記事の例はどれも、PyPIのdoeff 0.8.0で、載せたとおりに走ります。完全なファイルは、[examples/record_and_replay](https://github.com/proboscis/doeff/tree/main/examples/record_and_replay)にあります。docsの章[Cache System](https://github.com/proboscis/doeff/blob/main/docs/07-cache-system.md)は、`CacheGet`と`CachePut`を扱います。プログラムが、わざとキャッシュする時に自分で`yield`する物です。ここの`make_memo_rewriter`は、外から来た答えを取っておきます。プログラムは、何も頼みません。

時計をハンドラにし、何時間もの待ちを一瞬で走らせる話は、3本目の[テストで20秒待つのをやめる](doeff-time.md)にあります。[1本目の入門](doeff-main.md)は、時間・並行・リトライ・予算・I/Oのハンドラを、1つのプログラムの周りに重ねます。

自分のコードで、どの答えを取っておきたいですか。鍵を選びにくいのは、どこでしょう。issueは https://github.com/proboscis/doeff/issues で受け付けています。

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考

この記事のコードは、2026年10月29日に、PyPIのdoeff 0.8.0で確かめました。Pythonは3.11・3.12・3.14で試しました。

- [HttpRequestとHttpResponseの定義(doeff 0.8.0)](https://github.com/proboscis/doeff/blob/v0.8.0/packages/doeff-core-effects/doeff_core_effects/http_effects.hy)
- [make_memo_rewriterとcontent_addressの実装](https://github.com/proboscis/doeff/blob/v0.8.0/packages/doeff-core-effects/doeff_core_effects/memo_handlers.py)
- [SQLiteへの保存の実装](https://github.com/proboscis/doeff/blob/v0.8.0/packages/doeff-core-effects/doeff_core_effects/storage/sqlite.py)
- [http_production_handlerの実装](https://github.com/proboscis/doeff/blob/v0.8.0/packages/doeff-core-effects/doeff_core_effects/_http_handlers_impl.hy)
- [VCR.py(公式の説明)](https://vcrpy.readthedocs.io/)
- 前の回:[ライブラリのcommitの68%を、Claude Codeと書いた1年](doeff-commits.md)
- 次の回:[バックテストと本番で処理を2つ書くのをやめる](doeff-backtest.md)
