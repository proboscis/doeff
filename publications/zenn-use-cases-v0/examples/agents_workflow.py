"""エージェント操作だけをハンドラへ差し替え、業務の分岐を検証する(記事 doeff-agents.md の完全な例)。"""

from dataclasses import replace  # 仕様の一部だけを替えた写しを作る。
from pathlib import Path  # 実行場所をAgentSpecへ渡す。テストではディレクトリを使わない。

from doeff_agents import AgentType  # 起動するCLIの種類を公開の列挙型で指定する。
from doeff_agents.effects import (  # セッションの依頼と、ハンドラが返す値の型を使う。
    AgentSpec,  # 作業場所、依頼、結果スキーマをまとめる仕様。
    AwaitOutcome,  # 結果待ちの答え。終わり方と結果を一緒に持つ。
    AwaitResult,  # 結果と状態をAwaitOutcomeとして受け取る操作。
    AwaitResultEffect,  # AwaitResultが作る依頼の型。ハンドラが見分けに使う。
    AwaitStatus,  # 完了、入力待ち、タイムアウトを区別する列挙型。
    FollowUp,  # 続行可能なセッションへ追加指示を送る操作。
    LaunchSession,  # 実行IDから決まるセッションを起動または再取得する操作。
    LaunchSessionEffect,  # LaunchSessionが作る依頼の型。
    ReleaseSession,  # 利用を終えたセッションのハンドラ資源を解放する操作。
    ReleaseSessionEffect,  # ReleaseSessionが作る依頼の型。
    SessionHandle,  # セッションを指すハンドル。中身はセッションIDだけ。
    StopSession,  # 完了できないセッションを停止する操作。
)
from doeff_agents.handlers import agent_effectful_handler  # 本物のセッションを扱う公開のハンドラ。
from doeff_agents.handlers.testing import (  # 実CLIの代わりに指定した応答列を返す境界。
    ScenarioAgentHandler,  # アプリケーションの関数を変更せずエージェント操作を置き換える。
    ScenarioStep,  # 結果・入力待ち・失敗をテストデータとして記述する。
)
from doeff_agents.session_backend import SessionBackend, default_session_backend  # 端末への接続先。
from doeff_core_effects import Ask, reader  # 値を頼む依頼と、それに答えるreader。
from doeff_core_effects.handlers import await_handler, slog_discard_handler  # 非同期の待ちとログ。
from doeff_core_effects.scheduler import Spawn, Wait, scheduled  # 子タスクの開始と結果待ち。
from doeff_time.handlers.async_time import async_time_handler  # 時間の依頼を実時間で答える。
from jsonschema import validate  # テストデータだけを検査する。本番の検証処理の代用ではない。

from doeff import (  # 処理・ハンドラ・道具の定義と実行。
    McpToolDef,  # @doの関数をMCPの道具として公開する定義。
    Pass,  # 担当しない依頼を外側のハンドラへ渡す。
    Resume,  # 止まった処理へ値を返して続きを動かす。
    do,  # 依頼を出す処理を定義する。
    handler,  # Programに取り付けられるハンドラにする。
    run,  # 下のオフライン検証でだけProgramを動かす。
    with_handlers,  # Programへハンドラを付ける。
)


@do  # 呼んだ時点では起動せず、実行時にハンドラへ依頼を渡す。
def delegate_task(spec: AgentSpec):  # 依頼文と結果の形を持つ仕様を受け取る。
    session = yield LaunchSession(spec)  # 起動を頼み、セッションのハンドルを受け取る。
    outcome = yield AwaitResult(session)  # 結果を待ち、終わり方と結果を受け取る。
    yield ReleaseSession(session)  # 使い終えたセッションの解放を頼む。
    return outcome  # 成否は呼び出し側が確かめる。成功と決めつけない。


RESULT_SCHEMA = {  # 欲しい結果の形をJSON Schemaで決める。
    "type": "object",  # 結果は辞書。
    "required": ["summary", "ok"],  # 要約と可否を必ず持つ。
    "properties": {"summary": {"type": "string"}, "ok": {"type": "boolean"}},  # 値の型。
    "additionalProperties": False,  # ほかの項目は許さない。
}


@do  # 仕様の準備も、yieldで合成できる処理にする。
def review_spec(node: str, prompt: str):  # 作業名と依頼文から1件分の仕様を作る。
    return AgentSpec(  # 仕様を返すだけで、エージェントはまだ起こさない。
        run_id="article-example", node_id=node, attempt=0,  # この3つからセッションIDが決まる。
        agent_type=AgentType.CODEX, work_dir=Path("/example/docs"),  # 使うCLIと架空の作業場所。
        prompt=prompt, result_schema=RESULT_SCHEMA,  # 依頼文と、結果の形の約束。
    )


