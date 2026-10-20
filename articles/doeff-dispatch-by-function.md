---
title: "Claude Codeに仕事を配らせるのをやめ、関数で配る"
emoji: "🚦"
type: "tech"
topics: ["python", "doeff", "claudecode", "ai", "設計"]
published: true
published_at: 2026-10-23 08:00
---

筆者は、作業ボードのcardをClaude Codeの会話へ配る役も、Claude Codeの会話に任せていました。2026年10月10日の1日、その配り役が配ったのは、配れたcard 445枚のうち289枚でした。配る判断を関数に移して同じ1日を模擬すると、385〜446枚を配りました。

急ぎのcardの最長の待ちも、実際の557分から、模擬では11〜109分に縮みました。10月11日からは本番もこの関数で配り、AIの会話は詰まりを見張る役に回りました。

AIの会話に配らせると、配る判断が会話の頭の中にあります。

- 止まっても、なぜ止まったのかが外から見えません。
- 別の配り方ならどうだったかを、同じ日で試し直せません。
- 決まりを書き足しても、当てはめ方は会話の読み方しだいです。

cardは作業ボードに1件ずつ書いた仕事で、会話1つはClaude Codeの1つのセッションです。この記事は、配る判断をAIの会話から関数へ移した記録です。仕組みの全体は、この連載の1本目「1人でClaude Codeを最大50並べたら、詰まるのは人だった(公開予定)」です。doeffが初めての方は[doeffの連載の1本目](https://zenn.dev/proboscis/articles/doeff-introduction)から、コードは[GitHub](https://github.com/proboscis/doeff)から読めます。

## まず短いコード — 配る順を、純粋な関数にする

配る判断のうち、「どのcardを先に配るか」を関数にします。この関数を、配る順(strategy)と呼びます。配る順の約束は、次の3つです。

- 受け取るのは、配れるcardの一覧・空きの数・前の周期から持ち越した値です。
- 返すのは、配るcardの鍵の組と、次の周期へ持ち越す値です。
- 選ぶのは配れるcardだけで、数は空きまでです。同じcardを2度選びません。

```python
from dataclasses import dataclass  # cardを、書き換えないデータ型で表す。

PRIORITIES = ("p0", "p1", "p2", "p3")  # 急ぐ順の優先度。p0が一番急ぐ。


@dataclass(frozen=True)  # 書き換えないデータ型にする。
class ReadyCard:  # 配れるcard 1枚。
    key: str  # cardの鍵。
    priority: str  # 優先度(p0〜p3)。
    ready_at: int  # 配れるようになった時刻(分)。


def strict(ready, free, memo):  # C 厳密な優先: 急ぐ順、同じ優先度は古い順。
    ordered = sorted(ready, key=lambda card: (PRIORITIES.index(card.priority), card.ready_at))  # 急ぐ順に並べる。
    return tuple(card.key for card in ordered[:free]), memo  # 空きの数だけ選び、持ち越す値はそのまま返す。


def fifo(ready, free, memo):  # E 先着順: 配れるようになった古い順だけ。
    ordered = sorted(ready, key=lambda card: card.ready_at)  # 古い順に並べる。
    return tuple(card.key for card in ordered[:free]), memo  # 空きの数だけ選ぶ。


ready = (ReadyCard("A", "p2", 0), ReadyCard("B", "p0", 5), ReadyCard("C", "p3", 1), ReadyCard("D", "p1", 3))  # 配れる4枚。
print(strict(ready, 2, {})[0], fifo(ready, 2, {})[0])  # ('B', 'D') ('A', 'C')と表示される。
```

空きが2つの時、`strict`は急ぐ順に、p0のBとp1のDを選びます。`fifo`は優先度を見ずに、古い順にAとCを選びます。

どちらも、読み書きも時刻の読みもしない純粋な関数です。同じ入力なら、いつ呼んでも同じ答えを返します。AIの会話の判断は、同じ入力でも同じ答えになるとは限りません。

## 全体の図 — 配り役の会話を、見張りの会話に替える

![配る判断を、会話の頭の中から関数へ移す](/images/zenn-use-cases-v0/generated/dispatch-concept.png)
*左が前の形、右が今の形です。配る判断は関数が持ち、AIの会話は詰まりを見張るだけになりました。*

前の形では、配り役の会話が作業ボードを読み、頭の中で選び、新しい会話を起こしていました。今の形では、同じ所にscheduler(配りの機械)が入ります。

| 役 | 前の持ち主 | 今の持ち主 |
| --- | --- | --- |
| どのcardを先に配るか | 配り役の会話 | 配る順の関数(設定の値1つで選ぶ) |
| 幾つ配るか | 配り役の会話 | 空き(上限50から走っている会話を引き、利用枠でも抑える) |
| 会話を起こしてcardを渡す | 配り役の会話 | scheduler |
| 終わらずに手放されたcardを戻す | 配り役の会話 | scheduler(戻す回数に上限を付ける) |
| 配りの詰まりに気づいて知らせる | 無かった | 見張りの会話 |

cardを切る・依存を結ぶ・受け入れの条件を書くのは、今もAIの会話の仕事です。変わったのは、どれを始めるかを選ぶ所だけです。

## AIの配り役で起きたこと

配り役の会話は、作業ボードを読み、決まりに照らして配っていました。決まりは、依存の扱いや利用枠の扱いなどです。2026年10月10日の1日(日本時間)を後から数えると、次のようでした。

- 配れたcard 445枚のうち、配ったのは289枚でした。156枚は、その日のうちに配られませんでした。
- 急ぎ(優先度p0)のcard 1枚は、0時の時点で配れたのに、9時17分まで配られませんでした。待ちは0時から数えて557分です。
- 3時39分から5時7分までの88分は、1枚も配りませんでした。

次の夜も、同じことが起きました。10月11日の2時15分から4時5分まで、空きがあるのに1枚も配っていません。夜は利用枠を残そうと、配り役の会話が自分で控えたためです。

筆者は、利用枠や時刻を理由に配るのを止めない、という決まりを足しました。けれども、決まりを読むのも当てはめるのも、同じAIの会話です。次の夜に守られるかは、走らせてみるまで分かりません。

そこで、配る判断を関数に移しました。配る順は差し替えられる形にし、いくつかを同じ負荷で比べてから選びます。

## 1周期の配りを、doeffのProgramにする

doeffは、処理の中の「読む」「頼む」といった操作を、外側の部品で差し替えられるライブラリです。処理は操作を外側へ依頼するだけで、依頼に答える部品をハンドラと呼びます。

配りの1周期は、読む・選ぶ・頼むの3つです。冒頭の配る順の関数を、そのまま使います。

```python
from doeff import Effect, Pass, Resume, do, handler, run, with_handlers  # 依頼・再開・委譲と実行を使う。
from doeff_core_effects import Ask  # 設定の値を、キーで依頼する。
from doeff_core_effects.handlers import reader  # 設定の値を、辞書から答える。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class ReadReady(Effect):  # 「配れるcardの一覧を読みたい」という依頼。
    pass  # 引数は持たない。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class ReadFree(Effect):  # 「新しく配れる数(空き)を読みたい」という依頼。
    pass  # 引数は持たない。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class Dispatch(Effect):  # 「このcardを、新しい会話に頼みたい」という依頼。
    key: str  # 頼むcardの鍵。


@do  # 呼ぶと、1周期の配りの手順(Program)ができる。
def tick(memo):  # 前の周期から持ち越した値を受け取る。
    strategy = yield Ask("strategy")  # 配る順の関数を、設定から受け取る。
    ready = yield ReadReady()  # 配れるcardを読む。
    free = yield ReadFree()  # 空きを読む。
    picks, memo = strategy(ready, free, memo)  # 配る順の関数に選ばせる。
    keys = {card.key for card in ready}  # 選んでよいcardの鍵。
    if len(picks) > free or len(set(picks)) < len(picks) or not set(picks) <= keys:  # 約束の外の答えなら、
        raise ValueError(f"配る順の答えが約束の外です: {picks}")  # 黙って配らず、止める。
    for key in picks:  # 選んだcardを、
        yield Dispatch(key)  # 1枚ずつ頼む。
    return memo  # 次の周期へ持ち越す値を返す。


@handler  # Programに取り付けられるハンドラにする。
@do  # ハンドラもyieldで、再開や委譲を指示する。
def dry_run(effect, k):  # 記録だけの運転: 会話を起こさず、配るはずだったcardを表示する。
    if isinstance(effect, Dispatch):  # 頼む依頼だけを受け持つ。
        print("配るはずだった:", effect.key)  # 本番のハンドラなら、ここで会話を開く。
        return (yield Resume(k, None))  # 処理の続きを動かす。
    return (yield Pass(effect, k))  # 読みの依頼は、外側のハンドラへ渡す。


@handler  # Programに取り付けられるハンドラにする。
@do  # ハンドラもyieldで、再開や委譲を指示する。
def fixed_board(effect, k):  # テスト用に、決めた盤面と空きを答える。
    if isinstance(effect, ReadReady):  # 配れるcardの依頼には、
        return (yield Resume(k, ready))  # 冒頭の4枚を返す。
    if isinstance(effect, ReadFree):  # 空きの依頼には、
        return (yield Resume(k, 2))  # 2を返す。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


program = with_handlers([fixed_board, dry_run], tick({}))  # 盤面を外側に、記録だけの運転を内側に付ける。
run(reader(env={"strategy": strict})(program))  # 「配るはずだった: B」「配るはずだった: D」と表示される。
```

- `tick`は、配る順・配れるcard・空きを外側へ依頼し、選んだcardを1枚ずつ頼みます。
- 配る順の答えが約束の外なら、`tick`は黙って配らずに止まります。
- `dry_run`は頼む依頼だけを受け持ち、会話を起こさずに表示します。
- 読みの依頼は`Pass`で外側へ渡り、`fixed_board`が答えます。

`@do`を付けた関数は、呼ぶと実行の手順(`Program`)を返します。ハンドラは、届いた依頼`effect`と、止まった処理の続き`k`を受け取ります。`Resume(k, 値)`で値を渡し、続きを動かします。

![同じtickを、3つのハンドラの組で動かす](/images/zenn-use-cases-v0/generated/dispatch-flow.png)
*配りの判断は1つだけ書きます。本番・記録だけの運転・模擬で替えるのは、外側のハンドラの組だけです。*

本物のschedulerも、同じ形です。ハンドラの組は、次の3つを用意しました。

| 依頼 | 本番 | 記録だけの運転 | 模擬 |
| --- | --- | --- | --- |
| 配れるcardと空きの読み | 作業ボードと上限をHTTPで読む | 本番と同じ | メモリ上の世界の値 |
| 会話を起こしてcardを渡す | 会話を開き、依頼を送る | 配るはずだったcardを記録する | 世界に会話を足す |
| 盤面が変わるまで待つ | 作業ボードの変化の通知を待つ | 本番と同じ | 仮想の時計を進める |

どの組を使うかは、組み立ての1か所で選びます。処理の中に「本番か模擬か」の分岐はありません。本物はPythonの上で動くLispのHyで書き、doeffを使っています。

## 5つの配り方を、記録した1日の負荷で比べる

配る順は、次の5つを用意しました。どれも空きを残さず、配れるcardがある限り配ります。違うのは、どれを先に配るかです。

| 配る順 | 先に配るcard | 似ている既存の仕組み |
| --- | --- | --- |
| A 重みつきの経過 | 優先度の重み × 待った時間の大きい順 | Slurmのmultifactor priority |
| B 取り置き | Aの順で、上限の20%を急ぎに残し、利用枠が減ると低い物を止める | Linuxのtcのhtb |
| C 厳密な優先 | 優先度の順で、同じ優先度は古い順 | KubernetesのPriorityClass |
| D 重みつきの公平な分配 | 重みの比で、優先度ごとに順番を回す | WFQ・stride scheduling・LinuxのCFS |
| E 先着順 | 配れるようになった古い順 | 比べる基準 |

Dは、優先度ごとに「次の番の仮想の刻」を持ちます。配るたびに、その優先度の刻を重みの逆数だけ進めます。しばらくcardが無かった優先度は、今の仮想の刻から番を数え直します。重みは、p0から順に16・8・4・2です。

```python
WEIGHTS = {"p0": 16, "p1": 8, "p2": 4, "p3": 2}  # 優先度ごとの重み。配る数の比になる。


def wfq(ready, free, memo):  # D 重みつきの公平な分配。
    turn, clock = memo.get("turn", {}), memo.get("clock", 0.0)  # 優先度ごとの次の番と、今の仮想の刻。
    waiting, picks = tuple(sorted(ready, key=lambda card: card.ready_at)), ()  # 同じ優先度の中は古い順に取る。
    while waiting and len(picks) < free:  # 空きがある間、1枚ずつ取る。
        # 番が一番早い優先度を選ぶ。番の刻が同じなら、急ぐ方を先にする。
        start, _, chosen = min((max(turn.get(c.priority, 0.0), clock), PRIORITIES.index(c.priority), c.priority) for c in waiting)
        card = next(c for c in waiting if c.priority == chosen)  # その優先度の一番古いcard。
        waiting, picks = tuple(c for c in waiting if c != card), (*picks, card.key)  # 待ちから外し、配るcardに足す。
        turn, clock = {**turn, chosen: start + 1 / WEIGHTS[chosen]}, start  # 次の番を、重みの逆数だけ先へ進める。
    return picks, {"turn": turn, "clock": clock}  # 選んだcardと、次の周期へ持ち越す値。


many = tuple(ReadyCard(f"U{i}", "p0", i) for i in range(10)) + (ReadyCard("L1", "p3", 0), ReadyCard("L2", "p3", 1))  # 急ぎ10枚と低い2枚。


def low(picks):  # 選ばれたcardのうち、低い優先度の物だけを残す。
    return [key for key in picks if key.startswith("L")]  # 鍵がLで始まるcard。


print(low(strict(many, 9, {})[0]), low(wfq(many, 9, {})[0]))  # [] ['L1']と表示される。
```

急ぎのcard 10枚と低いcard 2枚が配れて、空きが9つあるとします。Cは、急ぎだけで9つを埋めます。Dは、9枚のうち1枚を低いcardに回します。急ぎが途切れずに来ても、低いcardが配られずに残り続けません。

次に、1日の負荷を仮想の時計で回し、3つの配る順を比べます。本番と同じ周回`serve`を、世界の値で答えるハンドラの上で動かします。

```python
@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class AwaitChange(Effect):  # 「盤面が変わるまで待ちたい」という依頼。
    pass  # 引数は持たない。


@do  # 本番と同じ周回: 盤面が変わるたびに、1周期を配る。
def serve():  # 止める合図が来るまで回る。
    memo = {}  # 配る順の関数が持ち越す値。
    while (yield AwaitChange()):  # 変わったら回し、止める合図(False)で抜ける。
        memo = yield tick(memo)  # 1周期を配る。


def simulate(strategy, cards, ceiling, minutes=1440):  # 1日の負荷を、仮想の1分刻みで回す。
    world = {"now": -1, "running": {}, "waits": {}}  # 世界の値: 今の分・走っている会話・cardごとの待ち。

    @handler  # Programに取り付けられるハンドラにする。
    @do  # ハンドラもyieldで、再開や委譲を指示する。
    def world_handler(effect, k):  # 配りの依頼に、世界の値で答える。
        if isinstance(effect, AwaitChange):  # 待ちの依頼には、時計を1分進め、
            world["now"] += 1  # 仮想の時計を進める。
            world["running"] = {key: end for key, end in world["running"].items() if end > world["now"]}  # 終わった会話を外す。
            return (yield Resume(k, world["now"] < minutes))  # 1日が終わったら止める合図を返す。
        if isinstance(effect, ReadReady):  # 配れるcardの依頼には、
            ready = tuple(ReadyCard(key, p, at) for key, p, at, _ in cards if at <= world["now"] and key not in world["waits"])  # 着いていて、まだ配っていないcard。
            return (yield Resume(k, ready))  # 一覧を返す。
        if isinstance(effect, ReadFree):  # 空きは、上限から走っている会話の数を引いた数。
            return (yield Resume(k, ceiling - len(world["running"])))  # 空きを返す。
        if isinstance(effect, Dispatch):  # 頼む依頼には、
            key, p, at, work = next(card for card in cards if card[0] == effect.key)  # 頼まれたcardを引く。
            world["waits"][key] = (p, world["now"] - at)  # 優先度と待った分を書く。
            world["running"][key] = world["now"] + work  # 仕事が終わる分を書く。
            return (yield Resume(k, None))  # 処理の続きを動かす。
        return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。

    run(reader(env={"strategy": strategy})(with_handlers([world_handler], serve())))  # 本番と同じserveを回す。
    waits = world["waits"]  # 配ったcardの待ち。
    left = [minutes - at for key, _, at, _ in cards if key not in waits]  # 配られずに1日を終えたcardの待ち。
    p0 = max(wait for p, wait in waits.values() if p == "p0")  # 急ぎのcardの最長の待ち。
    return len(waits), p0, max([wait for _, wait in waits.values()] + left)  # 配った枚数・急ぎの最長・全体の最長。


MIX = ("p0", "p1", "p1", "p2", "p2", "p2", "p2", "p2", "p3", "p3")  # 優先度の割合(10枚ごと)。
cards = [(f"K{i}", MIX[i % 10], (i * 7) % 1440, 20 + (i * 53) % 160) for i in range(400)]  # 鍵・優先度・着く分・仕事の分。
for name, strategy in (("C 厳密な優先", strict), ("D 重みつきの公平", wfq), ("E 先着順", fifo)):  # 同じ負荷で比べる。
    count, p0, longest = simulate(strategy, cards, ceiling=26)  # 上限26で1日を回す。
    print(f"{name}: 配った{count}枚・急ぎの最長{p0}分・全体の最長{longest}分")  # 3行を表示する。
```

表示は次の3行です。

```text
C 厳密な優先: 配った372枚・急ぎの最長12分・全体の最長612分
D 重みつきの公平: 配った374枚・急ぎの最長12分・全体の最長237分
E 先着順: 配った375枚・急ぎの最長131分・全体の最長135分
```

- 400枚のcardが1日に少しずつ着き、仕事の長さは20〜179分です。上限26では、空きより配れるcardが多い時間帯があります。
- Cは急ぎを最長12分で配る一方、低いcardを最長612分待たせます。
- Eは誰も135分より長く待たせない代わりに、急ぎも131分待たせます。
- Dは急ぎを12分で配り、全体の最長も237分に収めます。

2026年10月18日に5回走らせると、3つの配る順の1日は1.2〜2.9秒で終わりました。ライブラリの読み込みは含みません。

本物の比べ方も、この形です。2026年10月10日の作業ボードの記録から、負荷の形を作りました。着いた時刻・優先度・依存・仕事の長さです。そこへ、本番と同じ判断を1分ごとに呼びました。

利用枠の動きは記録から読めないので、2つの仮定を置きました。逼迫の仮定は、5時間ごとの利用枠の合計をその日の負荷の60%に置き、尽きる時間帯を作ります。余裕の仮定は、利用枠を負荷の10倍に置きます。

![配った枚数と、急ぎのcardの最長の待ち](/images/zenn-use-cases-v0/generated/dispatch-compare.png)
*2026年10月10日の負荷で比べました。実績はAIの配り役が実際に配った値で、A〜Eは2つの仮定での模擬の値です。*

| | 配った枚数 | 急ぎの最長の待ち | 全体の最長の待ち |
| --- | --- | --- | --- |
| 実績(AIの配り役) | 289枚 | 557分 | 674分 |
| A 重みつきの経過 | 392〜441枚 | 30〜38分 | 103〜176分 |
| B 取り置き | 369〜441枚 | 30分 | 103〜233分 |
| C 厳密な優先 | 395〜439枚 | 11分 | 279分 |
| D 重みつきの公平な分配 | 397〜446枚 | 30分 | 164〜176分 |
| E 先着順 | 385〜439枚 | 58〜109分 | 58〜159分 |

- どの配り方も、実績より多く配り、急ぎを短く待たせました。
- 配った数の差には、上限の差も入っています。10月10日の夕方から、配り役は同時に持たせる会話を20までに抑えていました。模擬の上限は50です。
- Cは急ぎが一番速い一方、到着の時刻を揺らした合成の負荷では、1日配られないcardが出ました。
- 全体の最長の待ちは、1日の終わりに残ったcardも、終わりまでの待ちで数えました。分は小数点以下を四捨五入しました。

記録は、負荷の形にだけ使いました。判断の正しさは、場面ごとのテスト9本で確かめています。判断を1つずつわざと壊し、テストが落ちるのを見てから直しました。

## 本番に入れて、AIの会話を見張りに回す

比べた報告で推したのは、Dでした。筆者は、本番の最初の配り方にCを選びました。低いcardの最長の待ちを本番で毎日測り、1日の後にBとDを見直すことにしました。1日を測った後も、2026年10月18日の時点でCのまま動かしています。

切り替えの前に、2026年10月11日の16時47分から1時間、記録だけの運転を回しました。212周期で読みの失敗は0、配るはずだったcardは5枚でした。17時57分に、設定の値1つで本運転にしました。戻す時も、同じ値を記録だけの運転に戻すだけです。

本運転の最初の24時間を、schedulerの記録から数えました。

| 優先度 | 配った枚数 | 待ちの中央値 | 最長の待ち(上限) |
| --- | --- | --- | --- |
| p0 | 5枚 | 9.2秒 | 3分未満 |
| p1 | 42枚 | 9.7秒 | 49分 |
| p2 | 253枚 | 11.5秒 | 71分 |
| p3 | 236枚 | 11.6秒 | 86分 |

- 24時間で536枚を配りました。負荷は日によって違うので、10月10日の289枚とは直に比べられません。
- 長い待ちは、どれも利用枠が尽きて空きが0の時間帯に起きました。その時間帯で一番長かったのは、15時49分から17時までの71分です。
- 配れるcardがあるのに空きが0だった周期は、全体の32%でした。
- Cの順そのものによる飢えは、この1日には見えませんでした。空きのある周期では、p3も中央値11.6秒で配られています。

schedulerが数える待ちは、保留のタグが付いていた時間も含みます。そこで表の最長は、配れるcardを全部配り切った周期から数え直した上限にしました。

見張りの会話は配る判断を持たず、毎時に次の4つを読みます。

| 詰まりの兆し | 読む物 |
| --- | --- |
| 配れるcardがあるのに配られない | schedulerの周期ごとの記録 |
| 配った会話が動き出さない | 会話の最初の返事の有無 |
| 同じcardが、配られては戻るのを繰り返す | cardの列の移り |
| 空きが0のまま続く | 周期ごとの空きの数 |

兆しを見つけたら、見張りの会話はcardに書き、全体を調整する会話へ知らせます。

## うまくいかなかったこと

- 配り直したcardに、前と同じ受付の鍵を使っていました。受付は同じ鍵の依頼を1つにまとめるので、新しい依頼が届きませんでした。
- 配った回数を、プロセスのメモリにだけ持っていました。最初の24時間にプロセスは16回起動し、そのたびに回数が0に戻りました。今は、配った記録を保存先に書いています。
- 模擬の利用枠は仮定の値です。本番の利用枠の動きとは、まだ突き合わせていません。
- 走っている会話を止めて急ぎに譲る形は、ありません。止めると、途中の仕事を失うからです。

## まとめ

- 配る判断をAIの会話に持たせると、止まった訳が見えず、別の配り方とも比べられません。
- 配る順は、配れるcardと空きから選ぶ純粋な関数にします。
- 読む・頼む・待つは依頼にし、本番・記録だけの運転・模擬でハンドラの組だけを替えます。
- 配り方は、記録した1日の負荷で比べてから選びます。設定の値1つで、いつでも替えられます。
- AIの会話は、詰まりの兆しを読む見張りに回します。

[doeffとは？：メイン記事へ戻る](https://zenn.dev/proboscis/articles/doeff-introduction)

## 参考

この記事のコードは、2026年10月18日に、PyPIのdoeff 0.7.0で確かめました。Pythonは3.12と3.14で試しました。GitHubにあるdoeffのmain(commit `c0dfc3f`)でも、同じく動きました。`doeff`を入れると、`doeff-core-effects`も入ります。

完全な例を`card_dispatch.py`として保存し、次のコマンドで実行します。

```bash
uv run --no-project --with "doeff==0.7.0" python card_dispatch.py  # PyPIのdoeffを入れて、完全な例を実行する。
```

- [doeff本体(この記事で確かめた版)](https://github.com/proboscis/doeff/tree/c0dfc3f242b20d5dce25324cbc03ead7f532726d)
- [この記事のコードをまとめた完全な例](https://github.com/proboscis/doeff/blob/main/publications/zenn-use-cases-v0/examples/card_dispatch.py)
- [Slurmのmultifactor priority](https://slurm.schedmd.com/priority_multifactor.html)
- [KubernetesのPod Priority and Preemption](https://kubernetes.io/docs/concepts/scheduling-eviction/pod-priority-preemption/)
- [LinuxのCFS](https://docs.kernel.org/scheduler/sched-design-CFS.html)
- [tc-htb(8)](https://man7.org/linux/man-pages/man8/tc-htb.8.html)
- [Weighted fair queueing(Wikipedia・元の論文はDemersほか1989)](https://en.wikipedia.org/wiki/Weighted_fair_queueing)
- 関連する回:テストで20秒待つのをやめる(公開予定)
- 前の回:1人でClaude Codeを最大50並べたら、詰まるのは人だった(公開予定)
- 次の回:CLAUDE.mdに決まり851本を書き足すのをやめた(公開予定)

数字は、非公開のリポジトリにある作業ボードの記録・比べた報告・schedulerの記録から数えました。読者は、数えた元を確かめられません。
