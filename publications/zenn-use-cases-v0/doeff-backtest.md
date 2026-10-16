---
title: "バックテストと本番で同じ処理を動かす — 時刻・値段・注文をハンドラで差し替える"
emoji: "📈"
type: "tech"
topics: ["python", "doeff", "バックテスト", "テスト"]
published: false
---

売買の判断をする処理を書くと、過去の相場のデータで動かして確かめたくなります。これをバックテストと呼びます。ところが、バックテスト用の処理と本番用の処理は、いつの間にか別物になりがちです。

- 処理の中で`datetime.now()`を読むと、過去のデータの時刻と合いません。
- 値段の取得と注文の送信を、バックテストのために書き直すことになります。
- 書き直した2つの処理は、直すたびに少しずつずれていきます。
- 1分ごとに動く処理の1週間分を確かめるのに、1週間は待てません。

この記事では、Pythonのライブラリdoeffで、時刻・値段・注文の3つを処理の外側へ出します。そうすると、1つの処理をバックテストと本番の両方で動かせます。doeffは、処理の中の「待つ」「読む」といった操作を、外側の部品で差し替えられるライブラリです。

:::message
この記事は、売買の判断そのものは扱いません。判断の処理は中身の無い置き物にし、値段も架空の数を使います。扱うのは、判断を包む処理の設計です。
:::

## まず短いコード

処理の側だけを先に見ます。時刻・値段・注文の3つとも、処理は外側に頼むだけです。

```python
from dataclasses import dataclass  # 依頼を、書き換えない値として定義する。

from doeff import Effect, do  # 依頼の基底の型と、処理の定義を使う。
from doeff_core_effects import Tell  # 「この値を書き残したい」という依頼。
from doeff_time import Delay, GetTime  # 待ちの依頼と、今の時刻を読む依頼を使う。


@dataclass(frozen=True)  # 依頼の中身を、後から書き換えない。
class GetPrice(Effect):  # 「今の値段を知りたい」という依頼。
    symbol: str  # どの銘柄の値段か。


@dataclass(frozen=True)  # 依頼の中身を、後から書き換えない。
class PlaceOrder(Effect):  # 「この注文を出したい」という依頼。
    symbol: str  # どの銘柄を。
    side: str  # "buy"(買い)か"sell"(売り)か。
    quantity: int  # 何単位か。


def decide(now, price):  # 判断の置き物。この記事では中身を書かない。
    return ("buy", 1)  # 時刻も値段も見ずに、毎回同じ注文を返す。


@do  # 呼んだだけでは動かない処理(Program)にする。
def trading_loop(symbol: str, steps: int):  # 1分ごとに、steps回だけ判断と注文を繰り返す。
    for _ in range(steps):  # 決めた回数だけ回す。
        now = yield GetTime()  # 今の時刻を、外側に頼んで受け取る。
        price = yield GetPrice(symbol)  # 今の値段を、外側に頼んで受け取る。
        side, quantity = decide(now, price)  # 置き物の判断に、時刻と値段を渡す。
        receipt = yield PlaceOrder(symbol, side, quantity)  # 注文を頼み、受付の番号を受け取る。
        yield Tell((now.strftime("%H:%M"), price, receipt))  # この回の控えを、外側に書き残す。
        yield Delay(60)  # 次の1分まで待つ。
```

`trading_loop`は、時計も取引所も知りません。`yield`で依頼を渡し、外側から答えを受け取ります。答える部品を替えると、同じ処理が過去のデータでも本番でも動きます。

![同じ処理に、バックテストと本番のハンドラを付け替える](/images/zenn-use-cases-v0/generated/backtest-concept.png)
*処理は時刻・値段・注文・待ちを依頼するだけです。バックテストでは仮想の時計と記録が、本番ではOSの時計と取引所が答えます。*

## 時刻・値段・注文を、外側への依頼にする

コードに出てきた語を、先に整理します。

- `@do`を付けた関数は、呼んでもすぐには動きません。手順を表す`Program`を返します。
- `yield GetPrice("DEMO")`は、「今の値段を知りたい」という依頼を外側へ渡します。
- このような依頼をエフェクト(effect)と呼びます。`Effect`を継承して自分で定義できます。
- 依頼を受けて、何を返すかを決める部品をハンドラ(handler)と呼びます。
- `GetTime`と`Delay`は、doeffの時間の部品doeff-timeが用意する依頼です。
- `Tell(値)`は、「この値を書き残したい」という依頼です。基本の部品doeff-core-effectsにあります。

