"""エージェント操作だけをシナリオハンドラへ差し替え、業務の分岐を検証する。"""

from pathlib import Path  # 実行場所をAgentSpecへ渡す。テストではディレクトリを使わない。

from doeff_agents import AgentType  # 起動するCLIの種類を公開の列挙型で指定する。
from doeff_agents.effects import (  # セッションの起動・待機・追加指示・終了操作を使う。
    AgentSpec,  # 作業場所、依頼、結果スキーマをまとめる仕様。
    AwaitResult,  # 結果と状態をAwaitOutcomeとして受け取る操作。
    AwaitStatus,  # 完了、入力待ち、タイムアウトを区別する列挙型。
    FollowUp,  # 続行可能なセッションへ追加指示を送る操作。
    LaunchSession,  # 実行IDから決まるセッションを起動または再取得する操作。
    ReleaseSession,  # 利用を終えたセッションのハンドラ資源を解放する操作。
    StopSession,  # 完了できないセッションを停止する操作。
)
from doeff_agents.handlers.testing import (  # 実CLIの代わりに指定した応答列を返す境界。
    ScenarioAgentHandler,  # アプリケーションの関数を変更せずエージェント操作を置き換える。
    ScenarioStep,  # 結果・入力待ち・失敗をテストデータとして記述する。
)
from doeff_core_effects.scheduler import Spawn, Wait, scheduled  # 子タスクの開始と結果待ち。
from jsonschema import validate  # テストデータだけを検査する。本番の検証処理の代用ではない。

from doeff import do, run  # @doで処理を合成し、runは下のオフライン検証でのみ使う。

RESULT_SCHEMA = {  # 本番の結果境界へ渡すJSON Schema。本文要約と可否を必須にする。
    "type": "object",  # 結果は辞書形式を期待する。
    "required": ["summary", "ok"],  # 要約と可否の欠落を許さない。
    "properties": {"summary": {"type": "string"}, "ok": {"type": "boolean"}},  # 値の型。
    "additionalProperties": False,  # この例で使わない項目が混ざることを禁止する。
}


@do  # 依頼仕様の準備も、呼び出し元からyieldで合成できるようにする。
def review_spec(node: str, prompt: str):  # 作業名と依頼文から1件分の仕様を返す。
    return AgentSpec(  # 戻り値は仕様だけで、ここではエージェントを起動しない。
        run_id="article-example",  # このオフライン例の実行を識別する。
        node_id=node,  # languageとexamplesを別セッションとして識別する。
        attempt=0,  # 最初の試行としてセッションIDへ含める。
        agent_type=AgentType.CODEX,  # 本番ハンドラならCodexを選ぶ。
        work_dir=Path("/example/docs"),  # テスト用の架空パス。本番では存在する作業場所を使う。
        prompt=prompt,  # このセッションに確認してほしい内容を渡す。
        result_schema=RESULT_SCHEMA,  # 本番のハンドラへ結果の契約を伝える。
    )


@do  # 起動から結果判断までを1つのProgramとして呼び出せるようにする。
def checked_review(spec: AgentSpec, timeout_seconds: float = 60):  # 1件のレビュー結果を返す。
    session = yield LaunchSession(spec)  # ハンドラが起動または再取得し、SessionHandleを返す。
    outcome = yield AwaitResult(session, timeout_seconds=timeout_seconds)  # 状態と結果を待つ。
    if outcome.status is AwaitStatus.AWAITING_INPUT and outcome.continuable:  # 追加指示が可能か判定。
        yield FollowUp(session, "与えた文書だけを対象に確認してください")  # 対象を補足して続行を促す。
        outcome = yield AwaitResult(session, timeout_seconds=timeout_seconds)  # 補足後の結果を待つ。
    if outcome.status is not AwaitStatus.EXITED:  # 入力待ちが続く場合とタイムアウトは未完了とする。
        yield StopSession(session, reason="レビューを完了できませんでした")  # 後続へ進まず停止する。
        yield ReleaseSession(session)  # 停止したセッションに対応する資源を解放する。
        raise RuntimeError(f"レビュー未完了: {outcome.status.value}")  # 呼び出し元へ失敗を伝える。
    if (  # 終了していても、結果の欠落・検証失敗・異常終了があれば利用しない。
        outcome.result is None  # 結果なしを成功扱いしない。
        or outcome.validation_error is not None  # ハンドラが報告した検証エラーを確認する。
        or outcome.exit_code not in (None, 0)  # 終了コードが提供された場合は0だけを認める。
    ):
        yield ReleaseSession(session)  # 終了済みの失敗でも資源を解放する。
        raise RuntimeError("レビュー結果を利用できません")  # 利用可能な結果がないことを伝える。
    result = outcome.result  # この境界から返った結果を後続へ渡す。ファイルを読みに行かない。
    yield ReleaseSession(session)  # 正常終了でも資源の利用を終える。
    return result  # summaryとokを持つ辞書を返す。ok=Falseも有効なレビュー結果。


