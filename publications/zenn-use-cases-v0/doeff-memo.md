---
title: "L1・L2・L3のメモ化も、ハンドラを重ねて作れる"
emoji: "🗄️"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

手元に結果があれば使う。なければ別の保存先を探し、見つかった結果を手元にも置く。こうした多段のメモ化を、doeffではハンドラの合成で書けます。

処理本体は「このキーの値が欲しい」と依頼するだけです。

```python
from doeff import do  # 関数を、依頼を含むProgramとして組み立てる。
from doeff_core_effects.memo_effects import MemoGet  # 保存先に依存しない読み出し操作を使う。

@do  # 呼び出した段階では保存先へアクセスしない。
def read_answer():  # L1・L2・L3の順序を処理本体へ持ち込まない。
    return (yield MemoGet("answer"))  # 見つかった値を受け取り、そのまま返す。
```

どこを探すかは、このProgramを包む`memo_handler`で決めます。

![保存先を重ね、同じMemo操作を渡す](/images/zenn-use-cases-v0/generated/memo-concept.png)

処理に近いL1からL2、L3へ問い合わせます。見つかった値は、戻る途中で手前の空の層へ保存されます。

## L1・L2・L3は、組み合わせて作る

L1・L2・L3は保存階層の呼び名です。固定の3層システムが組み込まれているわけではありません。以下では、探索順を確かめるために3層とも`InMemoryStorage`を使います。

まず共通のimportと、**検証用**の実行関数を用意します。この記事の後続のコードは、この定義を共有して順番に実行できます。アプリケーションで合成する関数は`@do`で書き、`run`は実行環境の境界に置きます。

```python
from pathlib import Path  # 後半のSQLite例で、保存先を組み立てる。
from tempfile import TemporaryDirectory  # 検証用のSQLiteだけを後で片付ける。

import pytest  # 保存にないキーへの読み出しが失敗することを検査する。
from doeff import run  # この例では検証関数の境界だけでProgramを実行する。
from doeff_core_effects.handlers import await_handler, slog_discard_handler  # 保存先の待機とログを処理する。
from doeff_core_effects.memo_effects import MemoDelete, MemoExists, MemoPut  # 削除・存在確認・保存を依頼する。
from doeff_core_effects.memo_handlers import memo_handler  # 保存先をMemo操作の担当にする。
from doeff_core_effects.memo_policy import Lifecycle, MemoPolicy, RecomputeCost  # コストと保持方針を表す。
from doeff_core_effects.scheduler import scheduled  # 保存先が出すAwaitを進める。
from doeff_core_effects.storage import InMemoryStorage, SQLiteStorage  # メモリとファイルの保存先を選べる。

def execute_for_test(program):  # 記事の検証専用の実行境界を用意する。
    wrapped = slog_discard_handler(program)  # Memoの診断ログは画面に表示しない。
    wrapped = await_handler()(wrapped)  # 保存先が出す非同期待機を処理する。
    return run(scheduled(wrapped))  # 最後まで進めて、Programの戻り値を検証へ渡す。
```

Programを組み立てる順番は次のとおりです。最後に付けた層が一番外側になります。この段階では値を読まず、ハンドラを付けたProgramを作ります。

```python
l1, l2, l3 = InMemoryStorage(), InMemoryStorage(), InMemoryStorage()  # 保存先を3つに分ける。
program = memo_handler(l1, name="L1")(read_answer())  # 本体に最も近い層
program = memo_handler(l2, name="L2")(program)  # L1の次の問い合わせ先
program = memo_handler(l3, name="L3")(program)  # 最後の問い合わせ先
```

次に、L3だけに42を入れます。存在確認・取得・保存・削除を順番に実行し、各層を直接観測します。保存先への直接操作は、この検証で初期状態を作り、結果を調べるためのものです。

