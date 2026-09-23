# durable記事の再精査

- 担当: 記事専用エージェント `review_durable`
- 日付: 2026-09-16
- 対象: `doeff-durable.md`、専用例 `examples/durable.py`
- 使用スキル: `doeff-patterns`、`doeff-runtime`、`doeff-hy-macros`

## 指摘と修正

1. 本文とコードを全行コメント付きにした。Pythonは目的と期待する結果、Hyは依頼と再生結果、シェルは各プロセスの期待動作を説明した。
2. `@do`による解析・見出し抽出・段階の合成を維持し、`yield prepare()`と`yield make_outline(...)`でつないだ。利用者の処理全体をasync関数に逃がしていない。SQLite内部が発行するAwaitとの役割の違いも説明した。
3. 最初のコードを早めに提示し、SQLiteの構成、3プロセスの結果、保存単位の説明へ並べ直した。
4. `@cache()`の既定キーが関数の完全修飾名と引数であることを明記した。処理の版は識別用の引数であり、関数本体の変更を自動検出する保証ではない。
5. プロセスをまたぐ完了結果の再利用と、生きた継続・Pythonスタック・未完了外部操作の復元を区別した。外部操作成功とMemo保存の間には再実行の可能性がある。
6. `durable.py`の説明を「2プロセス」から実際の「3プロセス」に修正。ハンドラ構成をtryの前に作り、構成失敗時に未定義のprogramをfinallyで削除する問題も除いた。
7. Conductorの時刻・乱数の再生について、実行IDだけでなく依頼の識別と順序が一致する部分を再利用することを説明した。
8. `run_recorded_agents`は合成用関数ではなく、run_syncを使う検証用の実行入口として位置付けた。記事の関数を既存回帰テストの実行入口に差し替え、呼び出し方法そのものを検証した。
9. 例外による中断・再利用の回帰テストと、実プロセスの強制終了試験を区別した。テスト名の「kill」を、そのまま実プロセスの強制終了という主張にしない。

## 実装根拠

- `packages/doeff-core-effects/doeff_core_effects/cache.py`: `cache()`のキー構築、MemoExists/Get、計算成功後のMemoPut。ハンドラ不在なら計算に進むため、永続化には保存ハンドラの設置が必要。
- `packages/doeff-core-effects/doeff_core_effects/_memo_handlers_impl.hy`: Memoの存在確認・取得・保存をstorageへ委譲する構成。
- `packages/doeff-core-effects/doeff_core_effects/storage/sqlite.py`: 値をpickleへ変換してSQLiteへ保存し、ストレージ操作をAwait経由で行う。close()は呼出元スレッドの接続を閉じる。
- `packages/doeff-conductor/src/doeff_conductor/api.py`: 同一run_idでワークフローの保存済みソースとjournal_state_dirを使う構成。
- `packages/doeff-conductor/src/doeff_conductor/workflow_effect_journal.py`: 時刻・乱数依頼のキー、順序、最長一致部分の再利用。
- `packages/doeff-conductor/src/doeff_conductor/handlers/journaled_agent.py`: AgentEffectをAgentReplaySessionへ渡し、有効な保存結果がなければ委譲先を使う。
- `packages/doeff-conductor/tests/test_agent_journal_c3.py`: モックによる例外後の有効結果の再利用、識別変更、履歴破損の回帰検査。

## 検証

- `uv run --no-sync python publications/zenn-use-cases-v0/reviews/durable-check.py`
  - SQLiteを共有する3つの実Pythonプロセスを起動。
  - 1回目は解析だけ実行、2回目は抽出だけ実行、3回目は両方を再利用。
  - 標準出力の完全一致で実行回数と返り値を検査。3プロセスとも標準エラーなし。
  - 記事のPython4ブロックを抽出して実行。本文のSQLite構成も2回目は計算本体を通らない。
  - Conductorの同一実行IDで時刻・乱数の結果が一致。
  - 本文のJournaledAgentHandler構成を既存回帰シナリオへ差し替え、完了済み2件の再利用と3件目だけの追加実行を確認。ここで表示される「simulated kill after node 2」は意図した例外で、テストは成功。
- `uv run --no-sync pytest packages/doeff-conductor/tests/test_agent_journal_c3.py -k 'not l2_launch' -q`
  - **3 passed、1 deselected**。例外後の再利用、識別変更による再計算、破損履歴のエラーを検証。
  - 焦点を絞った実行のため、対象外ADR未収集の既存警告1件あり。全リポジトリ検査を実行したという主張はしない。
- `uv run --no-sync ruff check publications/zenn-use-cases-v0/examples/durable.py publications/zenn-use-cases-v0/reviews/durable-check.py`
  - 成功。
- tokenizeによる確認で、専用例と本文4ブロックのPython実質**93行**にコメントあり。空行・括弧だけの行・モジュール説明文字列を除外。Hy構文解析およびHy・bash全実質行のコメントも確認。

## 画像仕様と共通ファイル

`reviews/durable-visuals.json`に概念図・処理図のコード、タイトル、矢印の意味、captionを記録した。本文の画像altとcaptionも一致させた。生成・差し替えは親担当。

共有ファイル`examples/workflow_journal.hy`は編集していない。記事のHyブロックと同じコメントを親担当が反映する必要がある。`examples/external_workflows.py`へのdurable固有の変更要求はない。

## 実行していない範囲

実エージェント起動、外部API、ネットワーク送信、実プロセスの強制終了、未完了外部操作の復旧、永続タイマーの復旧、外部操作を一度だけ実行する保証。runtimeやpackagesのコードは変更していない。
