---
title: "同じパイプラインを、逐次にも並行にも — doeff-traverse"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

データを一件ずつ処理していたコードを並行にしたい。失敗した一件を履歴に残し、成功した分だけ集計したい。doeff-traverseでは、**各要素に何をするかを書き、順番・並行数・失敗の扱いをハンドラで選びます。**

```python
from doeff import do  # 関数呼び出しを、実行前のProgramとして組み立てる。
from doeff_time import Delay  # 待機を依頼し、実時間か仮想時間かは外側で決める。
from doeff_traverse import Inspect, Traverse  # 要素への処理適用と、結果・履歴の取り出しを依頼する。

@do  # 呼び出すたびに、要素1件分の新しいProgramを作る。
def process(value: int):  # 入力値と同じ秒数の待機を含む仕事を表す。
    yield Delay(value)  # 1・2・3秒の待機後、この関数の続きを再開する。
    return value * 10  # 待機後の結果は10・20・30になる。

@do  # 一件ずつの処理も集合の処理も、同じyieldで合成する。
def pipeline():  # ここでは実行順や並行数を指定しない。
    collection = yield Traverse(process, [1, 2, 3])  # 3件を処理し、履歴付きCollectionを受け取る。
    items = yield Inspect(collection)  # 入力順のItemResultを得る。この入力では全件成功する。
    return [item.value for item in items]  # この例の結果は[10, 20, 30]になる。
```

`Traverse`には、`process(1)`のような実行済みの呼び出しを渡すのではなく、**入力ごとに新しいProgramを作る`process`関数**を渡します。ハンドラが必要なタイミングでそれを呼びます。`collection`は単なる結果のリストではなく、成功・失敗・スキップの履歴を保持する`Collection`です。

![処理は同じ、実行戦略を差し替える](/images/zenn-use-cases-v0/generated/traverse-concept.png)

`Traverse(process, [1, 2, 3])`という同じ依頼を、逐次か最大3並行かのどちらかで解釈します。どちらも結果は入力順の10・20・30です。

## 逐次か並行かを外側で選ぶ

冒頭の定義に続けて実行できる、確認用コードです。`run`はこの検証の入口で使い、処理本体の関数は`yield`で合成します。

```python
from datetime import datetime, timezone  # 仮想時計の開始日時をUTCで固定する。
from doeff import run  # 組み立てたProgramから、検証用に最終結果を得る。
from doeff_core_effects.scheduler import scheduled  # 並行タスクのSpawn/Gatherを処理する。
from doeff_time import sim_time_handler  # Delayを実際の待ち時間なしに進める。
from doeff_traverse import parallel, sequential  # 実行戦略を選ぶハンドラを使う。

for strategy in (sequential(), parallel(concurrency=3)):  # 逐次と最大3並行を別々に試す。
    program = strategy(pipeline())  # 同じ処理本体へ、選んだ戦略を取り付ける。
    program = sim_time_handler(  # 時刻の解釈も独立したハンドラとして重ねる。
        start_time=datetime(2026, 1, 1, tzinfo=timezone.utc)  # 両方を同じ時刻から開始する。
    )(program)  # 時刻とコレクション操作の両方を解釈できるProgramを得る。
    assert run(scheduled(program)) == [10, 20, 30]  # 方針が違っても、値と入力順は一致する。
```

[完全な検証例](examples/traverse_pipeline.py)では、`GetTime`で開始・終了の時刻も取得しています。仮想時間上の所要時間は、逐次では1＋2＋3＝6秒、3並行では最大の3秒になります。これは実時間で測った性能値ではありません。

また、`parallel`はスケジューラ上で計算を並行に進めます。CPU処理を自動的に別プロセスへ分配するという意味ではありません。

## 不正な文書を隔離して、成功分を並べ替え・集計する

次は、空の文書を`Fail`として報告する例です。処理本体は「この入力は不正だった」と伝えます。ハンドラ側で、失敗をその一件に隔離するか、バッチ全体へ伝えるかを決めます。

