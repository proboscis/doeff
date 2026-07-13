# doeff 2026-07-13 slog semantics — SlogEffect 型分離 + .log side-channel 全廃 master plan

driving ADR: `docs/adr/defadr_doeff_core_effects_001_slog_observability.hy`(ADR-DOE-CORE-EFFECTS-001, status: proposed)

決定の要旨(詳細・laws・counterexamples は ADR が正):

- R1: `slog()` は新設 `SlogEffect(msg, **kwargs)` を返す。`Slog` エイリアスは `SlogEffect` へ。`WriterTellEffect` は `Tell()` 専用に戻る。継承関係なし。
- R2: `slog_handler()` = SlogEffect の terminal sink(rich 非依存 1 行整形 → stderr、consume)。
- R3: handler install への mutable 収集属性(`.log`)全面禁止。収集は `Listen(prog, types=(SlogEffect,))` の値フローのみ。`writer()` も `.log` を落とし Tell の terminal sink になる。
- R4: unhandled SlogEffect は `UnhandledEffect` のまま(VM 特例なし)。可視性はエントリポイント(CLI `default_interpreter`・テストハーネス)が sink を標準装備して保証。
- R5: 静音は `slog_discard_handler()` の明示 opt-in。`slog_capture_handler` は導入しない(Listen と重複、side-channel 再導入の誘因)。
- R6: 横読み消費者(sim_time / docker / preset)を型分離に整合。存在しない `effect.message` 参照は禁止。

## Source of truth

| 種別 | パス | 役割 |
| --- | --- | --- |
| 実行可能 ADR | `docs/adr/defadr_doeff_core_effects_001_slog_observability.hy` | 決定・laws・defsemgrep 自己検証(本計画の正) |
| master plan | `docs/doeff-2026-07-13-slog-semantics-architecture-plan.md` | 本ファイル。/goal の実行面 |
| コード証拠 | `packages/doeff-core-effects/doeff_core_effects/effects.py:110-134` | 現行 WriterTellEffect / Slog エイリアス / slog() |
| コード証拠 | `packages/doeff-core-effects/doeff_core_effects/handlers.py:72-90,135-154,205-236` | writer() / slog_handler() / listen_handler(tee 実装) |
| コード証拠 | `doeff/cli/run_services.py:175-197` | default_interpreter(沈黙の原因サイト) |
| 静的ガード | `.semgrep.yaml`(222 rules、slog/.log/effect.message 関連は現状ゼロ) | Stage 1 で 3 rules 追加 |
| collection | `packages/doeff-adr/src/doeff_adr/pytest_plugin.py:16-39` + `packages/doeff-adr/pyproject.toml:20-21`(pytest11) | `docs/adr/defadr_*.hy` の収集。root testpaths 非包含 → パス明示必須 |
| 先行 ADR 様式 | `docs/adr/defadr_doeff_agents_004_effects_session_host.hy` | ID 形式・defsemgrep inline 形式の参照 |

## Current state(観測事実と Gap)

| # | 事実 / Gap | 証拠 |
| --- | --- | --- |
| F1 | 素の `run()` + slog → `UnhandledEffect`(2026-07-13 実測) | セッション実測ログ |
| F2 | `slog_handler()` は黙って list 収集、表示なし。CLI `default_interpreter` はその収集結果を捨てる | `handlers.py:135-154`, `run_services.py:191` |
| F3 | default stack で slog_handler が writer() より内側 → Tell も食われ writer() は常に空 | `run_services.py:190-197` |
| F4 | `.log` 生産者 2(handlers.py:89,153)、読者 3(`tests/test_core_effects.py:170-171`, `tests/effects/http_request_support.hy:85`, `packages/doeff-vm/tests/test_pyvm.py:210`)、stale docstring `handler_log`(handlers.py:76) | grep 実測 |
| F5 | preset は現行 API に対し破損: `log_display.py:94` が存在しない `effect.message` 参照 + 旧 `Pass()` シグネチャ。`tests/test_pass_then_typed_handler.py:54` も同属性参照 | 読解 |
| F6 | 横読み消費者: `sim_time.py:130`(log formatter)、`dockerfile.hy:58` | grep 実測 |
| F7 | `listen_handler` は tee(collect 後 Pass、types 指定可) → sink 変更と収集は独立 | `handlers.py:205-236` |
| Gap1 | `.log` 削除を記録した ADR・semgrep が存在しない(erosion の根) | `git log -S 'install.log'`、`.semgrep.yaml` 全 222 rules 走査 |
| Gap2 | repo 共通の `doeff_interpreter` fixture は存在しない(deftest は要求名 fixture を消費側が供給) | explorer 調査: `tests/conftest.py:274-277` は `interpreter` のみ |
| 未確認 | doeff-vm(Rust)側に WriterTellEffect 依存なし(grep ゼロ)— Rust 再ビルド不要の想定。Stage 2 で `make sync` 不要かは実装時に再確認 | grep 実測 |