```python
@do  # 本文からもyieldして使える、階層動作の検証Programにする。
def inspect_layers(l1, l2, l3):  # 3層は検証の観測対象として明示的に受け取る。
    yield l3.put("answer", 42)  # テストの準備として、一番外側だけに42を置く。
    assert (yield MemoExists("answer")) is True  # L1とL2を通過し、L3で存在を確認する。
    assert list(l1.keys()) == list(l2.keys()) == []  # 存在確認だけでは値を補充しない。
    value = yield MemoGet("answer")  # L3の42が、L2・L1へ戻りながら保存される。
    copied = [(yield l1.get("answer")), (yield l2.get("answer"))]  # 補充後の2層を直接観測する。
    assert value == 42  # L3に用意した値が、処理本体まで戻る。
    assert copied == [42, 42]  # 戻り値だけでなく、手前の2層への補充も確認する。
    yield l3.put("answer", 99)  # 外側だけを書き換え、どの層の値を読むかを区別する。
    assert (yield MemoGet("answer")) == 42  # L1に値があればそこで確定し、L3の99は読まない。
    assert (yield MemoPut("answer", 100)) is None  # 明示的な保存は対象全層へ伝播し、Noneを返す。
    assert all(dict(layer.items()) == {"answer": 100} for layer in (l1, l2, l3))  # 3層が100になる。
    assert (yield MemoDelete("answer")) is None  # 削除も対象全層へ伝播し、Noneを返す。
    assert all(list(layer.keys()) == [] for layer in (l1, l2, l3))  # 古い値を残さず全層から消す。
    assert (yield MemoExists("answer")) is False  # 全層の不在を、Falseとして受け取る。
    return value, copied  # 最初の取得と補充の結果として、(42, [42, 42])を返す。

l1, l2, l3 = InMemoryStorage(), InMemoryStorage(), InMemoryStorage()  # 空の3層を個別に作る。
program = memo_handler(l1, name="L1")(inspect_layers(l1, l2, l3))  # 本体のすぐ外側にL1を付ける。
program = memo_handler(l2, name="L2")(program)  # L1の次の問い合わせ先をL2にする。
program = memo_handler(l3, name="L3")(program)  # 最後の問い合わせ先をL3にする。
assert execute_for_test(program) == (42, [42, 42])  # L3からの取得・補充と後続操作が成功する。
```

この例で確認した動作は、次のとおりです。

| 操作 | 見つかった場合・終端の動作 | 手前の層への補充 |
| --- | --- | --- |
| `MemoExists` | 最初に見つかった層で`True`、全層にないと`False` | しない |
| `MemoGet` | 最初に見つかった層の値、全層にないと`KeyError` | 外側で取得した値を保存する |
| `MemoPut` | 対象の全層へ保存し、`None`を返す | 書き込みを外側へ伝える |
| `MemoDelete` | 対象の全層から削除し、`None`を返す | 削除を外側へ伝える |

`MemoGet`は、値がなければ勝手に計算を始める操作ではありません。全層での不在は、次のように検査できます。

```python
@do  # 存在しない値への直接読み出しを、検証からyieldまたはrunできるようにする。
def read_missing():  # MemoGetは自動計算の依頼ではないことを確認する。
    return (yield MemoGet("missing"))  # 外側にも値がなければKeyErrorになり、このreturnは完了しない。

with pytest.raises(KeyError, match="missing"):  # 保存済みの値がない場合の失敗を確認する。
    execute_for_test(memo_handler(InMemoryStorage())(read_missing()))  # 空の最後の層で不在が確定する。
```

## 再計算コストによって、通す層を選ぶ

`RecomputeCost`は、再計算が安価か、高価か、再現困難かを表します。ハンドラの`cost`は、そのうち何を扱うかを選ぶフィルタです。

| ハンドラの指定 | 受け取る操作のコスト |
| --- | --- |
| `cost=None`（既定） | すべて |
| `cost=RecomputeCost.CHEAP` | `CHEAP` |
| `cost=RecomputeCost.EXPENSIVE` | `EXPENSIVE`と`IRREPRODUCIBLE` |
| `cost=RecomputeCost.IRREPRODUCIBLE` | `IRREPRODUCIBLE`だけ |

L1はすべて、次の層は安価な結果、一番外側は高価・再現困難な結果を扱う構成にします。保存先は引き続きすべてメモリです。`EXPENSIVE`を指定するだけで永続保存になるわけではありません。

