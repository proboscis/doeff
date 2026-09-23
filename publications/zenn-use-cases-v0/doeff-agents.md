---
title: "エージェントの起動・結果待ち・共同作業を、doeffの処理として書く"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

エージェントを動かす仕事には、起動だけでなく、結果の形式、入力待ち、終了、タイムアウト、その後の判断があります。`doeff-agents`では、これらを`@do`の計算として組み立てます。

```python
from doeff import do  # 操作の列をProgramとして呼び出せるようにする。
from doeff_agents.effects import AgentSpec, AwaitResult, LaunchSession, ReleaseSession  # セッション操作。

@do  # 呼び出した時点では起動せず、実行時にハンドラへ操作を渡す。
def delegate_task(spec: AgentSpec):  # 依頼文と結果スキーマを持つ仕様を受け取る。
    session = yield LaunchSession(spec)  # 起動・再取得の結果としてSessionHandleを受け取る。
    outcome = yield AwaitResult(session)  # 結果だけでなく終了状態もAwaitOutcomeで受け取る。
    yield ReleaseSession(session)  # この例で利用したセッション資源を解放する。
    return outcome  # 成否を判定するための状態ごと返す。成功結果と決めつけない。
```

この最初の例は操作の形だけを示します。アプリケーションで結果を使うには、下の`checked_review`のように状態と検証エラーを判定します。処理本体は同じまま、実エージェントへ接続するハンドラと、決めた応答を返すテストハンドラを選べます。

![エージェントの操作を、ハンドラで解釈する](/images/zenn-use-cases-v0/generated/agents-concept.png)

LaunchSessionとAwaitResultの列は共通です。選んだハンドラが、実セッションまたはシナリオの応答として解釈します。

## 結果の境界を決める

`AgentSpec.result_schema`に、欲しい結果のJSON Schemaを指定します。`AwaitResult`が返す`AwaitOutcome`には`status`、`result`、`validation_error`、`exit_code`、`continuable`があります。

- `status`は`EXITED`、`AWAITING_INPUT`、`TIMED_OUT`の3種類です。スキーマ検証エラーは別の状態ではなく、`validation_error`で報告されます。
- `result`が利用者の結果境界です。エージェントへ結果ファイルを書かせて探索する処理は不要です。
- `continuable`が偽の結果へ追加指示を送っても、終了済みの仕事を継続できるとは限りません。

結果の取得、スキーマ検査、契約に応じた再取得・再試行は、doeff-agents側の実行境界が担当します。次の例は、そこから返った状態に対して、追加指示を一度だけ送る、未完了なら停止する、有効な結果だけを利用する、というアプリケーション側の方針を記述します。

## 2件のレビューを並行に進め、結果を合成する

文章とコード例を別々に確認します。`review_spec`も`checked_review`も`@do`なので、補助関数を`async def`へ移したり、ループ全体を`Await`で包んだりする必要はありません。結果の待機は`AwaitResult`、子タスクの待機は`Wait`として表現します。

```python
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

```

`Spawn`を2回発行してから`Wait`するため、2件を開始してから結果を待つ構成になります。コードの確認結果が`ok=False`でも、形式が正しいレビュー結果として受け取れます。実行成功と「文書を出せる」という業務上の判断は別です。

この例は、明示した状態分岐で停止・解放する処理を示しています。任意の外部例外や親タスクのキャンセル時まで含む、汎用的な資源管理や兄弟タスクの終了保証を実装したものではありません。また、`run_id`はテスト用に固定しています。本番の別の仕事では実行IDも区別し、`work_dir`には実在する作業場所を指定します。

## エージェントの操作だけをテストハンドラへ差し替える

`ScenarioAgentHandler.wrap`が、上の同じProgramにハンドラを付けます。関数をmockに置き換えるのではなく、起動・結果待ち・停止などのエフェクトに対する解釈を差し替えます。

**ここで実物として動くのは、`@do`の処理、スケジューラ、結果を見て分岐するコードです。エージェントの起動と応答はシナリオです。** 現行の`ScenarioAgentHandler`は、指定されたpayloadをそのまま返し、JSON Schema検証や本番の再試行処理を実行しません。テストデータの`validate`は、この例のデータに誤りがないことだけを確認します。

