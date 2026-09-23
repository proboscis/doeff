---
title: "ハンドラを組み合わせる — doeffの順序・委譲・適用範囲"
emoji: "🧱"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

doeffのハンドラは、処理へ一つだけ登録するものではありません。**計算を包むハンドラを重ねて、複数の操作を扱ったり、外側の実装が返した値を加工したりできます。**

たとえば「価格を読む」計算に、「基準価格を返す」「割引する」を重ねます。

```python
program = handler(base_price)(  # 基準価格100を返すハンドラを外側へ置く
    handler(discount)(ReadPrice())  # 価格の依頼だけを割引で包み、100から80への加工を指定する
)  # ここまでの範囲を包んだ計算を作り、この時点では実行しない
assert run(program) == 80  # 構成した計算を実行し、100の2割引きが80と確認する
```

これは、このあと定義する操作とハンドラを使った抜粋です。`ReadPrice()`は価格を依頼し、内側の`discount`が外側の`base_price`から100を受け取り、80を返します。

![ハンドラは、計算を包んで組み合わせる](/images/zenn-use-cases-v0/generated/handlers-concept.png)

計算の内側からハンドラを探し、ハンドラが発行する操作は、その外側へ依頼できます。

## まずは、異なる操作を組み合わせる

価格と通貨を別の操作にします。以下の例は外部サービスを使いません。[完全な実行例](examples/handler_composition.py)も置いてあります。

```python
from dataclasses import dataclass  # 引数を持たない依頼もデータ型として宣言できるようにする

from doeff import Effect, Pass, Resume, do, handler, run  # 依頼・委譲・再開と、計算の合成・実行のAPIを使う

@dataclass(frozen=True)  # 発行した依頼の属性を書き換えない型にする
class ReadPrice(Effect):  # 整数の価格を要求する専用の操作を定義する
    pass  # この依頼には追加の引数がないため、属性を増やさない

@dataclass(frozen=True)  # 発行した依頼の属性を書き換えない型にする
class ReadCurrency(Effect):  # 通貨コードを要求する専用の操作を定義する
    pass  # この依頼には追加の引数がないため、属性を増やさない

@do  # 依頼を受けて続きへ値を返せる価格ハンドラにする
def base_price(effect, k):  # 価格の依頼に100を返し、呼び出し元を再開する
    if isinstance(effect, ReadPrice):  # 価格の依頼だけを、このハンドラの処理対象にする
        return (yield Resume(k, 100))  # 価格を待つ元の続きへ100を渡し、その実行結果を返す
    return (yield Pass(effect, k))  # 担当外の依頼と元の続きを外側へ渡し、ここでは再開しない

@do  # 依頼を受けて続きへ値を返せる通貨ハンドラにする
def yen(effect, k):  # 通貨の依頼にJPYを返し、呼び出し元を再開する
    if isinstance(effect, ReadCurrency):  # 通貨の依頼だけを、このハンドラの処理対象にする
        return (yield Resume(k, "JPY"))  # 通貨を待つ元の続きへJPYを渡し、その実行結果を返す
    return (yield Pass(effect, k))  # 担当外の依頼と元の続きを外側へ渡し、ここでは再開しない

@do  # 価格と通貨の依頼をyieldで合成する計算にする
def quote():  # 価格と通貨を組み合わせた見積もり文字列を作る
    price = yield ReadPrice()  # 取り付けたハンドラの構成で決まる価格を受け取る
    currency = yield ReadCurrency()  # 外側のyenハンドラからJPYを受け取る
    return f"{currency} {price}"  # 通貨と価格を結合し、JPY 100などの見積もりを返す

program = handler(yen)(handler(base_price)(quote()))  # 見積もりを価格と通貨の受け手で包み、両方の依頼に備える
assert run(program) == "JPY 100"  # 二つの依頼が解決し、基準価格の見積もりになると確認する
```