```python
@do  # コストごとの操作も、通常の@do処理として合成する。
def store_results():  # キーごとに一貫した再計算コストを指定する。
    yield MemoPut("preview", "短い結果", recompute_cost=RecomputeCost.CHEAP)  # L1と安価層へ置く。
    yield MemoPut("analysis", "高価な結果", recompute_cost=RecomputeCost.EXPENSIVE)  # L1と高価層へ置く。
    yield MemoPut("snapshot", "再取得不能", recompute_cost=RecomputeCost.IRREPRODUCIBLE)  # 高価層も扱う。
    return (yield MemoGet("analysis", recompute_cost=RecomputeCost.EXPENSIVE))  # 高価な結果を受け取る。

l1, temporary, expensive = InMemoryStorage(), InMemoryStorage(), InMemoryStorage()  # 空の保存先を分ける。
program = memo_handler(l1)(store_results())  # L1は全コストを扱う。
program = memo_handler(temporary, cost=RecomputeCost.CHEAP)(program)  # 安価な結果だけを保存する。
program = memo_handler(expensive, cost=RecomputeCost.EXPENSIVE)(program)  # 高価・再現困難を保存する。
assert execute_for_test(program) == "高価な結果"  # 保存したanalysisを取得できる。
assert set(l1.keys()) == {"preview", "analysis", "snapshot"}  # 全コストの3件がL1に入る。
assert set(temporary.keys()) == {"preview"}  # 安価層は高価・再現困難な結果を素通りさせる。
assert set(expensive.keys()) == {"analysis", "snapshot"}  # 高価層は再現困難な結果も扱う。
```

### 削除にも、保存時と同じコストを渡す

削除のコストが違うと、保存した層に届かないことがあります。以下は、**誤った削除をあえて実行して、正しい削除と比べる検証**です。

```python
@do  # 誤った削除コストの影響を、再現できる検証Programとして示す。
def inspect_deletion(l1, expensive):  # 削除先と残存する値を直接観測する。
    yield MemoDelete("analysis")  # 既定はCHEAPなので、L1から消えても高価層には届かない。
    assert "analysis" not in list(l1.keys())  # L1からは消えたため、一見すると削除できている。
    assert "analysis" in list(expensive.keys())  # 高価層に古い値が残ることを確認する。
    restored = yield MemoGet("analysis", recompute_cost=RecomputeCost.EXPENSIVE)  # 残った値が戻る。
    assert restored == "高価な結果"  # 高価層に残っていた値が返される。
    assert "analysis" in list(l1.keys())  # 読み出しでL1にも再補充される。
    yield MemoDelete("analysis", recompute_cost=RecomputeCost.EXPENSIVE)  # 保存時と同じコストで消す。
    assert "analysis" not in list(l1.keys())  # 今度もL1から消える。
    assert "analysis" not in list(expensive.keys())  # 同じコストを扱う高価層からも消える。
    return (yield MemoExists("analysis", recompute_cost=RecomputeCost.EXPENSIVE))  # 全対象層でFalse。

program = memo_handler(l1)(inspect_deletion(l1, expensive))  # 前の例で保存した3層を使い続ける。
program = memo_handler(temporary, cost=RecomputeCost.CHEAP)(program)  # 安価層の経路を維持する。
program = memo_handler(expensive, cost=RecomputeCost.EXPENSIVE)(program)  # 高価層の経路を維持する。
assert execute_for_test(program) is False  # 正しいコストで削除すると、全対象層で不在になる。
```

コストはキーの一部にはなりません。たとえば同じ文字列キーで`CHEAP`と`EXPENSIVE`の別の値を保存すると、全コストを扱うL1では区別できません。**キーごとのコストを一貫させる**か、キー自体に区別を含めます。

## 使われるまで、保存先を作らない

現行の`memo_handler`は、保存先そのものに加え、保存先を返すProgramも受け取れます。外側の層に依頼が届かなければ、その層の生成処理も動きません。同じハンドラインスタンスでは、最初に必要になったときだけ生成します。

