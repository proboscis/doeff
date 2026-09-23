---
title: "Pythonのyieldからalgebraic effectsへ — doeffのRust VMを読む"
emoji: "⚙️"
type: "tech"
topics: ["python", "rust", "doeff"]
published: false
---

doeffでは、`yield ReadName()`と書くとハンドラが呼ばれ、ハンドラから渡された値で処理が再開します。この「途中で止めて、外側へ処理を渡し、続きへ戻る」を実装するのがRust VMです。

まず、動きを追うための小さなプログラムを置きます。この記事の`run`は、実行境界を示す検証コードです。

```python
from dataclasses import dataclass  # 引数を持たない不変の依頼型を定義するために読み込む。
from doeff import Effect, Pass, Resume, do, handler, run  # 依頼・継続・計算の構築・実行に使うAPIを読み込む。

@dataclass(frozen=True)  # 依頼の内容を後から変えられないデータ型にする。
class ReadName(Effect):  # 名前の取得を要求し、文字列を受け取る操作を定義する。
    pass  # この依頼には追加の引数がないため、フィールドを置かない。

@do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
def greet():  # 名前を要求して挨拶文字列を返す計算を定義する。
    name = yield ReadName()  # 名前を要求して停止し、ハンドラから渡された文字列を受け取る。
    return f"こんにちは、{name}さん"  # 取得した名前を挨拶に埋め込み、計算の結果として返す。

@do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
def supply_name(effect, k):  # 依頼と停止中の継続を受け取り、名前を供給するハンドラを定義する。
    if isinstance(effect, ReadName):  # 名前の取得依頼だけをこのハンドラで処理する。
        return (yield Resume(k, "花子"))  # 名前に「花子」を渡して継続を再開し、その結果を返す。
    return (yield Pass(effect, k))  # 担当しない依頼を、継続とともに外側のハンドラへ渡す。

assert run(handler(supply_name)(greet())) == "こんにちは、花子さん"  # 名前を供給して実行し、挨拶の内容を確認する。
```

`ReadName`は操作の依頼です。`k`は、その操作の結果を受け取ったあとの「続き」を表す**継続**です。ハンドラは依頼と継続を受け取り、どの値で、いつ続きを再開するかを決めます。これがdoeffでalgebraic effectsを使うときの基本形です。

![Pythonのyieldを、Rust VMが解釈する](/images/zenn-use-cases-v0/generated/vm-concept.png)

Pythonの本体はgeneratorのまま実行し、Rust VMがエフェクトの配送と継続の接続を管理します。

## `@do`を呼ぶと、まず実行する計算ができる

`greet()`を呼んだ時点では、名前を取得しません。`@do`は、元の関数の呼び出しを遅延させた`Expand`という計算のノードを返します。

次の確認では、`started`は実行順を観測するためだけのリストです。

```python
from doeff import Expand  # 構築された計算がExpandノードか確認するために読み込む。

started = []  # 構築時と実行時を区別するため、観測記録を空で用意する。

@do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
def deferred():  # 実行開始を記録してからgreetへ進む計算を定義する。
    started.append("実行開始")  # 本体が実際に実行された時点で、観測用の記録を1件残す。
    return (yield greet())  # 子の計算もyieldで実行し、その挨拶を結果として返す。

program = deferred()  # 計算を構築する。この時点では本体の記録処理を実行しない。
assert isinstance(program, Expand)  # 呼び出し結果が実行前のExpand型の値であることを確認する。
assert started == []  # 計算の構築だけでは本体が動いていないことを確認する。

assert run(handler(supply_name)(program)) == "こんにちは、花子さん"  # 実行境界で計算を動かし、子の計算の結果を確認する。
assert started == ["実行開始"]  # 実行後に、開始がちょうど1回記録されたことを確認する。
```

