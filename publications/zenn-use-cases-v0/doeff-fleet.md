---
title: "1人でClaude Codeを最大50並べたら、詰まるのは人だった"
emoji: "🛰️"
type: "tech"
topics: ["python", "doeff", "claudecode", "ai", "開発手法"]
published: false
---

筆者は、Claude Codeの会話を最大50並べて、1人で開発しています。2026年9月12日からの35日で、3つのリポジトリのmainへ14,707個のcommitが入りました。並べるほど詰まったのは、AIではなく人の注意でした。

AIのコーディングエージェントを1つ動かすのは、もう難しくありません。難しいのは、何十も並べた時です。

- どの会話が何をしているか、人が追えなくなります。
- 終わった仕事を確かめてmainへ入れる番が、人の前に溜まります。
- ある会話が別の会話の結果を待つと、その待ちを人が覚えておくことになります。

この記事は、この詰まりに向けて作った仕組みの記録です。作っているのはPythonのライブラリdoeffと、会話を並べる仕組みそのもので、配る判断もdoeffで書きました。連載が初めての方は[1本目の入門](doeff-main.md)から、コードは[GitHub](https://github.com/proboscis/doeff)から読めます。

## まず短いコード — 配る判断をProgramにする

仕組みの中心は、「次にどの仕事を、新しい会話へ配るか」の判断です。仕事は、作業ボード(かんばん)のcardに1件ずつ書いておきます。人が毎回配る先を決めると、ここが詰まります。そこで、配る判断をdoeffで書き、配り役(ディスパッチャ)と呼ぶ部品にしました。

doeffは、処理の中の「読む」「待つ」といった操作を、外側の部品で差し替えられるライブラリです。次のコードは、配る判断を縮めて書いた物です。

```python
from dataclasses import dataclass  # cardと依頼を、書き換えないデータ型で表す。

from doeff import Effect, Pass, Resume, do, handler, run, with_handlers  # 依頼・再開・委譲と実行を使う。
from doeff_core_effects import Ask  # 設定の値を、キーで依頼する。


@dataclass(frozen=True)  # 書き換えないデータ型にする。
class Card:  # 作業ボードのcard 1枚。
    key: str  # cardの鍵。
    column: str  # 今の列(todo・doing・done)。
    needs: tuple[str, ...] = ()  # 先に終わるべきcardの鍵。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class ReadBoard(Effect):  # 「作業ボードを読みたい」という依頼。
    pass  # 引数は持たない。


@do  # 呼ぶと、配る判断の手順(Program)ができる。
def pick_cards():  # 新しい会話へ配るcardを選ぶ。
    ceiling = yield Ask("ceiling")  # 同時に走らせる会話の上限を読む。
    cards = yield ReadBoard()  # cardの一覧を読む。
    done = {card.key for card in cards if card.column == "done"}  # 終わったcardの鍵。
    running = sum(card.column == "doing" for card in cards)  # 走っている会話の数(ここではdoingの枚数)。
    ready = [card.key for card in cards if card.column == "todo" and set(card.needs) <= done]  # 配れるtodo。
    return ready[: max(0, ceiling - running)]  # 上限までの空きの数だけ選ぶ。


@handler  # Programに取り付けられるハンドラにする。
@do  # ハンドラもyieldで、再開や委譲を指示する。
def fixed_board(effect, k):  # テスト用に、決めた盤面を答える。
    if isinstance(effect, Ask) and effect.key == "ceiling":  # 上限の依頼には、
        return (yield Resume(k, 3))  # 3を返す。
    if isinstance(effect, ReadBoard):  # 盤面の依頼には、
        return (yield Resume(k, [Card("A", "done"), Card("B", "todo", ("A",)), Card("C", "todo", ("D",)),  # 6枚。
                                 Card("D", "doing"), Card("E", "todo"), Card("F", "todo")]))  # Dだけが作業中。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


print(run(with_handlers([fixed_board], pick_cards())))  # ['B', 'E']と表示される。
```

`pick_cards`は、上限と作業ボードを外側へ依頼するだけです。テストでは`fixed_board`が、決めた盤面を答えます。

- Cは、先に終わるべきDが作業中なので、まだ配りません。
- 上限の3から、作業中の1つを引いた2枚を選びます。
- 選ぶのは、BとEです。Fは空きが無いので次の回に回ります。

コードの語を、先に整理します。

- `@do`を付けた関数は、呼ぶと実行の手順(`Program`)を返します。
- `yield Ask(...)`や`yield ReadBoard()`は、外側への依頼です。この依頼をエフェクトと呼びます。
- 依頼に答える部品をハンドラと呼びます。
- ハンドラは、届いた依頼`effect`と、止まった処理の続き`k`を受け取ります。
- `Resume(k, 値)`で値を渡して続きを動かし、担当外の依頼は`Pass(effect, k)`で外側へ渡します。

本物の配り役は、Pythonの上で動くLispのHyで、doeffを使って書いています。AIの利用枠の空きや、cardの優先度も見ます。それでも、骨はこの形です。

## 全体の図 — 人と会話の間に、3つの仕組みを置く

![1人と最大50の会話の間に、3つの仕組みを置く](/images/zenn-use-cases-v0/generated/fleet-concept.png)
*人は目標とcardを書き、判断が要る時だけ答えます。配り役が上限50まで会話を起こし、会話は自分の変更をマージキューへ載せます。*

会話を並べる仕組みは、筆者が自分のために作ったagoraです。リポジトリは公開していません。画面から会話を開き、仕事を頼みます。会話1つは、Claude Codeを画面なしで動かす1つのセッションです。

人と会話の間には、次の3つを置きました。

| 仕組み | 受け持つこと |
| --- | --- |
| 作業ボード | 仕事をcard 1枚に書く(本文・受け入れの条件・先に終わるべきcard) |
| 配り役 | 配れるcardを選び、新しい会話を起こして頼む(同時に50まで) |
| マージキュー | 会話が出した変更を、1件ずつmainの先端へ積み直して入れる |

会話はcardを受け取ると、自分の作業用のブランチを切ります。変更をcommitしてpushし、マージキューに載せます。そして頼んだ側へ報告を返し、cardを終わりの列へ動かします。

人が手を動かすのは、目標を書く時と、判断を問われた時だけです。会話は、人の判断が要る事を問いの書面にして出します。

## 人の注意を詰まらせない、4つの決まり

### 1. 仕事はcardに書き、受け入れの条件を付ける

cardには、「人に見える結果」と受け入れの条件を書きます。条件の無いcardは、配り役が配りません。

条件が無いと、会話はどこで手を止めればよいか分かりません。報告を読む側も、終わったかを判じられません。

1枚の大きさは、2時間ほどの仕事を目安にしています。大きな目標は、合格条件を持つマイルストーンに分けます。条件を満たすと、担当の会話が次のマイルストーンとcardを作ります。

### 2. 配る判断は、プログラムが持つ

冒頭のコードのように、配れるcardは次の条件で決まります。

- 列がtodoで、保留のタグが付いていません。
- 先に終わるべきcardが、全部終わっています。
- 空きがあります。空きは、上限から走っている会話の数を引いた数です。

実際の空きは、AIの利用枠の空きや、動かす機械の空きとの小さい方です。走っている数は、cardの枚数ではなく会話の数で数えます。1つの会話が、作業中のcardを何枚も持つことがあるからです。

上限の50は、筆者が「同時のsessionは50くらいは回せる」と見積もった値です。設定の既定値で、後から変えられます。

### 3. mainへのゲートは、衝突の印だけを見る

マージキューは、変更をmainの先端へ積み直してから、次の1行だけを確かめます。

```bash
git grep -n -E '^(<<<<<<< |>>>>>>> )' -- . && exit 1 || test $? -eq 1  # 衝突の印が無ければ0、在れば1で終わる。
```

行頭に衝突の印が無ければ、mainへ入れます。テストも静的な検査も、ここでは走らせません。

最初は、ゲートでテストの一部を走らせていました。2026年9月26日には、設計の記録の注の1語だけを変えた変更で、ゲートに517秒かかりました。並べた会話の数だけ、この待ちが積み重なります。

そこでゲートを衝突の確かめだけにし、全体のテストはマージとは別に1日1回走らせます。緑だった版だけを、安定版のブランチへ進めます。会話は、自分が変えた所にかかるテストを手元で走らせてから、列に載せます。

### 4. 会話は、何を待っているかを毎回書く

会話は、1回の返事(ターン)の終わりに、次の2行を書きます。

```text
status: done — 配る判断の模擬を足し、マージキューに載せた
WAIT: work — 頼んだ会話の次の依頼
```

1行目は状態で、4語から選びます。decideは判断を待つ、alertは知らせがある、doneは進んだ、blockedは進めない、です。

2行目は、何を待っているかです。workなら自分の仕事の続きを、user:decideなら人の判断を待っています。画面はこの2行を読み、会話の一覧に印を付けます。

人は、自分の判断を待つ会話だけを開けば足ります。2行が欠けた返事は、Claude Codeのhookが一度だけ止めて書き方を教えます。hookは、決めた時に決めたプログラムを走らせる仕組みです。

## 35日の数字

![35日の、日ごとのcommitの数](/images/zenn-use-cases-v0/generated/fleet-commits.png)
*3つのリポジトリのmainへ入ったcommitを、日ごとに数えました。範囲は2026年9月12日〜10月16日(日本時間)の35日です。*

| 項目 | 数 | 測った日と範囲 |
| --- | --- | --- |
| mainに入ったcommit | 14,707個 | 2026年9月12日〜10月16日の35日・3つのリポジトリ |
| そのうちAIとの共著の行があるcommit | 14,332個(97.5%) | 同じ範囲 |
| 1日で最も多かった日 | 10月12日の1,309個 | 同じ範囲 |
| 作業ボードのcard | 3,473枚(終わった物は3,058枚) | 2026年10月17日2時に読んだ全体 |
| cardを持った会話 | 1,672個 | 同じ時点の、各cardの持ち主 |
| mainへ入った変更 | 381件(入らなかった物は28件) | 2026年10月17日2時までの24時間・全部のリポジトリ |

commitの内訳は、配り役などの裏の仕組みが8,713個、画面が3,148個、doeffが2,846個です。共著の行は、commitの末尾の`Co-Authored-By:`の行で数えました。

数が多いことは、良い変更が多いことを意味しません。小さい変更を、小さいまま入れる流れの結果です。同時に走った会話の数の最大は、測っていないので分かりません。2026年10月17日2時に読んだ時、作業中のcardは33枚でした。

## doeffが効いた所 — 本番と模擬で、同じProgramを動かす

配り役を変えるたびに、本番で会話を起こして試すことはできません。間違えると、同じcardが2つの会話へ配られます。

配り役はdoeffのProgramなので、外側のハンドラを替えれば、同じコードを手元で動かせます。

| 依頼 | 本番のハンドラ | 模擬のハンドラ |
| --- | --- | --- |
| 作業ボードの読み書き | 記録のサービスへHTTPで読み書きする | メモリ上のボードを読み書きする |
| イベントの通知 | Redisで受け取る | メモリで受け取る |
| 今の時刻と待ち | 実際の時計で待つ | 仮想の時計を進める |
| 上限などの設定 | 宣言した設定を読む | シナリオで決めた値で上書きする |

冒頭のコードの続きとして、`pick_cards`を仮想の時間の中で回してみます。まず、配り役の周期と会話の仕事を書きます。

```python
from doeff_core_effects.scheduler import Gather, Spawn, scheduled  # 会話を別のタスクとして始め、終わりを待つ。
from doeff_time import Delay, GetTime, sim_time_handler  # 待ちと時刻の依頼と、仮想の時計。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class Move(Effect):  # 「cardを別の列へ動かしたい」という依頼。
    key: str  # 動かすcardの鍵。
    column: str  # 動かす先の列。


@do  # 会話1つの仕事を、待ちと列の移動で表す。
def conversation(key, minutes):  # 仕事にかかる分数を受け取る。
    yield Delay(minutes * 60)  # 仕事の間、待つ。
    yield Move(key, "done")  # 仕事を終えたcardをdoneへ動かす。


@do  # 配り役の周期を、配るcardが無くなるまで回す。
def dispatcher(work):  # cardの鍵と、仕事の分数の表を受け取る。
    start, log, tasks = (yield GetTime()), (), ()  # 開始の時刻・配った記録・会話のタスク。
    while len(log) < len(work):  # まだ配っていないcardがある間、回す。
        for key in (yield pick_cards()):  # 冒頭のpick_cardsを、そのまま使う。
            minute = int(((yield GetTime()) - start).total_seconds()) // 60  # 開始から何分目かを読む。
            yield Move(key, "doing")  # 配ったcardをdoingへ動かす。
            task = yield Spawn(conversation(key, work[key]))  # 新しい会話に仕事を始めさせる。
            log, tasks = (*log, (minute, key)), (*tasks, task)  # 配った記録とタスクを足した組を作る。
        yield Delay(60)  # 1分ごとに盤面を読み直す。
    yield Gather(*tasks)  # 全部の会話の仕事が終わるまで待つ。
    end = yield GetTime()  # 全部が終わった時刻を読む。
    return list(log), int((end - start).total_seconds()) // 60  # 配った記録と、かかった分数を返す。
```

- `Spawn`は、処理を別のタスクとして始めます。会話1つを、タスク1つで表しました。
- `Delay`は「待ちたい」、`GetTime`は「今の時刻を知りたい」という依頼です。
- `Move`は、cardの列を動かす依頼です。
- 本物の配り役は、1分ごとには起きません。ボードが動いたというイベントか、時計の合図で起きます。

次に、メモリ上のボードで答えるハンドラを付け、仮想の時計の上で動かします。

```python
from dataclasses import replace  # 列だけを替えたcardを作る。

cards = [Card("A", "done"), Card("B", "todo", ("A",)), Card("C", "todo", ("D",)),  # 冒頭と同じ鍵のcard。
         Card("D", "todo"), Card("E", "todo"), Card("F", "todo")]  # 今度はDもtodoから始める。
board = {card.key: card for card in cards}  # メモリ上のボード(鍵からcardを引く)。
peak = [0]  # 同時に走った会話の数の最大。


@handler  # Programに取り付けられるハンドラにする。
@do  # ハンドラもyieldで、再開や委譲を指示する。
def memory_board(effect, k):  # 届いた依頼に、メモリの盤面で答える。
    if isinstance(effect, Ask) and effect.key == "ceiling":  # 上限の依頼には、
        return (yield Resume(k, 2))  # 2を返す。
    if isinstance(effect, ReadBoard):  # 盤面の依頼には、
        return (yield Resume(k, list(board.values())))  # 今の盤面を返す。
    if isinstance(effect, Move):  # 列の移動の依頼には、
        board[effect.key] = replace(board[effect.key], column=effect.column)  # 列を書き換える。
        peak[0] = max(peak[0], sum(card.column == "doing" for card in board.values()))  # 最大を更新する。
        return (yield Resume(k, None))  # 続きを再開する。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


work = {"B": 30, "D": 40, "E": 60, "C": 20, "F": 10}  # cardごとの仕事の分数。
program = sim_time_handler()(with_handlers([memory_board], dispatcher(work)))  # 仮想の時計を外側に付ける。
print(run(scheduled(program)), peak[0])  # ([(0, 'B'), (0, 'D'), (30, 'E'), (40, 'C'), (60, 'F')], 90) 2と表示される。
```

表示は、配った記録・かかった分数・同時に走った会話の最大の3つです。

![同じpick_cardsを、本番と模擬のハンドラで動かす](/images/zenn-use-cases-v0/generated/fleet-flow.png)
*模擬では、メモリ上のボードと仮想の時計が答えます。90分の配りの流れが、待たずに終わります。*

- 0分に、BとDを配ります。Cは、Dが終わるまで配りません。
- 30分にBが終わり、空いた枠にEを配ります。
- 40分にDが終わり、Cがやっと配れるようになります。
- 同時に走った会話は、最大で2つです。上限の2を越えていません。

2026年10月17日に走らせると、90分の流れが約1秒で終わりました。この秒数は、ライブラリを読み込む時間を含みます。

本物の模擬も、同じ考えで作りました。本番の組み立てを書き換えず、記録・イベント・時計のハンドラだけを替えます。そして、1つのプロセスで決まった順に動かし、次の約束を確かめます。

- 配るのは、配れる条件を満たしたcardだけです。
- 1度に配る数は、空きの数までです。

約束の確かめは、配り役のコードを読み込まずに、設計から別に書きました。わざと壊したハンドラを差し、確かめが赤になることも見ます。時間の差し替えの詳しい話は、[20秒待つ処理を、20秒待たずにテストする](doeff-time.md)にあります。

## うまくいかなかったこと

並べると、人が1つずつ見ていた時には出なかった困りごとが出ます。

- 依存を後から結んでも、もう配ったcardは止まりません。この記事のcard自身が、依存を結ぶ前に配られました。今は、依存をcardを作る時に渡す決まりです。
- 会話が切った作業用のブランチの木(git worktree)が溜まります。2026年10月17日に読むと、1日を越えて使われていない木が550本ありました。
- 知らせ1通が、受け手の会話の返事を1回起こします。2026年10月9日には、人が見る会話へ作業の知らせが26通届きました。今は、進みはcardに書き、人には答えが要る物だけを送ります。

決まりを足すほど、会話が読み落とす決まりも増えます。要る時だけ決まりを見せるhookの話は、連載の8本目で書きます。題は「[CLAUDE.mdに決まり851本を書かず、hookで要る時だけ見せる](doeff-hooks.md)」です。

## まとめ

- AIの会話を並べるほど、人の注意が詰まりどころになります。
- 仕事はcardと受け入れの条件に書き、配る判断はプログラムに任せます。
- mainへのゲートは衝突の確かめだけにし、全体のテストはマージとは別に回します。
- 会話は、何を待っているかを毎回書きます。人は、判断を待つ会話だけを開きます。
- 配り役をdoeffのProgramにしたので、本番と模擬でハンドラだけを替えて試せます。

AIの起動と結果待ちを処理として書く話は、[AIエージェントの起動と結果待ち](doeff-agents.md)にあります。

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考

この記事のコードは、2026年10月17日に、PyPIのdoeff 0.6.0で確かめました。Pythonは3.12と3.14で試しました。GitHubにあるdoeffのmain(commit `9adc4f8`)でも、同じく動きました。`doeff`・`doeff-core-effects`・`doeff-time`の3つのパッケージが要ります。

doeff-timeはPyPIに無いので、GitHubから入れます。`--no-sources`を付けないと、doeff本体もGitHubから入り、Rustのビルドが要ります。完全な例を`fleet_dispatch.py`として保存し、次のコマンドで実行します。

```bash
uv run --no-sources --with "doeff==0.6.0" --with "doeff-time @ git+https://github.com/proboscis/doeff@9adc4f8#subdirectory=packages/doeff-time" python fleet_dispatch.py  # doeff本体はPyPI、doeff-timeだけGitHubから入れて、完全な例を実行する。
```

- [doeff本体(この記事で確かめた版)](https://github.com/proboscis/doeff/tree/9adc4f8e657a552f5253d8a7b422363789c2608c)
- [実時間・仮想時間のハンドラ](https://github.com/proboscis/doeff/tree/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-time)
- [この記事のコードをまとめた完全な例](examples/fleet_dispatch.py)
- 前の回:[DIコンテナなしで、依存を引数でバケツリレーしない](doeff-di.md)
- 次の回:[外部APIのテストを、通信せずに回す](doeff-replay.md)

数字は、3つのリポジトリのmainの`git log`と、作業ボードの一覧から数えました。agoraのリポジトリは公開していないため、数えた元は読者が確かめられません。
