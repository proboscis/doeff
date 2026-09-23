# packages網羅表 — 記事編集の根拠

2026-09-16に再精査。最初の棚卸しでは、`packages/`の全31ディレクトリを列挙。30にはパッケージ定義があり、旧`doeff-agentd`は廃止済みの残存ディレクトリとして区別した。すべてに紹介先か除外理由を割り当てた。

これは**静的な棚卸しと記事への対応付け**であり、全パッケージの実動作を検証した一覧ではない。README、公開エフェクト・ハンドラ、実例やテストの内容を読み、旧API・開発途中・環境依存の条件を記録した。テストへのリンクは根拠としての所在を示し、今回の実行済みを意味しない。

## パッケージごとの対応

| ディレクトリ | 提供内容 | 紹介先 | 根拠 | テスト等の所在 | 確認範囲・注意点 |
| --- | --- | --- | --- | --- | --- |
| `doeff-adr` | defadr・defsemgrep・deftestとpytest収集。設計判断を検査へ結び付ける | [tooling](doeff-tooling.md) | [定義・説明](../../packages/doeff-adr/README.md) | [所在](../../packages/doeff-adr/tests/test_wiring.py) | 検査を採用側で収集する必要がある |
| `doeff-agentd` | 廃止済み実装の残存ディレクトリ | [tooling](doeff-tooling.md) | [定義・説明](../../packages/doeff-agents/README.md) | 専用テスト未特定（確認対象を保証しない） | このcheckoutには未追跡のCargo.lockのみ。提供中のパッケージに数えない |
| `doeff-agentic` | 環境・セッション・メッセージ・ワークフローを扱う上位API | [agents](doeff-agents.md) | [定義・説明](../../packages/doeff-agentic/src/doeff_agentic/effects/__init__.py) | [所在](../../packages/doeff-agentic/tests/__init__.py) | READMEのRunAgent等は実装でlegacy/deprecated。新規の推奨例にしない |
| `doeff-agentic-cli` | ワークフローの一覧・監視・接続用Rust CLI | [agents](doeff-agents.md) | [定義・説明](../../packages/doeff-agentic-cli/src/main.rs) | 専用テスト未特定（確認対象を保証しない） | 実CLI接続・速度は未検証 |
| `doeff-agents` | 起動・観測・追加指示・停止・スキーマで指定した結果の受け取り | [agents](doeff-agents.md) | [定義・説明](../../packages/doeff-agents/src/doeff_agents/effects/agent.py) | [所在](../../packages/doeff-agents/tests/conftest.py) | 実エージェント未起動。AwaitOutcomeの状態を確認してresultを利用 |
| `doeff-conductor` | issue・隔離Git作業環境・エージェント・依存関係付きワークフロー・記録 | [agents](doeff-agents.md) | [定義・説明](../../packages/doeff-conductor/README.md) | [所在](../../packages/doeff-conductor/tests/__init__.py) | 時刻・乱数のみのワークフローは実行確認。エージェント・Git操作は未実行 |
| `doeff-core-effects` | 環境/DI・状態・ログ・失敗・Listen・HTTP・キャッシュ/メモ化・並行処理 | [composition](doeff-composition.md) | [定義・説明](../../packages/doeff-core-effects/doeff_core_effects/__init__.py) | 専用テスト未特定（確認対象を保証しない） | 下記の詳細表でtime/di/replay/durable/events/observeにも対応付ける |
| `doeff-docker` | Dockerfileの収集・build/run/push・ShellRun境界 | [remote](doeff-remote.md) | [定義・説明](../../packages/doeff-docker/src/doeff_docker/effects.hy) | [所在](../../packages/doeff-docker/tests/test_effects.py) | Dockerを操作していない |
| `doeff-domain` | エフェクト領域の宣言・重複導入や孤立した定義・ハンドラの宣言上の網羅を検査 | [tooling](doeff-tooling.md) | [定義・説明](../../packages/doeff-domain/src/doeff_domain/checks.py) | [所在](../../packages/doeff-domain/tests/test_domain_checks.py) | 任意導入。挙動の完全性証明ではない |
| `doeff-effect-analyzer` | Python/Hyの静的エフェクト依存解析、レポート出力 | [tooling](doeff-tooling.md) | [定義・説明](../../packages/doeff-effect-analyzer/src/lib.rs) | [所在](../../packages/doeff-effect-analyzer/tests/effect_tracking.rs) | READMEで開発途中と明示。解析の完全性や実行性能は主張しない |
| `doeff-events` | 型に基づくPublish/WaitForEventとメモリ上の購読 | [events](doeff-events.md) | [定義・説明](../../packages/doeff-events/src/doeff_events/handlers/memory.py) | [所在](../../packages/doeff-events/tests/conftest.py) | 記事例を実行。履歴・永続配送は保証しない。既存テストには旧APIも残る |
| `doeff-flow` | 実行トレース・観測エフェクト・JSONL・CLI監視 | [observe](doeff-observe.md) | [定義・説明](../../packages/doeff-flow/src/doeff_flow/trace.py) | [所在](../../packages/doeff-flow/tests/__init__.py) | RunResultのImportErrorを確認。トレースの構成例は未実行と明記 |
| `doeff-gemini` | Geminiの共通LLMエフェクト処理・構造化応答・画像連携 | [llm](doeff-llm.md) | [定義・説明](../../packages/doeff-gemini/README.md) | [所在](../../packages/doeff-gemini/tests/unit/test_cost_hook.py) | image記事にも掲載。プロバイダ実接続は未検証 |
| `doeff-git` | git操作とPR操作、本番/テスト用ハンドラ | [operations](doeff-operations.md) | [定義・説明](../../packages/doeff-git/src/doeff_git/handlers/production.py) | [所在](../../packages/doeff-git/tests/unit/test_effect_handlers.py) | commit/push/PR等は未実行 |
| `doeff-google-secret-manager` | 共通secretエフェクトをGoogle Cloud Secret Managerへ接続 | [operations](doeff-operations.md) | [定義・説明](../../packages/doeff-google-secret-manager/README.md) | [所在](../../packages/doeff-google-secret-manager/tests/unit/test_secrets.py) | 実認証・秘密情報アクセスは未実行 |
| `doeff-hy` | Hyのdefk/do!/<-/defhandler/deftest、計算合成の表記 | [hy](doeff-hy.md)、[tooling](doeff-tooling.md) | [定義・説明](../../packages/doeff-hy/README.md) | [所在](../../packages/doeff-hy/tests/test_sexpr.hy) | Python @doの必須前提ではない。traverse記事にも掲載 |
| `doeff-image` | ImageGenerate/ImageEdit/ImageResultという共通の依頼と結果 | [image](doeff-image.md) | [定義・説明](../../packages/doeff-image/src/doeff_image/effects/generate.py) | [所在](../../packages/doeff-image/tests/test_image_effects.py) | それ自体は生成プロバイダではない |
| `doeff-indexer` | Program/@do/Kleisli定義・型情報の静的索引と探索 | [tooling](doeff-tooling.md) | [定義・説明](../../packages/doeff-indexer/src/indexer.rs) | [所在](../../packages/doeff-indexer/tests/deps.rs) | 依存エフェクトの解析とは役割を分ける |
| `doeff-linter` | Pythonコード品質・不変性・doeffの利用規則を検査 | [tooling](doeff-tooling.md) | [定義・説明](../../packages/doeff-linter/src/lib.rs) | [所在](../../packages/doeff-linter/tests/conftest.py) | READMEの規則数や速度は転載しない |
| `doeff-llm` | Chat/StreamingChat/StructuredQuery/Embeddingの共通エフェクト | [llm](doeff-llm.md) | [定義・説明](../../packages/doeff-llm/src/doeff_llm/effects/structured.py) | 専用テスト未特定（確認対象を保証しない） | README間にStructuredOutput/StructuredQueryの差。現行実装のStructuredQueryを使用 |
| `doeff-ml-nexus` | 依存の転送・環境構築・シリアライズ・ローカル/リモートDocker・GPU指定 | [remote](doeff-remote.md) | [定義・説明](../../packages/doeff-ml-nexus/src/doeff_ml_nexus/interpreters.hy) | [所在](../../packages/doeff-ml-nexus/tests/test_docker.py) | Programのシリアライズ往復は成功。上位ファクトリはdefkの契約不足でimport失敗。Docker/SSH/GPUは未実行 |
| `doeff-notify` | 通知・スレッド・確認の語彙、console/log/testingハンドラ | [operations](doeff-operations.md) | [定義・説明](../../packages/doeff-notify/README.md) | [所在](../../packages/doeff-notify/tests/test_handlers.py) | 組み込みSlack配送はない。testingの未対応effect分岐に旧Pass()が残るため、記事は型で選別して正しいPass(effect, k)で委譲 |
| `doeff-openai` | OpenAIの共通LLMエフェクト処理・構造化応答・ストリーミング等 | [llm](doeff-llm.md) | [定義・説明](../../packages/doeff-openai/README.md) | [所在](../../packages/doeff-openai/tests/_runner.py) | モデル対応・価格・実接続は今回検証していない |
| `doeff-openrouter` | OpenRouterの共通LLMエフェクト処理・構造化応答 | [llm](doeff-llm.md) | [定義・説明](../../packages/doeff-openrouter/README.md) | [所在](../../packages/doeff-openrouter/tests/conftest.py) | streamingは未実装の例外、embeddingはPass。全エフェクト・全モデルの互換を主張しない |
| `doeff-secret` | 秘密情報の取得/保存/一覧/削除、環境変数とメモリ上のテスト用ストア | [operations](doeff-operations.md) | [定義・説明](../../packages/doeff-secret/src/doeff_secret/effects/secrets.py) | [所在](../../packages/doeff-secret/tests/test_effect_handlers.py) | 環境変数・メモリの未対応分岐に削除済みDelegate()が残る。環境変数からクラウドへの自動fallbackは動作確認済みとしない |
| `doeff-seedream` | Seedream画像生成・編集と共通画像エフェクトのハンドラ | [image](doeff-image.md) | [定義・説明](../../packages/doeff-seedream/README.md) | [所在](../../packages/doeff-seedream/tests/test_edit_image.py) | 実生成未実行 |
| `doeff-test-target` | 解析・挙動のテスト用シナリオ/エフェクト/ハンドラ | [tooling](doeff-tooling.md) | [定義・説明](../../packages/doeff-test-target/src/doeff_test_target/orchestrate.py) | [所在](../../packages/doeff-test-target/tests/test_smoke.py) | 利用者向け業務機能として売らない |
| `doeff-time` | Delay/WaitUntil/GetTime/ScheduleAt/SetTime、同期/asyncio/仮想時間 | [time](doeff-time.md) | [定義・説明](../../packages/doeff-time/src/doeff_time/handlers/sim_time.py) | [所在](../../packages/doeff-time/tests/conftest.py) | 仮想時間と実時間は既存記事例で実行確認 |
| `doeff-traverse` | Traverse/Reduce/Zip/Inspect/SortBy/Take/Fail/Skip、逐次/並行/失敗の戦略 | [traverse](doeff-traverse.md) | [定義・説明](../../packages/doeff-traverse/doeff_traverse/handlers.py) | [所在](../../packages/doeff-traverse/tests/test_memory_leak_multi_day.py) | Hy for/do/From/When/Reduceも掲載。旧foldは公開マクロになく置換。継続の複製ではなく新しい計算を生成 |
| `doeff-vm` | Pythonバインディング・VM接続・継続/トレース等の公開プリミティブ | [VM](doeff-vm.md) | [定義・説明](../../packages/doeff-vm/Cargo.toml) | [所在](../../packages/doeff-vm/tests/test_pyvm.py) | 独立した業務機能としては扱わない |
| `doeff-vm-core` | RustのVM本体・継続管理・実行機構 | [VM](doeff-vm.md) | [定義・説明](../../packages/doeff-vm-core/Cargo.toml) | [所在](../../packages/doeff-vm-core/tests/test_vm_module_split.py) | 速度比較や耐久性の実証はしていない |

