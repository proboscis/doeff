# 機能とコード例の対応表

更新：2026-09-16。掲載した機能を、具体的なコードと確認範囲へ対応付ける。
「構成コード」「定義のみ」は、外部環境での成功を意味しない。実装全体のテスト網羅率ではない。
[再現用スクリプト](verify_examples.py)はオフラインの実行対象を明示している。

| 機能 | 記事・コード | 確認範囲 |
| --- | --- | --- |
| エージェント起動・結果待ち・結果スキーマ・並行レビュー | [agents](doeff-agents.md)・[完全な例](examples/agents_workflow.py) | シナリオで成功・入力待ち・期限超過・解放を確認。シナリオ自体はスキーマ検査をしない。本番は未実行 |
| 追加指示・停止・解放 | [agents](doeff-agents.md)・[完全な例](examples/agents_workflow.py) | FollowUp・StopSession・ReleaseSessionの呼び出しを確認 |
| エージェント観測・MCP・本番ハンドラ | [agents](doeff-agents.md)（本文にコード） | ObserveAgentSession・McpToolDef・SessionBackendの構成コード。本番接続は未実行 |
| Agentic / Conductor / CLI | [agents](doeff-agents.md)・[完全な例](examples/external_workflows.py) | セッションの作成・メッセージ・2作業の統合、ps/watchの使用例。外部操作は未実行 |
| LLM chat / streaming / structured / embedding | [llm](doeff-llm.md)・[完全な例](examples/llm_pipeline.py) | 4段階を@doで合成。実SDKのSSE解析器へ固定データを渡し、各チャンクのAwaitとエラー伝播を確認 |
| OpenAI / Gemini / OpenRouterの本番ハンドラ | [llm](doeff-llm.md)（本文にコード） | 接続関数を掲載。生のハンドラと取り付け関数の差、応答形式の差を明記。実APIは未実行 |
| 画像生成・編集 / Gemini / Seedream | [image](doeff-image.md)・[完全な例](examples/image_pipeline.py) | 生成結果→編集を両テスト用ハンドラで確認。本番の接続コードは未実行 |
| Delay / GetTime / 実時間・仮想時間・同期待機 | [time](doeff-time.md)（本文にコード） | 同じworkflowの仮想時間・非同期の実時間・同期の実時間を確認 |
| WaitUntil / ScheduleAt / SetTime / 優先度 | [time](doeff-time.md)・[完全な例](examples/scheduling.py) | 時計設定・予約・優先度指定を確認。予約した本体のハンドラスコープの条件を明記 |
| 関数の色 / yieldの歴史 / Await | [color](doeff-color.md)（本文にコード） | Programとハンドラの例、新旧構文。初期asyncioは歴史用コードとして区別 |
| Ask / DI / Local / lazy_ask / env_var_ask | [di](doeff-di.md)・[完全な例](examples/dependencies.py) | 局所変更・復元・依存の取得4回・作成2回と環境変数の供給を確認 |
| Reader / State / Writer / Try | [composition](doeff-composition.md)・[完全な例](examples/composition.py) | 成功と失敗を確認。この構成で状態とログが自動では戻らないこともassert |
| Listen / Tell / Slog | [observe](doeff-observe.md)・[完全な例](examples/observability.py) | 返り値と2件のログの収集を確認 |
| L1/L2/L3の取得・補充・削除 | [memo](doeff-memo.md)（本文にコード） | 本文のメモリ3層例を実行確認。Redis/MinIOは組み込みと主張しない |
| MemoPut / コスト条件 / MemoPolicy | [memo](doeff-memo.md)・[完全な例](examples/memo_policy.py) | 保存する層と削除を確認。標準memo_handlerがTTLを適用しないこと、コスト条件が違う削除で古い値が復活することも確認 |
| rewriter / 部分的再利用 / データ源の差し替え | [boundaries](doeff-boundaries.md)・[完全な例](examples/document_pipeline.py) | ReadPage→HTTPへ翻訳。HTTP/Memo/時計を独立に交換。3件・仮想4秒、次のrunはHTTP不要で0秒を確認 |
| HTTP記録・再生 / production | [replay](doeff-replay.md)・[完全な例](examples/http_replay.py) | Memo書き換え＋SQLite＋HTTPハンドラを合成。新しい実行での保存値再利用と未保存時の拒否を確認 |
| cache / SQLite / プロセスをまたぐ再利用 | [durable](doeff-durable.md)・[完全な例](examples/durable.py) | prepare→finish→finishを別プロセスで実行。再利用と未完了の後続処理を確認 |
| Conductorの履歴 / 時刻・乱数 | [durable](doeff-durable.md)・[完全な例](examples/workflow_journal.hy) | 同じ実行IDで再実行し結果一致を確認。エージェントなし |
| JournaledAgentHandler | [durable](doeff-durable.md)（本文にコード） | 既存のテスト用実行基盤への接続コード。エージェントの強制終了→再開は未検証 |
| ドメインAPI / 状態操作への翻訳 | [boundaries](doeff-boundaries.md)・[完全な例](examples/game_replay.py) | DealDamageをGet/Putへ翻訳。文書取得の統合例も掲載 |
| カードゲーム / 複数対局 / 入力履歴からの再構成 | [games](doeff-games.md)・[完全な例](examples/game_replay.py) | 3ターンの対局を記録・JSON保存・再構成。状態を分けた2対局も確認 |
| Publish / WaitForEvent / 待機ループ / Race / Cancel | [events](doeff-events.md)・[完全な例](examples/event_loop.py) | 終了までの待機と期限の競争、残ったタスクの取消を確認 |
| Gather / Promise / ExternalPromise / Semaphore | [events](doeff-events.md)・[完全な例](examples/scheduler_coordination.py) | 準備完了の共有・同時実行制限・外部スレッドからの完了を確認 |
| Traverse / sequential / parallel / Inspect | [traverse](doeff-traverse.md)・[完全な例](examples/traverse_pipeline.py) | 本文の逐次・並行比較と、追加例の失敗履歴を確認 |
| Fail / fail-fast / Reduce / Zip / SortBy / Take | [traverse](doeff-traverse.md)・[完全な例](examples/traverse_pipeline.py) | 失敗を隔離した集計と、失敗を伝播する別解釈を確認 |
| for/do / From / When / Reduce | [hy](doeff-hy.md)・[完全な例](examples/hy_composition.hy) | 絞り込み→変換→集計を確認。旧foldは現行の公開マクロになく、推奨例から置換 |
| GitPull / Diff / Commit / Push / CreatePR / MergePR | [operations](doeff-operations.md)・[完全な例](examples/operations.py) | メモリ上で差分なしの分岐とPR作成・統合の段階を確認。実Git操作なし |
| Notify / NotifyThread / Acknowledge | [operations](doeff-operations.md)・[完全な例](examples/operations.py) | 通知・スレッド更新・確認の収集を検査 |
| GetSecret / SetSecret / ListSecrets / DeleteSecret | [operations](doeff-operations.md)・[完全な例](examples/operations.py) | 偽物の値をメモリで取得・保存・一覧・削除 |
| Git・通知の本番ハンドラ / env / Secret Manager | [operations](doeff-operations.md)（本文にコード） | 対象effectを選別した合成と環境変数取得を確認。通知PassとSecret Delegateの旧API不一致を明記 |
| Dockerfile / build / run / push / ShellRun | [remote](doeff-remote.md)・[完全な例](examples/container_program.py) | Dockerfile6命令、build/pushのShellRun依頼、固定DockerRun、Program復元の結果6を確認。実Dockerなし |
| ML-nexus / 転送 / ローカル・リモート / GPU | [remote](doeff-remote.md)・[完全な例](examples/external_workflows.py) | 転送依頼とCPU/GPU引数を固定ハンドラで確認。上位ファクトリはdefk契約不足でimport失敗。実Docker/SSH/GPUなし |
| doeff-flow / trace / CLI | [observe](doeff-observe.md)（本文にコード） | TracePush/Annotate/Snapshot/Capture・LiveTrace・CLIの例を掲載。ただし現行checkoutでRunResultのImportError。実行成功とは扱わない |
| defk / <- / ! / 契約 / do! / defhandler / deftest | [hy](doeff-hy.md)・[完全な例](examples/hy_composition.hy) | マクロの実行、生成されたdeftestの関数を確認 |
| indexer / analyzer / linter | [tooling](doeff-tooling.md)（本文にコード） | 入力を指定したCLI例を掲載。バイナリ未導入のため未実行。解析器は開発途中 |
| ADR / law / deftest / 収集 | [tooling](doeff-tooling.md)・[完全な例](examples/adr_example.hy) | ADR契約と生成された振る舞いのテストを実行。pytest収集の設定条件も明記 |
| defsemgrep / 正常例・反例 | [tooling](doeff-tooling.md)・[完全な例](examples/static_check.hy) | badの検出とgoodの通過を実際のSemgrepで確認 |
| domain / 対応ハンドラ / 所属の検査 | [tooling](doeff-tooling.md)・[完全な例](examples/domain_check.py) | 宣言の網羅と指定漏れの失敗を確認。孤立した操作を調べる呼び出し例も掲載 |
| VM / VM core / test-target / 旧agentd | [tooling](doeff-tooling.md)（本文にコード） | 内部基盤・検証用・廃止済みを区別。利用者向け機能や未対応APIとして宣伝しない |
| Rust VM・Python generator・Resume / Transfer / Pass | [VM](doeff-vm.md)・[完全な例](examples/vm_walkthrough.py) | PythonとRustの境界、1回だけの継続、ハンドラ探索と再開を検証 |
| ハンドラ合成・順序・下位effectへの翻訳 | [ハンドラ合成](doeff-handlers.md)・[完全な例](examples/handler_composition.py) | 順序による88/90の違い、Pass、局所適用、状態と時間への翻訳を検証 |
| coroutine・awaitable・TaskとProgramの違い | [coroutine比較](doeff-coroutines.md)・[完全な例](examples/coroutine_comparison.py) | 同じ挨拶、子Program、チャンク4件と終了判定の5回Awaitを検証 |