`handler(base_price)(quote())`は、`quote()`を`base_price`で包む計算を作ります。さらにその外側を`yen`で包みます。**包む段階では計算を実行しません。** ここでは結果の確認のため、最外側で`run()`しています。処理同士の合成は`@do`の中から`yield`で行います。

`ReadPrice()`は内側の`base_price`が受け取ります。一方、`ReadCurrency()`を受け取った`base_price`は担当外なので、`Pass(effect, k)`で外側へ委譲し、`yen`が通貨を返します。

`k`は、その操作の結果を待っている処理の続きです。`Resume(k, 100)`なら、`price = yield ReadPrice()`の`price`に100を渡して再開します。

## 「担当外なので渡す」と「結果を受けて加工する」は別

同じ操作を扱うハンドラも重ねられます。価格を2割引きにするハンドラは、次のように書けます。

```python
@do  # 外側への再依頼と割引後の再開を合成するハンドラにする
def discount(effect, k):  # 外側が返す価格を2割引きして、元の依頼へ返す
    if isinstance(effect, ReadPrice):  # 価格の依頼だけを、このハンドラの処理対象にする
        price = yield effect  # 同じ価格の依頼を外側へ発行し、加工前の値を受け取る
        return (yield Resume(k, price * 80 // 100))  # 整数で2割引きし、100なら80、110なら88で元の続きを再開する
    return (yield Pass(effect, k))  # 担当外の依頼と元の続きを外側へ渡し、ここでは再開しない
```

`price = yield effect`がポイントです。ハンドラの処理から、同じ価格の依頼をもう一度発行しています。この依頼は、いま動いている`discount`の**外側**へ進みます。外側から価格が返ったら、このハンドラの処理を続け、加工した値を元の`k`へ渡します。

一方、`Pass(effect, k)`は、依頼と元の続きを外側へ引き渡す操作です。`Pass`のあとに戻って値を加工する用途には使いません。現在のVMでは、`Pass`したハンドラの残りの処理は再開されません。

| 書き方 | 何を渡すか | そのあと |
| --- | --- | --- |
| `yield Pass(effect, k)` | 元の依頼と、その結果を待つ続き | このハンドラの処理を終え、外側に任せる |
| `value = yield effect` | ハンドラ自身から発行する依頼 | 外側からの値で、このハンドラの処理を続ける |
| `yield Resume(k, value)` | 元の依頼への結果 | 元の続きを、その値で再開する |

「自分には関係ない操作を通す」と「外側の実装を利用して操作の意味を追加する」を書き分けられるわけです。

## 順序は、処理の意味になる

手数料として10を足すハンドラも作ります。

```python
@do  # 外側への再依頼と手数料加算後の再開を合成するハンドラにする
def add_fee(effect, k):  # 外側が返す価格へ手数料10を足して返す
    if isinstance(effect, ReadPrice):  # 価格の依頼だけを、このハンドラの処理対象にする
        price = yield effect  # 同じ価格の依頼を外側へ発行し、加工前の値を受け取る
        return (yield Resume(k, price + 10))  # 手数料10を足し、100なら110、80なら90で元の続きを再開する
    return (yield Pass(effect, k))  # 担当外の依頼と元の続きを外側へ渡し、ここでは再開しない

fee_then_discount = handler(yen)(  # 通貨の供給を、手数料を加えてから割り引く構成の外側へ置く
    handler(base_price)(  # 価格の再依頼に最終的に100を返すハンドラを置く
        handler(add_fee)(handler(discount)(quote()))  # 戻る価格を手数料、割引の順に加工して88にする
    )  # ここまでの範囲を包んだ計算を作り、この時点では実行しない
)  # ここまでの範囲を包んだ計算を作り、この時点では実行しない

discount_then_fee = handler(yen)(  # 通貨の供給を、割引後に手数料を加える構成の外側へ置く
    handler(base_price)(  # 価格の再依頼に最終的に100を返すハンドラを置く
        handler(discount)(handler(add_fee)(quote()))  # 戻る価格を割引、手数料の順に加工して90にする
    )  # ここまでの範囲を包んだ計算を作り、この時点では実行しない
)  # ここまでの範囲を包んだ計算を作り、この時点では実行しない

assert run(fee_then_discount) == "JPY 88"  # 手数料のあとで割り引いた結果がJPY 88と確認する
assert run(discount_then_fee) == "JPY 90"  # 割引のあとで手数料を足した結果がJPY 90と確認する
```