## コアの能力も省略しない

パッケージ名だけでまとめると見落とすため、`doeff-core-effects`は操作群でも対応を記録する。

| 操作群 | APIの例 | 紹介先 |
| --- | --- | --- |
| 環境・依存性注入・局所的な環境 | Ask、Local、reader、lazy_ask、env_var_ask | [DI](doeff-di.md) |
| 状態・ログ・失敗 | Get、Put、Tell、Try、slog | [抽象の合成](doeff-composition.md) |
| エフェクトの収集・観測 | Listen、listen_handler | [観測](doeff-observe.md) |
| 非同期処理との接続 | Await、await_handler | [関数の色](doeff-color.md)、[時間](doeff-time.md) |
| タスク・完了待ち・取消・同時実行制限 | Spawn、Wait、Gather、Race、Cancel、Promise、ExternalPromise、Semaphore | [イベント・並行処理](doeff-events.md)、[traverse](doeff-traverse.md) |
| HTTPと記録・再生 | HttpRequest、http_production_handler、make_memo_rewriter、sqlite_memo_handler | [記録・再生](doeff-replay.md) |
| 計算結果の再利用・保存先の切り替え | cache、memo_handler、make_memo_rewriter、MemoPolicy、SQLiteStorage | [記録・再生](doeff-replay.md)、[永続実行](doeff-durable.md) |