依頼ごとに、バックテストと本番のハンドラが違う答え方をします。

| 処理の内側が書くこと | バックテストのハンドラ | 本番のハンドラ |
| --- | --- | --- |
| 今の時刻を知りたい | 仮想の時計を読む | OSの時計を読む |
| 1分待ちたい | 仮想の時計を1分進める | 実際に60秒待つ |
| 今の値段を知りたい | 記録から、今以前の最後の値段を返す | 取引所のAPIに問う |
| 注文を出したい | 送らずに、受付の番号だけを返す | 取引所のAPIへ送る |
| 控えを書き残したい | 手元に集める | 同じく、手元に集める |

判断の置き物`decide`も、時刻と値段を引数で受け取ります。自分で`datetime.now()`を読みません。中身を本物の判断に替えても、この形は変わりません。

## 過去のデータで動かすハンドラ

バックテスト用のハンドラを書きます。値段の依頼には過去の記録で答え、注文の依頼は受け付けるだけにします。

```python
from bisect import bisect_right  # 時刻の並びから、今以前の最後の記録を探す。
from datetime import datetime, timedelta, timezone  # 架空の記録の時刻を作る。

from doeff import Pass, Resume, handler, run, with_handlers  # ハンドラの定義・再開・委譲・実行を使う。
from doeff_core_effects.handlers import state, writer, writer_log  # 控えを集める部品。
from doeff_core_effects.scheduler import scheduled  # 待っている処理の再開と時計の進みを受け持つ。
from doeff_time import sim_time_handler  # 仮想の時計のハンドラ。


def backtest_market(history):  # 記録で値段に答え、注文を受け付けるハンドラを作る。
    times = [at for at, _ in history]  # 記録の時刻だけを、古い順に並べる。

    @handler  # Programに取り付けられるハンドラにする。
    @do  # ハンドラ自身も、yieldで依頼や再開を書く。
    def market(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
        if isinstance(effect, GetPrice):  # 値段の依頼なら、
            now = yield GetTime()  # 仮想の時計に、今の時刻を問う。
            index = bisect_right(times, now) - 1  # 今以前で最後の記録の位置。
            if index < 0:  # 今より前の記録が無ければ、
                raise LookupError(f"{now}より前の記録がありません")  # 値を作らずに止める。
            return (yield Resume(k, history[index][1]))  # その記録の値段で、処理を再開する。
        if isinstance(effect, PlaceOrder):  # 注文の依頼なら、
            now = yield GetTime()  # 注文を受けた時刻を問う。
            return (yield Resume(k, f"bt-{now:%H%M}"))  # 送らずに、時刻から作った受付の番号を返す。
        return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。

    return market  # 作ったハンドラを返す。


@do  # 補助の処理も、yieldで組み立てる。
def with_log(program):  # 処理を動かし終えてから、書き残した控えを読む。
    yield program  # 処理を最後まで動かす。
    return (yield writer_log())  # writerが集めた控えの一覧を受け取る。


START = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)  # 架空の記録の始まり。


def backtest(history, steps, start=START):  # 記録と仮想の時計で、trading_loopを回す。
    handlers = [state(), writer, backtest_market(history)]  # 控えの置き場・集め役・値段と注文。
    program = with_handlers(handlers, with_log(trading_loop("DEMO", steps)))  # 処理に付ける。
    return run(scheduled(sim_time_handler(start_time=start)(program)))  # startから仮想の時計で回す。


history = [(START + timedelta(minutes=i), 100.0 + i) for i in range(3)]  # 架空の値段100・101・102。
print(backtest(history, 3))  # 表示: [('09:00', 100.0, 'bt-0900'), ('09:01', 101.0, 'bt-0901'), ('09:02', 102.0, 'bt-0902')]
```