最初の例では、依頼が`discount → add_fee → base_price`の順に外側へ進みます。価格が戻るときは逆です。100に手数料を足して110、その110を割り引いて88になります。

二つ目では、戻ってきた100を先に80へ割り引き、それから手数料を足して90になります。**重ねられることと、順序を入れ替えても同じ意味になることは、別です。**

![順序を変えると、結果も変わる](/images/zenn-use-cases-v0/generated/handlers-flow.png)

依頼は内側から外側へ進み、価格を加工する順序は外側から内側です。手数料と割引の順序により、88と90に分かれます。

入れ子が長くなったら、`with_handlers`でも同じ構成を書けます。リストの先頭が最も外側、末尾が最も内側です。

```python
from doeff import with_handlers  # 外側から順に並べたリストでハンドラを合成する

program = with_handlers(  # 明示的なリストからハンドラの入れ子を作る
    [yen, base_price, add_fee, discount],  # 先頭を最外側とし、100から110、88と加工する順序にする
    quote(),  # 価格と通貨を依頼する見積もり計算を、全ハンドラで包む
)  # ここまでの範囲を包んだ計算を作り、この時点では実行しない
assert run(program) == "JPY 88"  # リストによる構成でも、手数料のあとで割り引く結果を確認する
```

`with_handlers`は、すべてのハンドラを同時に動かすAPIではありません。リストからハンドラの入れ子を作る補助関数です。

## 一部分にだけ適用する

ハンドラを、処理の途中で作る計算だけに取り付けることもできます。

```python
@do  # 異なる適用範囲の価格取得を順番に合成する計算にする
def scoped_prices():  # 割引の適用範囲が2回目の依頼だけであることを観測する
    normal = yield ReadPrice()  # 割引の外で依頼し、基準価格100を受け取る
    reduced = yield handler(discount)(ReadPrice())  # この依頼だけを割引で包み、価格80を受け取る
    restored = yield ReadPrice()  # 割引の範囲を出た次の依頼では、再び100を受け取る
    return normal, reduced, restored  # 適用範囲の違いを比較できる3価格の組を返す

assert run(handler(base_price)(scoped_prices())) == (100, 80, 100)  # 割引が2回目だけに適用され、次の依頼へ漏れないと確認する
```

`discount`が包むのは、2回目の`ReadPrice()`だけです。3回目へ割引の設定が漏れることはありません。グローバルな登録先を切り替えずに、計算の範囲で扱いを変えられます。

本体が呼ぶ`@do`の補助関数にも、その実行を包むハンドラが適用されます。ハンドラ内から補助関数を`yield`して、さらに外側の操作を使うこともできます。業務処理を`async def`へまとめて隠す必要はありません。

## ハンドラ自身も、別のエフェクトを使える

価格の取得を「状態から読む」「2秒待つ」という操作へ分解してみます。

