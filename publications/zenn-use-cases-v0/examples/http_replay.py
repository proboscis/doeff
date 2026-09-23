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
