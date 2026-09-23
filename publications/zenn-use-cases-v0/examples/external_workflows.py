"""外部実行環境を必要とする構成例。定義のみ。ここでは実行しない。"""

from pathlib import Path  # 転送元のパスと作業場所の型を表す。

from doeff import do  # 外部操作を実行せずProgramとして定義する。


@do  # 上位の開発ワークフローもyieldで合成する。
def conductor_review(run_id: str):  # 別の仕事を区別する実行IDを受け取る。
    from doeff_conductor.effects import (  # 開発作業向けの操作を使う。
        Agent,  # 作業を依頼し、スキーマに合う結果を受け取る操作。
        AgentTask,  # 作業環境と結果スキーマを含む仕事の仕様。
        CreateIssue,  # Conductorのissueを用意する操作。
        CreateWorkspace,  # 同じIDの論理作業環境を作成または再取得する操作。
        MergeWorkspaces,  # 複数環境を統合し、競合情報を含む結果を返す操作。
    )
    from doeff_core_effects.scheduler import Spawn, Wait  # 2件を開始して結果を待つ。

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




@do  # 環境の準備からイベント待ちまでを合成する。
def agentic_session(work_dir: str):  # 既存の作業ディレクトリを受け取る。
    from doeff_agentic.effects import (  # Agenticの環境とメッセージの操作を使う。
        AgenticCreateEnvironment,  # 作業ディレクトリを使う環境を作成する。
        AgenticCreateSession,  # 環境へ関連付けたセッションを作成する。
        AgenticCreateWorkflow,  # ワークフローのハンドルを作成する。
        AgenticGetMessages,  # 指定件数までのメッセージを取得する。
        AgenticNextEvent,  # セッションの次のイベントを待つ。
        AgenticSendMessage,  # セッションへ依頼文を送る。
    )
    from doeff_agentic.types import AgenticEnvironmentType  # 環境の種類を指定する列挙型。

    workflow = yield AgenticCreateWorkflow(name="説明書の検討")  # ワークフローハンドルを取得。
    environment = yield AgenticCreateEnvironment(  # この例では既存ディレクトリを共有する。
        env_type=AgenticEnvironmentType.SHARED, working_dir=work_dir, name="文書作業環境",  # 環境指定。
    )
    session = yield AgenticCreateSession(name="文書レビュー", environment_id=environment.id)  # 関連付け。
    yield AgenticSendMessage(session_id=session.id, content="説明書の改善点を列挙してください")  # 依頼。
    event = yield AgenticNextEvent(session_id=session.id, timeout=30)  # 次の1イベントを待つ。
    messages = yield AgenticGetMessages(session_id=session.id, limit=5)  # 最大5件のメッセージを得る。
    return workflow, session, event, messages  # 完了判定せず、観測した値を後続へ渡す。


@do  # 解決・転送・書き込みの順序をProgramとして保持する。
def prepare_remote_files(source_id: str, host: str, destination: str):  # 配置する対象と場所を固定する。
    from doeff_ml_nexus.effects import Resolve, RsyncTo, WriteFile  # 各操作の依頼型を読み込む。

    source = yield Resolve(target=source_id, kind=Path)  # ソース識別子に対応するPathを受け取る。
    yield RsyncTo(  # 転送先ホストへの配置を依頼し、完了を待つ。
        src=source, host=host, dst_path=destination,  # 解決したソースと指定先を結び付ける。
        excludes=(".git", ".venv"),  # この直接転送の例では履歴と仮想環境を含めない。
    )
    yield WriteFile(  # 配置先に追加する文字列も書き込み依頼として表す。
        host=host, path=f"{destination}/example.txt", content="文書処理の例",  # UTF-8本文の内容と場所。
    )
    return destination  # 3つの依頼が完了したら、配置先を呼び出し元へ返す。


@do  # 実行環境の構成と本体の実行を、同じProgramの中で合成する。
def run_in_remote_environment(source_id: str, host: str, base_image: str, program):  # 現版はimport不整合で実行不可。
    from doeff_ml_nexus.interpreters import make_remote_uv_interpreter  # リモート用の構成関数。

    interpreter = yield make_remote_uv_interpreter(  # ファクトリProgramを評価して実行関数を得る。
        source_id, host, base_image, False,  # この構成ではGPUを要求しない。
    )
    return (yield interpreter(program))  # 得た関数もProgramを返すので、yieldして最終結果を受け取る。


@do  # ローカルのDocker環境も、処理本体の外側で構成する。
def run_in_local_container(source_id: str, base_image: str, program):  # 現版はimport不整合で実行不可。
    from doeff_ml_nexus.interpreters import make_local_uv_interpreter  # ローカル用の構成関数。

    interpreter = yield make_local_uv_interpreter(source_id, base_image)  # 実行関数が返るまで待つ。
    return (yield interpreter(program))  # localhost向けの構成・ビルド・実行の結果を返す。
