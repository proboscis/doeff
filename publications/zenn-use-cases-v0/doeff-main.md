---
title: "Pythonの代数的エフェクトで、変えない処理にハンドラを重ねる"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "algebraiceffects", "設計"]
published: false
---

## doeffとは？

Pythonの代数的エフェクト(algebraic effects)を、[doeff](https://github.com/proboscis/doeff)で紹介します。doeffは、中核をRustで書いたオープンソースのライブラリです。関数はエフェクト、つまり名前を付けた操作を`yield`します。ハンドラは、そのエフェクトを関数の残りの部分と一緒に受け取り、関数をどう進めるかを決めます。

その決めごとの1つ1つは、別の方法でも手に入ります。テストのモック、偽の時計、リトライのデコレータ、自作のイベントループです。エフェクトは、そのすべてを1つの仕組みで扱えるようにします。だから同じコードの周りにいくつも重ねられ、コードに触れずにどれか1つを入れ替えられます。ここでは小さなプログラムを1つ作り、その周りに層を1つずつ足していきます。

理論は要りません。`yield`が何をするかを知っていれば、必要なことの大半はもう分かっています。

## 考え方:ハンドラが再開できる例外

例外を知っているなら、エフェクトは「ハンドラが答えられる例外」だと考えてください。答えた後、コードは例外を投げた行から続きます。OCaml 5も、エフェクトハンドラを「再開できる例外」として紹介しています。Pythonのジェネレータは元から止まったり再開したりできるので、残りはライブラリが補います。エフェクトごとにハンドラを探し、止まったコードを覚えておくRustのVMです。

- **エフェクト**は、`Spend(3)`・`Fetch(url)`・`Spawn(task)`のように、操作に名前を付けたただの値です。それ自体は何もしません。自分でクラスとして定義することも、doeffのパッケージが定義した物を使うこともできます。
- **プログラム**は、`@do`を付けたジェネレータ関数です。エフェクトを`yield`するたびに止まります。
- **ハンドラ**は、`@handler`を付けた`(effect, k)`の関数です。`@handler`で、ハンドラは`@do`の関数にもなります。エフェクトと、関数の残り(継続)である`k`を一緒に受け取り、何が起きるかを決めます。
- どのハンドラを付けるかは、**呼び出す側**が選びます。だから同じ関数が、ハンドラごとに違う意味になります。

| ハンドラが | 書き方 | プログラムは |
|---|---|---|
| 答えて、残りを走らせる | `yield Resume(k, value)` | `value`を受け取って進み、終わるとハンドラがその結果を受け取って、そのままか別の値を返す |
| 答えて、手を離す | `yield Transfer(k, value)` | `value`を受け取って進み、ハンドラに制御は戻らない |
| 例外を投げ込む | `yield ResumeThrow(k, error)` | `yield`の所で`error`が起き、自分の`try`で捕まえられる |
| エフェクトを外へ渡す | `yield Pass(effect, k)` | 1つ外のハンドラの答えを受け取る |
| 後のために取っておく | `k`をしまい、後のエフェクトを扱う時に`yield Transfer(k, value)` | ハンドラが続きを走らせるまで待つ |
| 捨てる | `k`を再開せずに`return value` | `yield`で止まったままで、`value`がそのハンドラの下の全体の結果になる |

いちばん小さな形です。`research`は話題ごとに`Spend`を1つ`yield`し、お金を使うとはどういうことかを何も知りません。

```python
# spend.py
from dataclasses import dataclass  # エフェクトを、値を持つクラスとして書く。

from doeff import EffectBase, Resume, Transfer, do, handler, run  # エフェクトの基底・再開・手放し・定義・実行を使う。


@dataclass(frozen=True)  # 変わらない値にする。
class Spend(EffectBase):  # エフェクト:「次の一歩にこれだけかかる」。
    cost: int  # かかる額。


@do  # researchを、yieldのたびに止まるプログラムにする。
def research(topics: list[str]):  # yieldのたびに止まる。お金のことは何も知らない。
    notes = []  # 調べたメモをためる。
    for topic in topics:  # 話題を1つずつ調べる。
        yield Spend(3)  # 1つの話題に3かかると告げ、ハンドラの答えを待つ。
        notes.append(f"notes on {topic}")  # 再開されたら、その話題のメモを足す。
    return notes  # 全部の話題を調べたら、メモの一覧を返す。


@handler  # Spendを受けるハンドラにする。
def free(effect: Spend, k):  # 答えて、researchの残りを走らせる。
    yield Transfer(k, None)  # 続きkへ制御を渡し、ハンドラには戻らない。


@handler  # Spendを受けるハンドラにする。
def refuse(effect: Spend, k):  # kを捨てる。researchは最初のSpendで止まる。
    return "refused"  # kを再開せず、この値を全体の結果にする。


def budget(limit: int):  # 上限limitの予算のハンドラを作る。
    left = limit  # 残りの額を、エフェクトの間で覚えておく。

    @handler  # Spendを受けるハンドラにする。
    def on_spend(effect: Spend, k):  # Spendが届くたびに呼ばれる。
        nonlocal left  # 外側の残りの額を書き換える。
        if effect.cost > left:  # 残りで足りなければ、
            return "over budget"  # kを捨てる。researchはこのSpendから先へ進まない。
        left -= effect.cost  # 足りれば、残りから引く。
        return (yield Resume(k, None))  # kを再開する。researchが進み、その結果をそのまま返す。

    return on_spend  # 作ったハンドラを返す。


TOPICS = ["tides", "moons"]  # 調べる話題は2つ。

if __name__ == "__main__":  # ファイルとして走らせた時に、4通りの結果を表示する。
    print(run(free(research(TOPICS))))  # 2つとも調べ終える。
    print(run(refuse(research(TOPICS))))  # 最初のSpendで止まり、refusedになる。
    print(run(budget(10)(research(TOPICS))))  # 予算10なら、2つとも調べ終える。
    print(run(budget(5)(research(TOPICS))))  # 予算5なら、2つ目の話題でover budgetになる。
```

```text
['notes on tides', 'notes on moons']
refused
['notes on tides', 'notes on moons']
over budget
```

`@handler`は、その下の関数を`@do`の関数にし、ハンドラにもします。だからハンドラは、いつも`@do`の関数です。ほかの`@do`の関数と同じく、ハンドラは`Resume(k, None)`を`yield`してdoeffへ渡します。その`yield`は、`research`の残りが返した値を返します。`return (yield Resume(k, None))`は、その結果をハンドラ自身の結果として渡す書き方です。ハンドラをプログラムに対して呼ぶと、ハンドラはそのプログラムの周りに付きます。`free(research(TOPICS))`は、周りに`free`を付けた`research(TOPICS)`です。型ヒント`effect: Spend`は、ハンドラがどのエフェクトを受けるかをdoeffに伝えます。

- `free`は`Transfer(k, None)`で答えます。`research`は最後まで走ります。
- `refuse`は`k`に触れずに返ります。`research`は最初の`Spend`で止まり、`"refused"`が実行全体の結果になります。
- `budget(5)`は、残りの額をエフェクトの間で覚えています。1回目の`Spend`は3かかり、ハンドラは`k`を再開します。2回目は残りが2なので、ハンドラは`"over budget"`を返し、2つ目の話題は調べられません。

`budget(5)(research(TOPICS))`の実行を、やりとり1つずつ追うと次のようになります。

1回目の`Spend(3)`は、残りの5に収まります。`budget`は`Resume`で`research`を再開し、tidesのメモができます。2回目の`Spend(3)`は、残りの2に収まりません。`budget`は続きの`k`を捨て、`"over budget"`が`run()`の結果になります。

これで層が1枚です。この先は、1つのプログラムの周りに層を何枚も重ねます。

## 1つのプログラムに、層を1枚ずつ

`monitor`は、2つのページを30分ごとに確かめます。ページ1つに、タスクが1つです。`yield`するエフェクトは7種類あります。自分で定義したのは`Fetch`・`Spend`・`Log`です。`Spawn`と`Gather`はdoeffのパッケージの物です。`Delay`と`GetTime`は[doeff-time](https://github.com/proboscis/doeff/tree/main/packages/doeff-time)の物です。doeff-timeは、時間のエフェクトとそのハンドラを集めたパッケージです。ページをどこから取るのか、お金を使うとは何かは、どこにも書いていません。30分にどれだけかかるのか、ログの行がどこへ行くのかも書いていません。

```python
# monitor.py(抜粋)
import asyncio  # 実時間の層で、非同期のHTTPの代わりに使う。
from dataclasses import dataclass  # エフェクトを、値を持つクラスとして書く。
from datetime import datetime, timezone  # 仮想の時計の始まりの刻を作る。

from doeff import Await, EffectBase, Gather, Pass, Resume, Spawn, Transfer, do, handler, run  # doeffの中核の名前を使う。
from doeff_core_effects.handlers import await_handler  # Awaitに答える参考のハンドラ。
from doeff_core_effects.scheduler import scheduled  # SpawnとGatherに答える参考のハンドラ。
from doeff_time import Delay, GetTime, async_time_handler, sim_time_handler  # 時間のエフェクトと、実時間・仮想時間のハンドラ。


# プログラム:2つのタスクが、30分ごとにページを1つ確かめる。
@dataclass(frozen=True)  # 変わらない値にする。
class Fetch(EffectBase):  # 「このページを取って」。
    url: str  # 取るページ。


@dataclass(frozen=True)  # 変わらない値にする。
class Spend(EffectBase):  # 「次の一歩にこれだけかかる」。
    cost: int  # かかる額。


@dataclass(frozen=True)  # 変わらない値にする。
class Log(EffectBase):  # 「この行を書いて」。
    line: str  # 書く行。


@do  # checkを、yieldのたびに止まるプログラムにする。
def check(url: str, rounds: int):  # 1つのページを、rounds回確かめる。
    sizes = []  # 取ったページの大きさをためる。
    for round_ in range(rounds):  # 1回ずつ確かめる。
        if round_:  # 2回目からは、
            yield Delay(30 * 60)  # 確かめる間に30分待つ。
        yield Spend(1)  # 1回の確かめに1かかると告げる。
        page = yield Fetch(url)  # ページを取ってもらう。
        now = yield GetTime()  # 今の刻を聞く。
        yield Log(f"{now:%H:%M} {url} {len(page)} bytes")  # 刻・ページ・大きさの行を書いてもらう。
        sizes.append(len(page))  # 大きさを覚える。
    return sizes  # 確かめた回の大きさの一覧を返す。


@do  # monitorを、yieldのたびに止まるプログラムにする。
def monitor(urls: list[str], rounds: int):  # ページごとにタスクを1つ起こし、全部を待つ。
    tasks = []  # 起こしたタスクをためる。
    for url in urls:  # ページごとに、
        tasks.append((yield Spawn(check(url, rounds))))  # checkをタスクとして起こす。
    return (yield Gather(*tasks))  # 全部のタスクの結果を、起こした順に待つ。
```

`scheduled`は、`Spawn`と`Gather`に答える参考のハンドラです。これだけを付けると、最初の`Spend`で`UnhandledEffect`になって止まります。どのエフェクトも、ハンドラが意味を与えるまでは意味を持ちません。

### 1枚目:テストの実行のための答え

```python
# monitor.py(抜粋)
# 層:1枚ごとに、1つのエフェクトに意味を与える。
PAGES = {"a.example": "<p>up</p>", "b.example": "<p>all good</p>"}  # テストで返すページ。


@handler  # Fetchを受けるハンドラにする。
def fake_web(effect: Fetch, k):  # I/Oに、辞書から答える。
    return (yield Resume(k, PAGES[effect.url]))  # 辞書のページを渡して再開する。


@handler  # Logを受けるハンドラにする。
def printed(effect: Log, k):  # ログの行を標準出力へ出す。
    print(effect.line)  # 行を表示する。
    return (yield Resume(k, None))  # 再開する。


@handler  # Spendを受けるハンドラにする。
def free(effect: Spend, k):  # お金はかからない。
    yield Transfer(k, None)  # 続きkへ制御を渡し、ハンドラには戻らない。


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)  # 仮想の時計の始まりは9:00。
URLS = ["a.example", "b.example"]  # 確かめるページは2つ。


def simulated(program):  # 仮想の時計。30分の待ちに時間がかからない。
    return scheduled(sim_time_handler(start_time=START)(program))  # 仮想時間とタスクのハンドラを付ける。
```

```python
print(run(simulated(printed(free(fake_web(monitor(URLS, 2)))))))  # テストの層を重ね、2回ずつ確かめる。
```

```text
09:00 a.example 9 bytes
09:00 b.example 15 bytes
09:30 a.example 9 bytes
09:30 b.example 15 bytes
[[9, 9], [15, 15]]
```

重ねたハンドラは、内側から読みます。`fake_web`が`Fetch`に辞書から答え、`free`が`Spend`に、`printed`が`Log`に答えます。`simulated`はハンドラを2つ付けます。1つは`Spawn`と`Gather`に答える`scheduled`です。もう1つは、`Delay`と`GetTime`に答えるdoeff-timeの`sim_time_handler`です。確かめる間の30分には時間がかからず、2つのタスクの行は刻の順に出ます。何度走らせても同じ順です。

### 2枚目:予算

`free`を、3回分の予算に入れ替えます。

```python
# monitor.py(抜粋)
def budget(limit: int):  # 予算を使い切ったタスクは、そこで止まる。
    left = limit  # 残りの額を、エフェクトの間で覚えておく。

    @handler  # Spendを受けるハンドラにする。
    def on_spend(effect: Spend, k):  # Spendが届くたびに呼ばれる。
        nonlocal left  # 外側の残りの額を書き換える。
        if effect.cost > left:  # 残りで足りなければ、
            return "over budget"  # kを捨て、そのタスクの結果にする。
        left -= effect.cost  # 足りれば、残りから引く。
        return (yield Resume(k, None))  # 再開する。

    return on_spend  # 作ったハンドラを返す。
```

```python
print(run(simulated(printed(budget(3)(fake_web(monitor(URLS, 2)))))))  # freeを3回分の予算に替える。
```

```text
09:00 a.example 9 bytes
09:00 b.example 15 bytes
09:30 a.example 9 bytes
[[9, 9], 'over budget']
```

2つのタスクは同じ`budget(3)`の下で走るので、予算は2つの確かめを合わせて数えます。4回目の`Spend`、つまり`b.example`の2回目の確かめで、残りが無くなります。ハンドラは`k`を再開せずに`"over budget"`を返します。だからそのタスクはそこで止まり、結果が`"over budget"`になります。もう1つのタスクは最後まで走ります。`check`には、このための`if`が1つもありません。

### 3枚目:失敗とリトライ

今度は最初の2回の取得が失敗します。プログラムとWebの間に、リトライの層を挟みます。

```python
# monitor.py(抜粋)
class Flaky(Exception):  # 一時的な失敗。
    pass  # 中身は要らない。


def flaky_web(failures: int):  # 最初の何回かの取得が失敗する。
    left = failures  # 残りの失敗の回数を覚えておく。

    @handler  # Fetchを受けるハンドラにする。
    def on_fetch(effect: Fetch, k):  # Fetchが届くたびに呼ばれる。
        nonlocal left  # 外側の回数を書き換える。
        if left > 0:  # 失敗の回数が残っていれば、
            left -= 1  # 1回減らし、
            raise Flaky(f"503 from {effect.url}")  # 503で失敗する。
        return (yield Resume(k, PAGES[effect.url]))  # 残っていなければ、ページを渡して再開する。

    return on_fetch  # 作ったハンドラを返す。


def retrying(times: int):  # Flakyなら、1つ外のハンドラにもう一度頼む。
    @handler  # Fetchを受けるハンドラにする。
    def retry(effect: Fetch, k):  # Fetchが届くたびに呼ばれる。
        for attempt in range(1, times + 1):  # 最大times回試す。
            try:  # 失敗を捕まえる。
                page = yield effect  # 同じFetchを、1つ外のハンドラに頼む。
                break  # 取れたら抜ける。
            except Flaky as error:  # 一時的な失敗なら、
                if attempt == times:  # 最後の回なら、
                    raise  # 失敗をそのまま外へ出す。
                yield Log(f"retry {attempt}: {error}")  # 失敗をLogで知らせ、もう一度試す。
        return (yield Resume(k, page))  # 取れたページを渡して再開する。

    return retry  # 作ったハンドラを返す。
```

```python
print(run(simulated(printed(free(flaky_web(2)(retrying(3)(monitor(URLS, 2))))))))  # 失敗するWebとリトライを重ねる。
```

```text
retry 1: 503 from a.example
retry 2: 503 from a.example
09:00 a.example 9 bytes
09:00 b.example 15 bytes
09:30 a.example 9 bytes
09:30 b.example 15 bytes
[[9, 9], [15, 15]]
```

ハンドラの中で`yield effect`と書くと、同じエフェクトをもう一度起こし、1つ外のハンドラがそれに答えます。ハンドラはほかのエフェクトも`yield`できます。`retry`は失敗のたびに`Log`で知らせ、その行は`check`のログの行と同じく外の`printed`まで届きます。注入したオブジェクトは、呼ばれたら答えて返るだけです。ハンドラはそれに加えて`k`を持つので、関数の残りを走らせるか、いつ走らせるか、何を渡すかを決められます。

### 4枚目:すべてのエフェクトを見る

```python
# monitor.py(抜粋)
def recording(tape: list[str]):  # プログラムがyieldしたエフェクトを全部残す。
    @handler  # 型ヒントが無いので、届いたエフェクトを全部受ける。
    def record(effect, k):  # エフェクトが届くたびに呼ばれる。
        tape.append(type(effect).__name__)  # エフェクトの型の名を書き留める。
        return (yield Pass(effect, k))  # 答えずに、外のハンドラへ渡す。

    return record  # 作ったハンドラを返す。
```

```python
tape: list[str] = []  # 書き留める先。
run(simulated(printed(free(fake_web(recording(tape)(monitor(URLS, 1)))))))  # fake_webの内側にrecordingを足し、1回ずつ確かめる。
print(tape)  # 書き留めたエフェクトの名を、順に表示する。
```

```text
09:00 a.example 9 bytes
09:00 b.example 15 bytes
['Spawn', 'Spend', 'Fetch', 'GetTimeEffect', 'Log', 'Spawn', 'Spend', 'Fetch', 'GetTimeEffect', 'Log', 'Gather']
```

`record`には型ヒントが無いので、届いたエフェクトを全部受け取ります。型の名を書き留め、`Pass`で外へ渡します。最初の`Fetch`は、次のように進みます。

`check`が`Fetch(url)`を`yield`して止まると、まず内側の`record`に届きます。`record`は名を書き留めて`Pass`で外へ渡し、`fake_web`がページを渡して`check`を再開します。

ハンドラに見えるのは、そこまで届いたエフェクトだけです。プログラムに近いハンドラが先に答えると、外のハンドラには届きません。同じ形で、エフェクトの名の代わりに答えを記録することもできます。例は`examples/testing_without_mocks/record_replay.py`です。言語モデルの答えを一度だけ記録し、テストではそれを再生します。テストではモデルを一度も呼びません。

### 5枚目:実時間と本物のI/O

```python
# monitor.py(抜粋)
async def slow_get(url: str) -> str:  # 非同期のHTTPクライアントの代わり。
    await asyncio.sleep(0.01)  # 通信の待ちの代わりに、少し眠る。
    return PAGES[url]  # ページを返す。


@handler  # Fetchを受けるハンドラにする。
def http_web(effect: Fetch, k):  # I/Oを、非同期のクライアントで行う。
    page = yield Await(slow_get(effect.url))  # クライアントのcoroutineを、Awaitで待ってもらう。
    return (yield Resume(k, page))  # 取れたページを渡して再開する。


def real(program):  # 実時間の時計。asyncioで待つ。
    return scheduled(await_handler()(async_time_handler()(program)))  # 実時間・Await・タスクのハンドラを付ける。
```

```python
run(real(printed(free(http_web(retrying(3)(monitor(URLS, 2)))))))  # 本番の層を重ねる。実際に30分かかる。
```

実時間の時計で動く、同じ`monitor`です。だからこの実行には本当に30分かかります。doeff-timeの`async_time_handler`は`asyncio`で待ちます。`http_web`は、非同期のクライアントのcoroutineを`Await`で`yield`します。それに答えるのは`await_handler`です。`async`なのは`slow_get`だけなので、`async`が`check`や`monitor`へ広がりません。

### 重ねた層を並べる

| 実行 | `monitor`の周りの層(内側から) | 何が変わるか |
|---|---|---|
| テスト | `fake_web`・`free`・`printed`・`simulated` | ページは辞書から、30分は一瞬で、毎回同じ順 |
| 予算つき | `free`の代わりに`budget(3)` | 予算を使い切ったタスクは、そこで止まる |
| 不安定 | `fake_web`の代わりに`retrying(3)`と`flaky_web(2)` | 失敗はリトライされ、ログに残る |
| 観察 | `fake_web`の内側に`recording(tape)`を足す | すべてのエフェクトが、順に残る |
| 本番 | `fake_web`の代わりに`retrying(3)`と`http_web`、`simulated`の代わりに`real` | 実時間の時計と、非同期のクライアント |

どの行でも、`monitor`は同じ関数です。モック、偽の時計、自作のイベントループは、このうちの1行をくれます。ここでは、どの行も数行のハンドラを重ねた物で、1枚を入れ替えてもほかの層には触れません。

## すべての下にある1つの仕組み

doeffのパッケージは、`Spawn`・`Gather`・`Wait`・`Race`・`Cancel`をエフェクトとして定義しています。`scheduled`はそれらに答える参考のハンドラで、`budget`と同じように書かれています。上の表のとおり、待っているタスクごとに`k`を取っておき、タスクが進めるようになったら、その`k`へ`Transfer`します。どのハンドラも、`scheduled`の代わりにこれらのエフェクトに答えられます。時計をハンドラにできるのは、このためです。`sim_time_handler`は、すべてのタスクが待っている時だけ時計を進めます。いちばん早く起きる刻へ飛び、そのタスクを再開します。スケジューラには、どのプログラムでも`yield`できるエフェクトを通してしか触れません。docsの章[Why Effects?](https://github.com/proboscis/doeff/blob/main/docs/20-why-effects-over-di.md#virtual-time-hours-of-waiting-in-an-instant)では、同じような時計を約30行で作っています。

doeffのパッケージが定義するエフェクトも、`Fetch`や`Spend`と同じように動きます。どれにも参考のハンドラがあり、代わりに自分のハンドラを付けられます。

| 何を | プログラムがyieldする物 | 参考のハンドラ |
|---|---|---|
| 状態 | `n = yield Get("fetched")`と`yield Put("fetched", n + 1)` | `state()`が辞書を持ち、値を渡して`k`を再開する |
| 例外 | `result = yield Try(size(url))`は`Ok(value)`か`Err(error)`を返す | `try_handler`(どのハンドラも`ResumeThrow(k, error)`でプログラムに例外を投げ込める) |
| 並行処理 | `task = yield Spawn(check(url))`の後に`yield Gather(*tasks)` | `scheduled()`が、待っているタスクごとに`k`を取っておく |
| 観察 | `value, log = yield Listen(program)` | `listen_handler`が、`program`の`Tell`の行を集める |
| I/O | `page = yield Fetch(url)` | 自分のハンドラか参考のハンドラ(ファイル・SQL・プロセスは`doeff[hy]`、HTTPは`doeff[http]`) |

この形は、トレードのプログラム、シミュレーション、バックテストに合います。同じプログラムを、記録した市場のイベントの上で、仮想の時計と紙の注文で走らせられます。本番では、実時間の時計と取引所で走らせます。違うのは、時間・市場のデータ・注文のハンドラだけです。

## テストも、重ねた層の1つ

上の1枚目はテストです。多くの人が最初に出会う使い方でもあります。Pythonのテストには、誰も触りたがらない`mock.patch`がよくあります。対象を文字列で探すので、importを動かすと外れることがあり、外れてもテストが通ったまま本物が動くことがあります。エフェクトなら、テストはパッチの代わりにハンドラを付けます。

```python
# welcome.py
from dataclasses import dataclass  # エフェクトを、値を持つクラスとして書く。

from doeff import EffectBase, do  # エフェクトの基底と、プログラムの定義を使う。


@dataclass(frozen=True)  # 変わらない値にする。
class LoadUser(EffectBase):  # 「この利用者を読んで」。
    user_id: int  # 読む利用者のid。


@dataclass(frozen=True)  # 変わらない値にする。
class SendMail(EffectBase):  # 「このメールを送って」。
    to: str  # 宛先。
    body: str  # 本文。


@do  # welcomeを、yieldのたびに止まるプログラムにする。
def welcome(user_id: int):  # 利用者を読み、歓迎のメールを送る。
    user = yield LoadUser(user_id)  # 利用者を読んでもらう。
    body = f"Welcome, {user.name}!"  # 本文を作る。
    yield SendMail(user.email, body)  # メールを送ってもらう。
    return body  # 送った本文を返す。
```

```python
# test_welcome.py
from doeff import Resume, handler, run  # 再開・ハンドラ・実行を使う。

from infra import User  # 利用者の値の型(本物の基盤の側に在る)。
from welcome import LoadUser, SendMail, welcome  # テストする処理と、そのエフェクト。


@handler  # LoadUserを受けるハンドラにする。
def fake_load_user(effect: LoadUser, k):  # 偽の利用者を読む。
    return (yield Resume(k, User("Ada", "ada@example.com")))  # 決めた利用者を渡して再開する。


def test_welcome():  # 送った本文と宛先を確かめる。
    sent = []  # 送ったメールを書き留める。

    @handler  # SendMailを受けるハンドラにする。
    def fake_send_mail(effect: SendMail, k):  # 送らずに書き留める。
        sent.append((effect.to, effect.body))  # 宛先と本文を書き留める。
        return (yield Resume(k, None))  # 再開する。

    assert run(fake_send_mail(fake_load_user(welcome(1)))) == "Welcome, Ada!"  # 偽の2つを重ねて走らせる。
    assert sent == [("ada@example.com", "Welcome, Ada!")]  # 1通だけ、Adaに送った。
```

何もパッチしておらず、importと揃えておく文字列もありません。偽物は上の層と同じハンドラで、それぞれに`@handler`を付け、同じように`welcome(1)`の周りに重ねます。今度は、そのうちの1つを付け忘れてみます。

```python
run(fake_load_user(welcome(1)))  # fake_send_mailを付け忘れて走らせる。
```

```text
doeff Traceback (most recent call last):

  welcome()  welcome.py:22
    yield SendMail(user.email, body)  # メールを送ってもらう。
    handlers:
      fake_load_user ↗

UnhandledEffect: no handler found for effect: SendMail (SendMail(to='ada@example.com', body='Welcome, Ada!'))
  handlers in scope (innermost→outermost): fake_load_user
  skipped by annotation: fake_load_user (effect: LoadUser) — a handler only sees the effects its effect parameter is annotated with
  hint: install a handler for SendMail: run(fn(prog)), where fn is a def (effect: SendMail, k) marked with @handler that returns (yield Resume(k, value))
```

実行は、誰も答えなかった`yield`で止まり、本物は何も呼ばれていません。Python自身のトレースバックの前に、doeffが3つのことを表示します。その`yield`、そこで付いていたハンドラ、それぞれがエフェクトを通した訳です。完全なファイルは[examples/testing_without_mocks](https://github.com/proboscis/doeff/tree/main/examples/testing_without_mocks)にあります。`mock.patch`の版と、その2つの失敗の仕方も並べています。

## doeffの立ち位置

比べるために、同じ小さな3つの仕事を5通りで書きました。歓迎のメール、ログ付きの銀行の残高、3つの並行な取得です。行は空行・コメント・importを除いて数え、各テストで差し替えを1つずつ外して何が起きるかを見ました。

| | 素のPython | effect | effectful | doeff |
|---|---|---|---|---|
| ハンドラが受け取る物 | —(呼ばれた側が返るか投げる) | 意図 | 操作の引数 | エフェクトと継続`k` |
| 3つの仕事の行数 | 53 | 97 | 70 | 88 |
| 誰も扱わないエフェクト | 引数ならたいていエラー、`mock.patch`なら本物のコードが動く | エラー | 間違った値か、黙って通る | エラー(ハンドラに型ヒントがある時) |
| 状態・ログ・並行処理が組み込みか | いいえ | 並行処理はスレッドプールで | いいえ(`asyncio`を使う) | はい |
| 最新のリリース | — | 2019 | 2026 | 2026 |

素のPythonがいちばん短く、いちばん読みやすいです。effectfulは関数をまったく普通のままに保てて、これは本当の利点です。doeffでは、業務のコードに`yield`と`@do`が入り、覚える名前も増えます。その代わり、ハンドラがプログラムの残りを持ち、ハンドラが欠ければエラーになります。1つのハンドラでプログラム全体を包め、並行処理の下でもハンドラがタスクごとに分かれます。あるテストでは、`asyncio`のタスク2つが、それぞれ自分のeffectfulのハンドラを付けました。2つは待ってから、始めた順に起きました。待った後、1つ目のタスクは2つ目のタスクの答えを受け取り、2つ目は評価されていない項を受け取りました。同じテストをdoeffで書くと、2つは分かれたままでした。これが問題になるのは、タスクの中でハンドラを付ける時だけです。`asyncio.run`の周りに一度だけ付けるなら、effectfulは正しく動きます。

`returns`は、GitHubのスターが約4.4Kの人気の関数型ライブラリです。`Result`のような型付きの入れ物をくれます。ただ、コードを再開するハンドラは無いので、この比べ方には入りません。

Pythonの外でいちばん近いのはOCaml 5です。ハンドラが継続を受け取る点も、継続を1回しか再開できない点も、エフェクトが型に出ない点も同じです。Kokaは、その2つの点でさらに先へ進んでいます。

| | 2回以上再開できるか | エフェクトが型に出るか |
|---|---|---|
| OCaml 5 | いいえ:2回目の再開は`Continuation_already_resumed`を投げる | いいえ:扱われないエフェクトは、起きた時に`Effect.Unhandled`を投げる |
| Koka | はい:`resume`を2回以上呼べ、バックトラックや確率的プログラミングに使える | はい:コンパイラがすべての関数のエフェクトを推論する |
| doeff | いいえ:2回目の`Resume`は`RuntimeError`を投げる | いいえ:ハンドラが欠けると、プログラムを走らせた時に`UnhandledEffect`になる |

## かかるコスト

エフェクトは、1つずつハンドラまで行って戻ってきます。doeffのREADMEにあるベンチマークは、その往復1回のCPU時間を、各場合50回ずつ測っています。機械は共有のIntel i9-7980XE、OSはLinux、PythonはCPython 3.14.7です。

| エフェクトの往復1回 | 1エフェクトあたりµs(中央値) |
|---|---:|
| 素のPythonの関数呼び出し | 0.13 |
| doeff、`Resume`する`@handler` | 4.3 |
| effect 1.1.0 | 12.7 |
| effectful 0.4.0 | 57.9 |

`scheduled`は、0.7.0から既定でRustで動きます。小さなタスクがたくさんある場合、起こしたタスク1つあたり約17µsかかります。`asyncio`では約11µsです。

数マイクロ秒は、データベースの問い合わせやHTTPのリクエストの横では誤差です。数値計算のきついループの中では誤差ではありません。きついループは素のPythonのままにし、その周りでエフェクトを`yield`してください。

スクリプトはリポジトリの`benchmarks/compare_libraries.py`です。READMEの[Performance](https://github.com/proboscis/doeff#performance)の節に、すべての場合と測り方を載せています。自分の機械で数字を取りたい時に使ってください。

## 限界

- doeffは0.xです。APIは0.4から0.5で変わりました。0.6から、`pip install doeff`は中核だけを入れます。Hyの言語とHyで書いたハンドラは、`pip install "doeff[hy]"`で入ります。
- 継続は1回きりです。ハンドラは止まったコードを1回しか再開できないので、バックトラックの探索のように2回再開する技は使えません。非決定的な選択も使えません。代わりに、選択ごとにプログラム全体を走らせ直してください。
- エフェクトは型に出ません。プログラムが`yield`するエフェクトのすべてに、付けたハンドラが答えるかどうかを、型検査器は確かめません。ハンドラが欠けていれば、プログラムを走らせた時に`UnhandledEffect`になります。
- エフェクトを起こせるのは、`@do`の関数の本体だけです。そこから呼んだ普通の関数は起こせません。だから`async`と同じく、`@do`は呼び出し元へ広がります。
- リスト内包表記の中では`yield`を書けないので、エフェクトを起こすループは`for`文になります。
- 型ヒントの無いハンドラは、すべてのエフェクトを受け取ります。型ヒントの無い偽物は、別のハンドラに向けたエフェクトに黙って答えてしまうことがあります。

## 試す

```bash
pip install doeff doeff-time   # CPython 3.11〜3.14。Linux・macOS・Windows向けのビルド済みのwheelがある。
python -m doeff demo           # doeffのデモを走らせる。
```

[What doeff Is](https://github.com/proboscis/doeff/blob/main/docs/positioning/what-doeff-is.md)(英語)が、仕組み・使い道・限界を定めています。ここに載せた例はどれも、doeff 0.8.0とdoeff-timeで、載せたとおりに走ります。完全なファイルは[examples/effects_tour](https://github.com/proboscis/doeff/tree/main/examples/effects_tour)にあります。テストの分は[examples/testing_without_mocks](https://github.com/proboscis/doeff/tree/main/examples/testing_without_mocks)です。docsの章[Why Effects?](https://github.com/proboscis/doeff/blob/main/docs/20-why-effects-over-di.md)(英語)は、エフェクトを依存性の注入や素の`async`と、もっと詳しく比べています。

どこで使いにくいと感じましたか。`yield`の書き方が邪魔になるコードや、素の引数のほうが簡単だった場合があれば教えてください。Issueは https://github.com/proboscis/doeff/issues で受け付けています。

## 困りごとから選ぶ入口

同じ書き方を、依存の供給・状態・失敗・並行処理・結果の再利用にも使えます。LLM・画像生成・エージェント・イベント・外部操作のパッケージも、同じ処理の形でつながります。

この記事は、doeffを週に1本ずつ紹介する連載の1本目です。10本の順は次のとおりで、公開した回からリンクを付けていきます。

1. Pythonの代数的エフェクトで、変えない処理にハンドラを重ねる(この記事)
2. [Claude Codeを起動してテストするのをやめる](doeff-agents.md)
3. [テストで20秒待つのをやめる](doeff-time.md)
4. [依存を引数で引き回すのをやめる](doeff-di.md)
5. [ライブラリのcommitの68%を、Claude Codeと書いた1年](doeff-commits.md)
6. [外部APIのテストで通信するのをやめる](doeff-replay.md)
7. [バックテストと本番で処理を2つ書くのをやめる](doeff-backtest.md)
8. [公開APIの型の穴をテストで塞ぎ、黙って開かせない](doeff-types.md)
9. [async/awaitがあるのに、なぜyieldで書くのか](doeff-coroutines.md)
10. [ADRを書いて終わりにするのをやめる](doeff-adr.md)

下の表は、困りごとごとの回の一覧です。各表では、上の10本の回を先に並べました。10本に無い回は、読まれ方を見て後から出す候補です。

### AIを使う処理を組み立てたい

| 悩み | doeffならこう扱える | 回 |
| --- | --- | --- |
| コーディングエージェントの起動・結果待ち・複数作業の連携が複雑 | セッションと結果の契約を操作として扱い、開発の手順へ組み込む | [エージェントの起動と結果待ち](doeff-agents.md) |
| LLMごとのSDK呼び出しが処理に広がっている | チャット・ストリーミング・構造化応答・埋め込みを共通の依頼にする | [LLMの共通エフェクト](doeff-llm.md) |
| 画像の生成・編集を制作パイプラインへ組み込みたい | 共通の画像エフェクトを使い、GeminiやSeedreamのハンドラを選ぶ | [画像生成・編集](doeff-image.md) |

### 時間・依存・状態・保存の悩み

| 悩み | doeffならこう扱える | 回 |
| --- | --- | --- |
| 待機を含むテストが遅い | 処理本体はそのままで、実時間と仮想時間のハンドラを切り替える | [時間とスケジューリング](doeff-time.md) |
| 依存を渡すだけの引数が増えている | 必要な場所でAskし、本番用・テスト用の依存をハンドラから渡す | [DI](doeff-di.md) |
| 過去のデータと本番で、同じ処理を動かしたい | 時刻・値段・注文を依頼にし、バックテストと本番のハンドラを付け替える | [バックテストの設計](doeff-backtest.md) |
| asyncへの変更が呼び出し元まで広がる | 処理を`@do`と`yield`でつなぎ、非同期SDKとの接続はハンドラ側に置く | [関数の色](doeff-color.md) |
| 状態・ログ・失敗の書き方がばらばら | 設定の読み取り・状態・ログ・失敗の操作を、同じ`yield`でつなぐ | [抽象の合成](doeff-composition.md) |
| 高価な処理を最初からやり直している | 完了した結果を保存し、再実行の時に再利用する | [永続実行](doeff-durable.md) |
| 手元・共有・永続保存の処理が重複する | 3つの層への問い合わせと、手前の層への保存を合成する | [階層メモ化](doeff-memo.md) |

### ゲームやイベント駆動の悩み

| 悩み | doeffならこう扱える | 回 |
| --- | --- | --- |
| 入力待ちでゲームの進行処理が分断される | 入力待ち・効果の解決・次のターンを一続きに書き、入力をハンドラで渡す | [カードゲーム](doeff-games.md) |
| イベント待ちのポーリングが増えている | イベントを待つ操作と届ける操作を分け、時間や完了待ちとも組み合わせる | [イベントと待機ループ](doeff-events.md) |
| 逐次・並行・失敗時の方針を変えるたびに本体を書き換える | 処理の記述は1つにして、実行と失敗時の戦略をハンドラで選ぶ | [コレクション処理](doeff-traverse.md) |

### 外部サービスや実行環境の悩み

| 悩み | doeffならこう扱える | 回 |
| --- | --- | --- |
| 調査やテストのたびにHTTPを呼び直している | HTTP取得と保存のハンドラを合成し、保存にあればHTTPを呼ばない | [記録・再生とメモ化](doeff-replay.md) |
| Git・通知・秘密情報の操作がテストでも本物の環境を触る | 操作をエフェクトにし、本番用とテスト用のハンドラで扱う | [外部操作](doeff-operations.md) |
| 処理をコンテナや別の機械へ持っていきたい | 環境の構築・転送・計算の実行をエフェクトで組み立てる | [Dockerと別環境での実行](doeff-remote.md) |

### 処理の把握・設計・検査の悩み

| 悩み | doeffならこう扱える | 回 |
| --- | --- | --- |
| AIが書く差分を読み切れず、ライブラリの約束が崩れないか不安 | 文書のコード・型・設計の約束・PyPIからの入れ方を、機械が毎日確かめる | [commitの68%をClaude Codeと書いた記録](doeff-commits.md) |
| 出したライブラリの型が、使う人のstrictでUnknownになる | 型の分からない公開の名と訳を表にし、増えても減ってもテストを赤にする | [公開APIの型の穴をテストで塞ぐ](doeff-types.md) |
| 設計で決めた約束が、コードと食い違っていく | ADRに規則・反例・検査を書き、pytestで走らせる | [設計の記録をテストに](doeff-adr.md) |
| 長い処理がどこまで進んだか分からない | ログと実行トレースで、通った経路や今の操作を観測する | [ログと実行観測](doeff-observe.md) |
| 定義を見つけにくい・設計上の約束が崩れる | 索引・リンター・実行できる設計記録・エフェクトの検査を組み合わせる | [開発支援](doeff-tooling.md) |
| 何をエフェクトにすればよいか迷う | 副作用やドメインの操作として境界を決め、操作の約束と実行方法を分ける | [エフェクトの境界](doeff-boundaries.md) |
| 合成処理をもっと簡潔に書きたい | Hyのマクロで、同じエフェクトとハンドラを組み立てる | [Hy](doeff-hy.md) |

### 仕組みを理解したい

| 疑問 | 何を確かめられるか | 回 |
| --- | --- | --- |
| coroutineと何が違うのか | `async def`・`await`と`@do`・`yield`で、依頼の渡し先と再開の仕方を比べる | [coroutineとの違い](doeff-coroutines.md) |
| ハンドラを重ねると何が起きるのか | 探す順番、担当外の`Pass`、別のエフェクトへの翻訳を実行して確かめる | [ハンドラの合成](doeff-handlers.md) |
| Rust VMはPythonの`yield`をどう動かすのか | 依頼の探索、中断した継続、`Resume`による再開をコードと対応付ける | [Rust VM](doeff-vm.md) |

## 参考

- [doeffのリポジトリ(GitHub)](https://github.com/proboscis/doeff)
- [What doeff Is(英語)](https://github.com/proboscis/doeff/blob/main/docs/positioning/what-doeff-is.md)
- [英語の原文:Algebraic effects for Python](https://github.com/proboscis/doeff/blob/main/docs/articles/algebraic-effects-in-python.md)
- 次の回:[Claude Codeを起動してテストするのをやめる](doeff-agents.md)