## 依存図

```
ADR-DOE-CORE-EFFECTS-001 (proposed)
  |
  |-- Stage 1: RED  (failing tests + semgrep 3 rules が現行コードに火を吹く)
  |       tests/test_slog_semantics.py (新規, 全部 fail)
  |       .semgrep.yaml: no-handler-log-attribute / no-effect-message-attribute /
  |                      no-structured-payload-on-writer-tell  (hit 確認)
  |
  |-- Stage 2: CORE (doeff-core-effects + CLI)          <- Stage 1 の RED を GREEN 化
  |       effects.py: SlogEffect 新設 / Slog 付替 / slog() 変更
  |       handlers.py: slog_handler=stderr sink / slog_discard_handler /
  |                    writer() .log 撤去 / docstring 修正
  |       run_services.py: default_interpreter 装備替え
  |
  |-- Stage 3: REPAIR (横読み消費者 + .log 読者の移行)   <- Stage 2 後に並列可
  |       sim_time / dockerfile.hy / preset(修理 or 退役) / .log 読者 3 サイト
  |
  '-- Stage 4: GATE (full pytest + make lint + ADR collection green)
          -> ADR status accepted への昇格判断(frontier)
```

## Completion gates

1. `uv run pytest docs/adr tests packages/doeff-core-effects -q` green(ADR 契約 + defsemgrep 自己検証 + 新 deftest 群を含む)。
2. `make lint` green — 新 3 rules を含む semgrep がリポジトリ全体でクリーン(= `.log` 生産者/読者・`effect.message`・kwargs 付き WriterTellEffect 構築が根絶)。
3. 実挙動確認: `echo` 相当の実 CLI 実行で slog が stderr に 1 行出る / stdout は結果のみ(`uv run python -m doeff run ...` またはスクリプト実測)。
4. ADR `:plans` ↔ 本 plan の相互リンクが現物と一致。ADR status 昇格は全 gate 通過後に別途判断。

## Master TODO(2026-07-13 実施済み — 実績注記付き)

| ID | source | 作業 | red(反例) | green(機構) | status |
| --- | --- | --- | --- | --- | --- |
| T1 | R1-R5 | `tests/test_slog_semantics.py` 新規(8 tests) | 実測 RED: 7 fail / 1 pass(unhandled-loud は pin) | Stage 2 実装で 8 passed | done |
| T2 | R3,R6 | `.semgrep.yaml` に rules 追加。実測 fire: no-handler-log-attribute → handlers.py:89,153 / no-effect-message-attribute → preset 2 + tests 1 / no-structured-payload-on-writer-tell → effects.py:134。**訂正**: effect.message の素朴 pattern は他ドメイン effect の正当な .message フィールドに 17 件誤検知 → isinstance ガード形状(WriterTellEffect/SlogEffect 限定)へ絞り込み。**追加**: no-writer-tell-alias-slog(`WriterTellEffect as Slog` 別名 import の禁止 — 実施中に memo/cache で実例発見)で計 4 rules | 4 rules fire 確認済み | 根絶後 0 findings 確認済み | done |
| T3 | R1 | `effects.py`: SlogEffect 新設 / slog() 差替 / `Slog = SlogEffect` / WriterTellEffect は Tell 専用(kwargs 撤去、repr は `Tell(...)`)。exports 追従(doeff / doeff_core_effects) | T1 RED | 実装済み | done |
| T4 | R2,R3,R5 | `handlers.py`: slog_handler = stderr sink(`_format_slog_line`: `LEVEL msg key=value`)、slog_discard_handler 新設、writer() = Tell の silent sink(.log と handler_log 言及撤去) | T1/T2 RED | 実装済み | done |
| T5 | R4 | `run_services.py`: **コード変更不要と判明** — slog_handler 自体が sink になったため既存装備で可視化が成立(T1(a) で担保)。writer() も Tell を受けるようになった(F3 解消) | T1(a) fail | T1(a) pass | done |
| T6 | R3 | `.log` 読者移行 — 当初 3 サイト + 追加発見 2(`tests/test_core_effects.py` TestWriter/TestComposed の `w.log`)= 計 5 サイトを Listen 値フローへ。`handler-log` ヘルパ削除(http_request_support.hy) | T2 rule hit | Listen 書換、全 green | done |
| T7 | R6 | **訂正: 両方とも無変更が正**。sim_time の log_formatter は Tell(Writer チャネル)刻印機構でテストも全て Tell 使用(`test_sim_time.py:337-345`)→ WriterTellEffect のまま正しい。dockerfile.hy は Writer-as-data(命令を Tell で流し観測)→ 正しい。docker.hy の slog は新シグネチャ互換 | — | doeff-docker 10/10 pass | done |
| T8 | R6 | preset 裁定: **R6 違反除去 + 型整合のみ実施**(log_display/production/testing/effects/log.py を SlogEffect へ、`.message` 参照根絶)。パッケージ自体は削除済み `default_handlers` API 依存で**変更前から実行不能**(17/21 fail、変更後も同数 = 新規回帰ゼロ)。retire か全面書き直しかは orch フォローアップへ。`tests/test_pass_then_typed_handler.py` も stale 参照修正 | F5 | R6 clean、preset は pre-existing 状態維持 | done(残: retire 判断) |
| T9 | gate | 下記「Stage 4 gate 実績」参照 | — | — | done |

