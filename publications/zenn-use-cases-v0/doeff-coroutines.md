---
title: "coroutineがあるのに、なぜdoeff？ — awaitとeffect handlerの役割を分ける"
emoji: "🔀"
type: "tech"
topics: ["python", "asyncio", "doeff"]
published: false
---

`await`も`yield`も、途中で止めた処理の続きを進める場所になります。では、`async def`で書ける処理を、なぜdoeffの`@do`で書くのでしょうか。

doeffでは、**「何をしてほしいか」を依頼のデータにして、外側のハンドラへ渡せます。** ハンドラは結果を返すだけでなく、その結果で続きをいつ再開するかも扱います。名前取得、状態、通信、時間などの依頼を、この形で組み合わせられます。

短い例で、似ている部分から比べます。

## 同じ「名前を取得して、挨拶する」を書く

まずは`asyncio`の比較例です。ここだけは`async def`を使い、coroutineとしてどうつながるかを示します。名前取得は通信しないテスト用の実装です。

```python
import asyncio  # 比較用のcoroutineを、標準のイベントループで動かす。

async def read_name_async():  # 非同期の名前取得を、通信しない比較用の処理として用意する。
    await asyncio.sleep(0)  # いったん現在のTaskから実行を譲り、再開を待つ。
    return "花子"  # 待機から戻ったら、名前の文字列を呼び出し元へ返す。

async def greet_async():  # 名前の取得と挨拶の組み立てをcoroutineとしてつなぐ。
    name = await read_name_async()  # 子coroutineを進め、その戻り値「花子」を受け取る。
    return f"こんにちは、{name}さん"  # 取得した名前を含む挨拶を返す。

assert asyncio.run(greet_async()) == "こんにちは、花子さん"  # 実行境界でイベントループを動かし、挨拶を確認する。
```

次はdoeffです。`ReadName`を、名前を取得してほしいという依頼として定義します。取得方法はこのデータに含めません。

```python
from dataclasses import dataclass  # 名前取得の依頼を、不変のデータ型として定義する。
from doeff import Effect, Pass, Resume, do, handler, run  # 依頼・継続・計算の合成と検証用の実行を使う。

@dataclass(frozen=True)  # 発行した依頼を、あとから書き換えない型にする。
class ReadName(Effect):  # ハンドラへ名前の文字列を要求する操作を宣言する。
    pass  # この依頼には引数がないため、フィールドを追加しない。

@do  # 呼び出すと、名前取得と挨拶を合成した実行前のProgramを返す。
def greet():  # coroutine版と同じ挨拶を、依頼とその結果から作る。
    name = yield ReadName()  # 取得方法をハンドラに委ね、返された名前を受け取る。
    return f"こんにちは、{name}さん"  # ハンドラが返した名前で挨拶を作る。

@do  # 名前を求める依頼に、続きへの値の供給で応じる計算にする。
def hanako(effect, k):  # 依頼と、名前を受け取ったあとの続きを引き受ける。
    if isinstance(effect, ReadName):  # 名前取得の依頼だけを、このハンドラで処理する。
        return (yield Resume(k, "花子"))  # 停止中のyieldへ「花子」を渡し、挨拶の続きを再開する。
    return (yield Pass(effect, k))  # 担当外の依頼は、元の継続を保って外側へ渡す。

assert run(handler(hanako)(greet())) == "こんにちは、花子さん"  # 名前供給を取り付けて実行し、同じ挨拶を確認する。
```

`k`は、依頼の結果を受け取ったあとの「続き」を表す**継続**です。`handler(hanako)(greet())`は、`greet()`をハンドラの適用範囲で包みます。この例では、名前を要求した位置からその境界までの継続を、VMがハンドラへ渡します。

`Resume(k, "花子")`がその続きを再開すると、`name = yield ReadName()`の`name`へ「花子」が入り、挨拶の組み立てに進みます。`ReadName()`が自分で取得関数を呼ぶわけではありません。

![待機する対象と、解釈する依頼を分ける](/images/zenn-use-cases-v0/generated/coroutines-concept.png)

awaitはawaitableを進める構文です。doeffでは依頼のデータと継続を、実行時に取り付けたハンドラへ渡します。