```python
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


verify()  # 上の検証を実行する。実CLI、外部API、MCPサーバーは起動しない。
```

期待する結果は`ready=False`、要約2件、追加指示1回、正常系の解放2回です。別ケースではタイムアウト時の停止・解放と、結果の欠落・検証エラーの拒否を確認します。

待機時間を0にしているのは、シナリオの1応答ごとに業務上の分岐を確認するためです。本番の待機では、ハンドラが一定の間隔で観測し、一時的な入力待ちを内部で待ち続ける場合があります。このテストは時間の経過や実プロセスの動作を検証しません。

[完全な実行例](examples/agents_workflow.py)に同じコードがあります。

## MCPの道具も、同じハンドラの環境へ接続する

MCPツールの処理も`@do`で書けます。以下の道具は`Ask`で文書を要求するため、文書の供給元を呼び出し側で選べます。`ObserveAgentSession`はセッションIDから観測用の情報を得る操作で、検証済みの作業結果を受け取る`AwaitResult`とは用途が異なります。

```python
from dataclasses import replace  # 既存の仕様を変更せず、道具を追加した仕様を作る。
from doeff.mcp import McpToolDef  # @doの関数を公開するMCPツールの定義を使う。
from doeff_core_effects import Ask  # 文書の供給元を外側の環境に委ねる。
from doeff_agents.effects import ObserveAgentSession  # 観測スナップショットを要求する操作。

@do  # 道具の呼び出しも呼び出し元のVM内で実行できるProgramにする。
def read_document():  # エージェントから引数なしで文書を読むための道具。
    return (yield Ask("document_text"))  # readerハンドラが提供する文書文字列を返す。

read_tool = McpToolDef(  # メタデータと@do関数をまとめる。ここではサーバーを起動しない。
    name="read_document",  # エージェントに公開する道具名。
    description="レビュー対象の文書を読む",  # 道具を選ぶための説明。
    params=(),  # 入力パラメータは不要。文書はAskで取得する。
    handler=read_document,  # 呼び出し時にProgramを返す関数を登録する。
)

@do  # 仕様の更新とレビューを同じ処理として合成する。
def review_with_tool(spec: AgentSpec):  # 読み取り道具付きで1件を確認する。
    tool_spec = replace(spec, mcp_tools=(*spec.mcp_tools, read_tool))  # 既存の道具へ追加する。
    return (yield checked_review(tool_spec))  # 結果は引き続きAwaitOutcome.resultの境界で受け取る。

@do  # 観測も効果として要求し、観測方法をハンドラに委ねる。
def observe_session(session_id: str):  # SessionHandleそのものではなくID文字列を受け取る。
    snapshot = yield ObserveAgentSession(session_id=session_id, lines=40)  # 状態と出力の観測を依頼。
    return snapshot  # AgentSessionSnapshotを返す。これを作業の構造化結果として扱わない。
```

## 本番へ接続するハンドラを外側に置く

実セッションには公開の`agent_effectful_handler()`を使います。バックエンドは`SessionBackend`という公開の境界を通して指定します。アプリケーションへ端末管理の内部クラスや結果取得の仕組みを持ち込みません。

```python
from doeff_core_effects import reader  # Askへ文書とセッションバックエンドを提供する。
from doeff_core_effects.handlers import await_handler, slog_discard_handler  # SDK待機とログを解釈する。
from doeff_agents.handlers import agent_effectful_handler  # 実セッション用の公開ハンドラを使う。
from doeff_agents.session_backend import SessionBackend, default_session_backend  # 公開の接続境界。
from doeff_time.handlers.async_time import async_time_handler  # 時間効果を実時間で処理する。

@do  # 本番の接続準備も、呼び出してyieldするまで実行しない。
def live_review(work_dir: Path, document_text: str):  # 実在する作業場所と文書を受け取る。
    spec = yield review_spec("document", "read_documentで文書を読み、確認してください")  # 仕様を準備。
    spec = replace(spec, work_dir=work_dir)  # テスト用の架空パスを、呼び出し側の実在パスへ置き換える。
    program = agent_effectful_handler()(review_with_tool(spec))  # 起動・結果待ちを本番へ接続する。
    program = reader(env={  # 内側のハンドラと道具が出すAskをここで解釈する。
        SessionBackend: default_session_backend(),  # 標準バックエンドを公開関数から取得する。
        "document_text": document_text,  # read_documentへ返す文章を指定する。
    })(program)  # 内側のProgramへ環境を適用し、Askの返り値を決める。
    program = async_time_handler()(program)  # 内部で必要になる時間効果を実時間へ接続する。
    program = await_handler()(slog_discard_handler(program))  # 必要な非同期待機とログを処理する。
    return (yield scheduled(program))  # スケジューラ込みの計算として結果を呼び出し元へ返す。
```