@do  # 2件のレビューと最終判断を同じProgramに合成する。
def review_document(timeout_seconds: float = 60):  # 文章とコードの両方を確認する。
    language_spec = yield review_spec("language", "文書の読みやすさを確認")  # 文章用の仕様を取得。
    example_spec = yield review_spec("examples", "文書のコード例を確認")  # コード用の仕様を取得。
    language = yield Spawn(checked_review(language_spec, timeout_seconds))  # 文章の子タスクを開始。
    examples = yield Spawn(checked_review(example_spec, timeout_seconds))  # コードの子タスクも開始。
    language_result = yield Wait(language)  # 文章の結果が返るまで、この計算を待機させる。
    example_result = yield Wait(examples)  # コードの結果も取得する。起動は上の行で済んでいる。
    return {  # 両方の返り値から公開用の判断材料を組み立てる。
        "ready": language_result["ok"] and example_result["ok"],  # 両方Trueの場合だけ準備完了。
        "summaries": [language_result["summary"], example_result["summary"]],  # 判断理由を残す。
    }


p_review = review_document()  # 未実行のProgramを保持する。外側で実行ハンドラを選べる。


def verify() -> None:  # runを許すオフライン検証。CLI起動や外部への送信は行わない。
    language_result = {"summary": "文章を確認", "ok": True}  # 文章は問題なしという応答を用意。
    example_result = {"summary": "コードを確認", "ok": False}  # コードには問題がある応答を用意。
    validate(language_result, RESULT_SCHEMA)  # シナリオの正常系データが宣言した型に合うか確認。
    validate(example_result, RESULT_SCHEMA)  # ok=Falseもスキーマ上は正しい結果だと確認。
    testing = ScenarioAgentHandler(scripts={  # 実セッション操作の代わりに応答列を設置する。
        "article-example-language-0": [ScenarioStep.success(language_result)],  # 文章は即時に完了。
        "article-example-examples-0": [  # コード側だけ入力待ちから完了へ進む。
            ScenarioStep.awaiting_input("対象の確認が必要"),  # 最初のAwaitResultでは追加指示が必要。
            ScenarioStep.success(example_result),  # FollowUp後のAwaitResultで有効な結果を返す。
        ],
    })
    result = run(scheduled(testing.wrap(review_document(timeout_seconds=0))))  # 応答列だけを実行。
    assert result == {"ready": False, "summaries": ["文章を確認", "コードを確認"]}  # 両方Trueではない。
    assert len(testing.follow_up_messages("article-example-examples-0")) == 1  # 追加指示は1回だけ。
    assert len(testing.released_sessions) == 2  # 両セッションとも利用後に解放された。

    timed_out = ScenarioAgentHandler(scripts={  # 結果が期限内に得られないケースを用意する。
        "article-example-language-0": [ScenarioStep.timeout()],  # AwaitResultへタイムアウトを返す。
    })
    spec = run(review_spec("language", "文章を確認"))  # 外部操作のない仕様作成だけを実行する。
    try:  # タイムアウトが例外として呼び出し元へ届くか確認する。
        run(timed_out.wrap(checked_review(spec, timeout_seconds=0)))  # 即時の応答なのでscheduler不要。
    except RuntimeError as error:  # 期待する失敗の理由まで確認する。
        if "レビュー未完了" not in str(error):  # 別の実行エラーを成功扱いしない。
            raise AssertionError("想定外の例外です") from error  # 元の失敗理由を保持する。
    else:  # 例外なしで戻った場合は検証失敗とする。
        raise AssertionError("タイムアウトを成功にしてはいけません")  # 未完了結果の流出を検出する。
    assert len(timed_out.stopped_sessions) == 1  # 未完了のセッションへ停止が1回届いた。
    assert len(timed_out.released_sessions) == 1  # 停止後の資源解放も1回届いた。

    for step in (  # 欠落と検証エラーを、結果を使えない別々のケースとして確認する。
        ScenarioStep.absent(),  # 終了していても結果がなければ失敗する。
        ScenarioStep.terminal_invalid(validation_error="型が不一致"),  # 検証エラーでも失敗する。
    ):
        invalid = ScenarioAgentHandler(scripts={spec.session_id: [step]})  # 1件分の失敗応答を設置。
        try:  # 結果境界が報告した異常を見落とさないことを確認する。
            run(invalid.wrap(checked_review(spec, timeout_seconds=0)))  # 本物のエージェントは使わない。
        except RuntimeError as error:  # 未完了とは別の、結果利用不可エラーを期待する。
            if "レビュー結果を利用できません" not in str(error):  # 検証結果の扱いを確認する。
                raise AssertionError("想定外の例外です") from error  # 別の原因なら検証を失敗させる。
        else:  # 無効な結果を返してしまった場合は検証を失敗させる。
            raise AssertionError("無効な結果を成功にしてはいけません")  # 誤った後続処理を防ぐ。
        assert invalid.released_sessions == [spec.session_id]  # 失敗しても明示した解放が行われる。


if __name__ == "__main__":  # 直接実行したときだけオフライン検証を走らせる。
    verify()  # 並行処理・入力待ち・未完了・無効結果・解放を確認する。
    print("シナリオによる並行レビュー・追加指示・結果判定・停止と解放: OK")  # 成功を表示する。
