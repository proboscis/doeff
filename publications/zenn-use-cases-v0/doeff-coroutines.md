---
title: "async/awaitがあるのに、なぜyieldで書くのか"
emoji: "🔀"
type: "tech"
topics: ["python", "asyncio", "doeff", "algebraiceffects"]
published: false
---

Pythonの`await`とdoeffの`yield`は、どちらもcoroutineを止めます。違うのは、止まった時の要求がどこへ行き、止まったコードを誰が再開するかです。`await`では、呼んだコードと、いちばん上に1つだけ在るイベントループが受け持ちます。エフェクトでは、呼ぶ側が付けたいちばん近いハンドラが、関数の残りを持って答えます。

doeffで、Pythonの代数的エフェクト(algebraic effects)を紹介します。[1本目の入門](doeff-main.md)では、仕組みを見ました。関数はエフェクト、つまり操作に名前を付けた値を`yield`します。ハンドラはそれを、関数の残りである`k`と一緒に受け取ります。そして、関数をどう進めるかを決めます。コードは[GitHub](https://github.com/proboscis/doeff)から読めます。

asyncioでコードを書く人なら、まず訳を問うはずです。Pythonには、止まって再開する関数の構文がもう在ります。`async def`と`await`です。なぜ`@do`と`yield`で書くのでしょうか。

どちらもcoroutineで、止まり方は同じです。違いは2つで、代数的エフェクトにはそれぞれの名前があります。1つは、止まった時に要求がどこへ行くかです。つまり、どのハンドラが操作を受け取るかです。もう1つは、止まったコードがどう進むかです。つまり、受け取った側が継続で何をできるかです。この記事では、走らせられるコードで1つずつ見ます。

## 同じ関数を、2通りに書く

```python
# greet.py(抜粋)
import asyncio  # 比べるためのcoroutineを、標準のイベントループで走らせる。
from dataclasses import dataclass  # エフェクトを、値を持つクラスとして書く。

from doeff import EffectBase, Gather, Resume, Spawn, do, handler, run  # エフェクトの基底・並行・再開・定義・実行を使う。
from doeff_core_effects.scheduler import scheduled  # SpawnとGatherに答える参考のハンドラ。


# asyncとawaitでは、呼び出しが、答えるコードを名指す。
async def read_name_async() -> str:  # 実装そのもの。
    await asyncio.sleep(0)  # いったんイベントループへ譲る。
    return "Ada"  # 実装が決めた名前を返す。


async def greet_async() -> str:  # 呼んだ関数が返す名前で挨拶する。
    name = await read_name_async()  # read_name_asyncを、この場で今走らせる。
    return f"Hello, {name}"  # 名前を入れた挨拶を返す。


# @doとyieldでは、呼び出しが操作を名指し、ハンドラが答える。
@dataclass(frozen=True)  # 変わらない値にする。
class ReadName(EffectBase):  # エフェクト:「名前をください」。取り方は何も言わない。
    pass  # 欄の無いエフェクト。


@do  # greetを、yieldのたびに止まるプログラムにする。
def greet():  # ハンドラが答えた名前で挨拶する。
    name = yield ReadName()  # 止まる。いちばん近いReadNameのハンドラが答える。
    return f"Hello, {name}"  # 名前を入れた挨拶を返す。


@handler  # ReadNameを受けるハンドラにする。
def ada(effect: ReadName, k):  # 「Ada」と答え、greetの残りを走らせる。
    return (yield Resume(k, "Ada"))  # kを「Ada」で再開し、その結果を返す。
```

```python
print(asyncio.run(greet_async()))  # async版を、asyncioのイベントループで走らせる。
print(run(ada(greet())))  # yield版を、周りにadaを付けて走らせる。
```

```text
Hello, Ada
Hello, Ada
```

2つの関数は似ていて、同じ行を印字します。違うのは、止まる所でそれぞれが何を名指すかです。

- `await read_name_async()`で、`greet_async`は関数を名指します。どのコードかはimportで決まります。そのコードは同じ呼び出しの連なりの一部として、その場で走ります。
- `yield ReadName()`で、`greet`は操作を名指して止まります。`ReadName()`は値で、名前の取り方を言いません。答えるのは`ada`です。呼ぶ側が`greet()`の周りに`ada`を付けたからです。

`greet_async`が受け取るのは、`read_name_async`が返す名前だけです。`greet`は、周りのハンドラが答える物を何でも受け取ります。async版にほかの道が無い訳を知るため、`await`を越えて実際に何が渡るかを見ます。

## `await`の行き先

`async def`の関数はcoroutineで、generatorと同じ仕組みの上に作られています。止まったcoroutineは、誰かが`send()`を呼ぶと進みます。asyncioは、generatorから始まりました。PEP 492が、Python 3.5で`async`と`await`を足しました。その前のasyncioのcoroutineは、`yield from`を使うgeneratorでした。`await`は今も`yield from`と同じく働きます。待たれた物が何かをyieldすると、それは間のcoroutineをすべて素通りします。そして、`send()`を呼んだ者へ出ます。

これは、asyncioを使わずに見られます。`Ask`は自分自身をyieldするawaitableで、`drive`がイベントループの役をします。

```python
# drive.py(抜粋)
import asyncio  # 後で、同じcoroutineをasyncioでも走らせる。
from dataclasses import dataclass  # 要求を、値を持つクラスとして書く。


@dataclass(frozen=True)  # 変わらない値にする。
class Ask:  # coroutineがawaitで上へ渡す要求。
    key: str  # 何を尋ねるか。

    def __await__(self):  # awaitされた時の動き。
        return (yield self)  # send()を呼んだ者が、この値を受け取る。


async def greet_async():  # 走らせる者が答えた名前で挨拶する。
    name = await Ask("name")  # 名前を尋ねて止まる。
    return f"Hello, {name}"  # 名前を入れた挨拶を返す。


async def twice_async():  # 中を通るAskを、1つも見ない。
    return (await greet_async(), await greet_async())  # 挨拶を2回作り、組で返す。


def drive(coroutine, answers: dict[str, str]):  # どのAskも、ここへ届く。
    value = None  # 最初のsend()は、Noneで始める。
    while True:  # coroutineが終わるまで回す。
        try:  # 次に止まる所まで進める。
            request = coroutine.send(value)  # 値を送って進め、次の要求を受け取る。
        except StopIteration as stop:  # coroutineが終わったら、
            return stop.value  # その戻り値を返す。
        value = answers[request.key]  # 要求の鍵で答えを引き、次のsend()で渡す。
```

```python
print(drive(twice_async(), {"name": "Ada"}))  # 手で書いたdriveで走らせる。
```

```text
('Hello, Ada', 'Hello, Ada')
```

`drive`は、すべての`Ask`を受け取ります。`greet_async`が1段下で出す`Ask`もです。`twice_async`はそれを見られず、答える事も変える事もできません。`await`が素通りさせるからです。同じcoroutineをasyncioで走らせると、`drive`の場所にイベントループが入ります。

```python
asyncio.run(twice_async())  # 同じcoroutineを、asyncioのイベントループで走らせる。
```

```text
RuntimeError: Task got bad yield: Ask(key='name')
```

coroutineを走らせる`Task`は、`send()`を呼ぶコードです。だから、プログラムのすべての要求は最後にそこへ届きます。Taskが分かるのは、2種類だけです。

- awaitの連なりのいちばん下には、自分自身をyieldする`Future`があります。Taskはそれにcallbackを付け、futureが済むと`send(None)`を呼びます。すると、futureの`__await__`が`future.result()`を返します。
- `asyncio.sleep(0)`が使う素の`yield`は、「先にほかのタスクを走らせて」の意味です。

それ以外の物は、bad yieldです。trioも、自分で決めた要求の物で同じように働きます。

![awaitの要求は、間のcoroutineを素通りしてTaskへ届く](/images/zenn-use-cases-v0/generated/coroutines-concept.png)
*twice_asyncは、greet_asyncの要求を見ません。受け取るのは、send()を呼ぶイベントループのTaskだけです。*

代数的エフェクトの言葉で言うと、次のとおりです。

- `await`を越えるのは操作で、止まったcoroutineはone-shotの継続です。
- イベントループはハンドラです。`asyncio.run`がいちばん上に1度だけ付ける、ただ1つのハンドラです。答えるのは、ループが決めた操作だけです。
- `await child()`は呼び出しです。子は呼び手の一部として走ります。子とループの間のcoroutineは、子の要求に加わりません。

一部の要求に答え、ほかを上へ渡すdriverは、自分でも書けます。そのdriverが、ハンドラです。doeffはそういうdriverで、Rustで書いたVMが走らせます。どの呼ぶ側も、プログラムのどの部分の周りにもハンドラを付けられます。doeffは素のgeneratorを走らせるので、プログラムにイベントループは要りません。

## `yield`の行き先

doeffでは、エフェクトはいちばん上へは行きません。それを出したコードを囲む、いちばん内側のハンドラへ行きます。呼ぶ側は、プログラムのどの部分の周りにもハンドラを付けられます。間のハンドラは、エフェクトを出し直して答える事もできます。出し直したエフェクトには、1つ外のハンドラが答えます。

```python
# greet.py(抜粋)
# 呼ぶ側が、プログラムの一部の周りに付けるハンドラ。
@handler  # ReadNameを受けるハンドラにする。
def grace(effect: ReadName, k):  # 同じエフェクトに、別の答え。
    return (yield Resume(k, "Grace"))  # kを「Grace」で再開する。


@handler  # ReadNameを受けるハンドラにする。
def titled(effect: ReadName, k):  # 1つ外のハンドラに尋ねてから答える。
    name = yield effect  # 同じエフェクトを出し直し、外の答えを受け取る。
    return (yield Resume(k, f"Dr. {name}"))  # 前に「Dr. 」を付けて、kを再開する。


@do  # twiceを、yieldのたびに止まるプログラムにする。
def twice():  # 挨拶を2回。2回目は、自分のハンドラの下で作る。
    first = yield greet()  # `await greet_async()`と同じく、子はここで走る。
    second = yield grace(greet())  # この呼び出しだけが、周りにgraceを付けて走る。
    return first, second  # 2つの挨拶を組で返す。
```

```python
print(run(ada(twice())))  # 周りにadaを付けて走らせる。
print(run(ada(titled(twice()))))  # twiceとadaの間に、titledを挟む。
```

```text
('Hello, Ada', 'Hello, Grace')
('Hello, Dr. Ada', 'Hello, Grace')
```

`yield greet()`は、`await greet_async()`に当たる書き方です。子は親の一部として走り、子のエフェクトは親のハンドラを通って外へ出ます。`twice`は、2回目の呼び出しの周りにだけ`grace`を付けます。その呼び出しの`ReadName`は`grace`で止まり、1回目の物は外へ進みます。

2つ目の実行では、`twice`と`ada`の間に`titled`が入ります。ハンドラの中の`yield effect`はエフェクトを出し直し、1つ外のハンドラが答えます。`titled`はその答えを受け取り、前に`Dr. `を付けて`k`を再開します。2回目の呼び出しは、`titled`へ届きません。`grace`のほうが近いからです。`greet`は、どの実行でも同じ関数です。

2つ目の実行の1つ目の`ReadName`は、次のように進みます。

![titledが、ReadNameを外のadaへ出し直す](/images/zenn-use-cases-v0/generated/coroutines-flow.png)
*titledはReadNameをadaへ出し直し、答えのAdaにDr. を付けて、greetの続きkを再開します。*

| | `async def`と`await` | `@do`と`yield` |
|---|---|---|
| 止まる所を越える物 | 待たれた物がyieldする物(asyncioでは`Future`) | エフェクト(操作に名前を付けた値) |
| 受け取る者 | `send()`を呼ぶコード(いちばん上の、イベントループの`Task`) | このエフェクトを出したコードを囲む、いちばん内側のハンドラ |
| 間のコード | 素通りさせる | 間のハンドラが答えるか、1つ外のハンドラへ出し直す |
| 受け取る者を選ぶ者 | `asyncio.run`が、プログラム全体に1度 | 呼ぶ側が、プログラムの部分ごとに |

asyncioにも、「この呼び出しだけ」の道具はあります。`contextvars.ContextVar`です。呼び出しの周りで値を置き、その値は各タスクへ写されます。範囲を決められるのは値で、その値を呼ぶ関数にもできます。その関数は、答えて返ります。呼び手の残りは受け取らないので、呼び手が進むか、いつどう進むかを決められません。それが、2つ目の違いです。

## 止まったコードの進み方

futureを待つタスクは、futureが済むと進みます。ループが`send(None)`を呼び、coroutineはfutureから結果を読みます。ループのもう1つの手は、中へ投げ込む事だけです。`task.cancel()`は、`await`の所で`CancelledError`を起こします。ハンドラは`k`を持つので、コードがどう進むかはハンドラが決めます。

| 止まったコードが | asyncio | doeffのハンドラ |
|---|---|---|
| 値を受け取って進む | futureが済むと、ループが`send(None)`を呼びます。制御は、次に止まる所で戻ります。 | `yield Resume(k, value)`で、プログラムの残りが返した物を受け取ります。ハンドラの仕事が済んだなら、`yield Transfer(k, value)`です。 |
| 止まった所で例外を受ける | `future.set_exception(error)`か、`task.cancel()` | `yield ResumeThrow(k, error)` |
| ほかのコードの後で進む | ループの、走れるタスクの列 | ハンドラが`k`を取っておきます。`scheduled`は、こうしてタスクを走らせます。 |
| 進まずに、実行が別の値で終わる | 当たる手はありません。futureが済まないタスクは、ずっと待ちます。 | `k`を再開せずに`return value` |
| 2回進む | できません。coroutineは1回だけ走ります。 | できません。`k`はone-shotで、2回目の`Resume`は`RuntimeError`になります。 |

```python
# greet.py(抜粋)
# 止まったコードがどう進むかは、ハンドラが決める。
@handler  # ReadNameを受けるハンドラにする。
def closed(effect: ReadName, k):  # kを再開しない。実行はここで終わる。
    return "closed today"  # kに触れず、この値を全体の結果にする。


@do  # togetherを、yieldのたびに止まるプログラムにする。
def together():  # 2つのタスク。どちらも、周りのハンドラの下で走る。
    first = yield Spawn(greet())  # 1つ目のタスクを起こす。
    second = yield Spawn(grace(greet()))  # 2つ目は、周りにgraceを付けたプログラム。
    return (yield Gather(first, second))  # 2つが終わるのを待ち、結果を並べて返す。
```

```python
print(run(closed(twice())))  # twiceは、最初のyieldで止まる。
print(run(scheduled(ada(together()))))  # scheduledが、SpawnとGatherに答える。
```

```text
closed today
['Hello, Ada', 'Hello, Grace']
```

`closed`は、再開しない事で答えます。`twice`は最初の`yield`で止まり、`"closed today"`が実行全体の結果になります。asyncioには、タスクを`await`の所に置いたまま、実行を別の値で終える手がありません。取り消すと、代わりにタスクの中で例外が起きます。

`k`を後のために取っておく事が、並行の仕組みです。`Spawn`と`Gather`はエフェクトで、`scheduled`はそれらのハンドラです。`scheduled`は、待つタスクごとに`k`を取っておきます。タスクが進めるようになると、その`k`へ制御を渡します。asyncioのループが、自分のタスクにしている仕事です。`scheduled`も、`ada`と同じく付けます。どちらのタスクも、`together`の周りのハンドラの下で走ります。2つ目のタスクのプログラムは`grace(greet())`なので、Graceに挨拶します。

coroutineオブジェクトとは、働きの違う所が1つあります。coroutineオブジェクトは、止まった1回の実行です。最後まで走ると、使い切られます。`drive.py`の`greet_async`と`drive`で見ます。

```python
greeting = greet_async()  # coroutineオブジェクトを1つ作る。
print(drive(greeting, {"name": "Ada"}))  # 1回目は、最後まで走る。
drive(greeting, {"name": "Ada"})  # 同じオブジェクトを、もう一度走らせる。
```

```text
Hello, Ada
RuntimeError: cannot reuse already awaited coroutine
```

`@do`の関数を呼ぶと、プログラムができます。実行を記述した値です。`run()`は、そのたびに新しいgeneratorを始めます。だから1つのプログラムを、`ada`の下で走らせ、次に`grace`の下で走らせられます。

```python
hello = greet()  # プログラムを1つ作る。まだ何も走らない。
print(run(ada(hello)))  # そのプログラムを、adaの下で走らせる。
print(run(grace(hello)))  # 同じプログラムを、次はgraceの下で走らせる。
```

```text
Hello, Ada
Hello, Grace
```

1回の実行の中では、2つの形は同じです。`k`はone-shotで、coroutineも1回きりです。doeffは、止まったコードを複製しません。変えるのは、それを誰が持つかです。

## 非同期ライブラリを通したI/O

PythonのI/Oのライブラリは、多くが`async`です。HTTPのclient、データベースのdriver、モデルの提供元のSDKなどです。doeffは、それらを書き直せとは言いません。`Await(coroutine)`はエフェクトの1つで、`await_handler()`がそれに答えます。`await_handler()`は、プロセスで共有する裏のasyncioのループでcoroutineを走らせます。済むと、その結果で`k`を再開します。待つ間は、ほかのdoeffのタスクが走ります。イベントループは今も在ります。ただし、プログラムの上から、1つのハンドラの後ろへ移りました。

`Await`をどこに置くかで、ハンドラに何が見えるかが決まります。ここでは、モデルのSDKが返すような断片のストリームを、2通りに集めます。

- ループをプログラムにし、断片ごとに`Await`を1つ出す形
- ループ全体を1つのcoroutineに入れ、その周りに`Await`を1つ置く形

```python
# stream.py(抜粋)
import asyncio  # 断片の間の待ちを作る。
from collections.abc import AsyncIterator  # 非同期のストリームの型。

from doeff import Await, Listen, do, run  # coroutineを待つエフェクト・集めるエフェクト・定義・実行を使う。
from doeff_core_effects.handlers import await_handler, listen_handler  # AwaitとListenに答える参考のハンドラ。
from doeff_core_effects.scheduler import scheduled  # 待つ間に、ほかのタスクを走らせる。


async def tokens() -> AsyncIterator[str]:  # 非同期SDKのストリームの代わり。
    for chunk in ("Hello", ",", "", " world"):  # 空の断片も1つ混ぜる。
        await asyncio.sleep(0.01)  # 断片が届くまでの待ち。
        yield chunk  # 断片を1つ返す。


@do  # ループをプログラムにする。
def collect(stream: AsyncIterator[str]):  # 断片1つごとに、Awaitを1つ出す。
    chunks = aiter(stream)  # ストリームから、非同期イテレータを取り出す。
    text = ""  # まだ何も届いていない本文。
    while True:  # 終わりが来るまで、1断片ずつ待つ。
        chunk = yield Await(anext(chunks, None))  # 次の1断片を待つ。終わりならNone。
        if chunk is None:  # Noneが終わり。""は断片の1つ。
            return text  # つないだ本文を返す。
        text += chunk  # 断片をつなぐ。


async def collect_async(stream: AsyncIterator[str]) -> str:  # ループ全体を、1つのcoroutineに入れる。
    text = ""  # まだ何も届いていない本文。
    async for chunk in stream:  # asyncioのループの中で、断片を順に待つ。
        text += chunk  # 断片をつなぐ。
    return text  # つないだ本文を返す。


@do  # 1つのAwaitで包むプログラムにする。
def collect_in_one(stream: AsyncIterator[str]):  # 全体の周りに、Awaitを1つ。
    return (yield Await(collect_async(stream)))  # coroutineが済むまで、1回だけ待つ。


@do  # 結果と待ちを集めるプログラムにする。
def counted(program):  # 結果と、programが出したAwaitの全部。
    return (yield Listen(program, types=(Await,)))  # programを包み、Awaitだけを書き留める。


def bridged(program):  # Awaitにはasyncioで答え、その内側にListenを置く。
    return scheduled(await_handler()(listen_handler(program)))  # 外からscheduled・await_handler・listen_handlerの順に付ける。
```

```python
text, waits = run(bridged(counted(collect(tokens()))))  # 断片ごとにAwaitを出す形で走らせる。
print(repr(text), len(waits))  # 本文と、見えた待ちの数。
```

```text
'Hello, world' 5
```

```python
text, waits = run(bridged(counted(collect_in_one(tokens()))))  # 全体を1つのAwaitで包む形で走らせる。
print(repr(text), len(waits))  # 本文と、見えた待ちの数。
```

```text
'Hello, world' 1
```

`Listen`も、エフェクトです。`listen_handler`は、`program`の周りにハンドラを1つ付けて答えます。そのハンドラは各`Await`を書き留め、`await_handler`へ渡します。断片ごとに`Await`を出すと、待ちが5回見えます。4つの断片と、終わりの1回です。ループを1つのcoroutineに入れると、見えるのは1回です。`collect_async`の中の待ちはasyncioのループへ行き、そこへはどのハンドラも届きません。

だから、手順は`@do`のコードに置きます。`Await`は、ライブラリが出す1つの操作の所に置きます。そうすれば、プログラムの周りに重ねた物はどれも、一歩ずつを見られます。ログ、時計、予算、そして答えの記録です。テストはその記録を再生し、本物のI/Oを1度もしません。

`collect`は、`not chunk`ではなく`chunk is None`を見ます。空の文字列も断片で、`anext(chunks, None)`が`None`を返すのは終わりの時だけだからです。

## `async`と`await`のほうが良い道具の時

- **走らせ方が1つなら、素の書き方で足ります。** 依存の実装が1つで、その呼び出しの周りに何も置かないなら、素の`await`か、引数で渡す関数のほうが短く済みます。エフェクトが効くのは、同じコードを違うハンドラの下で走らせる時です。一歩ずつの周りに何かを置きたい時もです。
- **`@do`は、`async`と同じく呼ぶ側へ広がります。** doeffは、関数の色を消しません。素と`async`の2色を1色に替えます。一歩がイベントループを待つかは、ハンドラが決めます。
- **asyncioは、待つ方法以上の物です。** ソケット、サブプロセス、ロック、キューと、多くのライブラリが付いてきます。doeffはそれらへ、`Await`で1操作ずつ届きます。
- **asyncioのアプリの中で`run()`を呼ぶと、ループが止まります。** `run()`は、終わるまで戻らないからです。worker threadで呼びます。`await asyncio.to_thread(run, program)`です。
- **`Await`の橋には、それ自身の約束があります。** プロセスのすべての`Await`は、1つの裏のループで走ります。asyncのコードの中の同期の呼び出しでそのループを止めると、プロセスのすべての`Await`が止まります。待っているdoeffのタスクを取り消すと、coroutineに`CancelledError`が届きます。ただし、頼むだけです。取り消しは、coroutineの`finally`が走り終わるのを待たずに返ります。
- **ハンドラは、その操作の約束を引き受けます。** 取り消しの時や例外の時に何が起き、どの順で起きるかは、ハンドラの仕事になります。ほかのコードと同じく、テストします。
- **タスクの費用は高めです。** doeffのRustのスケジューラは、起こしたタスク1つに約17 µsのCPUを使います。asyncioは約11 µsです。

## まとめ

| | `async def`と`await` | `@do`と`yield` |
|---|---|---|
| 止まる時に渡す物 | awaitable(いちばん下は、ループのためのfuture) | エフェクト(操作に名前を付けた値) |
| 行き先 | 間のcoroutineをすべて通った先の、いちばん上のイベントループ | それを出したコードを囲む、いちばん内側のハンドラ |
| 受け取る者を選ぶ | `asyncio.run`が、プログラムに1度 | 呼ぶ側が、プログラムのどの部分にも |
| 止まったコードが進む時 | futureが済んだ時 | ハンドラが決めた時に、決めた形で(進めない事もある) |
| 関数を呼ぶと得る物 | coroutineオブジェクト(1回だけ走る) | プログラム(何度でも走る) |
| 今ある非同期ライブラリ | `await`する | 操作ごとに`yield Await(...)` |

代数的エフェクトの言葉では、`async`と`await`は、ハンドラが1つのエフェクトシステムです。そのハンドラはいちばん上に固定され、イベントループの操作に答えます。doeffは、coroutineをPythonのgeneratorとして残します。そして、どの呼ぶ側にもハンドラを付けさせます。自分の操作にも、並行にも、asyncioそのものにもです。

## 試す

```bash
pip install doeff   # CPython 3.11から3.14で入る。
python greet.py  # 同じ挨拶の2通りと、ハンドラの付け方を走らせる。
python drive.py  # awaitの行き先と、1回きりのcoroutineを走らせる。
python stream.py  # 断片ごとのAwaitと、1つのAwaitを比べる。
```

この記事の例はどれも、PyPIのdoeff 0.8.0で、載せたとおりに走ります。完全なファイルは、[examples/yield_and_async](https://github.com/proboscis/doeff/tree/main/examples/yield_and_async)にあります。docsの章[Async Effects](https://github.com/proboscis/doeff/blob/main/docs/04-async-effects.md)は、`Await`・`Spawn`・`Gather`・`Race`と取り消しを扱います。FAQの[How is it different from asyncio?](https://github.com/proboscis/doeff/blob/main/docs/faq.md#how-is-it-different-from-asyncio)も、同じ問いに答えています。

[1本目の入門](doeff-main.md)は、時間・並行・リトライ・予算・I/Oのハンドラを、1つのプログラムの周りに重ねます。時計をハンドラにし、何時間もの待ちを一瞬で走らせる話は、3本目の[テストで20秒待つのをやめる](doeff-time.md)にあります。

asyncioのコードで、イベントループが邪魔になる所はありますか。素の`await`のほうが明らかに良い場面はありますか。issueは https://github.com/proboscis/doeff/issues で受け付けています。

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考

この記事のコードは、2026年10月29日に、PyPIのdoeff 0.8.0で確かめました。Pythonは3.11・3.12・3.14で試しました。

- [Python公式: Coroutine objects](https://docs.python.org/3/reference/datamodel.html#coroutine-objects)
- [Python公式: Coroutines and tasks](https://docs.python.org/3/library/asyncio-task.html)
- [Python公式: contextvars](https://docs.python.org/3/library/contextvars.html)
- [PEP 492 – Coroutines with async and await syntax](https://peps.python.org/pep-0492/)
- [Awaitの橋渡しの実装(await_handlerの説明に、取り消しと共有ループの約束)](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-core-effects/doeff_core_effects/handlers.py)
- 前の回:[公開APIの型の穴をテストで塞ぎ、黙って開かせない](doeff-types.md)
- 次の回:[ADRを書いて終わりにするのをやめる](doeff-adr.md)