@handler  # Programに取り付けられるハンドラにする。
def fixed_agent(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
    if isinstance(effect, LaunchSessionEffect):  # 起動の依頼なら、
        return (yield Resume(k, SessionHandle(effect.spec.session_id)))  # 起こさずハンドルだけ返す。
    if isinstance(effect, AwaitResultEffect):  # 結果待ちの依頼なら、
        outcome = AwaitOutcome(AwaitStatus.EXITED, result={"summary": "読みやすい", "ok": True})  # 決めた答え。
        return (yield Resume(k, outcome))  # 待たずに、その答えで続きを動かす。
    if isinstance(effect, ReleaseSessionEffect):  # 解放の依頼なら、
        return (yield Resume(k, None))  # 解放する物が無いのでNoneを返す。
    return (yield Pass(effect, k))  # 担当しない依頼は外側のハンドラへ渡す。


@do  # 仕様の準備と依頼を1つの処理にまとめる。
def first_review():  # 冒頭のdelegate_taskを1件だけ動かす。
    spec = yield review_spec("language", "文書の読みやすさを確認")  # 仕様を受け取る。
    return (yield delegate_task(spec))  # 冒頭の処理をそのまま呼ぶ。


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
    yield ReleaseSession(session)  # 正常終了でも資源の利用を終える。
    return outcome.result  # summaryとokを持つ辞書を返す。ok=Falseも有効なレビュー結果。


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


@do  # 道具の中身も、yieldで依頼を出せる処理にする。
def read_document():  # エージェントが引数なしで呼ぶ道具。
    return (yield Ask("document_text"))  # 文書の中身を外側に頼む。


read_tool = McpToolDef(  # 名前・説明・引数・中身をまとめる。ここではサーバーを起こさない。
    name="read_document", description="レビューする文書を読む", params=(), handler=read_document,  # 定義。
)


@do  # 本番の組み立ても、yieldされるまで動かない。
def live_review(work_dir: Path, document_text: str):  # 実在する作業場所と文書を受け取る。
    spec = yield review_spec("document", "read_documentで文書を読み、確認してください")  # 仕様を作る。
    spec = replace(spec, work_dir=work_dir, mcp_tools=(*spec.mcp_tools, read_tool))  # 場所と道具を足す。
    program = agent_effectful_handler()(checked_review(spec))  # エージェントの依頼を本物へ繋ぐ。
    program = reader(env={  # 内側から来るAskに答える。
        SessionBackend: default_session_backend(),  # 端末への接続先を渡す。
        "document_text": document_text,  # read_documentへ返す文書。
    })(program)  # 内側のProgramへ環境を付ける。
    program = async_time_handler()(program)  # 待ちの間の時間の依頼を実時間で答える。
    program = await_handler()(slog_discard_handler(program))  # 非同期の待ちとログに答える。
    return (yield scheduled(program))  # スケジューラを付けて動かし、結果を返す。


def verify() -> None:  # runを許すオフライン検証。CLI起動や外部への送信は行わない。
    outcome = run(with_handlers([fixed_agent], first_review()))  # 自作のハンドラを付けて動かす。
    assert outcome.status is AwaitStatus.EXITED  # 終わったという答えが届いた。
    assert outcome.result == {"summary": "読みやすい", "ok": True}  # 決めた結果がそのまま届いた。

    testing = ScenarioAgentHandler(scripts={  # セッションIDごとに、答えの列を決める。
        "article-example-language-0": [ScenarioStep.success({"summary": "文章を確認", "ok": True})],  # すぐ終わる。
        "article-example-examples-0": [  # こちらは入力待ちを挟む。
            ScenarioStep.awaiting_input("対象の確認が必要"),  # 1回目のAwaitResultは入力待ち。
            ScenarioStep.success({"summary": "コードを確認", "ok": False}),  # FollowUpの後に結果を返す。
        ],
    })
    result = run(scheduled(testing.wrap(review_document(timeout_seconds=0))))  # シナリオの答えで動かす。
    assert result == {"ready": False, "summaries": ["文章を確認", "コードを確認"]}  # 片方がok=False。
    assert len(testing.follow_up_messages("article-example-examples-0")) == 1  # 追加の指示は1回だけ。
    assert len(testing.released_sessions) == 2  # 2つのセッションとも解放された。

    for payload in ({"summary": "文章を確認", "ok": True}, {"summary": "コードを確認", "ok": False}):  # シナリオの結果。
        validate(payload, RESULT_SCHEMA)  # シナリオのデータが宣言した形に合うことだけを確かめる。

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

    tool_text = run(reader(env={"document_text": "本文"})(read_document()))  # 道具だけをreaderで動かす。
    assert tool_text == "本文"  # 道具は外側が渡した文書を返す。
    assert read_tool.name == "read_document"  # エージェントに見える道具の名前。
    assert read_tool.params == ()  # 引数なしで呼べる道具として定義した。


if __name__ == "__main__":  # 直接実行したときだけオフライン検証を走らせる。
    verify()  # 自作ハンドラ・並行処理・入力待ち・未完了・無効結果・解放・道具を確認する。
    print("自作ハンドラ・シナリオによる並行レビュー・追加指示・結果判定・停止と解放・道具: OK")  # 成功を表示する。
