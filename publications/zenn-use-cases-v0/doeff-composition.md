---
title: "ReaderもStateも失敗も、同じyieldでつなぐ — doeffで抽象を組み合わせる"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

設定、状態、ログ、失敗。それぞれに便利な抽象があります。doeffでは、**それらを使う処理を`@do`と`yield`でつなぎ、結果を普通の変数として受け取れます**。

たとえば「見出しを変換し、成功・失敗と試行回数とログを返す」処理です。

```python
from doeff import do  # 関数の呼び出しを、合成できるProgramにする。
from doeff_core_effects import Get, Try, writer_log  # 状態・失敗・ログの取得を組み合わせる。


@do  # 結果だけでなく、同じ試行の状態とログも値として取り出す。
def inspect(text: str):  # 入力ごとに成功・失敗と、その後に残る情報を調べる。
    outcome = yield Try(convert_title(text))  # 成功ならOk、空文字ならErrを受け取る。
    count = yield Get("attempts")  # Tryの後も試行回数1が残っていることを観測する。
    messages = yield writer_log()  # Tellで蓄積したメッセージ一覧を受け取る。
    return outcome, count, messages  # 結果・回数・ログを検証側へ返す。
```

`convert_title`は次の節で定義します。この記事のコードは上から順に同じファイルへ置いて実行できます。[完成したコード](examples/composition.py)もリポジトリにあります。

![異なる抽象を、同じyieldでつなぐ](/images/zenn-use-cases-v0/generated/composition-concept.png)

Tryで成功・失敗を値にし、Getで状態、writer_logでログを受け取ります。設定や時間も子Programの中で同じyieldを使います。

## 計算をつなぐ書き方を一つにする

| 操作 | この例で受け取る値・起きること | 関数型プログラミングで対応する考え方 |
| --- | --- | --- |
| `Ask` | 待機時間の設定値`2` | Reader |
| `Get` / `Put` | 試行回数を`0`から`1`へ更新 | State |
| `Tell` / `writer_log()` | 開始ログを蓄積し、一覧を取得 | Writer |
| `Try` | `Ok(変換結果)`または`Err(ValueError)` | Result / Eitherに近い扱い |
| 別の`@do`関数を`yield` | 子Programを進め、戻り値を取得 | モナド的な逐次合成 |
| `Delay` / `GetTime` | 待機を依頼し、再開後の時刻を取得 | 時間を扱うeffect |

読み方は「結果を受け取り、次の処理へ渡す」です。Reader、State、Writerなどを組み合わせるための連結操作を、呼び出し側で使い分けずに済みます。

同時に、**意味の違いはハンドラに残ります**。どの設定を読むか、状態を共有するか、ログをどう保存するか、失敗時に何を残すかは、別々に決める必要があります。

ここで揃えているのは計算の連結方法です。任意のモナドや継続の複製を、そのまま提供するという意味ではありません。[反復・絞り込み・集計](doeff-traverse.md)では、要素ごとに新しいProgramを作る方法を紹介します。

## 設定・状態・ログ・時間を使う変換処理

待機を小さな`@do`関数に分けます。`convert_title`からは`yield wait_before_conversion()`で呼び出し、その返り値である時刻を受け取ります。

```python
from doeff_core_effects import Ask, Put, Tell  # 設定取得、試行回数の更新、開始ログを依頼する。
from doeff_time import Delay, GetTime  # 待機と現在時刻を、時間ハンドラへ依頼する。


@do  # 待機も時刻取得も、呼び出し元へ合成できるProgramにする。
def wait_before_conversion():  # 待機後の時刻を後続の処理へ返す。
    seconds = yield Ask("delay_seconds")  # readerから2秒という設定値を受け取る。
    yield Delay(seconds)  # 時間ハンドラに2秒後の再開を依頼する。
    return (yield GetTime())  # 仮想時計の開始から2秒後を返す。


@do  # 変換処理の状態・ログ・待機を、同じProgramにまとめる。
def convert_title(text: str):  # 正常な見出しと空文字の両方を入力データとして扱う。
    count = yield Get("attempts")  # 初期状態から試行回数0を読む。
    yield Put("attempts", count + 1)  # この試行を数え、状態を1へ更新する。
    yield Tell("見出しの変換を開始")  # 失敗した場合にも残す開始ログを発行する。
    finished_at = yield wait_before_conversion()  # 子Programを実行し、待機後の時刻を受け取る。
    title = text.strip()  # 前後の空白を除き、変換対象の文字列を得る。
    if not title:  # 空の見出しなら、変換結果を作れないと判断する。
        raise ValueError("見出しが空です")  # 呼び出し元のTryへ変換失敗を伝える。
    return title.upper(), finished_at  # 成功なら大文字の見出しと完了時刻を返す。
```

空文字を渡すと、回数の更新・ログの発行・待機が終わった後に失敗します。`inspect`の`Try`は、その失敗を`Err`へ変換します。正常な入力なら`Ok`の中に見出しと時刻が入ります。