- `backtest_market`は、値段と注文の2つの依頼を受け持ちます。
- 値段の依頼が来ると、まず`GetTime()`で今の時刻を問います。
- ハンドラの中の依頼は、さらに外側のハンドラへ届きます。ここでは仮想の時計が答えます。
- `bisect_right`で、今以前で最後の記録を探します。
- 注文の依頼は、どこにも送りません。時刻から作った受付の番号を返します。
- 時刻と待ちの依頼は担当外なので、`Pass`で外側へ渡します。
- `Resume(k, 値)`は、止まっていた処理へ値を渡して続きを動かします。
- `writer`は`Tell`の値を集め、`state()`の置き場に置きます。`writer_log()`で読み出せます。
- `with_handlers`の並びは、左ほど外側です。`scheduled`は、待つ処理の再開を受け持ちます。

![値段の依頼は、仮想の時計に今を問うてから答える](/images/zenn-use-cases-v0/generated/backtest-flow.png)
*09:01の回の流れです。ハンドラは時計に今を問い、09:01以前の最後の記録101.0を返します。09:02の記録は、まだ読めません。*

### 未来の値段を読ませない

**今以前の記録だけを返す所が、この設計の要です。** バックテストでよくある誤りに、未来の値段を読んでしまう事があります。処理の側は値段を時刻で引けないので、気を付ける所はハンドラの中の1か所にまとまります。

```python
import pytest  # 例外が起きることを確かめる。


def test_no_price_from_the_future():  # 30秒後に変わる値段を、今の時点で読めない。
    later = [(START, 100.0), (START + timedelta(seconds=30), 999.0)]  # 30秒後に値段が変わる記録。
    assert backtest(later, 1) == [("09:00", 100.0, "bt-0900")]  # 09:00には、まだ999.0を読めない。


def test_no_record_before_start():  # 記録より前の時刻では、値段を作らずに止まる。
    early = START - timedelta(minutes=1)  # 記録の1分前から時計を始める。
    with pytest.raises(LookupError):  # 値段が無いと、例外で分かる。
        backtest(history, 1, start=early)  # 08:59に値段を問う。
```

記録の時刻は、その値段が分かった時刻にします。たとえば1分足に始まりの時刻を付けると、困った事が起きます。09:00の時点で、まだ分からない09:00台の終値を読めてしまうからです。

注文がどの値段で成立したかや手数料も、このハンドラの中で扱えます。この記事では、受付の番号を返すだけにしました。どう扱っても、処理の側は変わりません。

## 本番では、ハンドラだけを替える

本番では、取引所のクライアントで答えるハンドラを付けます。`trading_loop`と`decide`は、そのまま使います。

```python
from typing import Protocol  # 本番のクライアントに求める形を書く。

from doeff_time import sync_time_handler  # OSの時計で、実際に待つハンドラ。


class Broker(Protocol):  # 本番の取引所のクライアントに求める2つの操作。
    def price(self, symbol: str) -> float: ...  # 今の値段を返す。
    def order(self, symbol: str, side: str, quantity: int) -> str: ...  # 注文を送り、受付の番号を返す。


def live_market(broker: Broker):  # 本番のクライアントで答えるハンドラを作る。
    @handler  # Programに取り付けられるハンドラにする。
    @do  # ハンドラ自身も、yieldで再開や委譲を書く。
    def market(effect, k):  # バックテストのハンドラと、同じ依頼を受け持つ。
        if isinstance(effect, GetPrice):  # 値段の依頼なら、
            return (yield Resume(k, broker.price(effect.symbol)))  # 取引所に問うた値段を返す。
        if isinstance(effect, PlaceOrder):  # 注文の依頼なら、
            receipt = broker.order(effect.symbol, effect.side, effect.quantity)  # 取引所へ送る。
            return (yield Resume(k, receipt))  # 取引所の受付の番号を返す。
        return (yield Pass(effect, k))  # 時刻と待ちは、外側のOSの時計へ渡す。

    return market  # 作ったハンドラを返す。


def run_live(broker: Broker, steps: int):  # 本番では、OSの時計と取引所のクライアントを付ける。
    handlers = [state(), writer, live_market(broker)]  # 値段と注文の答え方だけが違う。
    program = with_handlers(handlers, with_log(trading_loop("DEMO", steps)))  # 同じ処理を使う。
    return run(scheduled(sync_time_handler()(program)))  # 1分ごとに、実際に待ちながら動く。
```

- `Broker`は、取引所ごとのSDKを包んで揃える形です。値段と注文の2つだけを求めます。
- `live_market`は、`backtest_market`と同じ2つの依頼を受け持ちます。
- `sync_time_handler`は、OSの時計を読み、`Delay(60)`で実際に60秒待ちます。

