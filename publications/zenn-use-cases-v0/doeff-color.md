---
title: "asyncが呼び出し元へ広がる問題と、doeffの「一つの色」"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

同期処理を非同期に変えると、呼び出し元にも`async def`と`await`が広がることがあります。doeffでは、処理を`Program`という共通の形式へ揃えます。待ち方が変わっても、設定の取得と子の計算をつなぐ本体は同じです。

```python
from doeff import do  # 関数呼び出しから、まだ実行していないProgramを作る。
from doeff_core_effects import Ask  # 設定値の取得をハンドラへ依頼する。
from doeff_time import Delay  # 待ち方を決めず、経過してほしい秒数を表す。

@do  # 待機と結果を、呼び出し元がyieldできるProgramにする。
def worker(seconds: float):  # 必要な待機時間を受け取り、待ち方はハンドラへ委ねる。
    yield Delay(seconds)  # 指定した秒数の経過を依頼し、完了すると次の行へ進む。
    return "完了"  # 待機が終わった後に、呼び出し元へ文字列を返す。

@do  # 設定取得と子の待機処理を、ひとつのProgramへ合成する。
def workflow(seconds: float):  # 3種類の時計で共有する処理の順序を表す。
    prefix = yield Ask("result_prefix")  # この実行範囲の設定から「処理結果」を受け取る。
    result = yield worker(seconds)  # 子のProgramを進め、待機後に「完了」を受け取る。
    return f"{prefix}: {result}"  # 設定と子の結果を使い、「処理結果: 完了」を返す。
```

`Ask`は設定取得のエフェクト、`worker(seconds)`は別の計算を表す`Program`です。どちらも、`yield`で外側へ渡して結果を受け取ります。`workflow`は、`worker`の待機が同期か非同期か、実時間か仮想時間かを知りません。

![処理をつなぐ書き方をProgramへ揃える](/images/zenn-use-cases-v0/generated/color-concept.png)

設定の取得も子の計算もyieldでつなぎ、時間の依頼をどう扱うかは外側のハンドラで選びます。

## 同じ本体を、3種類の時計で動かす

先ほどの定義に続けて、次の検証を実行できます。処理本体を作る`workflow(0.01)`は一度だけ呼び、同じ`Program`を3通りのハンドラ構成で実行します。

```python
from doeff import Program, run  # Programと普通の値を区別し、検証の境界で実行する。
from doeff_core_effects.handlers import await_handler, lazy_ask  # 非同期待機と設定取得を解釈する。
from doeff_core_effects.scheduler import scheduled  # 待っているProgramの再開を管理する。
from doeff_time import async_time_handler, sim_time_handler, sync_time_handler  # 3種類の時計を選べるようにする。

p_workflow: Program[str] = workflow(0.01)  # まだ待機せず、10ミリ秒を待つ処理を値として保持する。

def verify() -> None:  # 同じ本体を3通りで実行し、得られる文字列を比較する。
    assert isinstance(p_workflow, Program)  # 呼び出しただけでは結果の文字列にならないと確認する。
    configured = lazy_ask({"result_prefix": "処理結果"})(p_workflow)  # 設定取得を共通のハンドラで解釈する。
    blocking = run(scheduled(sync_time_handler()(configured)))  # 実行スレッドで10ミリ秒待って結果を得る。
    simulated = run(scheduled(sim_time_handler()(configured)))  # 実時間の待機なしで仮想時計を10ミリ秒進める。
    asynchronous = run(  # asyncioとの橋渡しを取り付け、同じ本体を非同期の時計で動かす。
        scheduled(await_handler()(async_time_handler()(configured)))  # Delayが内部で発行するAwaitを処理する。
    )
    assert blocking == simulated == asynchronous == "処理結果: 完了"  # 3種類の解釈で結果が一致すると確認する。

verify()  # 外部通信せず、同期・非同期・仮想時間の3つの構成を確認する。
```

結果はいずれも`"処理結果: 完了"`です。ここでは比較をすぐ実行できるよう、待機を10ミリ秒にしています。

| 選ぶハンドラ | `Delay`の解釈 | 実行に必要な構成 |
|---|---|---|
| `sync_time_handler()` | 実行スレッドを停止して実時間を待つ | 時間ハンドラと`scheduled` |
| `async_time_handler()` | 内部で`asyncio.sleep`を待つ | 時間ハンドラ、`await_handler`、`scheduled` |
| `sim_time_handler()` | 待機中のタスクに合わせて仮想時計を進める | 時間ハンドラと`scheduled` |

`async_time_handler`を使うときも、利用側の`worker`や`workflow`を`async def`へ書き換えません。`Await`と非同期実行基盤への橋渡しはハンドラ側にあります。なお、この例の`run`は結果が返るまで呼び出し元を待たせます。内部の待ち方が非同期であることと、外側のAPIがawaitableを返すことは別です。

同期版の`Delay`は実行スレッドを止めるため、その間に同じスレッド上の別タスクを進める用途には向きません。同じ依頼に応答できても、並行実行時の性質まで同じになるわけではありません。

[実行できる完全な例](examples/color_comparison.py)では、仮想時計で正確に10秒経過することも検証しています。時刻や予定の扱いは[時間の記事](doeff-time.md)で掘り下げます。