```python
from doeff_core_effects import Get, state  # 価格を状態から読む依頼と、その値を供給するハンドラを使う
from doeff_core_effects.scheduler import scheduled  # 時間ハンドラが使う待機・再開をスケジューラで扱う
from doeff_time import Delay, GetTime, sim_time_handler  # 待機と時計を仮想時間で解釈し、実時間の待機を省く

@do  # 状態取得と時間待ちを、差し替え可能な依頼として合成する
def read_delayed_price():  # 状態の価格を読み、仮想時間で2秒後に返す手順を作る
    price = yield Get("unit_price")  # 外側のstateから、この例で初期値に指定した100を読む
    yield Delay(2)  # 時間ハンドラへ2秒の待機を依頼し、仮想時刻を進める
    return price  # 待機が終わったら、先ほど読んだ価格100を返す

@do  # 補助計算からの価格取得で依頼に応じるハンドラにする
def stored_price(effect, k):  # 価格の依頼を、状態取得と待機を行う手順へ翻訳する
    if isinstance(effect, ReadPrice):  # 価格の依頼だけを、このハンドラの処理対象にする
        price = yield read_delayed_price()  # 補助計算をyieldし、その状態取得と待機も外側の解釈へ委ねる
        return (yield Resume(k, price))  # 補助計算が返した価格で元の続きを再開する
    return (yield Pass(effect, k))  # 担当外の依頼と元の続きを外側へ渡し、ここでは再開しない

@do  # 時計と価格の依頼を順番に扱う計算にする
def timed_price():  # 解釈された価格と、その取得に経過した秒数を返す
    start = yield GetTime()  # 価格取得前の仮想時刻を記録し、経過時間の基準にする
    price = yield ReadPrice()  # 取り付けたハンドラの構成で決まる価格を受け取る
    end = yield GetTime()  # 価格取得後の仮想時刻を読み、2秒の進みを観測する
    return price, (end - start).total_seconds()  # 割引後の価格80と、仮想時間の経過2秒を返す

program = scheduled(  # 待機と再開を扱うスケジューラで計算全体を包む
    sim_time_handler()(  # DelayとGetTimeを仮想時間として解釈する
        state(initial={"unit_price": 100})(  # この範囲で読む基準価格を100として供給する
            handler(stored_price)(handler(discount)(timed_price()))  # 計時する価格取得を割引で包み、その外側で状態と待機へ翻訳する
        )  # ここまでの範囲を包んだ計算を作り、この時点では実行しない
    )  # ここまでの範囲を包んだ計算を作り、この時点では実行しない
)  # ここまでの範囲を包んだ計算を作り、この時点では実行しない
assert run(program) == (80, 2.0)  # 価格80と仮想時間2秒を確認し、実時間2秒の待機は要求しない
```

`stored_price`は、取得手順をまとめた`@do`関数`read_delayed_price`を`yield`します。その補助関数が発行する`Get`は外側の`state`へ、`Delay`は外側の`sim_time_handler`へ渡ります。仮想時間のハンドラとスケジューラを組み合わせているので、壁時計で2秒待つ例ではありません。`timed_price`の結果は「割引後の価格80、仮想時間の経過2秒」です。

この構成では、ドメインの操作を別のエフェクトへ翻訳するハンドラを、その翻訳先を提供するハンドラが包んでいます。`stored_price`の中で使う状態・時間の操作にも、受け手が必要です。

## 組み合わせるときに決めること

先ほどの価格例なら、「`ReadPrice`は整数の価格を返す」「手数料と割引をどちらから適用するか」「割引をどの計算に適用するか」が契約です。

状態やスケジューラまで組み合わせる場合は、さらに、状態をどの計算で共有するか、子タスクがどのハンドラの下で動くか、待機中のキャンセルをどう扱うかも意味を持ちます。ハンドラを重ねる構文だけでは、それらの契約まで同一にはなりません。

上の例は一つの計算の状態と仮想時間を検証しています。並行処理・キャンセルを含めた構成は、[時間とスケジューリング](doeff-time.md)と[イベントを待つ処理](doeff-events.md)のコードで扱います。

ハンドラは「差し替え可能な実装」であると同時に、**組み合わせて解釈を作るための部品**です。処理本体を変えずに、実装を選び、解釈を重ね、その適用範囲を狭められます。

→ [Rust VMは、エフェクトと続きをどう実行するのか](doeff-vm.md)

→ [doeffとは？ — メイン記事へ](doeff-main.md)