この定義のimportとProgram構築まで確認しています。`live_review`の実行、実エージェントの起動・認証、MCP接続は未検証です。認証はClaude Code/Codex自身の認証状態を使い、LLMプロバイダのAPIキーを起動仕様へ流用しません。

## 作業環境の分離や統合まで扱う

`doeff-conductor`は、issue、論理的な作業環境、エージェントへの仕事、作業結果の統合を組み合わせる層です。次は2つの作業環境でレビューし、双方が合格した場合に作業を統合する定義です。`CreateIssue`はConductorの管理するissueの作成であり、GitHubへの送信と同義ではありません。

```python
from doeff_conductor.effects import (  # 開発作業向けの操作を使う。
    Agent,  # 作業を依頼し、スキーマに合う結果を受け取る操作。
    AgentTask,  # 作業環境と結果スキーマを含む仕事の仕様。
    CreateIssue,  # Conductorのissueを用意する操作。
    CreateWorkspace,  # 同じIDの論理作業環境を作成または再取得する操作。
    MergeWorkspaces,  # 複数環境を統合し、競合情報を含む結果を返す操作。
)

@do  # 上位の開発ワークフローもyieldで合成する。
def conductor_review(run_id: str):  # 別の仕事を区別する実行IDを受け取る。
    issue = yield CreateIssue(title="説明書のレビュー", body="文章とコード例を確認する")  # 課題を用意。
    language_workspace = yield CreateWorkspace(workspace_id=f"{run_id}-language", issue=issue)  # 文章用。
    code_workspace = yield CreateWorkspace(workspace_id=f"{run_id}-code", issue=issue)  # コード用。
    schema = {  # この上位ワークフローが欲しい結果の形式を宣言する。
        "type": "object", "required": ["ok"],  # okを持つ辞書を必須とする。
        "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False,  # 可否は真偽値。
    }
    language = yield Spawn(Agent(AgentTask(  # 文章側を子タスクとして開始する。
        run_id=run_id, node_id="language", attempt=0, env=language_workspace,  # 実行と作業場所を指定。
        prompt="文章を改善して、結果を返してください", result_schema=schema,  # 依頼と結果の契約。
        verification_class="unit", agent_type="codex",  # 検証区分と利用するCLIを指定。
    )))
    code = yield Spawn(Agent(AgentTask(  # 文章側の完了を待つ前にコード側も開始する。
        run_id=run_id, node_id="code", attempt=0, env=code_workspace,  # 独立した作業環境を指定。
        prompt="コード例を検証して、結果を返してください", result_schema=schema,  # コードへの依頼。
        verification_class="unit", agent_type="codex",  # 同じ結果契約で別の仕事を実行する。
    )))
    language_result = yield Wait(language)  # 文章側の検証済み結果を受け取る。
    code_result = yield Wait(code)  # コード側の検証済み結果も受け取る。
    if not (language_result["ok"] and code_result["ok"]):  # 一方でも不合格なら統合しない。
        return {"ready": False}  # 統合できる状態でないと呼び出し元へ伝える。
    merged = yield MergeWorkspaces(  # 両方の作業を別の統合先へまとめる。
        workspace_id=f"{run_id}-combined", workspaces=(language_workspace, code_workspace),  # 統合対象。
    )
    if not merged.merged or merged.workspace is None:  # 競合や統合先の欠落を成功扱いしない。
        raise RuntimeError(merged.message)  # ハンドラの報告した統合失敗を伝える。
    return {"ready": True, "workspace": merged.workspace}  # 統合した作業環境を後続へ返す。
```