## 関数の色の問題とは

比較のため、`asyncio`で同じ待機を表すと次のようになります。この`async def`はPythonの非同期構文を説明するための例です。

```python
import asyncio  # 比較用に、標準の非同期タイマーと実行ループを使う。

async def worker_async(seconds: float):  # 非同期タイマーをawaitするcoroutineを作る。
    await asyncio.sleep(seconds)  # asyncioの時計で指定した時間が経過するまで実行を譲る。
    return "完了"  # 待機が終わったら、awaitしている呼び出し元へ文字列を返す。

async def workflow_async(seconds: float):  # 子のcoroutineを待つため、呼び出し元もasync defにする。
    result = await worker_async(seconds)  # 子の結果「完了」を受け取ってから次へ進む。
    return f"処理結果: {result}"  # 同じ形式の結果の文字列を返す。

assert asyncio.run(workflow_async(0.01)) == "処理結果: 完了"  # 非同期版の比較結果を検証する。
```

同期関数から普通の値を受け取っていた箇所を、このような非同期関数へ置き換えると、呼び出し元も`await`する形へ変更が広がることがあります。この区別を「関数の色」と呼びます。

`await`自体はPython共通の構文であり、`asyncio`専用ではありません。別の非同期フレームワークも同じ構文を使えます。ただし、待つ対象の互換性、タスクの管理、時計やキャンセルの契約まで共通とは限りません。doeffも、既存のSDKを何もせず自由に取り替えられるわけではありません。**依頼をエフェクトとして切り出し、その契約を満たすハンドラを取り付ける**ことで、呼び出し元への変更を抑えます。

## なぜyield？ asyncioも、最初はyield fromだった

ジェネレータは値を列挙するだけでなく、途中で中断し、外側から値を受け取って続きを動かせます。初期の`asyncio`もこの仕組みを使っていました。以下は歴史を説明するコードで、現在のPython向けの実行例ではありません。

```python
import asyncio  # 当時の、ジェネレータをcoroutineとして扱うAPIを参照する。

@asyncio.coroutine  # 当時のAPIで、このジェネレータを非同期処理として宣言する。
def old_job():  # async def導入前の待機処理を示す。
    yield from asyncio.sleep(10)  # 子の待機処理へ委譲し、10秒後に続きを再開する。
    return "完了"  # 待機を終えた結果を呼び出し元へ返す。
```

`asyncio`の提案である[PEP 3156](https://peps.python.org/pep-3156/)は、`yield from`に基づくスケジューラを説明しています。その後、Python 3.5の[PEP 492](https://peps.python.org/pep-0492/)で`async def`と`await`が導入されました。

この歴史はdoeffの書き方を理解する手がかりですが、古い`yield from`とdoeffの`yield`は同じプロトコルではありません。`yield from`は別のイテレータへの委譲です。doeffでは`yield worker(seconds)`と書き、子の`Program`をランタイムへ渡します。`yield Delay(seconds)`なら、時間の経過という依頼を渡します。

`yield`だけでエフェクトシステムになるわけでもありません。`@do`がジェネレータ関数の実行を`Program`として包み、ランタイムがエフェクトと継続をハンドラへつなぎます。[coroutineとの比較](doeff-coroutines.md)と[Rust VMの解説](doeff-vm.md)では、この境界を詳しく扱います。

## 色を消すというより、つなぎ方を揃える

`worker(10)`は`"完了"`という文字列ではなく、待機して文字列を返す`Program`です。別の`@do`関数から値を使うには、`yield worker(10)`が必要です。普通の値と計算の区別は残ります。

そのうえで、先ほどの例では`Ask`による設定取得と、`worker`の待機を同じ作法で合成できました。**処理をつなぐ書き方を揃え、実行方法を選ぶ境界をハンドラへ集める**ことに利点があります。

![子Programからの結果を、同じyieldで受け取る](/images/zenn-use-cases-v0/generated/color-flow.png)

workflowは子Programの完了を待ちます。Delayの解釈を替えても、workerから受け取る値は「完了」です。

ただし、通常のPythonコードとして直接行った通信や`time.sleep`まで、ハンドラが自動で捕まえることはできません。差し替えたい操作を、まずエフェクトの境界にする必要があります。

また、継続を一度だけ再開するdoeffのVMは、同じ継続を複製して何度も再開する機能を提供していません。「一つの色」は、あらゆる計算の性質を無条件に同じにするという意味ではありません。依頼と結果の契約を揃え、必要な能力を[ハンドラの合成](doeff-handlers.md)で選べることを指しています。

---

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考資料・検証版

導入方法は[公式README](https://github.com/proboscis/doeff#installation)を参照してください。以下はこの記事で確認した開発版へのリンクです。公開パッケージの最新版との一致は別途確認が必要です。

- [doeff本体と導入方法](https://github.com/proboscis/doeff/tree/d4705914e39740aee98a9f57a4535c463d9479cc)
- [実時間・仮想時間のハンドラ](https://github.com/proboscis/doeff/tree/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-time)
- [ハンドラの能力と関数の色の整理](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/docs/22-capability-classes.md)
- [実行できる比較と検証](examples/color_comparison.py)
