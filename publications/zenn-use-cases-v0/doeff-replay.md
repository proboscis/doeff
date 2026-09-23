---
title: "HTTPの取得と応答の再利用を、ハンドラの合成で分ける"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

外部サービスを使う処理を確かめたい。同じ応答を再利用したい。未保存の依頼は通信させずに失敗させたい。この三つを一つのHTTPクライアント設定に押し込める必要はありません。**HTTPの取得を担当するハンドラと、結果の保存・再利用を担当するハンドラを合成します。**

処理本体は、必要なHTTP依頼と、その応答から取り出したい値を書きます。

```python
from doeff import do  # 呼び出し時には実行せず、合成できるProgramを作る。
from doeff_core_effects import HttpRequest  # 取得方法を指定せず、HTTPの依頼を表す。

@do  # この関数をyieldできる処理へ変換する。
def fetch_text(url: str):  # 入力URLの本文を文字列として返す。
    response = yield HttpRequest("GET", url)  # 担当ハンドラからHttpResponseを受け取る。
    response.raise_for_status()  # 400以上なら成功した本文として扱わず、例外にする。
    return response.text  # 正常な応答の本文を呼び出し元へ返す。
```

`HttpRequest`の標準ハンドラが返す型は、doeffの`HttpResponse`です。処理本体がHTTPクライアントを作る必要はありません。

![HTTPの取得と保存を、別のハンドラで合成する](/images/zenn-use-cases-v0/generated/replay-concept.png)

メモ化の判定、保存先、保存ミス時の取得方法を独立に選びます。

## 三つの役割を組み合わせる

この例では、次の三つを取り付けます。

| 役割 | 使うもの | 何を受け取り、何を返すか |
| --- | --- | --- |
| HTTP依頼をメモ化する | `make_memo_rewriter(HttpRequest)` | 保存済みなら応答を返す。未保存ならHTTP依頼を外側へ出し直し、得た応答を保存する |
| 保存する | `sqlite_memo_handler(database)` | `MemoExists`・`MemoGet`・`MemoPut`をSQLiteへ照会・保存する |
| 取得する | `fixed_http`または`reject_http` | `HttpRequest`へ固定応答を返すか、取得を拒否する |

HTTP依頼は、処理本体に最も近いメモ化ハンドラが先に受け取ります。保存先へ照会し、**保存ヒットなら外側のHTTP取得ハンドラまで進みません**。保存ミスのときだけHTTP依頼を出し直します。

この順序が合成の要点です。HTTPを先に受け取る位置へ取得ハンドラを置くと、メモ化を通過せず取得してしまいます。さらに外側へログと非同期待機のハンドラ、スケジューラを取り付けます。SQLiteの実装が出す`Await`は、保存I/Oを待つためのものです。アプリケーション全体を`async def`へ移す必要はありません。

## 固定応答・SQLite保存・再生専用を実際に組み合わせる

次の例は、pytestを含むこのリポジトリの開発環境で、そのままオフライン実行できます。`fixed_http`は標準HTTPハンドラと同じ`HttpResponse`を返します。テスト用のHTTPクライアントやクライアントのfactoryは作りません。交換するのは、**`HttpRequest`をどう解釈するか**です。

`reject_http`は、HTTP依頼が届いたら必ず例外にします。そのハンドラを付けた二回目の実行が成功するため、保存ヒット時にHTTPへ到達していないことも確認できます。