## パッケージを横断するゲームエンジンの入口

[カードゲーム](doeff-games.md)はcore-effectsの状態/スケジューラ、eventsの入力待ち、timeの仮想時間、traverseの複数対局、メモ化と履歴保存による再開設計を横断する。記事の戦闘例は実行確認。追加した入力履歴のJSON保存・再構成と2対局は実行確認。強制終了途中の保存・再開は未検証。既存のゲーム紹介文には旧APIや外部プロジェクトの実績主張があり、今回はそれを検証済みの現行実装として転載していない。

## 調査で修正した説明

- 「一度しか継続を再開しない」だけでは反復や探索的な合成を説明しきれない。`doeff-traverse`は新しい計算を作る関数を渡して繰り返す層を持つ。VMの継続複製とは区別する。
- プロバイダのREADMEに旧実行APIや旧エフェクト名がある。新規記事では現行のエフェクト定義を使用し、旧例をそのまま転載しない。
- flowの観測機能と、永続結果の再利用は役割が異なる。flowが自動的に障害復旧を提供するとはしない。
- eventsはメモリ上の購読、notifyの組み込み配送はconsole/log/testing。外部分散キューやSlack接続まで実装済みと広げない。
- ml-nexusはProgramのシリアライズ往復を確認した。上位ファクトリはdefkの契約不足でimportできず、既存テスト7件はwriter外側のstate不足で失敗する。記事の正しいハンドラ構成は成功するが、実コンテナでの成功保証はしない。

