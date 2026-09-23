# agents記事の個別レビュー

- 担当: 記事別レビューエージェント `/root/review_agents`
- 日付: 2026-09-16
- 対象: `doeff-agents.md`、`examples/agents_workflow.py`
- 適用した指針: doeff-patterns、doeff-runtime、`REVIEW-INSTRUCTIONS.md`
- 状態: 本文・専用例の修正とオフライン検証を完了。画像生成・共通目録の更新は親担当。

## 指摘と修正

1. **シナリオと本番の検証範囲が曖昧だった。** `ScenarioAgentHandler.handle-await-result`はpayloadをそのまま`AwaitOutcome.result`へ入れる。スキーマ検査・本番の再試行を検証するテストではないことを本文で明示した。`jsonschema.validate`はシナリオの正常系データの検査として分離した。
2. **状態と検証エラーを同列に説明していた。** `AwaitStatus`はEXITED、AWAITING_INPUT、TIMED_OUTの3種類。検証エラーは`validation_error`という別のフィールドだと修正した。
3. **合成する補助関数の書き方が不統一だった。** `review_spec`を`@do`にし、呼び出し元で`yield review_spec(...)`とした。MCP付きレビューと本番のハンドラ構築も`@do`から合成する。`async def`やループ全体を包む`Await`は使っていない。
4. **全行の目的・期待動作が読めなかった。** 記事中の全7 Pythonコードブロック、CLI例、専用Python例の実質行へ日本語コメントを付けた。多行引数も、各フィールドの意味や期待する結果を記した。
5. **例外・終了・業務上の不合格の区別が薄かった。** `ok=False`は有効なレビュー結果であり、スキーマ違反と別だと明記した。入力待ちへの追加指示、タイムアウト時の停止・解放、結果欠落・検証エラー時の利用拒否を確認した。
6. **資源管理を過大に見せる余地があった。** 明示した状態分岐の解放のみを扱う例だと明記した。任意の外部例外・親のキャンセル・兄弟タスクの終了を含む全面的な保証はしていない。
7. **本番の作業場所がテスト用パスのままになり得た。** `live_review(work_dir, document_text)`で実在する作業場所を受け、テスト仕様のパスを置き換える形にした。実行IDを別の仕事では変える必要も記した。
8. **MCP観測と結果取得の境界が曖昧だった。** `ObserveAgentSession`はID文字列を受け取ると明示。`AgentSessionSnapshot`を構造化結果の代わりに扱わず、結果は`AwaitOutcome.result`を使う。
9. **上位パッケージの抽象が混同され得た。** ConductorのCreateIssueはその管理するissueでありGitHub送信とは同義でないとした。AgenticのCreateWorkflowを呼んだだけでsessionが自動登録されるとは説明しない。両者とも実実行済みとはしていない。
10. **図を本文に合わせ直した。** `agents-visuals.json`に白背景の平面的な図と、各行コメント付きの正しいコード抜粋を用意した。本文のalt/captionをこの仕様へ変更した。

## 実装根拠

| 対象 | 読んだ実装 | 確認した内容 |
|---|---|---|
| AgentSpec / AwaitOutcome / 操作 | `packages/doeff-agents/src/doeff_agents/effects/agent.py` | 引数、状態3種、結果フィールド、セッションID、公開コンストラクタ |
| シナリオ | `packages/doeff-agents/src/doeff_agents/handlers/testing.hy` | 応答列、payloadの扱い、FollowUp、Stop、Releaseの記録 |
| 公開ハンドラ | `packages/doeff-agents/src/doeff_agents/handlers/__init__.py` | `agent_effectful_handler()`、`Ask(SessionBackend)`の公開境界 |
| 待機・MCP接続 | `packages/doeff-agents/src/doeff_agents/handlers/effectful.hy` | 待機0の挙動、入力待ちの観測、終了操作、MCPの接続 |
| MCP道具 | `doeff/mcp.py` | `McpToolDef`のhandlerはProgramを返す`@do`関数 |
| Conductor | `packages/doeff-conductor/src/doeff_conductor/effects/agent.py`、`workspace.py`、`issue.py` | AgentTask / Agent、作業環境の再取得、統合結果 |
| Agentic | `packages/doeff-agentic/src/doeff_agentic/effects/session.py`、`environment.py`、`workflow.py`、`messaging.py` | 環境、セッション、メッセージ、イベントの引数 |

## 検証

### 専用例

実行したコマンド:

```bash
uv run --no-sync python publications/zenn-use-cases-v0/examples/agents_workflow.py
uv run --no-sync ruff check publications/zenn-use-cases-v0/examples/agents_workflow.py
```

いずれも終了コード0。Ruffは`All checks passed!`。

確認した期待結果:

- 2件の子タスクを開始してから両結果を取得する。
- 文章True・コードFalseから`ready=False`と要約2件が返る。
- 入力待ちのコード側へFollowUpが1回記録される。
- 正常系の2セッションが解放される。
- タイムアウトは成功結果にせず、停止1回・解放1回となる。
- 結果欠落とvalidation_errorのケースでは、利用不可の例外と解放になる。

失敗分岐を検証するため、doeffのトレース表示にRuntimeErrorが3件出る。例はその例外の種類と内容を確認して正常終了する。

### 本文

Markdownの全7 Pythonコードブロックを順にcompile/execした。実行されるのは関数定義・仕様/道具の構築と、明示した`verify()`だけである。加えて次を確認した。

- `read_document()`を`reader(env={"document_text": "確認対象の文書"})`で解釈すると、指定文字列が返る。
- `live_review(Path("/example/docs"), "文書")`、`conductor_review("example-run")`、`agentic_session("/example/docs")`は未実行のProgramを構築する。
- 実質行にコメントがあることをコードブロック全行で走査した。空行と閉括弧だけの行は除く。
- `published: false`を維持した。

### 未実行と制限

実エージェント、認証、MCPサーバー、ConductorのGit作業環境・統合、Agenticサービス、CLIコマンドは実行していない。本番のスキーマ検査・自動再試行・経過時間・任意のキャンセルの検証もしていない。シナリオテストをそれらの代用とは記載していない。

## 親担当への引き継ぎ

- `agents-visuals.json`のconcept/flowからimagegenで差し替える。本文の画像パスは既存のgenerated/agents-{concept,flow}.pngを維持した。
- 共通captions目録、画像manifest、網羅表、TODOはこの担当では変更していない。
- 共有`examples/external_workflows.py`は変更していない。記事中のConductor/Agentic例は同じ公開APIを使い、全行コメント付きで改めた。共有ファイルの全行コメントへの対応は親担当が行う。
- 製品runtime・packagesの変更、commit、pushは行っていない。