この記事では、実際の取引所には繋いでいません。値段と受付の番号を固定で返す偽物のクライアントを`run_live`に渡しました。2026年10月17日に1回だけ動かすと、表示は`[('17:38', 100.0, '受付A')]`でした。時刻はUTCです。全体で約60秒かかりました。

非同期のSDKを使う時は、ハンドラの中で`Await`を使います。処理の側に`async def`は広がりません。詳しくは[関数の色の記事](doeff-color.md)で扱います。

## 1週間分を、1週間待たずに回す

1分ごとに動く処理の1週間分は、10,080回です。仮想の時計なら、待たずに回せます。

```python
def test_one_week_without_waiting():  # 1週間分の1分ごとの処理を、1週間待たずに回す。
    minutes = 7 * 24 * 60  # 1週間は10,080分。
    week = [(START + timedelta(minutes=i), 100.0) for i in range(minutes)]  # 架空の値段を毎分置く。
    log = backtest(week, minutes)  # 同じ処理を、仮想の時計で回す。
    assert len(log) == minutes  # 毎分1回、10,080回の注文を受け付けた。
    assert log[-1] == ("08:59", 100.0, "bt-0859")  # 最後の回は、7日後の09:00の1分前。
```

仮想の時計は、動ける処理が無くなった時だけ、次に起きる予定の時刻へ飛びます。`Delay(60)`で待つ間に、実際の60秒は過ぎません。時計の進み方は、[20秒待つ処理を20秒待たずにテストする記事](doeff-time.md)で詳しく扱います。

2026年10月17日に、このテストを同じ機械で3回走らせました。1回あたりの実時間は3.1〜3.8秒でした。同じ形で30日分(43,200回)を回すと、約17秒でした。ライブラリを読み込む時間は、この数に含みません。

:::message
この記事のコードは、2026年10月17日にdoeffのmain(コミット`d1c6746`)で確かめました。コードは上から順に、前のコードの続きとして動きます。`doeff`・`doeff-core-effects`・`doeff-time`の3つのパッケージが要ります。
:::

## まとめ

- 時刻・値段・注文を`yield`の依頼にすると、答える部品を外側で選べます。
- バックテストでは、仮想の時計と過去の記録のハンドラを付けます。
- 本番では、OSの時計と取引所のクライアントのハンドラに替えます。処理は変えません。
- 未来の値段を読ませない守りは、値段のハンドラの1か所に置けます。テストでも確かめられます。
- 仮想の時計なら、1分ごとに動く処理の1週間分を数秒で回せます。

依存を外側から渡す考え方は、[DIの代わりにeffect handlerの記事](doeff-di.md)で扱います。外部のAPIを通信せずにテストする方法は、[記録・再生とメモ化の記事](doeff-replay.md)にあります。`Tell`と状態の組み合わせは、[抽象の合成の記事](doeff-composition.md)で詳しく扱います。

[全体を実行できる例](examples/backtest.py)もリポジトリにあります。

[doeffでできること：メイン記事へ戻る](doeff-main.md)

## 参考資料・検証版

導入方法は[公式README](https://github.com/proboscis/doeff#installation)にあります。以下は、この記事で確かめた版へのリンクです。公開パッケージの最新版で同じように動くかは、確かめていません。

- [doeff本体(この記事で確かめた版)](https://github.com/proboscis/doeff/tree/d1c67469d14d7ab3ee185f5161f6965092213e9d)
- [時刻と待ちの依頼と、3つの時計のハンドラ](https://github.com/proboscis/doeff/tree/d1c67469d14d7ab3ee185f5161f6965092213e9d/packages/doeff-time)
- [Tell・state・writerの実装](https://github.com/proboscis/doeff/blob/d1c67469d14d7ab3ee185f5161f6965092213e9d/packages/doeff-core-effects/doeff_core_effects/handlers.py)
- [待っている処理の再開を受け持つスケジューラ](https://github.com/proboscis/doeff/blob/d1c67469d14d7ab3ee185f5161f6965092213e9d/packages/doeff-core-effects/doeff_core_effects/scheduler.py)
- 前の回:[外部APIのテストを、通信せずに回す](doeff-replay.md)
- 次の回:[AIに要る時だけ規則を見せるhook](doeff-hooks.md)