## Stage 4 で発見・修理した追加の浸食(当初計画外)

1. **`WriterTellEffect as Slog` ローカル stale エイリアス × 2**: `memo_handlers.py:199` / `cache_handlers.py:165`。ADR が殺した conflation の局所再生。→ 正規 `Slog`(= SlogEffect)import へ修正 + `no-writer-tell-alias-slog` rule 追加。
2. **memo-layer telemetry の意味論違反**: `_memo_handlers_impl.hy` が観測用 telemetry を bare `WriterTellEffect` で放流(旧世界では slog_handler が偶然消費)。型分離後は unhandled となり、rewriter の `except UnhandledEffect` が **MemoPut ごと**飲み込んで memo HIT が壊れる regression として顕在化(`test_memo_rewriter_no_terminal` で検出、baseline 比較で当方起因と確定)→ telemetry を SlogEffect へ。
3. **テストスタックの暗黙依存 × 2**: `tests/test_do_bang_setv.py` / `tests/test_try_handler_scope.py` が「writer が slog を偶然消費」に依存 → slog_handler をスタックに明示装備。

## Stage 4 gate 実績(2026-07-13)

| gate | 結果 |
| --- | --- |
| full pytest(testpaths + docs/adr) | **1 failed / 963 passed / 86 skipped** — 唯一の fail は `tests/architecture/test_no_public_withhandler_shim.py`(offender = `packages/doeff-adr/tests/test_defadr_macros.py`、本チェンジ未接触の pre-existing)。slog 関連は全 green(ADR 自己検証 5 tests 含む) |
| semgrep(make lint-semgrep) | 本 ADR の 4 rules = **0 findings(clean)**。リポジトリ全体は pre-existing 140 findings で元々 red(vm-* / bare-return 等、slog 無関係)。変更ファイルへの新規 findings なし |
| ruff(変更ファイル) | clean(memo_handlers.py の F401×2 は baseline でも発生する pre-existing) |
| CLI 実挙動 | `default_interpreter` 実行で stdout=`RESULT-OK` のみ、stderr=`    INFO cli-visible step=gate` — 当初の「slog が見えない」混乱の解消を実機確認 |
| ADR status | `proposed` のまま(landing は orch 経由。merge 後に accepted 昇格判断) |

## main 統合(2026-07-13、merge origin/main 時の裁定)

merge 時に判明: main は 0acce3a9(2026-07-09)で同じ .log 廃止を別解で実施済みだった
(State 収集 + `writer_log()`/`slog_log()` 値フロー、handler の installer 化、
handler からの direct IO 排除哲学、59 ファイル掃討)。ユーザー裁定による統合形:

- **slog_handler = 表示専用**(installer 形状、stderr 1 行、収集しない)— observability の
  IO boundary として direct-IO 排除哲学と整合(ShellRun handler と同型)
- **Tell の蓄積 = main の writer + writer_log()**(State 収集)を正とする
- **slog_log() は退役**(導入 4 日 — 表示専用 sink に読む state が無い)。slog の収集は
  `Listen(prog, types=(SlogEffect,))` の値フロー
- SlogEffect 型分離(本 ADR R1)は main と競合せず採用。handler 不在の Listen/Tell/slog は
  UnhandledEffect で loud(ユーザー確認済み)
- 反省として記録: 本チェンジは実装開始前に origin/main との乖離(85 commits)を確認しなかった。
  同じ結合核の再設計が 4 日前に landing 済みで、merge 時に設計衝突として顕在化した。
  結合核に触る前に main を fetch して対象サブシステムの履歴を見るのを必須手順とする

## Pre-existing 負債の棚卸し(本チェンジ外・要フォローアップ)