## この追補で実行した確認

[機能とコード例の対応表](feature-examples.md)と[検証記録](README.md#検証記録)を参照。
21本のオフライン例、SQLiteを使う3プロセス、Hy・ADR・Semgrep、エージェントを使わないConductorの履歴再利用を実行した。
外部連携の定義コードと、テスト用ハンドラで動かしたコードを区別している。
全31ディレクトリの対応、23記事の導線とコード、46図も確認する。

今後パッケージを追加・廃止した場合はこの表とメイン記事の入口を一緒に見直す。記事本文の正本は各Markdown、プレビューはそこから生成する。

## 深掘り記事と図の追加

- [多段メモ化](doeff-memo.md)：core-effectsのmemo_handler、コストによる経路選択、ストレージ階層を説明。メモリ3層の取得・補充・削除を実行確認。Redis・MinIOは組み込みとして確認していないため、アダプタを用意する構成例と明示。
- [エフェクトの境界](doeff-boundaries.md)：任意のドメイン操作、Get/Putへの翻訳、自作ハンドラと契約、継続と協調的優先度を説明。状態更新例を実行確認。
- [Hy](doeff-hy.md)：Pythonとの共通基盤、defk・契約・<-・式中の!を説明。掲載したPython・Hyの例を実行確認。
- [関数の色](doeff-color.md)：PEP 3156とPEP 492を参照し、初期asyncioのyield fromとdoeffのyieldの共通点・違いを記載。旧asyncio例は歴史の説明用で、現行環境では実行しない。
- 全23記事に計46図を配置。imagegenによる掲載PNGと生成・編集プロンプトを管理し、全記事の画像参照と容量を検査。

最新の実装根拠・成功した検査・残る不整合は[記事別レビュー](reviews/README.md)に記録する。全31ディレクトリの実環境での成功を意味する表ではない。