## ハンドラを取り付ける順序にも意味がある

この例の`writer`は、ログを保存するために内部で`Get`と`Put`を発行します。その依頼を受ける`state`を、**`writer`の外側**へ取り付けます。式では`state(...)(writer(program))`となります。

`writer`を取り付けただけでは、プログラムの戻り値にログが追加されるわけではありません。本文の`yield writer_log()`で明示的に取得します。同様に、最後の状態も`yield Get(...)`で取得しています。

```python
from datetime import datetime, timedelta, timezone  # 仮想時計の開始時刻と2秒後を比較する。
from doeff import Err, Ok, run  # 成功・失敗の型と、テスト境界での実行に使う。
from doeff_core_effects.handlers import reader, state, try_handler, writer  # 依頼ごとの解釈を選ぶ。
from doeff_core_effects.scheduler import scheduled  # 仮想時間ハンドラが使う待機と再開を扱う。
from doeff_time.handlers.sim_time import sim_time_handler  # 実時間を待たず仮想時計を進める。

START = datetime(2026, 1, 1, tzinfo=timezone.utc)  # 毎回同じUTC時刻から検証を始める。


def execute_test(text: str):  # 各テストのために独立したハンドラ構成を作る。
    program = try_handler(inspect(text))  # Tryを解釈して例外をErrへ変換する。
    program = writer(program)  # Tellを受け取り、保存用のGet/Putを外側へ依頼する。
    program = state(initial={"attempts": 0})(program)  # 本文とwriterが同じ状態を参照する。
    program = reader(env={"delay_seconds": 2})(program)  # 子Programへ2秒の設定を供給する。
    program = sim_time_handler(start_time=START)(program)  # 実行ごとに仮想時計を初期化する。
    return run(scheduled(program))  # テスト境界で実行し、inspectの3要素を受け取る。
```

`state(...)`を呼び出した時点で、そのハンドラが使う状態が作られます。上のテスト用関数では毎回作り直すため、前の実行の試行回数やログが混ざりません。

時間の依頼を仮想時計で扱うために、`sim_time_handler`と`scheduled`を組み合わせています。実時間・仮想時間の切り替えは[時間の記事](doeff-time.md)、ハンドラ同士が発行するeffectの届き方は[ハンドラの合成の記事](doeff-handlers.md)で掘り下げます。

## 成功・失敗の後に何が残るかまで確かめる

```python
def verify() -> None:  # 外部接続なしで、成功・失敗・再実行の意味を確かめる。
    success, count, messages = execute_test(" doeff ")  # 正常な入力を最初から処理する。
    assert isinstance(success, Ok)  # Tryが成功をOkとして返したことを確認する。
    assert success.value == ("DOEFF", START + timedelta(seconds=2))  # 変換結果と仮想時刻が合う。
    assert count == 1  # 成功した変換が1回として数えられる。
    assert messages == ["見出しの変換を開始"]  # Writerのログを返り値として確認できる。
    failure, count, messages = execute_test("   ")  # 空の見出しを、新しい状態で処理する。
    assert isinstance(failure, Err)  # 例外が検証側へ漏れず、失敗の値として届く。
    assert isinstance(failure.error, ValueError)  # 元の例外型が保たれる。
    assert str(failure.error) == "見出しが空です"  # 変換失敗の理由が保たれる。
    assert count == 1  # Tryは失敗前に行った状態更新を巻き戻さない。
    assert messages == ["見出しの変換を開始"]  # 失敗前のTellも巻き戻されない。
    _, repeated_count, repeated_messages = execute_test("again")  # 3回目もハンドラを作り直す。
    assert repeated_count == 1  # 別の実行に試行回数が漏れない。
    assert repeated_messages == ["見出しの変換を開始"]  # 別の実行のログも混ざらない。


if __name__ == "__main__":  # このファイルを直接実行した場合に検証する。
    verify()  # 全検証が通れば出力せず終了する。
```

`Try`はこの構成で状態やログを巻き戻しません。トランザクションが必要なら、その保証を持つ別のeffect・ハンドラや保存処理を設計します。

![失敗しても、試行回数と開始ログは残る](/images/zenn-use-cases-v0/generated/composition-flow.png)

空の見出しはErrになりますが、Tryの後で読む試行回数は1、ログには開始メッセージが残ります。この構成に自動の巻き戻しはありません。

関数の色との関係は[同じ計算型で処理をつなぐ記事](doeff-color.md)、effectを副作用とドメインAPIのどちらで切るかは[境界の設計の記事](doeff-boundaries.md)を参照してください。

---

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考資料・検証版

導入方法は[公式README](https://github.com/proboscis/doeff#installation)を参照してください。以下はこの記事で確認した開発版へのリンクです。公開パッケージの最新版との一致は別途確認が必要です。

- [Reader・State・Writer・Try・writer_logの実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/handlers.py)
- [基本effectと合成のテスト](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/tests/test_core_effects.py)
- [仮想時間ハンドラの実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-time/src/doeff_time/handlers/sim_time.py)
