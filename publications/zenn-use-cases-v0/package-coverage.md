# packages網羅表 — 記事編集の根拠

2026-09-16に再精査し、2026-10-17に今の`packages/`へ合わせ直した。9月の棚卸しでは`packages/`の全31ディレクトリを列挙し、30にはパッケージ定義があり、旧`doeff-agentd`は廃止済みの残存ディレクトリとして区別した。その後に9パッケージが加わり（[下の節](#9月の棚卸し以後に足した9パッケージ)）、`doeff-agentd`はgitの管理から2026-07-30に外れていたので表から外した。今は全39ディレクトリで、すべてにパッケージ定義（pyproject.tomlかCargo.toml）がある。すべてに紹介先か除外理由を割り当てた（記事が無いものは「記事なし」と書き、理由を注意点に書く）。

これは**静的な棚卸しと記事への対応付け**であり、全パッケージの実動作を検証した一覧ではない。README、公開エフェクト・ハンドラ、実例やテストの内容を読み、旧API・開発途中・環境依存の条件を記録した。テストへのリンクは根拠としての所在を示し、今回の実行済みを意味しない。

## パッケージごとの対応

| ディレクトリ | 提供内容 | 紹介先 | 根拠 | テスト等の所在 | 確認範囲・注意点 |
| --- | --- | --- | --- | --- | --- |
| `doeff-adr` | defadr・defsemgrep・deftestとpytest収集。設計判断を検査へ結び付ける | [adr](doeff-adr.md)・[tooling](doeff-tooling.md) | [定義・説明](../../packages/doeff-adr/README.md) | [所在](../../packages/doeff-adr/tests/test_wiring.py) | 検査を採用側で収集する必要がある |
| `doeff-agentic` | 環境・セッション・メッセージ・ワークフローを扱う上位API | [agents](doeff-agents.md) | [定義・説明](../../packages/doeff-agentic/src/doeff_agentic/effects/__init__.py) | [所在](../../packages/doeff-agentic/tests/__init__.py) | READMEのRunAgent等は実装でlegacy/deprecated。新規の推奨例にしない |
| `doeff-agentic-cli` | ワークフローの一覧・監視・接続用Rust CLI | [agents](doeff-agents.md) | [定義・説明](../../packages/doeff-agentic-cli/src/main.rs) | 専用テスト未特定（確認対象を保証しない） | 実CLI接続・速度は未検証 |
| `doeff-agents` | 起動・観測・追加指示・停止・スキーマで指定した結果の受け取り | [agents](doeff-agents.md) | [定義・説明](../../packages/doeff-agents/src/doeff_agents/effects/agent.py) | [所在](../../packages/doeff-agents/tests/conftest.py) | 実エージェント未起動。AwaitOutcomeの状態を確認してresultを利用 |
| `doeff-claude-code` | `claude -p`（print mode）のprocessの寿命を受け持つeffectとhandler。手番の開始・入力の追加・中断・出来事の読み取り・許可への回答・会話の終了と、本番/fakeのhandler | 記事なし（上位は[agents](doeff-agents.md)） | [定義・説明](../../packages/doeff-claude-code/README.md) | [所在](../../packages/doeff-claude-code/tests/test_handler.hy) | doeff-agentsのheadless handlerの土台。[agents](doeff-agents.md)は上位のdoeff-agentsだけを扱い、このpackageのeffectは紹介していない。実CLIは未起動 |
| `doeff-claude-judge` | doeff-system-oneの`Judge`を、Claudeの構造化の答え(structured output)への1回の問いに写し、答えを`Verdict`へ戻す純粋な変換 | 記事なし(関連は[hooks](doeff-hooks.md)) | [定義・説明](../../packages/doeff-claude-judge/README.md) | [所在](../../packages/doeff-claude-judge/tests/test_wire.py) | provider側のpackage(doeff-jevと同じ位置)。答えを運ぶhandlerはまだ無く、通信しない。[hooks](doeff-hooks.md)はこのpackageを名指ししない |
| `doeff-cluster` | Programをk8sのDeploymentのように動かし続ける実行基盤。coordinator・worker・名前付きlease・Rollout・drain・effectの記録と再生 | 記事なし | [定義・説明](../../packages/doeff-cluster/README.md) | [所在](../../packages/doeff-cluster/tests/test_coordinator.hy) | 業務を知らない基盤で、コード例を載せる記事がまだ無い。READMEは長く、冒頭の全体図・用語・module表を読んだ範囲。coordinator・workerの起動とk8s配備は未実行 |
| `doeff-codex` | `codex app-server`（JSON-RPC・stdio）をheadlessで走らせる層。ターンの開始・入力の追加・中断・出来事の読み取り・要求への回答・会話の終了と、本番/fakeのhandler | 記事なし（上位は[agents](doeff-agents.md)） | [定義・説明](../../packages/doeff-codex/README.md) | [所在](../../packages/doeff-codex/tests/test_scenarios.hy) | doeff-agentsのheadless codex handlerの土台。[agents](doeff-agents.md)は上位の`AgentType.CODEX`を使う例だけで、このpackageのeffectは紹介していない。実codexは未起動 |
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
| `doeff-hy-pytest` | doeff-hyのテストを走らせるpytestプラグイン。Python 3.14でHyの`ast.unparse`が止まらなくなる問題の修正と、テスト単位の時間制限（任意） | 記事なし（関連は[hy](doeff-hy.md)） | [定義・説明](../../packages/doeff-hy-pytest/README.md) | [所在](../../packages/doeff-hy-pytest/tests/test_plugin_distribution.py) | doeff-hyが依存するので、doeffを入れれば自動で入る。[hy](doeff-hy.md)の`deftest`はpytestで走らせるが、このプラグインは紹介していない。時間制限は既定で無効 |
| `doeff-image` | ImageGenerate/ImageEdit/ImageResultという共通の依頼と結果 | [image](doeff-image.md) | [定義・説明](../../packages/doeff-image/src/doeff_image/effects/generate.py) | [所在](../../packages/doeff-image/tests/test_image_effects.py) | それ自体は生成プロバイダではない |
| `doeff-indexer` | Program/@do/Kleisli定義・型情報の静的索引と探索 | [tooling](doeff-tooling.md) | [定義・説明](../../packages/doeff-indexer/src/indexer.rs) | [所在](../../packages/doeff-indexer/tests/deps.rs) | 依存エフェクトの解析とは役割を分ける |
| `doeff-jev` | doeff-system-oneの`Judge`をTypeSafeのJevで判定するhandler。宛先と鍵の解決、Vercel AI Gateway経由、答えのcache、判定の記録 | 記事なし（関連は[hooks](doeff-hooks.md)） | [定義・説明](../../packages/doeff-jev/README.md) | [所在](../../packages/doeff-jev/tests/test_handler.py) | provider側のpackage（doeff-llmに対するdoeff-openaiと同じ位置）。[hooks](doeff-hooks.md)は`Judge`の問いだけを書き、このhandlerは名指ししない。TypeSafe・Vercelへの実接続は未実行 |
| `doeff-linter` | Pythonコード品質・不変性・doeffの利用規則を検査 | [tooling](doeff-tooling.md) | [定義・説明](../../packages/doeff-linter/src/lib.rs) | [所在](../../packages/doeff-linter/tests/conftest.py) | READMEの規則数や速度は転載しない |
| `doeff-llm` | Chat/StreamingChat/StructuredQuery/Embeddingの共通エフェクト | [llm](doeff-llm.md) | [定義・説明](../../packages/doeff-llm/src/doeff_llm/effects/structured.py) | 専用テスト未特定（確認対象を保証しない） | README間にStructuredOutput/StructuredQueryの差。現行実装のStructuredQueryを使用 |
| `doeff-ml-nexus` | 依存の転送・環境構築・シリアライズ・ローカル/リモートDocker・GPU指定 | [remote](doeff-remote.md) | [定義・説明](../../packages/doeff-ml-nexus/src/doeff_ml_nexus/interpreters.hy) | [所在](../../packages/doeff-ml-nexus/tests/test_docker.py) | Programのシリアライズ往復は成功。上位ファクトリはdefkの契約不足でimport失敗。Docker/SSH/GPUは未実行 |
| `doeff-notify` | 通知・スレッド・確認の語彙、console/log/testingハンドラ | [operations](doeff-operations.md) | [定義・説明](../../packages/doeff-notify/README.md) | [所在](../../packages/doeff-notify/tests/test_handlers.py) | 組み込みSlack配送はない。testingの未対応effect分岐に旧Pass()が残るため、記事は型で選別して正しいPass(effect, k)で委譲 |
| `doeff-openai` | OpenAIの共通LLMエフェクト処理・構造化応答・ストリーミング等 | [llm](doeff-llm.md) | [定義・説明](../../packages/doeff-openai/README.md) | [所在](../../packages/doeff-openai/tests/_runner.py) | モデル対応・価格・実接続は今回検証していない |
| `doeff-openrouter` | OpenRouterの共通LLMエフェクト処理・構造化応答 | [llm](doeff-llm.md) | [定義・説明](../../packages/doeff-openrouter/README.md) | [所在](../../packages/doeff-openrouter/tests/conftest.py) | streamingは未実装の例外、embeddingはPass。全エフェクト・全モデルの互換を主張しない |
| `doeff-records` | 版つきの行・変更の列・追記の列のeffect（ReadRow・ListRows・PutRow・WatchChanges・AppendEvent等）と、memory/PostgreSQLのhandler、適合の筋書き | 記事なし | [定義・説明](../../packages/doeff-records/README.md) | [所在](../../packages/doeff-records/tests/test_laws.hy) | 置き場をhandlerで選ぶ記録基盤で、コード例を載せる記事がまだ無い。READMEは長く、冒頭の公開effectと定義の節を読んだ範囲。PostgreSQLとHTTPサービスは未実行 |
| `doeff-secret` | 秘密情報の取得/保存/一覧/削除、環境変数とメモリ上のテスト用ストア | [operations](doeff-operations.md) | [定義・説明](../../packages/doeff-secret/src/doeff_secret/effects/secrets.py) | [所在](../../packages/doeff-secret/tests/test_effect_handlers.py) | 環境変数・メモリの未対応分岐に削除済みDelegate()が残る。環境変数からクラウドへの自動fallbackは動作確認済みとしない |
| `doeff-seedream` | Seedream画像生成・編集と共通画像エフェクトのハンドラ | [image](doeff-image.md) | [定義・説明](../../packages/doeff-seedream/README.md) | [所在](../../packages/doeff-seedream/tests/test_edit_image.py) | 実生成未実行 |
| `doeff-system-one` | 較正された判定器に問う`Judge` effect。問いの3種類（choice・noul・score）と答えの型、確信度を見る純粋な道具、テスト用のscripted handler | [hooks](doeff-hooks.md) | [定義・説明](../../packages/doeff-system-one/README.md) | [所在](../../packages/doeff-system-one/tests/test_scripted_handler.py) | provider非依存の層。[hooks](doeff-hooks.md)は`Judge`を評価器の問いとして短く載せる。問いの3種類は紹介していない。実際の判定器は呼んでいない |
| `doeff-test-target` | 解析・挙動のテスト用シナリオ/エフェクト/ハンドラ | [tooling](doeff-tooling.md) | [定義・説明](../../packages/doeff-test-target/src/doeff_test_target/orchestrate.py) | [所在](../../packages/doeff-test-target/tests/test_smoke.py) | 利用者向け業務機能として売らない |
| `doeff-time` | Delay/WaitUntil/GetTime/ScheduleAt/SetTime、同期/asyncio/仮想時間 | [完全な例](examples/scheduling.py)(2026-10-30から、[time](doeff-time.md)はdoeffだけで時計を自分で書く形にし、doeff-timeを使わない) | [定義・説明](../../packages/doeff-time/src/doeff_time/handlers/sim_time.py) | [所在](../../packages/doeff-time/tests/conftest.py) | 仮想時間と実時間は既存記事例で実行確認 |
| `doeff-traverse` | Traverse/Reduce/Zip/Inspect/SortBy/Take/Fail/Skip、逐次/並行/失敗の戦略 | [traverse](doeff-traverse.md) | [定義・説明](../../packages/doeff-traverse/doeff_traverse/handlers.py) | [所在](../../packages/doeff-traverse/tests/test_memory_leak_multi_day.py) | Hy for/do/From/When/Reduceも掲載。旧foldは公開マクロになく置換。継続の複製ではなく新しい計算を生成 |
| `doeff-validation` | 独立した検査を全部走らせ、落ちた検査の失敗を全部集めて`ValidationException`で知らせる。doeff-traverseの上の薄い層 | 記事なし（土台は[traverse](doeff-traverse.md)） | [定義・説明](../../packages/doeff-validation/README.md) | [所在](../../packages/doeff-validation/tests/test_validate.py) | 逐次・並行・最初の失敗で止める、の選択はtraverseのhandlerで行う。Hyの`validate`/`check`マクロはdoeff-hy側にある。記事には登場しない |
| `doeff-vm` | Pythonバインディング・VM接続・継続/トレース等の公開プリミティブ | [VM](doeff-vm.md) | [定義・説明](../../packages/doeff-vm/Cargo.toml) | [所在](../../packages/doeff-vm/tests/test_pyvm.py) | 独立した業務機能としては扱わない |
| `doeff-vm-core` | RustのVM本体・継続管理・実行機構 | [VM](doeff-vm.md) | [定義・説明](../../packages/doeff-vm-core/Cargo.toml) | [所在](../../packages/doeff-vm-core/tests/test_vm_module_split.py) | 速度比較や耐久性の実証はしていない |

## 9月の棚卸し以後に足した9パッケージ

2026-09-16の棚卸しの後に、次の9パッケージが`packages/`へ加わった。上の表に足し、READMEの冒頭とpyproject.tomlを読んだ静的な棚卸しにとどめる。テストは実行しておらず、記事への対応付けも次の通り限られる。

| パッケージ | 加わった日 | 記事での扱い |
| --- | --- | --- |
| `doeff-jev` | 2026-09-22 | 記事なし |
| `doeff-system-one` | 2026-09-22 | [hooks](doeff-hooks.md)が`Judge`を短く載せる |
| `doeff-claude-code` | 2026-09-25 | 記事なし。上位のdoeff-agentsは[agents](doeff-agents.md) |
| `doeff-cluster` | 2026-09-25 | 記事なし |
| `doeff-hy-pytest` | 2026-09-26 | 記事なし |
| `doeff-records` | 2026-09-26 | 記事なし |
| `doeff-validation` | 2026-09-26 | 記事なし |
| `doeff-codex` | 2026-10-10 | 記事なし。上位のdoeff-agentsは[agents](doeff-agents.md) |
| `doeff-claude-judge` | 2026-10-18 | 記事なし |

日はgitで`packages/<名>`が最初に入ったコミットの日付。「記事なし」の8つは、記事を書く時にこの表へ紹介先を足す。外した`doeff-agentd`は、2026-07-30のコミット7e12d0068でgitの管理から削除された。9月の棚卸しのcheckoutには未追跡のCargo.lockだけの残骸があったが、2026-10-17のこの作業の時点のcheckoutには無い。`verify_publication.py`は`packages/`の中のディレクトリを全部数えるので、残骸が手元に残る環境では表に無い名として落ちる。

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
`packages/`の全ディレクトリと表の行の対応、記事の導線とコード、図も確認する（2026-09-16の追補では31ディレクトリ・23記事・46図）。

今後パッケージを追加・廃止した場合はこの表とメイン記事の入口を一緒に見直す。`verify_publication.py`は、この表の行（先頭の列の`doeff-…`）と`packages/`の中のディレクトリが過不足なく一致するかを毎回照らす。記事本文の正本は各Markdown、プレビューはそこから生成する。

## 深掘り記事と図の追加

- [多段メモ化](doeff-memo.md)：core-effectsのmemo_handler、コストによる経路選択、ストレージ階層を説明。メモリ3層の取得・補充・削除を実行確認。Redis・MinIOは組み込みとして確認していないため、アダプタを用意する構成例と明示。
- [エフェクトの境界](doeff-boundaries.md)：任意のドメイン操作、Get/Putへの翻訳、自作ハンドラと契約、継続と協調的優先度を説明。状態更新例を実行確認。
- [Hy](doeff-hy.md)：Pythonとの共通基盤、defk・契約・<-・式中の!を説明。掲載したPython・Hyの例を実行確認。
- [関数の色](doeff-color.md)：PEP 3156とPEP 492を参照し、初期asyncioのyield fromとdoeffのyieldの共通点・違いを記載。旧asyncio例は歴史の説明用で、現行環境では実行しない。
- 全23記事に計46図を配置。imagegenによる掲載PNGと生成・編集プロンプトを管理し、全記事の画像参照と容量を検査。

最新の実装根拠・成功した検査・残る不整合は[記事別レビュー](reviews/README.md)に記録する。全ディレクトリの実環境での成功を意味する表ではない。