- doeff-preset: 17/21 fail(削除済み `default_handlers` / 旧 protocol handler 規約 / 無引数 `Pass()`)→ **解消済み(2026-07-13 ユーザー裁定: retire)**: ADR-DOE-PRESET-001 でパッケージごと退役。enforcement は `tests/test_doeff_preset_retired.py` + semgrep `no-doeff-preset-import`。examples の残余 rot(`run_program` / `default_handlers`)は別負債として下記に残る
- doeff-time: 18 fail(`_protocol_handler() missing 'k'` — 旧規約)
- doeff-flow: collection error(`RunResult` は doeff_vm から削除済み)
- doeff-notify: 3 fail(`default_handlers` 削除)
- doeff-agents / doeff-agentic の examples 14 ファイル: 削除済み API(`run_program(scoped_handlers=...)` / `default_handlers`)参照の bit-rot(preset 退役時に doeff_preset 参照のみ除去済み — ADR-DOE-PRESET-001 R4)
- `tests/architecture/test_no_public_withhandler_shim.py`: `packages/doeff-adr/tests/test_defadr_macros.py` が banned pattern を含む
- `make lint-semgrep`: 全体 140 pre-existing findings / `memo_handlers.py` F401×2

## Staged implementation plan(TDD 順序厳守)— 全 Stage 完了(2026-07-13)

- Stage 0(完了): ADR + 本 plan 作成、collection 確認。
- Stage 1(完了): T1 失敗テスト(7 fail 実測)+ T2 semgrep rules(fire 実測)。
- Stage 2(完了): T3 → T4 → T5。T1 8/8 green。`.rs` 変更なし(doeff-vm に WriterTellEffect 依存なしを確認済み、`make sync` 不要)。
- Stage 3(完了): T6 / T7 / T8。
- Stage 4(完了): T9 — gate 実績は上表。

## Subagent spawn strategy

| role | task | scope | 並列群 | 期待出力 | 検証コマンド | 権限 | model |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 実装(core) | T1, T3, T4, T5(意味論の中核 — sink 契約・型分離・エントリポイント) | doeff-core-effects, doeff/cli, tests/ | A(直列) | 単一チェンジセット | `uv run pytest tests/test_slog_semantics.py docs/adr -q` | edit+run, commit 禁止 | frontier(inline 推奨) |
| 静的ガード | T2(.semgrep.yaml 3 rules + hit/clean 記録) | .semgrep.yaml | A | rules + fire ログ | `make lint-semgrep` | edit+run, commit 禁止 | frontier(policy 判断を含む) |
| 移行(mechanical) | T6(.log 読者 3 サイトの Listen 化) | tests/, packages/doeff-vm/tests | B(Stage 2 後) | 差分のみ | `uv run pytest tests/test_core_effects.py tests/effects packages/doeff-vm/tests -q` | edit+run, commit 禁止 | sonnet(assert の意味は既決) |
| 移行(mechanical) | T7(sim_time / dockerfile の isinstance 追従) | packages/doeff-time, packages/doeff-docker | B | 差分のみ | 各 package の pytest | edit+run, commit 禁止 | sonnet |
| 裁定+修理 | T8(preset 修理 or 退役の判断と実施) | packages/doeff-preset | B | 裁定メモ + 差分 | preset tests + `make lint` | edit+run, commit 禁止 | frontier(退役判断は decision-dense) |
| verifier | 相互リンク・stale 用語(.log / effect.message / handler_log 残骸)・collection 到達性 | repo 全体 read-only | 各 Stage 末 | 指摘表 | `rg -n 'install\.log|effect\.message|handler_log'` | read-only | sonnet |

親セッションが全差分を frontier レベルでレビューしてから単一チェンジセットとして扱う。ワーカー途中で方針判断が必要になったら improvise せず報告(coupling-core routing)。

## Non-goals

- VM(Rust)への lastResort・SlogEffect 特例の追加(R4 で明示棄却)。
- Writer/Listen の意味論変更(tee 実装・types 既定は無変更)。
- rich ベース表示の core への導入(preset の領分)。
- 本リポジトリへの commit / push / PR 作成 — landing は orch 経由(リポジトリ規約)。本 plan はそのための issue 素材を兼ねる。
- ログレベルによるフィルタリング機構・環境変数での sink 切替(将来課題。今回は「見える」の回復のみ)。

## Immediate next action

実装は working tree 上で全 Stage 完了(commit 未実施 — landing は orch 経由が本リポジトリの規約)。次のアクション:

1. orch issue を作成し、本 plan + ADR を参照して changeset を landing する(commit / PR は orch が選んだエージェントのみ)。
2. merge 後に ADR status を `accepted` へ昇格。
3. フォローアップ orch issue: doeff-preset の retire or 全面書き直し裁定(pre-existing 負債の棚卸し参照)。