```python
"""HTTPの取得とSQLiteへの保存を別のハンドラへ分け、通信せずに合成を検証する。"""

from pathlib import Path  # 保存先を型付きのパスとして組み立てる。
from tempfile import TemporaryDirectory  # 検証用SQLiteだけを一時領域へ置く。

import pytest  # 未保存時に期待した例外が出ることを、テストとして確認する。
from doeff_core_effects import HttpRequest, HttpResponse  # HTTPの依頼と正式な応答型を使う。
from doeff_core_effects.handlers import (  # 保存層が出す待機とログを外側で解釈する。
    await_handler,  # SQLiteの非同期I/Oを待ち、結果をProgramへ戻す。
    slog_discard_handler,  # 検証中はメモ化の診断ログを表示しない。
)
from doeff_core_effects.memo_handlers import (  # 再利用の判断と保存先を独立に選ぶ。
    make_memo_rewriter,  # HttpRequestをMemoExists/Get/Putと取得処理へ分解する。
    sqlite_memo_handler,  # Memo系の依頼をSQLiteへ保存・照会する。
)
from doeff_core_effects.scheduler import scheduled  # 保存層のAwaitを進める実行環境を付ける。

from doeff import Pass, Resume, do, handler, run  # 依頼の委譲・再開・合成と検証用runを使う。


@do  # 呼び出すと即通信せず、HTTP依頼を含むProgramになる。
def fetch_text(url: str):  # URLから本文を返す処理は、保存先や取得方法を知らない。
    response = yield HttpRequest("GET", url)  # 取り付けたハンドラからHttpResponseを受け取る。
    response.raise_for_status()  # 400以上なら本文を成功値として返さず、例外にする。
    return response.text  # 成功時は応答本文の文字列を呼び出し元へ返す。


@handler  # 以下の依頼処理をProgramに取り付けられるハンドラへ変換する。
@do  # 再生時の不一致も、処理本体と同じyieldの境界で扱う。
def reject_http(effect, k):  # 保存にないHTTP依頼を受け取った場合だけ失敗させる。
    if not isinstance(effect, HttpRequest):  # Memoやログなどの別の依頼は担当しない。
        return (yield Pass(effect, k))  # 別のハンドラへ依頼と続きをそのまま渡す。
    raise LookupError(effect.url)  # HTTPへ接続せず、未保存のURLをエラーに含める。


def verify() -> None:  # テスト境界として複数のrunを実行し、保存と再利用を確かめる。
    calls: list[str] = []  # 実際に取得担当ハンドラまで届いたURLだけを数える。

    @handler  # 固定応答を返す取得方法を、本番HTTPと交換できる形にする。
    @do  # 応答の返却はResumeで行い、呼び出し元のyieldを再開する。
    def fixed_http(effect, k):  # HTTPクライアントを作らず、依頼そのものを受け取る。
        if not isinstance(effect, HttpRequest):  # HTTP以外の依頼には介入しない。
            return (yield Pass(effect, k))  # MemoやAwaitは外側の担当へ通す。
        calls.append(effect.url)  # この行へ到達した回数が、保存ミス後の取得回数になる。
        response = HttpResponse(  # 本番ハンドラと同じ公開応答型を、固定値で組み立てる。
            status=200,  # 成功としてraise_for_statusを通過させる。
            headers={},  # この本文取得例では応答ヘッダを使わない。
            content="記録した本文".encode(),  # UTF-8の生の応答本体も本文と一致させる。
            text="記録した本文",  # fetch_textの最終的な戻り値になる文字列を指定する。
            url=effect.url,  # どの依頼に対する応答かを保持する。
            elapsed_seconds=0.0,  # 通信していないため、計測時間は固定値にする。
        )
        return (yield Resume(k, response))  # 保存処理へ応答を戻し、最後にfetch_textを再開する。

    def execute(program):  # テストのrun境界で、共通の待機・ログ・スケジューラを設置する。
        wrapped = slog_discard_handler(program)  # メモ化の診断ログを受け取る。
        wrapped = await_handler()(wrapped)  # SQLiteが発行するAwaitを処理する。
        return run(scheduled(wrapped))  # 待機を含むProgramを最後まで進め、本文を返す。

    url = "https://example.invalid/article"  # 実在サービスへ接続しない例示専用URLを使う。
    with TemporaryDirectory() as directory:  # この検証が所有する保存領域だけを後で片付ける。
        database = Path(directory) / "http.sqlite"  # 別のハンドラを作っても同じ保存先を参照する。

        first = make_memo_rewriter(HttpRequest)(fetch_text(url))  # まず依頼の保存済み判定を挟む。
        first = sqlite_memo_handler(database)(first)  # Memoへの照会と保存をSQLiteに割り当てる。
        first = fixed_http(first)  # 保存ミス時に委譲されるHTTPを、固定応答で取得する。
        assert execute(first) == "記録した本文"  # 初回は取得後に保存し、本文を返す。
        assert calls == [url]  # 取得担当へ到達したのは初回の一度だけ。

        second = make_memo_rewriter(HttpRequest)(fetch_text(url))  # 新しいProgramでも同じ依頼を出す。
        second = sqlite_memo_handler(database)(second)  # 新しいハンドラから同じSQLiteを開く。
        second = reject_http(second)  # HTTPへ到達すると必ず失敗する取得担当へ交換する。
        assert execute(second) == "記録した本文"  # 別のrunでも保存済み応答だけで成功する。
        assert calls == [url]  # 保存ヒット時にはHTTP取得を追加実行しない。

        missing = make_memo_rewriter(HttpRequest)(fetch_text(url + "/missing"))  # URL変更は保存ミス。
        missing = sqlite_memo_handler(database)(missing)  # 同じ保存先に変更後の依頼はまだない。
        missing = reject_http(missing)  # 未保存時に実取得へ進むことを明示的に拒否する。
        with pytest.raises(LookupError) as failure:  # 未保存時に狙った例外が必ず出ることを検査する。
            execute(missing)  # 保存にない依頼はreject_httpまで届くため、失敗するはず。
        assert failure.value.args == (url + "/missing",)  # 不一致のURLがエラーに残ることも確かめる。

        fresh = make_memo_rewriter(HttpRequest)(fetch_text(url + "/missing"))  # 同じ未保存依頼を作る。
        fresh = sqlite_memo_handler(database)(fresh)  # 保存ミスの判定方法と保存先は変えない。
        fresh = fixed_http(fresh)  # 今度は未保存時の取得を許すハンドラへ交換する。
        assert execute(fresh) == "記録した本文"  # HTTP担当が応答を返せば保存して成功する。
        assert calls == [url, url + "/missing"]  # 各URLの初回だけが取得担当へ到達する。


if __name__ == "__main__":  # 直接実行したときだけ、このオフライン検証を開始する。
    verify()  # 保存ヒット・別runでの再利用・保存ミス時の交換方針を検査する。
    print("HTTPとMemoの合成・SQLite再利用・未保存時の拒否: OK")  # 全assert通過後だけ成功を表示。

```