```python
from doeff import do, run  # 処理の合成と、最後の検証境界での実行を分ける。
from doeff_core_effects.scheduler import scheduled  # 並行処理のタスクを進める。
from doeff_traverse import Fail, Inspect, Reduce, SortBy, Take, Traverse, Zip  # 各段階の依頼。
from doeff_traverse.handlers import fail_handler, parallel, parallel_fail_fast  # 失敗方針を選ぶ。

@do  # 1件の文書を処理するProgramを作る。
def read_length(text: str):  # 文書の内容を受け取り、正常なら文字数を返す。
    if not text:  # この例では空文字を不正な入力とする。
        return (yield Fail(ValueError("本文がありません"), stage="文字数"))  # 対応をハンドラへ委ねる。
    return len(text)  # 「短文」は2、「もう少し長い文」は7になる。

@do  # Traverseへ渡す関数は、純粋な操作でもProgramを返す。
def label(text: str):  # 元の文書の並びを保ち、表示名を作る。
    return f"文書:{text}"  # 「短文」なら「文書:短文」を返す。

@do  # Reduceへ渡す関数も、累積値を返すProgramを作る。
def add(total: int, length: int):  # これまでの合計と、次の成功した文字数を受け取る。
    return total + length  # 0→2→9の順で合計を更新する。

@do  # 各段階をyieldし、実行戦略を決めずに合成する。
def summarize_documents():  # 合計だけでなく、入力の成否も呼び出し元へ返す。
    texts = ["短文", "", "もう少し長い文"]  # 正常2件と不正1件を用意する。
    lengths = yield Traverse(read_length, texts, label="文字数")  # 2・失敗・7を履歴付きで得る。
    labels = yield Traverse(label, texts, label="表示名")  # 同じ3件へ同じ順で表示名を付ける。
    joined = yield Zip(labels, lengths)  # 対応する位置を結び、空文書の失敗も引き継ぐ。
    ranked = yield SortBy(lambda value: value[1], joined, reverse=True)  # 成功分を文字数の降順へ。
    top = yield Take(1, ranked)  # 成功した先頭1件を取り、失敗の履歴は残す。
    total = yield Reduce(add, 0, lengths)  # 成功した文字数2と7を足して9を得る。
    top_history = yield Inspect(top)  # 最大の文書と、保持された失敗の記録を取り出す。
    length_history = yield Inspect(lengths)  # 元の3件それぞれの成否と履歴を取り出す。
    return total, top_history, length_history  # 集計だけで失敗が隠れないよう履歴も返す。

p_documents = summarize_documents()  # 戦略をまだ持たない、固定入力のProgramを用意する。
```

`Zip`は現在の並びの対応する位置を結びます。この例では、同じ入力順のコレクションを結んでから`SortBy`で並べ替えています。別々に並べ替えた結果を、元の文書IDで自動的に結合する機能ではありません。

`SortBy`のキーだけは通常の純粋な関数です。一方、`Traverse`と`Reduce`へ渡す処理は`@do`関数にします。この呼び出し規約を区別する必要があります。

### 一件の失敗を履歴に残す

```python
program = parallel(concurrency=2)(fail_handler(p_documents))  # Failを例外に変え、要素ごとに隔離。
total, top, history = run(scheduled(program))  # 検証の入口で最大2並行の計算を実行する。
assert total == 9  # Reduceには成功した2文字と7文字だけが渡る。
assert [row.value for row in top if not row.failed] == [("文書:もう少し長い文", 7)]  # 最大の成功値。
assert len(top) == 2  # Take(1)は成功1件に加え、失敗の履歴1件も保持する。
assert sum(row.failed for row in history) == 1  # 3件のうち空文書だけが失敗している。
assert len(history) == 3  # 成功2件と失敗1件が履歴に残り、入力が消えていない。
```

内側の`fail_handler`が`Fail`を例外へ変え、外側の`parallel`が要素ごとの例外を捕まえて`Collection`に記録します。**`parallel`でバッチが完了しても、全要素が成功したとは限りません。** `Inspect`で失敗も確認します。`sequential`も、各要素の失敗を履歴に残して続行する方針です。

また、`Take(1)`は「成功した一件」を選びます。失敗の記録を消さないため、`Inspect(top)`の要素数はこの例では2です。

### 最初の例外をバッチ全体へ伝える