```python
@do  # 保存先の生成自体を、必要になるまで実行しないProgramにする。
def open_expensive_store(opened):  # openedは検証専用の生成回数記録で、接続設定ではない。
    opened.append("高価層")  # この行へ到達したときだけ、保存先が必要になったと分かる。
    return InMemoryStorage()  # 検証では通信を伴わない保存先を返す。

opened = []  # 保存先が必要になった回数を検証用に記録する。
lazy_layer = memo_handler(open_expensive_store(opened), cost=RecomputeCost.EXPENSIVE)  # まだ生成しない。
cheap_layer = memo_handler(InMemoryStorage(), cost=RecomputeCost.CHEAP)  # 安価な保存だけを受け持つ。
assert opened == []  # ハンドラを取り付ける準備だけでは生成処理が動かない。
execute_for_test(lazy_layer(cheap_layer(MemoPut("preview", 1))))  # 安価な依頼は高価層を通過する。
assert opened == []  # 高価層の保存先は、まだ必要ない。
execute_for_test(lazy_layer(cheap_layer(store_results())))  # 高価な依頼で初めて保存先が必要になる。
assert opened == ["高価層"]  # 複数の操作が届いても、このハンドラ内では一度だけ生成する。
```

## 保存先をSQLiteに替える

メモリ3層の例は、プロセス終了を越えて値を保持しません。ファイルへ残したければ、同じ`memo_handler`に`SQLiteStorage`を渡せます。ここでは、新しい保存先インスタンスと新しい`run`で42を読み直します。

```python
with TemporaryDirectory() as directory:  # この検証が作るファイルだけを後で片付ける。
    database = Path(directory) / "memo.sqlite"  # 保存と読み出しで同じSQLiteファイルを使う。
    first = memo_handler(SQLiteStorage(database))(MemoPut("saved", 42))  # ファイルへ保存する層を付ける。
    assert execute_for_test(first) is None  # 保存完了を待ってから、次の実行へ進む。
    second = memo_handler(SQLiteStorage(database))(MemoGet("saved"))  # 別の保存先インスタンスで開き直す。
    assert execute_for_test(second) == 42  # 前の実行で保存した値を、新しい実行から取得する。
```

この検証は同一プロセス内の再オープンです。プロセスをまたぐ例は[永続実行の記事](doeff-durable.md)にあります。SQLiteは値をpickle形式で保存するため、保存する値はその形式で扱える必要があります。RedisやMinIOを使いたい場合は、対応する保存先アダプタが必要です。この記事で確認した組み込み保存先はメモリとSQLiteです。

## 保持方針は、指定と実行を区別する

`MemoPolicy`にはコスト、保持の意図、TTL、メタデータを載せられます。ただし、**標準の`memo_handler`が使う方針は再計算コストです**。保存先には`put(key, value)`を呼び、TTL・`lifecycle`・`metadata`を渡しません。

したがって、標準の層に`ttl`を指定するだけで期限切れになったり、`PERSISTENT`だけでメモリが永続化されたりはしません。以下では`ttl=0`を指定しても値を読めることを、実際に確認します。保持方針を実行したければ、`MemoPut`の方針を解釈するハンドラを設計します。

```python
@do  # 保持方針と実際の保存動作を同じProgram内で確認する。
def inspect_policy():  # 方針の指定だけで期限処理が動くと誤解しないための例にする。
    policy = MemoPolicy(  # 依頼に添付する方針を明示的に構築する。
        recompute_cost=RecomputeCost.EXPENSIVE,  # 標準memo_handlerが経路選択に使う値。
        lifecycle=Lifecycle.PERSISTENT,  # 永続保持の意図であり、メモリを永続化はしない。
        ttl=0,  # 即時期限切れを意図しても、標準memo_handlerはこの値を処理しない。
        metadata={"format": "article-outline-v1"},  # 形式の版を依頼に添える。
    )
    request = MemoPut("outline", ["準備", "遊び方"], policy=policy)  # 方針付きの保存依頼を作る。
    assert request.policy is policy  # 同じMemoPolicyが依頼に保持されることを確かめる。
    yield request  # 標準memo_handlerはキーと値を保存先へ渡し、TTLなどは渡さない。
    return (yield MemoGet("outline", recompute_cost=RecomputeCost.EXPENSIVE))  # ttl=0でも値が残る。

result = execute_for_test(memo_handler(InMemoryStorage())(inspect_policy()))  # 標準のメモリ層を使う。
assert result == ["準備", "遊び方"]  # 方針の指定だけでは、即時期限切れにはならない。
```

