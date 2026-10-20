---
title: "async/awaitがあるのに、なぜyieldで書くのか"
emoji: "🔀"
type: "tech"
topics: ["python", "asyncio", "doeff", "設計"]
published: true
published_at: 2026-11-03 08:00
---

Pythonにはasync/awaitがあるのに、ライブラリdoeffは処理を`yield`で書きます。理由は、止まった所へ答える役を、呼ばれた関数ではなく外側に置けるからです。async/awaitで書いた処理を、テストで動かす場面で比べます。

途中で名前を読み、外部のAPIを待つような処理です。async/awaitで書くと、テストでは次のようになります。

- `await`の先で何が起きるかは、呼んだ関数の実装で決まります。
- テストで替えるには、`unittest.mock.patch`を当てるか、関数を引数で渡す形に直します。
- 奥の関数が何を何回待ったかを外から見るには、待つ関数ごとに包みを足します。

doeffは、処理の読み書きや待ちを、外側の部品で差し替えるPythonのライブラリです。doeffでは、この「止まって続きを待つ」所を`yield`で書きます。連載が初めての方は[1本目の入門](https://zenn.dev/proboscis/articles/doeff-introduction)から、コードは[GitHub](https://github.com/proboscis/doeff)から読めます。

`yield`で渡すのは実装ではありません。「これをしてほしい」という依頼のデータです。同じ挨拶の処理を、2つの形で並べます。

```python
import asyncio  # 比べるためのcoroutineを、標準のイベントループで動かす。
from dataclasses import dataclass  # 依頼を、書き換えられないデータ型にする。

from doeff import Effect, Pass, Resume, do, handler, run, with_handlers  # 依頼・ハンドラ・実行。

async def read_name_async():  # await版の部品。名前を取る実装そのもの。
    await asyncio.sleep(0)  # 通信の代わりに、いったんイベントループへ譲る。
    return "花子"  # 実装が決めた名前を返す。

async def greet_async():  # await版の挨拶。
    name = await read_name_async()  # 呼んだ実装を進め、その戻り値を受け取る。
    return f"こんにちは、{name}さん"  # 名前を入れた挨拶を返す。

@dataclass(frozen=True)  # 出した依頼を、後から書き換えない型にする。
class ReadName(Effect):  # yield版の部品。「名前を返して」という依頼で、取り方は書かない。
    pass  # 引数の無い依頼なので、欄は無い。

@do  # 呼んでもすぐには動かず、動かす手順を返す。
def greet():  # yield版の挨拶。
    name = yield ReadName()  # 依頼を出して止まり、外側から返った名前を受け取る。
    return f"こんにちは、{name}さん"  # 名前を入れた挨拶を返す。

@handler  # 手順に取り付けられるハンドラにする。
@do  # ハンドラも、yieldで再開と委譲を指示する。
def hanako(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
    if isinstance(effect, ReadName):  # 名前の依頼なら、
        return (yield Resume(k, "花子"))  # 続きに「花子」を返して再開する。
    return (yield Pass(effect, k))  # 担当しない依頼は、外側のハンドラへ渡す。

assert asyncio.run(greet_async()) == "こんにちは、花子さん"  # await版は、呼んだ実装が名前を決める。
assert run(with_handlers([hanako], greet())) == "こんにちは、花子さん"  # yield版は、ハンドラが決める。
```

`greet_async`は、`read_name_async`という実装を直に進めます。`greet`は、`ReadName`という依頼を出して止まるだけです。「花子」を返したのは、外側に付けた`hanako`です。

![awaitは実装を進め、yieldは依頼を外側へ出す](/images/zenn-use-cases-v0/generated/coroutines-concept.png)
*awaitの先は、呼んだ実装で決まります。yieldの先は、外側に付けたハンドラが決めます。*

## doeffの言葉を4つだけ

- **エフェクト**: 処理が外へ出す「これをしてほしい」という依頼の値です。`ReadName()`もその1つです。
- **ハンドラ**: 届いた依頼に、何を返していつ再開するかを決める部品です。
- **`@do`**: 付けた関数は呼んでもすぐには動かず、動かす手順を返します。この手順をProgramと呼びます。
- **`yield`**: 依頼を出して止まります。ハンドラの答えを受け取ると、続きが動きます。

ハンドラの中では、次の3つの書き方を使います。

- `isinstance`で、届いた依頼の種類を見分けます。
- `Resume(k, 値)`は、止まった処理へ値を返して続きを動かします。
- 担当しない依頼は、`Pass(effect, k)`で外側のハンドラへ渡します。

`k`は止まった処理の続きで、**継続**と呼びます。`with_handlers`でハンドラを付け、`run`で動かします。

## awaitもyieldも、止まって続きを待つ

まず、似ている所から見ます。Pythonのcoroutineも、generatorと同じく止まった所から再開できます。止まった所へは、`send()`で値を送ります。

歴史もつながっています。初期の`asyncio`は、generatorと`yield from`でcoroutineを書いていました。PEP 492が、Python 3.5で`async`と`await`を足しました。

つまり、違いは止まれるかどうかではありません。**止まった所へ、誰が値を返すか**です。

| 見る所 | async/await | doeffのyield |
| --- | --- | --- |
| 関数を呼んで得る物 | coroutineオブジェクト | Program(動かす手順) |
| 止まる所で渡す物 | awaitable(実装そのもの) | 依頼のデータ |
| 止まった所へ値を返す者 | awaitableの実装とイベントループ | 依頼を担当するハンドラ |
| 担当しない操作 | ハンドラを探す決まりは無い | `Pass`で外側のハンドラへ渡す |
| 動かす物 | `asyncio`などのイベントループ | doeffの仮想機械(VM・Rust製) |

`await`は、`asyncio`だけの構文ではありません。別のイベントループも、同じ構文を使えます。`await`のたびにイベントループへ戻るわけでもありません。上の例は、戻る動きを見せるために`asyncio.sleep(0)`を置いています。

doeffのVMは、Pythonのgeneratorを進めます。依頼はまずいちばん内側のハンドラに届きます。担当しなければ、`Pass`で外側へ進みます。時間や並行の依頼を使う時は、スケジューラも付けます。

## 同じProgramに、別の答えを付ける

上のコードに続けて実行します。「太郎」と答えるハンドラを足し、子の処理を呼ぶ`twice`も書きます。

```python
@handler  # 同じ依頼へ、別の答え方をするハンドラにする。
@do  # 再開と委譲を、hanakoと同じ書き方で指示する。
def taro(effect, k):  # この範囲では、名前の依頼に「太郎」で答える。
    if isinstance(effect, ReadName):  # 名前の依頼だけを担当する。
        return (yield Resume(k, "太郎"))  # 続きに「太郎」を返して再開する。
    return (yield Pass(effect, k))  # 担当しない依頼は、外側へ渡す。

@do  # 子のProgramをyieldで呼ぶ処理にする。
def twice():  # 挨拶を2回作り、順に組で返す。
    first = yield greet()  # awaitの代わりにyieldで、子の結果を受け取る。
    second = yield greet()  # 2回目も、子の奥でReadNameの依頼が出る。
    return first, second  # 2つの挨拶を、呼んだ順に返す。

p_greet = greet()  # 動かす手順を1つ作る。まだ何も動かない。
assert run(with_handlers([hanako], p_greet)) == "こんにちは、花子さん"  # 同じ手順を花子で動かす。
assert run(with_handlers([taro], p_greet)) == "こんにちは、太郎さん"  # 同じ手順を太郎で動かす。
assert run(with_handlers([taro], twice())) == ("こんにちは、太郎さん",) * 2  # 子の奥の依頼にも届く。
```

`greet`は1文字も変えていません。答えを替えたのは、外側に付けたハンドラだけです。テストのためのpatchも、引数の追加も要りません。

`twice`の中の`yield greet()`は、`await child()`に当たる書き方です。子の奥で出た`ReadName`も、外側の同じハンドラに届きます。async版で同じことをするには、`read_name_async`を途中の関数すべてに引数で渡すか、patchを当てます。

coroutineとの違いが、もう1つあります。coroutineオブジェクトは1回しか待てず、2回目は`RuntimeError`になります。Programは動かす手順なので、上の`p_greet`のように何度でも動かせます。動かすたびに、VMが関数を呼び直して新しいgeneratorを作ります。

継続`k`は、一度だけ再開できる**one-shot**の続きです。止まった続きを複製して、2回動かしているのではありません。

## 非同期SDKとは、必要な1操作の所でつなぐ

既存の非同期SDKは、`await`で使う前提で書かれています。doeffでは、`Await`という依頼でつなぎます。

大事なのは、つなぐ範囲を狭くすることです。手順を丸ごと`async def`に書き、全体を1回の`Await`で包むとします。すると、中の操作はハンドラから見えません。

文字列の断片を返す非同期ストリームを、集める例で見ます。収集のループは`@do`に置きます。`Await`は、SDKから次の1断片を待つ所だけに使います。

```python
from collections.abc import AsyncIterator  # 文字列を非同期に1つずつ返す入力の型。

from doeff_core_effects import Await  # coroutineの完了を待つ依頼。

@do  # 収集のループも、yieldで依頼を出す処理にする。
def collect_chunks(stream: AsyncIterator[str]):  # 断片を、届いた順につなぐ。
    iterator = aiter(stream)  # 入力から非同期イテレータを取り出す。
    text = ""  # まだ何も届いていない本文。
    while True:  # 終わりが来るまで、1断片ずつ待つ。
        chunk = yield Await(anext(iterator, None))  # 次の1断片を待つ。終わりならNone。
        if chunk is None:  # 断片の文字列と、終わりの合図を見分ける。
            return text  # つないだ本文を返す。
        text += chunk  # 空の文字列も正しい断片として、順につなぐ。

@do  # 収集と仕上げを、同じyieldでつなぐ。
def introduction(stream: AsyncIterator[str]):  # 断片を集め、前後の空白を除く。
    body = yield collect_chunks(stream)  # 子のProgramから、つないだ本文を受け取る。
    return body.strip()  # 全部そろってから、前後の空白を除く。
```

この例の入力は`str`だけなので、`None`を終わりの合図にできます。空の文字列`""`は終わりではありません。本物のSDKでは、SDKのチャンクの型から本文の欄を取り出します。

![ストリームは、1断片ずつdoeffへ戻す](/images/zenn-use-cases-v0/generated/coroutines-flow.png)
*収集のループは`@do`に残します。次の1断片を待つ所だけを、`Await`で非同期の実装へつなぎます。*

## 何回待ったかを、外側から数える

通信しない非同期イテレータで動かします。`async def`は、SDKの代わりの入力を作るためだけに使います。

収集の処理は、doeffの`Listen`で包みます。`Listen`は、包んだ処理の中で出た依頼のうち、決めた型の物を集める依頼です。

```python
from doeff_core_effects import Listen  # 包んだ処理の中で出た依頼を、型を決めて集める依頼。
from doeff_core_effects.handlers import await_handler, listen_handler  # 待ちと、集める役。
from doeff_core_effects.scheduler import scheduled  # 待ちが終わったら、止まった処理を再開する。

async def chunks_for_test():  # SDKの代わりに、通信しない非同期イテレータを作る。
    for chunk in (" はじめに", "", "、", "doeff "):  # 空の断片も1つ混ぜる。
        await asyncio.sleep(0)  # 断片が届くまでの待ちを、イベントループで作る。
        yield chunk  # 断片を1つ返す。

@do  # 収集を包み、中で出たAwaitを集める処理にする。
def observed():  # 本文と、届いた待ちの依頼の列を返す。
    return (yield Listen(introduction(chunks_for_test()), types=(Await,)))  # 収集は変えずに包む。

stack = [await_handler(), listen_handler]  # 先頭が外側。Listenに答える役を内側に置く。
text, waits = run(scheduled(with_handlers(stack, observed())))  # 本物の待ちで動かす。
assert text == "はじめに、doeff"  # 空の断片で止まらず、順につないで空白を除いた。
assert len(waits) == 5  # 4つの断片と終わりの確かめで、5回の待ちが届いた。
```

`listen_handler`は、包んだ範囲に見張りのハンドラを1つ足します。見張りは届いた依頼を記録し、`Pass`で外側へ渡すだけです。実際の待ちには、外側の`await_handler()`が答えます。

5回は、4つの断片と、終わりを確かめる1回の合計です。`collect_chunks`を1文字も変えずに、待ちの回数を外から数えられました。

これが、収集を`yield collect_chunks(stream)`と書く訳です。手順がdoeffの中にあるので、待ちの間にログや時刻の依頼を足しても、同じハンドラの組で扱えます。収集を丸ごと1つの`async def`にして`Await`で包むと、届く待ちは1回だけになります。

## yieldを選ばなくてよい所

yieldにも代償があります。awaitで足りる場面と合わせて書いておきます。

- 依頼が1つで浅いなら、関数を引数で渡すだけで足ります。
- `@do`の処理を呼ぶ側も、`yield`で受け取ります。普通の値とProgramの区別は残ります。
- `asyncio`は時間だけの仕組みではありません。ネットワーク、サブプロセス、同期の道具も扱います。
- ハンドラを書く側は、値だけでなく、取り消しや例外や順序の約束も守ります。

`Await`の橋渡しにも約束があります。2026年10月17日に、mainの実装とテストで確かめた範囲です。

- `await_handler()`は、プロセスで共有する裏の`asyncio`のループで、coroutineを動かします。
- 別のイベントループに結び付いたFutureは、そのままでは待てません。
- doeffの側でタスクを取り消すと、橋渡し先のcoroutineに`CancelledError`が届きます。
- ただし、取り消しは頼むだけで、片付けの終わりは待ちません。
- 裏のループを同期の呼び出しで止めると、同じプロセスのすべての`Await`が止まります。

## まとめ

- `await`も`yield`も、止まって続きを待つ書き方です。違いは、止まった所へ誰が値を返すかです。
- `await`は呼んだ実装を進めます。`yield`は依頼のデータを出し、外側のハンドラが答えます。
- 答え方を替えても、処理の本体は変わりません。子の奥の依頼にも、同じハンドラが届きます。
- 非同期SDKとは、`Await`で1操作ずつつなぎます。手順は`@do`に残します。

| 比べる所 | async/awaitだけで書く | yieldで依頼として書く |
| --- | --- | --- |
| テストで替える所 | patchを当てるか、関数を引数で渡します | 外側のハンドラを1つ替えます |
| 子の奥の操作 | 途中の関数すべてに引数を通します | 外側のハンドラにそのまま届きます |
| 何を何回待ったか | 待つ関数ごとに包みを足します | 数えるハンドラを1つ足します |
| 同じ処理を2回動かす | coroutineを作り直します | 同じProgramを何度でも動かせます |
| 非同期SDK | そのままawaitします | Awaitで1操作ずつつなぎます |

時間の依頼を仮想の時計で答える例は、3本目のテストで20秒待つのをやめる(公開予定)にあります。次の10本目では、設計で決めた約束を、doeffのテストとして走らせます。

[doeffとは？：メイン記事へ戻る](https://zenn.dev/proboscis/articles/doeff-introduction)

## 参考

この記事のコードは、2026年10月17日に、PyPIのdoeff 0.6.0で確かめました。Pythonは3.12と3.14で試しました。GitHubにあるdoeffのmain(commit `9adc4f8`)でも、同じく動きました。コードは上から順に、前のコードの続きとして動きます。通信も本物のSDKも使っていません。

試す時は、[完全な例](https://github.com/proboscis/doeff/blob/main/publications/zenn-use-cases-v0/examples/coroutine_comparison.py)を`coroutine_comparison.py`として保存し、次のコマンドで実行します。比べる2つの形、ハンドラの差し替え、待ちの数え方をまとめて確かめます。

```bash
uv run --with "doeff==0.6.0" python coroutine_comparison.py  # PyPIのdoeff 0.6.0を入れて、完全な例を実行する。
```

- [Python公式: Coroutine objects](https://docs.python.org/3/reference/datamodel.html#coroutine-objects)
- [Python公式: Coroutines and tasks](https://docs.python.org/3/library/asyncio-task.html)
- [Python公式: Event loop](https://docs.python.org/3/library/asyncio-eventloop.html)
- [PEP 492 – Coroutines with async and await syntax](https://peps.python.org/pep-0492/)
- [Awaitの橋渡しの実装(await_handlerの説明に、取り消しと共有ループの約束)](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-core-effects/doeff_core_effects/handlers.py)
- 前の回:公開APIの型の穴をテストで塞ぎ、黙って開かせない(公開予定)
- 次の回:ADRを書いて終わりにするのをやめる(公開予定)