`@do`内部では、次の構造を作ります。これは[デコレータ実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/doeff/do.py#L268)の抜粋です。

```python
def wrapper(*args, **kwargs):  # 元の関数の引数を保持し、遅延実行の計算を返す。
    def thunk():  # VMの実行時に呼ばれる、引数を閉じ込めた関数を作る。
        return _make_stream(fn(*args, **kwargs))  # 元の関数を呼び、得たgeneratorを命令ストリームで包む。

    return Expand(Apply(Pure(VMCallable(thunk)), []))  # 関数呼び出しの結果を命令列として展開する計算を返す。
```

`Pure`は値、`Apply`は呼び出し、`Expand`は得られた命令ストリームを実行するノードです。`_make_stream`がPythonのgeneratorを`IRStream`で包み、VMから一歩ずつ進められるようにします。通常のアプリケーションで、この構造を手書きする必要はありません。

## PythonのgeneratorとRust VMをつなぐ

[`run`](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/doeff/run.py#L10)は`PyVM`を作り、その`run`へ計算を渡します。次のように、同じ計算をVMの入口へ直接渡しても実行できます。

```python
from doeff_vm import PyVM  # PythonからRust VMを直接起動する入口を読み込む。

assert PyVM().run(handler(supply_name)(greet())) == "こんにちは、花子さん"  # VMを直接呼んでも同じ挨拶が返ることを確認する。
```

Pythonとの橋渡しをする層は、generatorの`send`を呼び、返ってきた値をVMの命令へ分類します。`send`の引数が、停止中の`yield`式の値になります。

```python
# 再開されたとき、sendされた「花子」がnameへ入る。
@do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
def greet():  # 名前を要求して挨拶文字列を返す計算を定義する。
    name = yield ReadName()  # 名前を要求して停止し、ハンドラから渡された文字列を受け取る。
    return f"こんにちは、{name}さん"  # 取得した名前を挨拶に埋め込み、計算の結果として返す。
```

`yield ReadName()`は、`Effect`を継承した値を渡しています。[Pythonとの橋渡しの実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-vm/src/python_generator_stream.rs#L686)が、これを内部命令`Perform`として扱います。明示的な`Perform`を使った次の形も実行できます。

```python
from doeff import Perform  # 依頼の実行を明示するVM命令を読み込む。

@do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
def explicit_greet():  # Performを明示した場合も同じ挨拶になる計算を定義する。
    name = yield Perform(ReadName())  # ReadNameの実行を明示し、再開時に名前の文字列を受け取る。
    return f"こんにちは、{name}さん"  # 取得した名前を挨拶に埋め込み、計算の結果として返す。

assert run(handler(supply_name)(explicit_greet())) == "こんにちは、花子さん"  # 明示的なPerformでも同じ結果になることを確認する。
```

Rust側では`Eval`、`Send`、`Raise`という信号を処理するステップ機械が、命令の評価、値を渡す再開、例外を渡す再開を進めます。Pythonの`return`によるgeneratorの終了は、`StopIteration.value`から計算の戻り値へ変換されます。[VMの実行ループ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-vm/src/pyvm.rs#L280)、[generatorの橋渡し](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-vm/src/python_generator_stream.rs#L259)

**Rustが担当するのは、この制御の実行です。Pythonの関数本体をRustへコンパイルしているわけではありません。** 文字列の組み立てなど、`yield`と次の`yield`の間のPythonコードはPythonとして動きます。

## ハンドラを選ぶのは、実行中の境界

`handler(supply_name)(program)`は、計算を`WithHandler`で包みます。VMはその場所にハンドラの境界と、本体を実行する領域を作ります。

エフェクトが発生すると、現在位置から親をたどり、最も近いハンドラ境界へ配送します。この記事の`handler`による構成では、エフェクトを処理できるかどうかをハンドラ本体が判断し、対象外なら`Pass(effect, k)`で外側へ渡します。

```python
forwarded = []  # 内側のハンドラを通過した依頼の型を観測する記録を用意する。

@do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
def forward(effect, k):  # 依頼を観測して外側へ渡すハンドラを定義する。
    forwarded.append(type(effect).__name__)  # 配送先を確認するため、受け取った型名を記録する。
    return (yield Pass(effect, k))  # 担当しない依頼を、継続とともに外側のハンドラへ渡す。

program = handler(supply_name)(handler(forward)(greet()))  # 内側に通過用、外側に名前供給用のハンドラを取り付ける。
assert run(program) == "こんにちは、花子さん"  # Passの先で名前が供給され、本体が完了することを確認する。
assert forwarded == ["ReadName"]  # 内側のハンドラが名前の依頼を1回受け取ったことを確認する。
```

この順序では、`forward`が先に受け取り、`Pass`で外側の`supply_name`へ渡します。VMが全ハンドラのPythonコードを調べ、`isinstance`に一致するものを先回りして選ぶわけではありません。[配送先の探索](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-vm-core/src/vm/dispatch.rs#L247)、[`Pass`の実行](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-vm-core/src/vm/step.rs#L710)

## 継続は、切り離した実行領域の列

VMでは、処理のフレームを収める領域を**fiber**と呼びます。ここでのfiberは、OSのスレッドではありません。実行中のfiberは親への参照でつながっています。

`Perform`を処理するとき、VMは現在の本体から、見つかったハンドラ境界までを切り離します。**ハンドラ境界そのものも含む列**が、`k`の実体です。Pythonからは`PyK`というオブジェクトを通して渡されます。

```python
@do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
def supply_name(effect, k):  # 依頼と停止中の継続を受け取り、名前を供給するハンドラを定義する。
    if isinstance(effect, ReadName):  # 名前の取得依頼だけをこのハンドラで処理する。
        # kには、名前を受け取った後のgreetの続きと境界がある。
        return (yield Resume(k, "花子"))  # 名前に「花子」を渡して継続を再開し、その結果を返す。
    return (yield Pass(effect, k))  # 担当しない依頼を、継続とともに外側のハンドラへ渡す。
```

ハンドラ本体は、その境界の**外側**で実行されます。そのため、ハンドラ自身が別のエフェクトを`yield`すれば、自然に外側のハンドラへ届きます。ドメイン操作を保存や通信の操作へ翻訳できる理由もここにあります。

`Resume`では切り離した列をつなぎ直し、先頭の処理へ値を渡します。`Pass`では、元の継続を保ちながら次の外側の境界まで列を広げ、次のハンドラへ渡します。この接続操作を行うのが[`perform_effect`と`reattach_chain`](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-vm-core/src/vm/dispatch.rs)です。

![続きを切り離し、値を渡してつなぎ直す](/images/zenn-use-cases-v0/generated/vm-flow.png)

kには停止中の本体からハンドラ境界までが含まれます。Resumeはこの継続を一度だけつなぎ直し、yieldの位置へ値を渡します。

## 続きが完了した後、ハンドラへ戻ることもできる

ハンドラの`Resume`は、その後に処理を書くこともできます。次の例では、名前を渡して本体を再開し、本体が返した結果を受け取ってから記録します。

```python
trace = []  # 本体とハンドラの進行順を観測する記録を用意する。

@do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
def body():  # 名前の依頼前と再開後を記録する計算を定義する。
    trace.append("本体: 依頼前")  # 本体が名前を要求する直前であることを記録する。
    name = yield ReadName()  # 名前を要求して停止し、ハンドラから渡された文字列を受け取る。
    trace.append(f"本体: {name}で再開")  # 渡された名前で本体が再開したことを記録する。
    return name  # 受け取った名前を、この計算の完了結果として返す。

@do(non_tail=True)  # 継続の完了後もハンドラで処理を続けることを明示する。
def traced(effect, k):  # 継続を再開し、その完了後にも記録するハンドラを定義する。
    if isinstance(effect, ReadName):  # 名前の取得依頼だけをこのハンドラで処理する。
        trace.append("ハンドラ: 再開前")  # 依頼を受け取り、まだ本体を再開していない時点を記録する。
        result = yield Resume(k, "花子")  # 本体を再開し、継続が完了して返した結果を受け取る。
        trace.append(f"ハンドラ: 本体の結果={result}")  # 本体の完了後、ハンドラに戻ったことと結果を記録する。
        return result  # 本体から受け取った完了結果を、さらに外側へ返す。
    return (yield Pass(effect, k))  # 担当しない依頼を、継続とともに外側のハンドラへ渡す。

assert run(handler(traced)(body())) == "花子"  # 本体とハンドラを通って最終的に名前が返ることを確認する。
assert trace == [  # 本体、ハンドラ、再開した本体、戻ったハンドラの順序を確認する。
    "本体: 依頼前",  # 最初は本体が名前を要求する直前まで動く。
    "ハンドラ: 再開前",  # 次に依頼がハンドラへ届く。
    "本体: 花子で再開",  # 名前を渡すと、停止していた本体が続きへ進む。
    "ハンドラ: 本体の結果=花子",  # 本体の完了後にハンドラが結果を受け取る。
]  # 期待する4件の順序の定義を閉じ、観測結果との一致を確かめる。
```

`result`は、`ReadName`の結果ではなく、再開した継続が完了して戻した結果です。この例では本体も名前をそのまま返すので、どちらも「花子」になります。

この書き方では、ハンドラのgeneratorが本体の完了まで残ります。`@do(non_tail=True)`は、その非末尾の再開を意図したことを明示します。[`@do`の確認と警告](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/doeff/do.py#L203)

## `Transfer`は、現在のハンドラの続きへ戻らない

再開後にすることがなければ、`Transfer(k, value)`で継続へ移れます。VMは実行中のハンドラのフレームを外してから、継続をつなぎ直します。

```python
from doeff import Transfer  # ハンドラの処理を終えて継続へ移る命令を読み込む。

@do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
def tail_name(effect, k):  # 名前を供給した後は自分へ戻らず継続へ移るハンドラを定義する。
    if isinstance(effect, ReadName):  # 名前の取得依頼だけをこのハンドラで処理する。
        return (yield Transfer(k, "花子"))  # 現在のハンドラ呼び出しを終え、名前を渡して本体へ移る。
    return (yield Pass(effect, k))  # 担当しない依頼を、継続とともに外側のハンドラへ渡す。

@do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
def two_names():  # 同じハンドラ境界で2回の依頼を扱えるか確認する計算を定義する。
    first = yield ReadName()  # 1回目の名前を要求し、供給された文字列を受け取る。
    second = yield ReadName()  # 再開後にも名前を要求し、同じ境界のハンドラから値を受け取る。
    return first, second  # 2回の依頼の結果を組として返す。

assert run(handler(tail_name)(two_names())) == ("花子", "花子")  # Transferの後でも2回とも同じ境界で処理できることを確認する。
```

ここでなくなるのは、**現在実行しているハンドラ呼び出しのフレーム**です。継続にはハンドラ境界が含まれているため、2回目の`ReadName`も同じハンドラで扱えます。`Transfer`を「以後そのハンドラが取り外される」と理解すると、この動作を説明できません。[`Resume`と`Transfer`の実行](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-vm-core/src/vm/step.rs#L227)

また、`@do`がソースから安全な末尾位置と判定した`return (yield Resume(...))`は、橋渡し層で`Transfer`へ変換されます。冒頭の短いハンドラがこの形です。再開後に処理を行う前節の例とは区別します。[末尾位置の分類](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-vm/src/python_generator_stream.rs#L319)

## 同じ継続を2回再開することはできない

doeffの継続は**one-shot**、一度だけ利用できる方式です。次は、その制約を確認するため、意図的に誤ったハンドラを書いた例です。

```python
@do(non_tail=True)  # 継続の完了後もハンドラで処理を続けることを明示する。
def resume_twice(effect, k):  # 二重再開の拒否を検証するため、意図的に誤ったハンドラを定義する。
    if isinstance(effect, ReadName):  # 名前の取得依頼だけをこのハンドラで処理する。
        yield Resume(k, "花子")  # 最初の再開を行い、同じkをここで消費する。
        return (yield Resume(k, "太郎"))  # 消費済みのkを再利用する誤り。VMが例外で拒否することを期待する。
    return (yield Pass(effect, k))  # 担当しない依頼を、継続とともに外側のハンドラへ渡す。

try:  # 二重再開が実行時エラーになることを検証する範囲を始める。
    PyVM().run(handler(resume_twice)(greet()))  # 意図的に誤ったハンドラを実VMで動かし、二重再開を試す。
except RuntimeError as error:  # 期待する実行時エラーだけを受け取り、内容の確認へ進む。
    failure = str(error)  # 継続の消費を理由に失敗したか、後で確かめるために文字列を得る。
else:  # 例外なしで終わった場合は、one-shotの制約が守られていない。
    raise AssertionError("同じ継続の二重再開を受理しました")  # 二重再開を受理した場合、検証自体を失敗させる。
assert "consumed" in failure or "one-shot" in failure  # 失敗理由が継続の再利用であることを確認する。
```

Rustの[`Continuation`](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-vm-core/src/continuation.rs#L322)は、列を`Option<DetachedFiberChain>`に保持しています。利用時の`take()`が所有権を移し、元は空になります。同じ`k`を再び使おうとしても、つなぎ直す列は残っていません。

この制約は、同じ処理を何度も実行できないという意味ではありません。新しい`greet()`を作れば、別の計算として実行できます。同じ停止状態を複製して探索する方式と、新たに計算を作る[Traverse](doeff-traverse.md)や、履歴を使った[durable execution](doeff-durable.md)は区別します。

## この仕組みから、ハンドラの合成へ

ここまでの要点は、`yield`が渡した依頼だけでなく、**その後の続きもハンドラへ渡る**ことです。Rust VMは、ハンドラ境界を含む継続を切り離してつなぎ直し、Pythonのgeneratorへ値を返します。

アプリケーション側では`@do`で処理をつなぎ、実行境界でハンドラを組み合わせます。ドメインの操作を別のエフェクトへ翻訳する例や、内外の順序で意味が変わる例は、[ハンドラの合成可能性](doeff-handlers.md)で扱います。

`async def`のコルーチンと、この`@do`による計算の違いは、[コルーチンとエフェクトハンドラ](doeff-coroutines.md)で比較します。

---

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 検証したコードと版

[完全な実行例](examples/vm_walkthrough.py)で、遅延実行、ネストした`@do`、`Pass`による配送、再開後の戻り順、`Transfer`後の境界、二重再開の拒否を、実際のRust VMで確認しています。外部サービスや非同期SDKは使用しません。

```bash
# 実VMで各assertを実行し、成功時は検証結果と4段階の実行順を表示する。
uv run --no-sync python publications/zenn-use-cases-v0/examples/vm_walkthrough.py
```

実装リンクは開発checkoutの`d4705914e39740aee98a9f57a4535c463d9479cc`に固定しています。VM内部の構造は、この版の実装を説明したものです。