この小さな名前取得だけなら、関数を引数で渡すDIでも差し替えられます。doeffの利点が広がるのは、こうした依頼が複数の関数の奥に現れ、同じ実行範囲で解釈や記録、待機の扱いを組み合わせたいときです。

## coroutineとTaskとハンドラは、担当が違う

`async def`を呼んで得るcoroutineオブジェクトと、それを実行する`asyncio.Task`は別です。coroutineを呼んだだけでは、その本体の実行は始まりません。`asyncio.run`や、実行中のcoroutineからの`await`などで進めます。Taskはcoroutineの実行を管理し、Futureの完了待ちなどに応じて停止・再開します。[Python公式：Coroutines and tasks](https://docs.python.org/3/library/asyncio-task.html)

`await`自体はPythonの構文で、`asyncio`専用の予約語ではありません。別の実行基盤もこの構文を使えます。また、`await`のたびに必ずイベントループへ制御を返すわけではありません。上の比較例は、その動きを明確にするために`asyncio.sleep(0)`を置いています。

| 見るところ | Pythonのcoroutine | doeffのProgram |
| --- | --- | --- |
| 関数を呼んで得るもの | coroutineオブジェクト | `@do`が構築する、実行前の計算 |
| 子の処理の結果を受け取る | `await child()` | `yield child()` |
| この例の実行基盤 | `asyncio`のイベントループとTask | Rust VM。時間や並行処理を使う構成ではスケジューラも設置 |
| 操作の扱い | 呼んだawaitableの実装と実行基盤に従う | 依頼のデータを、範囲に取り付けたハンドラへ配送する |
| 操作を外側へ渡す仕組み | 言語標準にdoeffの`Pass`相当のハンドラ探索規約はない | `Pass(effect, k)`で元の依頼と継続を外側へ渡す |

Pythonのcoroutineにも`send`や`throw`があり、停止した処理を進めるプロトコルがあります。ただし、それだけでは「最寄りのハンドラ境界を探す」「そこまでの継続を取り出す」「担当外なら外へ渡す」という規約にはなりません。doeffはgeneratorを進める機構の上に、この実行モデルを実装しています。[Python公式：Coroutine objects](https://docs.python.org/3/reference/datamodel.html#coroutine-objects)

もちろん、coroutineや独自のawaitableを使って、同様の仕組みを作ることはできます。違いは、`await`という構文だけで、そのハンドラ機構まで用意されるかどうかです。[VM内部の説明](doeff-vm.md)では、PythonのgeneratorとRustの実装の接続を追います。

## 同じProgramに、別の解釈を取り付ける

名前を「太郎」にするハンドラを追加しても、`greet`は変更しません。

```python
@do  # 同じ名前取得の依頼へ、別の解釈を取り付けられるようにする。
def taro(effect, k):  # この適用範囲で、ReadNameの返答を「太郎」にする。
    if isinstance(effect, ReadName):  # 名前の取得だけを、このハンドラの担当にする。
        return (yield Resume(k, "太郎"))  # 同じ本体を「太郎」で再開し、別の挨拶を得る。
    return (yield Pass(effect, k))  # 別の依頼は、その継続とともに外側へ渡す。

p_greet = greet()  # 名前取得をまだ実行せず、同じ処理本体を再利用する値として保持する。
assert run(handler(hanako)(p_greet)) == "こんにちは、花子さん"  # この実行は「花子」を返す解釈で動かす。
assert run(handler(taro)(p_greet)) == "こんにちは、太郎さん"  # 次の実行は本体を変えず「太郎」の解釈で動かす。
```

この`p_greet`は、実行のたびにgeneratorを作る計算です。停止中の同じ継続を巻き戻して再開しているのではありません。doeffの継続は**one-shot**で、一度だけ再開できるものです。coroutineオブジェクトの再利用、Programの再実行、継続の再開は分けて考えます。

子の計算も`@do`でそろえると、名前取得の深さにかかわらず同じハンドラが届きます。

```python
@do  # 子Programの結果をyieldで受け取る、合成可能な計算にする。
def twice():  # 2回の挨拶を、呼んだ順に組として返す。
    first = yield greet()  # 1回目の子Programを実行し、挨拶文字列を受け取る。
    second = yield greet()  # 2回目の子Programも同じ範囲で実行し、挨拶を受け取る。
    return first, second  # 結果の順序を保った2要素の組を返す。

assert run(handler(hanako)(twice())) == (  # 外側のハンドラが両方の子Programへ届くことを確認する。
    "こんにちは、花子さん",  # 1回目のReadNameが「花子」で再開されると期待する。
    "こんにちは、花子さん",  # 2回目も同じ解釈で処理されると期待する。
)  # 2つの挨拶の一致を確認する。
```

複数の依頼への対応、ハンドラの順番、部分的な適用範囲は、[ハンドラの合成の記事](doeff-handlers.md)で扱います。

## 非同期SDKとは、必要な1操作のところでつなぐ

既存の非同期SDKを使うときは、`Await`で接続できます。ただし、アプリケーションの手順を丸ごと`async def`へ移して、その全体を`Await`で包むと、内側の処理はdoeffの依頼として現れません。

たとえば、文字列の断片を返す非同期ストリームを集めるなら、収集関数も`@do`で書きます。`Await`を置くのは、SDKから次の1断片を待つ位置です。

```python
from collections.abc import AsyncIterator  # 文字列を非同期に順番に返す入力の型を表す。
from doeff_core_effects import Await  # SDKの次の1要素を待つ依頼を使う。

@do  # 収集ループ自体を、別のProgramからyieldできる計算にする。
def collect_chunks(stream: AsyncIterator[str]):  # 文字列の断片を、受信順に結合する。
    iterator = aiter(stream)  # 入力の非同期イテレータを取得し、最初の断片から読む。
    text = ""  # まだ何も届いていない本文を空文字列で表す。
    while True:  # 反復の終了を検出するまで、1要素ずつ待機を依頼する。
        chunk = yield Await(anext(iterator, None))  # 次の1要素を待ち、反復終了ならNoneを受け取る。
        if chunk is None:  # str型の断片と、反復の終了を区別する。
            return text  # 終了したら、結合済みの本文を呼び出し元へ返す。
        text += chunk  # 空文字列も正常な断片として扱い、受信順に結合する。

@do  # 収集と仕上げを、同じyieldによる合成でつなぐ。
def introduction(stream: AsyncIterator[str]):  # 断片を集め、前後の空白を除いた紹介文を返す。
    body = yield collect_chunks(stream)  # 子Programの完了を受け取り、結合済みの本文を得る。
    return body.strip()  # 全文が揃ってから、前後の空白を取り除く。
```

この例の入力は`str`だけなので、`None`を終了の合図にできます。空文字列`""`は終了ではありません。

本物のストリームでは、SDK固有のチャンクから必要なフィールドを取り出します。たとえばOpenAIのチャンクを文字列として扱うことはできません。[LLMの記事](doeff-llm.md)では、その型と本文の取り出しまで含めて示しています。

![ストリームは、1断片ずつdoeffへ戻す](/images/zenn-use-cases-v0/generated/coroutines-flow.png)

収集ループと結合処理は@doに残し、次の1要素を待つ操作だけをAwaitで非同期実装へ接続します。

次は、通信しない非同期ストリームで実行する確認コードです。`async def`はSDK側の反復プロトコルを再現するテスト用の入力にだけ使います。

```python
from doeff_core_effects.handlers import await_handler  # Awaitを非同期処理の完了通知へ接続する。
from doeff_core_effects.scheduler import scheduled  # 完了を待って、停止中のProgramを再開する。

async def chunks_for_test():  # SDKを呼ばず、テスト用の非同期イテレータを作る。
    for chunk in (" はじめに", "", "、", "doeff "):  # 空の断片を挟み、結合順と終了判定を検査する。
        await asyncio.sleep(0)  # ネットワークを使わず、各要素の到着を非同期に進める。
        yield chunk  # テスト入力として、今回の文字列の断片を1つ返す。

program = introduction(chunks_for_test())  # 収集して空白を除く手順を、実行前のProgramとして作る。
result = run(scheduled(await_handler()(program)))  # 橋渡しとスケジューラを明示して、検証の境界で実行する。
assert result == "はじめに、doeff"  # 空の断片で終了せず、受信順の結合と空白除去ができたか確認する。
```

確認に使った`await_handler`は、共有のバックグラウンド`asyncio`ループでawaitableを実行し、外部promiseを通してdoeffのスケジューラへ結果を戻します。したがって、このハンドラを付ければ任意の非同期フレームワークや、別ループに所属するオブジェクトが無条件に動くという意味ではありません。接続先のawaitableと実行環境の契約は残ります。[確認したAwaitハンドラの実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/handlers.py#L427)

同じ実装では、doeff側のタスクのキャンセルが、実行中の橋渡し先coroutineまで伝播しない制約も明記されています。構文をそろえることと、キャンセルや資源の寿命の契約が同じになることは別です。

## 1回のAwaitで、どこまでを隠しているか

収集を`@do`に置くと、断片ごとの待機を外側で観測できます。次のハンドラは、取得処理には介入せず、到着した待機依頼を数えます。リストは検証用の観測記録です。

```python
requested = []  # 収集ループから届いたAwaitの件数を記録する場所を用意する。

@do  # 待機を観測し、実処理は外側へ渡すハンドラにする。
def count_awaits(effect, k):  # 収集関数を書き換えず、待機境界の回数を調べる。
    if isinstance(effect, Await):  # 次の1断片を待つ依頼だけを記録する。
        requested.append("Await")  # 1件の待機依頼が到着したことを観測記録へ残す。
    return (yield Pass(effect, k))  # 実際の待機は、外側のawait_handlerに引き継ぐ。

collecting = handler(count_awaits)(introduction(chunks_for_test()))  # 新しいストリームの収集を、待機の観測で包む。
assert run(scheduled(await_handler()(collecting))) == "はじめに、doeff"  # 観測を加えても本文が同じと確認する。
assert requested == ["Await"] * 5  # 4断片の取得と反復終了の確認が、計5回の依頼として届くと確認する。
```

これが、`yield collect_chunks(stream)`と書く理由です。収集の手順をdoeffの中に保つので、必要になれば、その途中にログや状態の依頼を加え、同じハンドラ構成へ渡せます。

## asyncioを「時間しか扱えない」とは考えない

`asyncio`はネットワークI/O、サブプロセス、タスクの同期なども扱います。イベントループの`loop.time()`は単調時計を使い、カレンダー上の時刻とは区別されます。独自のイベントループ実装も可能です。「実時間の待機だけの仕組み」と説明すると、比較を誤ります。[Python公式：Event loop](https://docs.python.org/3/library/asyncio-eventloop.html)

doeffの特徴は、時間も、名前取得のようなドメインの依頼も、**ハンドラで解釈する共通の境界にできること**です。そのうえで、どの時計やスケジューラを選ぶかを実行側で組み合わせます。実時間から仮想時間への切り替えは、[時間の記事](doeff-time.md)で動くコードを示しています。

初期の`asyncio`がgeneratorベースのcoroutineと`yield from`を使っていたことも、この話につながります。PEP 492は、その背景から`async`/`await`によるnative coroutineを導入しました。doeffは`yield`の停止・再開を利用して、依頼の配送とハンドラの継続制御を組み立てています。古い`asyncio`の書き方を再導入しているわけではありません。[PEP 492](https://peps.python.org/pep-0492/)

普通の値とProgramの区別は残ります。それでも、複数の関心事を同じ`@do`と`yield`でつなげる。この設計上の意味は、[関数の色の記事](doeff-color.md)で続けます。

## 検証した範囲

[実行例の全体](examples/coroutine_comparison.py)では、coroutineとProgramの同じ挨拶、同一Programのハンドラ差し替え、子Programの合成、文字列の収集、1断片ごとの待機境界を確認しています。ネットワークや実LLMは呼びません。

```sh
uv run --no-sync python publications/zenn-use-cases-v0/examples/coroutine_comparison.py  # 挨拶・合成・本文とAwait 5回のassertをオフラインで確認する。
```

doeffは開発版`d4705914e39740aee98a9f57a4535c463d9479cc`の実装に照合しています。`--no-sync`は依存関係が導入済みのこのcheckoutでの検証方法です。新しく試す場合の導入手順は[公式README](https://github.com/proboscis/doeff#installation)を参照してください。

---

[doeffとは？：メイン記事へ戻る](doeff-main.md)