```python
try:  # 先ほどと同じProgramを、失敗を隔離しない方針で確認する。
    program = parallel_fail_fast(concurrency=2)(fail_handler(p_documents))  # 戦略だけを変える。
    run(scheduled(program))  # 空文書のValueErrorが、この検証境界へ届くことを期待する。
except ValueError as error:  # 想定した種類の失敗だけを検証対象にする。
    if str(error) != "本文がありません":  # 別原因の例外を成功扱いしない。
        raise AssertionError("想定外の例外です") from error  # 元の例外を原因として残す。
else:  # 例外が届かなかった場合は、検証そのものを失敗させる。
    raise AssertionError("失敗時には全体が中断されるはずです")  # 方針差が欠けていると知らせる。
```

一件の失敗で全体が無効になる処理や、開発中に不具合をすぐ発見したい場面では、`parallel_fail_fast`を選べます。この検証では、期待した`ValueError`のトレースが表示されます。

## Hyでは取り出し・絞り込み・集計を内包表記で書ける

Hyの`for/do`は、要素ごとのProgramを作って`Traverse`へ渡す形に展開されます。`From`と`When`はその構文であり、別の非同期ループへ処理を移すものではありません。

次は、正の値だけを2倍して合計する例です。

```hy
(require doeff-hy.macros [defk <- for/do]) ; 関数・yieldの束縛・内包表記のマクロを読み込む。
(import doeff [do :as _doeff-do run]) ; 展開後の@do相当の処理と、検証用の実行関数を用意する。
(import doeff-traverse [Traverse :as _doeff_traverse_Traverse ; Fromの展開先になる依頼型。
                       Skip :as _doeff_traverse_Skip ; Whenが偽の要素をスキップする依頼型。
                       Reduce]) ; 成功した要素だけを累積する依頼型。
(import doeff-traverse.handlers [sequential]) ; この検証では要素を順番に処理する。

(defk add [total value] ; 累積値と次の要素を受け取り、合計を返すProgramを作る。
  {:pre [(: total int) (: value int)] :post [(: % int)]} ; 入力2つと結果が整数であると検査する。
  (+ total value)) ; 0→2→8の順で合計を更新する。

(defk eligible-total [values] ; 入力のリストを絞り込み、合計するProgramを作る。
  {:pre [(: values list)] :post [(: % int)]} ; 入力はリスト、最終結果は整数と検査する。
  (<- selected ; 内包表記の実行を依頼し、履歴付きCollectionを受け取る。
    (for/do ; 各要素を処理するProgramをTraverseでまとめる。
      (<- value (From values)) ; 入力1・-1・3を、それぞれvalueへ渡す。
      (When (> value 0)) ; -1はSkipとなり、この要素の残りの計算を進めない。
      (* value 2))) ; 成功した1と3から2と6を返す。
  (<- total (Reduce add 0 selected)) ; スキップした要素を除き、2と6を足す。
  total) ; 計算の呼び出し元へ8を返す。

(assert (= (run ((sequential) (eligible-total [1 -1 3]))) 8)) ; 実行結果が8になると確かめる。
```

`When`で除外した要素も、内部では`failed=True`と`skipped`の履歴で保持され、`Reduce`の集計対象から外れます。失敗とスキップを区別したい場合は、`Inspect`で取得した`history`の`event`を確認します。

[Hyの記事](doeff-hy.md)に、ハンドラや契約も組み合わせたコードがあります。コレクションの反復では要素ごとに新しいProgramを作るため、VMの一つの継続を何度も再開する必要はありません。

## 処理の流れ

![文書ごとの計算と、失敗・集計の方針を分ける](/images/zenn-use-cases-v0/generated/traverse-flow.png)

`parallel`では2・失敗・7を履歴に残し、`Reduce`が成功分を合計して9を返します。`parallel_fail_fast`を選ぶと、同じ空文書の例外が全体へ伝わります。

## 実装・実例を読む

- [実行可能な全文と検証](examples/traverse_pipeline.py)
- [操作の定義](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-traverse/doeff_traverse/effects.py)
- [逐次・並行・失敗時のハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-traverse/doeff_traverse/handlers.py)
- [Hyの内包表記の実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-hy/src/doeff_hy/macros.hy)
- [ハンドラの合成](doeff-handlers.md)
- [Rust VMと継続](doeff-vm.md)

この草稿は上記の開発版を参照しています。掲載したPythonとHyの例は、外部サービスへ接続せずに確認しています。

[メイン記事へ戻る](doeff-main.md)