これは定義の確認までです。Git作業環境、エージェント、統合を扱うConductorのハンドラ構成が実行側に必要です。`ready=True`はここで指定したレビューと作業環境の統合の成功を示し、公開やmainへのmergeを行ったという意味ではありません。

## 環境・セッション・メッセージを操作するAPI

`doeff-agentic`には環境、セッション、メッセージ、ワークフローのAPIがあります。`doeff-agents`のセッション結果境界と、名称が似た別の抽象です。ここでは環境を作り、1件のメッセージを送り、次のイベントを受け取る形を示します。

```python
from doeff_agentic.effects import (  # Agenticの環境とメッセージの操作を使う。
    AgenticCreateEnvironment,  # 作業ディレクトリを使う環境を作成する。
    AgenticCreateSession,  # 環境へ関連付けたセッションを作成する。
    AgenticCreateWorkflow,  # ワークフローのハンドルを作成する。
    AgenticGetMessages,  # 指定件数までのメッセージを取得する。
    AgenticNextEvent,  # セッションの次のイベントを待つ。
    AgenticSendMessage,  # セッションへ依頼文を送る。
)
from doeff_agentic.types import AgenticEnvironmentType  # 環境の種類を指定する列挙型。

@do  # 環境の準備からイベント待ちまでを合成する。
def agentic_session(work_dir: str):  # 既存の作業ディレクトリを受け取る。
    workflow = yield AgenticCreateWorkflow(name="説明書の検討")  # ワークフローハンドルを取得。
    environment = yield AgenticCreateEnvironment(  # この例では既存ディレクトリを共有する。
        env_type=AgenticEnvironmentType.SHARED, working_dir=work_dir, name="文書作業環境",  # 環境指定。
    )
    session = yield AgenticCreateSession(name="文書レビュー", environment_id=environment.id)  # 関連付け。
    yield AgenticSendMessage(session_id=session.id, content="説明書の改善点を列挙してください")  # 依頼。
    event = yield AgenticNextEvent(session_id=session.id, timeout=30)  # 次の1イベントを待つ。
    messages = yield AgenticGetMessages(session_id=session.id, limit=5)  # 最大5件のメッセージを得る。
    return workflow, session, event, messages  # 完了判定せず、観測した値を後続へ渡す。
```

この例も定義の確認までです。1イベントを受け取っただけで作業完了とは判定しません。また、このコードにはワークフローへセッションを明示登録する操作がなく、自動で関連付けられるとは説明していません。

`doeff-agentic-cli`で既存の実行を見る場合は、次の形です。

```bash
doeff-agentic ps  # 既存の実行一覧を表示する。ここではコマンドを実行していない。
doeff-agentic watch RUN_ID  # RUN_IDを実在する実行IDへ置き換え、その実行の状態を監視する。
```

旧`RunAgent`系のAPIは非推奨の実装が残っているため、新しい例では使っていません。[上位層の定義例](examples/external_workflows.py)も参照できます。

## 処理の流れ

![2件のレビューを開始し、結果を待って可否を合成する](/images/zenn-use-cases-v0/generated/agents-flow.png)

2件を開始してから、それぞれの結果を待ちます。シナリオ例は文章がok=True、コードがok=Falseなので、最終結果はready=Falseです。

単発の変換や構造化回答には[LLM呼び出し](doeff-llm.md)、接続とテストを組み合わせる考え方には[ハンドラの合成](doeff-handlers.md)がつながります。エージェントも、同じ`@do`の計算へ組み込める操作の1つです。

## 実装・実例を読む

- [セッション仕様・結果型・公開コンストラクタ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-agents/src/doeff_agents/effects/agent.py)
- [シナリオハンドラの実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-agents/src/doeff_agents/handlers/testing.hy)
- [効果ハンドラ内の待機とMCP接続](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-agents/src/doeff_agents/handlers/effectful.hy)
- [Conductorのワークフロー向け操作](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-conductor/src/doeff_conductor/effects/agent.py)
- [Agenticの環境・セッションの操作](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-agentic/src/doeff_agentic/effects/__init__.py)

参照対象は上記の開発版です。記事で実行したのはオフラインのシナリオ例であり、実エージェント、認証、外部サービスとの接続の動作確認とは分けています。

[メイン記事へ戻る](doeff-main.md)