[実行可能な完全な例](examples/http_replay.py)をリポジトリに保存しています。

期待する結果は、最初のURLで取得一回、保存済みURLの別`run`で取得ゼロ、未保存URLでは拒否、取得担当を交換すると未保存URLの取得一回です。検証は最後に`HTTPとMemoの合成・SQLite再利用・未保存時の拒否: OK`を表示します。未保存を拒否する検査では、捕捉する`LookupError`のdoeffトレースも表示されます。

ここでは同じプロセス内で新しいProgramと新しいSQLiteハンドラを作り、別の`run`から保存値を再利用しています。プロセスの強制終了と復旧を試す例は、[永続実行の記事](doeff-durable.md)に分けています。

## 本番では取得担当を交換する

本番の組み立てでは、上の`fixed_http(first)`に相当する場所を標準HTTPハンドラへ置き換えます。以下は構成例であり、この検証では通信を実行していません。

```python
from doeff_core_effects.http_handlers import http_production_handler  # 標準のHTTP取得担当を使う。

# firstは、上の例でメモ化とSQLiteを取り付けた直後のProgramを指す。
first = http_production_handler()(first)  # 取得担当を標準実装にする。実行時に通信する。
```

保存先を変える場合は、取得担当ではなくMemoハンドラを交換します。メモリ・SQLite・複数層の合成は、[メモ化の記事](doeff-memo.md)で扱います。同じ考え方で、[他のハンドラとの合成](doeff-handlers.md)もできます。

## 再利用の範囲と、現在の実装の限界

この例で囲んでいるのはGETの本文取得だけです。`make_memo_rewriter(HttpRequest)`は、対象範囲のHTTP依頼すべてを型で選ぶため、POSTの再利用可否まで判断してくれるわけではありません。保存する範囲とキーに含める条件は、アプリケーションで決めます。

標準のキーは依頼内容から作られます。保存されるのはHTTPの応答値であり、操作の実行順序やPythonの停止位置ではありません。また、HTTPハンドラが400や500の応答を**値として返せば、その応答も保存対象**になります。後続の`raise_for_status()`が失敗したからといって、自動で保存が取り消されるわけではありません。成功応答だけを残す必要があれば、その判断も取得結果を保存へ渡す前のハンドラに持たせます。

保存ミス時の標準動作は、外側へ依頼を出し直して取得することです。再生専用にしたいときは、上の`reject_http`のように未保存時の取得を明示的に拒否します。また、Memoの保存ハンドラを付け忘れた場合、現在の`make_memo_rewriter`は取得へ進み、保存できなくても結果を返します。保存必須の用途では、この例のように別の実行で再利用できることまで確認します。

既存の`http_fixture_handler`も公開APIとして存在します。ただし`mode="record"`では内部でHTTP取得担当を組み立てるため、取得と保存を独立に交換する説明の主役には向きません。この記事ではHTTPとMemoを別のハンドラとして合成しています。既存APIの実装自体は変更していません。

SQLiteの保存値は現在pickleで保持されます。この例では自分が作った一時データだけを読みます。保存値を共有する場合は、機密情報を含む応答の扱いと、読み込むデータの信頼範囲も合わせて設計します。

## 処理の流れ

![保存ヒットはHTTPを通らず、保存ミスだけ取得担当へ進む](/images/zenn-use-cases-v0/generated/replay-flow.png)

保存ミス時の取得・拒否をHTTPハンドラで切り替え、得た応答の保存をMemoハンドラへ任せます。

## 実装・実例を読む

- [HttpRequestとHttpResponseの定義](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/http_effects.hy)
- [メモ化への変換と保存ミス時の委譲](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/memo_handlers.py)
- [Memo保存層のハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/_memo_handlers_impl.hy)
- [SQLiteへの保存実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/storage/sqlite.py)

この草稿は上記の開発版を参照しています。検証では外部サービスへの接続を行っていません。

[メイン記事へ戻る](doeff-main.md)