## 操作のメモ化と、HTTPの取得方法を別々に選ぶ

ここまでの`memo_handler`は、すでに存在するMemo操作を保存先へ割り当てる担当でした。普通のエフェクトをメモ化したいときには、`make_memo_rewriter`をさらに重ねます。

たとえばHTTPなら、次の3つを分けます。

1. `make_memo_rewriter(HttpRequest)`が、依頼を保存済みか確認する。
2. `memo_handler`が、確認・読み出し・保存をどこで行うか決める。
3. HTTPハンドラが、保存ミスのときに応答を取得する。

```python
from doeff_core_effects import HttpRequest  # 通信方法と独立したHTTP依頼を使う。
from doeff_core_effects.http_handlers import http_production_handler  # 実際のHTTP取得を担当する。
from doeff_core_effects.memo_handlers import make_memo_rewriter  # HTTP依頼の再利用判定を追加する。

@do  # HTTPの処理本体は、取得方法と保存先のどちらからも独立させる。
def fetch_text(url: str):  # 実行方法を引数のモードやクライアントで切り替えない。
    response = yield HttpRequest("GET", url)  # HTTP担当かMemoの保存済み応答を受け取る。
    response.raise_for_status()  # 失敗したHTTP状態は例外として呼び出し元へ伝える。
    return response.text  # 成功した本文だけを返す。

p_request = make_memo_rewriter(HttpRequest)(fetch_text("https://example.invalid/article"))  # 再利用判定。
p_cached = memo_handler(InMemoryStorage())(p_request)  # Memoの保存先を選ぶ。
p_http = http_production_handler()(p_cached)  # 未保存時のHTTP担当を選ぶ。ここでは実行しない。
```

この構成例はProgramの組み立てまでです。通信は実行していません。テストでは、HTTP担当を固定応答のハンドラへ替えます。保存先をSQLiteへ替える操作とは独立しています。利用側が`client_factory`でHTTPクライアントを作る必要はありません。[記録・再生の記事](doeff-replay.md)では、保存ヒットならHTTPを拒否するハンドラでも成功し、保存ミスなら失敗するところまで検証しています。

`make_memo_rewriter`は保存ヒットなら元の操作を省略し、保存ミスなら外側へ依頼して結果を保存します。キーは既定では依頼内容から作ります。長期に再利用する設計では、入力・モデル・処理の版など、何を同じ依頼とみなすかを決める必要があります。

## 処理の流れ

![L3の42を、L2・L1へ補充して返す](/images/zenn-use-cases-v0/generated/memo-flow.png)

L1とL2に値がないときだけ外側へ問い合わせ、L3の42を取得するとL2、L1の順に保存して処理を再開します。

ハンドラの合成で、探索順や保存先を処理本体から分けられます。一方、複数の保存先にまたがる操作は一つのトランザクションではありません。保存失敗・同時更新・外側だけの変更を含めた整合性は、用途に合わせて設計します。

[完全な検証例](examples/memo_policy.py)には、上記の階層・コスト経路・削除・遅延生成・SQLiteの検証を保存しています。ハンドラが依頼を外側へ渡す仕組みは[ハンドラの合成](doeff-handlers.md)へ。結果の再利用と、途中で止まった処理を再開する設計の関係は[永続実行](doeff-durable.md)へ続きます。

---

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考資料・検証版

開発checkout `d4705914e39740aee98a9f57a4535c463d9479cc` の以下の実装と、掲載コードを確認しています。

- [公開API・コスト判定・操作のメモ化](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/memo_handlers.py)
- [階層への委譲・補充・保存・削除・遅延生成](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/_memo_handlers_impl.hy)
- [MemoPolicyの定義](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/memo_policy.py)
- [メモリ・SQLite保存先の実装](https://github.com/proboscis/doeff/tree/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/storage)
