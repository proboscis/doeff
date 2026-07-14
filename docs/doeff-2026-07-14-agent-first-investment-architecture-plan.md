# doeff 投資計画 2026-07-14 — agent-first 前提の能力消化と自己 enforcement

> 出自: 2026-07-14 の doeff 価値評価(消費 14 リポジトリを 7 体の調査エージェントで横断監査)と、
> それに続く maintainer との設計議論。前提は **書き手は常にエージェント、人間はレビュアーのみ**。
> 反復パターン「力を作る速度が使う速度を上回る」(retry/budget/replay 文書化済み未使用、
> deftest 配線済み未使用、VM oracle 実装済み未配線、semgrep 229 ルール未実行)への対処として、
> 本計画は **新しい力ではなく、既にある力を安全に・安く使える状態にする**投資を優先する。
>
> 裁定済み(2026-07-14 maintainer):
> - **A3 = guard-error 確定**(auto-bind 不採用。エラーメッセージは修正プロンプト形式)
> - **B3 = dev ビルドは doeff-vm-core feature `invariant-checks` + `python_bridge` 常時有効**
> - **ゲート正典はローカル pytest**(GitHub CI は予算により停止中 — 再有効化は選択肢にない)

## Source of truth

| 種別 | パス | 内容 |
|---|---|---|
| defadr | `docs/adr/defadr_doeff_hy_001_statement_bind_guard.hy` | ADR-DOE-HY-001: statement 位置 bind guard(Track A) |
| defadr | `docs/adr/defadr_doeff_enforce_001_pytest_canonical_gate.hy` | ADR-DOE-ENFORCE-001: pytest 正典ゲート(Track B) |
| defadr | `docs/adr/defadr_doeff_core_002_lazy_creation_trace.hy` | ADR-DOE-CORE-002: 生成トレース遅延化(Track C、enforcement は C1 後) |
| defadr | `docs/adr/defadr_doeff_hy_002_deftest_expressiveness.hy` | ADR-DOE-HY-002: deftest 表現力契約(Track D) |
| defadr | `docs/adr/defadr_doeff_domain_001_vocabulary_cohesion.hy` | ADR-DOE-DOMAIN-001: defdomain / SEDA / 適合検査(Track E) |
| 理論文書 | `docs/22-capability-classes.md` | class 0–3 権力分類・採用損益規則・anti-pattern(2026-07-14 新規) |
| 既存主張 | `docs/20-why-effects-over-di.md` | DI 対比の主張(22 番が理論で補完) |
| 監査 | `docs/crystallization/erosion-audit-2026-07-02.md` | enforcement 侵食監査(本計画 Track B の根拠) |
| 既存 issue | `ci-wire-enforcement-layer` / `doeff-adr-wiring-selfcheck` | 監査由来の配線 issue(Track B に吸収) |
| WIP 資産 | `packages/doeff-effect-analyzer/README.md`, `specs/effect-analyzer/` | SEDA(Track E2 の土台) |
| 下流証拠 | proboscis-ema VAULT『doeff Tracing Overhead … 3.7x』/ ISSUE-TRD-185/186 | Track C の実測根拠、scheduler リーク事故 |
| 下流証拠 | mediagen `tests/support/run_test.py` / ADR-008 §5 | Track D の違反シグナル(シム再発明) |
| 下流証拠 | agent-control-plane ADR 0056 / 0031 / erosion-audit 核#8 | Track A の gotcha 実証、Track E の増殖実害 |

## Current state(観測事実と Gap)

| # | 事実 / Gap | 根拠 |
|---|---|---|
| F-1 | ~~statement 位置素通し~~ **解消(T-A1、2026-07-14)**: 4 emit サイトに guard 実装、HY-001 green、1094 テスト無回帰 | macros.hy(_wrap-statement-guard) |
| F-2 | ~~pyproject testpaths に docs/adr 不在~~ **解消(T-B1、2026-07-14 PR #521)**: root testpaths に docs/adr を追加(flip 実施)。defadr 収集自己検査と strict 検査が既定 pytest で稼働し、DOMAIN-001 は xfail(strict) 相当で E1 待ちを表明 | pyproject.toml / tests/test_adr_wiring_gate.py |
| F-3 | ~~oracle 起動経路ゼロ~~ **解消(T-B3、2026-07-14)**: dev ビルド常時有効 + hard-fail pytest 常駐(実測 True) | Makefile / tests/test_vm_invariant_checks_enabled.py |
| F-4 | ~~semgrep 手動のみ~~ **ゲート稼働 + 違反解消済み(T-B2、2026-07-14 PR #523)**: violations 0、baseline 0。残: 個別ルールの defsemgrep fixture 化・semgrep の dev 依存化 | tests/test_semgrep_gate.py / docs/adr/semgrep-baseline.json |
| F-5 | deftest は最大 Python 消費者(mediagen)で使用 0 / 260。**:env 経路は doeff 側では機能する**と実証済み(HY-002 roundtrip green)— 残ギャップは消費側配線と語彙拡張(D2) | mediagen conftest / docs/adr/conftest.py |
| Gap-1 | ~~docs/adr に conftest.py が無い~~ **解消済み(Stage 0)**: `docs/adr/conftest.py` を新設(:env を reader で反映・エラー再送出。ADR-DOE-HY-002 R3 の参照実装) | 2026-07-14 実装 |
| Gap-2 | T-A1/B3 は green、root testpaths の flip 済みで ADR 検査と wiring 自己検査が既定ゲートに常駐。残る designed-red は DOMAIN-001 の xfail(strict) 相当 1 件のみ(E1 未実装中は xfail、defdomain 実装で緑化したら fail して記帳を強制) | `uv run pytest -q` 2026-07-14 / tests/test_adr_wiring_gate.py |
| Gap-3 | ~~CORE-002 enforcement blocked~~ **解消(T-C1、2026-07-14)**: 現行 main に per-effect 捕捉は存在しないと確定。回帰ガード deftest green。下流 TRD-002 は旧版への計測 — pin 更新後の再検証を下流へ差し戻し(issue に追記済み) | C1 spike / CORE-002 enforcement |
| Gap-4 | `doeff-main-with-handler-stack`(489MB 姉妹 checkout)は 2026-07-11 に main へ完全マージ済みの残骸と判明 — 計画対象外、削除は maintainer 判断 | git merge-base 検証 2026-07-14 |

## 依存関係(ASCII)

```
                 [B: pytest 正典ゲート]  ←—— ゲートが無ければ全 enforcement は飾り
                  B1 testpaths+自己検査
                  B2 defsemgrep 化       B3 VM oracle 配線(裁定済)
                  B4 台帳 ratchet
                     │ (ゲート成立)
      ┌──────────────┼──────────────────┬───────────────┐
      ▼              ▼                  ▼               ▼
[A: bind guard]  [C: trace(再スコープ)] [D: deftest 契約] [E: defdomain/SEDA/適合]
  A1 ✅ 実装済み   C1 ✅ 税は不存在      D1 ✅ 大半完了     E1 defdomain(設計=frontier)
  A2 下流洗い出し  C2 ✅ 行解決遅延化    D2 シム語彙吸収    E2 SEDA CLI/MCP
  (A3 裁定済み)   C3 裁定(優先度低)    D3 mediagen 移行   E3 適合検査
                                        (下流)            (B 完了が前提: R4)
      │
      ▼
[将来(本計画外): class 2 新プリミティブ — rate-limit scheduler / batching / hedging]
```

## Completion gates(計画全体の完了条件)

1. `uv run pytest`(既定 testpaths)が docs/adr の全 defadr enforcement を収集・実行する(ENFORCE-001 law)。
2. 本計画で追加した red enforcement 4 本が green(HY-001 guard / ENFORCE-001 testpaths+oracle / DOMAIN-001 defdomain)。
3. ~~ADR-DOE-CORE-002 に実測に基づく enforcement を追加~~ **達成(2026-07-14)**: 回帰ガード green、T-C2 は PR #520 で完了。残: 下流 TRD-002 の再検証。
4. ADR-DOE-HY-002 の :env roundtrip が green、かつ mediagen の run_test.py が deftest 語彙で置換可能なことを下流で実証。
5. 全 5 ADR の :status が "accepted" に昇格(enforcement green が条件)。
6. semgrep 229 ルール+VM oracle が「バイナリ/feature 不在 = hard fail」で既定ゲートに常駐。

## Master TODO

| ID | 出典 ADR | 作業 | red 反例(現状) | green 機構 | 状態 |
|---|---|---|---|---|---|
| T-B1 | ENFORCE-001 R2 | testpaths に docs/adr 追加 + defadr 収集自己検査を doeff-adr に実装 | test-…-docs-adr-in-default-testpaths が red | root testpaths の flip 実施。`pytest_collection_finish` で全 defadr 候補と session.items を照合する自己検査(既定 warn / strict で非ゼロ終了)と `doeff-adr verify-wiring` CLI を実装し、ADR-DOE-ADR-001 に記録。DOMAIN-001 は xfail(strict) 相当で既定ゲートを green 維持 | **完了(2026-07-14 PR #521)** |
| T-B2 | ENFORCE-001 R3 | .semgrep.yaml を既定 pytest ゲートで実行 | 手動 make のみ・skip 可能 | tests/test_semgrep_gate.py が 229 ルールを fail-closed で実行。PR #523 で違反 46→0、docs/adr/semgrep-baseline.json を baseline 0 に更新 | ゲート稼働 + 違反解消済み(PR #523、baseline 0)。残: 個別ルールの defsemgrep fixture 化、semgrep の dev 依存化 |
| T-B3 | ENFORCE-001 R4 | dev ビルド invariant-checks 常時有効 + oracle の pytest 起動 | test-…-vm-oracle-wired が red | **完了(2026-07-14)**: doeff-vm に feature 転送、make sync に --features、PyO3 で invariant_checks_enabled() 公開(doeff_vm/__init__.py 再輸出込み)、tests/test_vm_invariant_checks_enabled.py が hard-fail 常駐(実測 True)。oracle は全 VM 実行で常時演習される。make test-vm-invariants 追加 | **完了** |
| T-B4 | ENFORCE-001 R5 | enforcement 台帳 ratchet メタテスト | 台帳の黙減が検出されない | **完了(2026-07-14)**: docs/adr/enforcement-ledger.json(defadr 15 / semgrep 229 / deftest 8 / defsemgrep 19 / law 49)+ tests/test_enforcement_ledger.py 等値 ratchet green | **完了** |
| T-A1 | HY-001 R1-R4 | statement 位置 runtime guard 実装(修正プロンプト形式メッセージ) | test-…-bare-statement-program-raises が red | **完了(2026-07-14)**: macros.hy に _guard-statement-value / _wrap-statement-guard を実装、defk(_build-fn-with-contracts)/do!/defp(_build-defp)/deftest の4 emit サイトに適用。HY-001 green、既定スイート 1094 passed 無回帰 | **完了** |
| T-A2 | HY-001 R5 | 下流(ema/ACP/mediagen/reactor 系)で guard 有効化 → 休眠 discard 全数洗い出し・修正 | 休眠 bare form 数 未知 | 各リポジトリのスイート green + 洗い出し報告。**⚠️ Hy バイトコード罠**: マクロ変更はソース mtime に映らないため、掃引は必ず `PYTHONPYCACHEPREFIX=<fresh>` か touch で全再コンパイルして走らせること(doeff 自身の検証でこの罠を実踏) | 未着手(codex、リポジトリ毎) |
| T-A4 | HY-001 付随 | CPS の鋭い縁(ネスト try 内 `<-`、closure 内 bind)の解消 or 明示エラー化 | ACP ADR0056 facts の回避イディオム | マクロ改善 or 展開時エラー + テスト | 未着手 |
| T-C1 | CORE-002 R3 | profiling spike: 現行 main の捕捉サイト・コスト内訳の確定、ADR facts 更新 | Gap-3(サイト未特定) | **完了(2026-07-14)**: 現行 main に per-effect 捕捉は存在しない(opt-in effect + エラー経路のみ、linecache は描画時限定。100k Ask 実測で linecache/stack-walk/整形 = 0 呼び出し)。ADR facts 更新済み + 回帰ガード deftest green。**下流 3.7x は旧版への計測 — pin 更新後の再検証を下流 issue に**。副産物: @do の generator 再構築グルーが Python 側時間の ~39%(別 issue 種、本 ADR 対象外) | **完了** |
| T-C2 | CORE-002 R2(再スコープ) | run.py の traceback 行テキスト解決を `StackSummary.extract(walk_tb(tb), lookup_lines=False)` で遅延化 | 例外1回につき linecache 先読み | `extract_tb` に存在しない lookup_line 引数を使わず等価代替で実装 + 既存テスト green | **完了(2026-07-14 PR #520)** |
| T-C3 | CORE-002 R3(再スコープ) | GetTraceback/GetExecutionContext の VM 組み込み vs observability handler 化 — perf 根拠は消滅、可観測性設計論のみで裁定 | — | frontier+人間の裁定 | 裁定待ち(優先度低下) |
| T-D1 | HY-002 R2/R3 | deftest params 忠実受け渡し + docs/adr conftest fixture(:env 反映の参照実装) | — | :env roundtrip **green 済み(Stage 0 実測)**。残作業 = fixture 契約の文書化 + 未対応 params の hard fail | 大半完了 |
| T-D2 | HY-002 R1 | mediagen run_test.py の要求(部分スタック+差し替え)を deftest 語彙へ吸収 | シムの存在自体 | 語彙追加 + 等価性デモテスト | T-D1 後 |
| T-D3 | HY-002 R1 受入 | mediagen テストのパイロット deftest 移行(下流 dogfood) | mediagen deftest 0/260 | 下流 PR(mediagen ADR-008 residual 消化) | T-D2 後 |
| T-E1 | DOMAIN-001 R1 | defdomain マクロ設計・実装 | test-…-defdomain-exists が red | 同 deftest green(挙動ピンに昇格) | 設計=frontier |
| T-E2 | DOMAIN-001 R2 | SEDA を CLI/MCP としてエージェント照会可能に(WIP 完成) | 照会面なし | seda CLI + MCP tool + テスト | T-E1 と並行可 |
| T-E3 | DOMAIN-001 R3 | 適合検査 3 種(被覆・部分集合・同義語彙禁止) | ACP 核#8 型の増殖が素通り | 適合検査テスト green | T-E1+B 完了後 |

## 段階計画

- **Stage 0(完了 / 本セッション)**: 価値評価・理論文書(22 番)・本計画・5 defadr(red enforcement 4 本 + blocked 1 件)作成。A3/B3 裁定記録。
- **Stage 1(着地済み)**: T-B1/T-B4/T-C1 完了、T-D1 大半完了、T-B2 はゲート稼働 + 違反解消済み。残: T-B2 の defsemgrep fixture 化・semgrep の dev 依存化。
- **Stage 2**: T-A1 完了。残: T-A2(下流展開、リポジトリ毎に codex)。
- **Stage 3**: T-C2 完了。残: T-D2 → T-D3(下流 PR)。
- **Stage 4(設計セッション)**: T-E1 defdomain 設計 + T-C3 裁定を同一 frontier セッションで実施 → T-E2/T-E3 実装。
- **Stage 5(本計画外・次期計画)**: class 2 新プリミティブ(rate-limit token-bucket scheduler → batching/dedup/hedging)。前提 = 本計画の Gate 1-6。

## Subagent spawn strategy

| 役割 | タスク | スコープ | 並列組 | 期待出力 | 検証コマンド | 権限 | model |
|---|---|---|---|---|---|---|---|
| worker | T-B1 testpaths+自己検査 | pyproject.toml, packages/doeff-adr | G1 | diff + green 自己検査 | `uv run pytest docs/adr -q` | edit | sonnet |
| worker | T-B2 defsemgrep 化 | .semgrep.yaml, docs/adr, pyproject dev-deps | G1 | defsemgrep 群 + hard-fail 挙動 | `uv run pytest docs/adr -q`(semgrep 有/無 両方) | edit | sonnet |
| worker | T-B4 台帳 ratchet | docs/adr, tests | G1 | ratchet メタテスト | seeded-violation で red 確認 | edit | sonnet |
| worker | T-C1 profiling spike | doeff/, packages/doeff-core-effects(read)+ scratch | G1 | 捕捉サイト特定レポート + コスト内訳 | py-spy / cProfile 数値 | read+scratch | sonnet |
| worker | T-D1 params 受け渡し+fixture | packages/doeff-hy, docs/adr/conftest.py | G1 | :env roundtrip green | `uv run pytest docs/adr -q` | edit | sonnet |
| 設計 | T-A1 guard 設計(メッセージ仕様込み) | macros.hy | G2 | 設計メモ → 実装指示 | — | read | **frontier(inline)** |
| worker | T-A1 実装 | packages/doeff-hy | G2 | guard + green enforcement | `uv run pytest docs/adr packages/doeff-hy -q` | edit | sonnet |
| worker | T-A2 下流洗い出し(リポジトリ毎 1 体) | 各消費リポジトリ | G3 | 休眠 discard 一覧 + 修正 PR | 各リポジトリのスイート | edit(worktree) | sonnet |
| 設計 | T-E1 defdomain / T-C3 裁定 | macros.hy, SEDA specs | G4 | ADR 更新 + 実装仕様 | — | read | **frontier+人間** |
| verifier | 各 Stage 末の cross-link / 収集確認 | docs/, pyproject | 各 G 末 | 収集数・red/green 表 | `uv run pytest --collect-only -q docs/adr` | read | sonnet |

制約: worker は commit 禁止(親が diff をレビューして単一変更セットで確定)。doeff への着地は orch 経由
(CLAUDE.md 書込権限規則)。マクロ層(macros.hy)と VM は結合核 — sonnet worker は決定済み仕様の実装のみ、
方針判断が必要になったら改変せず報告で戻す。

## Non-goals

- GitHub Actions の再有効化(予算制約、maintainer 裁定 2026-07-14)。
- auto-bind の導入(A3 裁定で不採用。再検討するとしても T-A2 完了後の別 ADR)。
- class 2 新プリミティブ(rate-limit/batching/dedup/hedging)の実装 — 本計画の Gate 達成が前提の次期計画。
- 新規サブパッケージの追加、Haskell client の拡張、multi-shot 方向の検討。
- `doeff-main-with-handler-stack` checkout の削除(マージ済み残骸と確認済みだが、削除は maintainer 自身が行う)。
- 本番・下流リポジトリのデプロイ/ライブ系操作。

## 進捗記録 2026-07-14(Stage 1–2 実施)

同日の goal セッションで以下を実施(すべてローカル編集 — 着地は orch 経由):

- **T-A1 完了**: statement bind guard 実装(macros.hy)。HY-001 red→green、既定スイート 1094 passed 無回帰。
  エラーメッセージは修正プロンプト形式(`Fix: bind it — (<- _ <expr>) — ...`)。
- **T-B3 完了**: VM conformance oracle を dev 既定に(B3 裁定の実装)。`invariant_checks_enabled()` 実測 True。
- **T-B4 完了**: enforcement 台帳 + 等値 ratchet 稼働。
- **T-B2 ゲート稼働**: 229 ルールが既定 pytest で fail-closed 実行。初回実行が**既存違反 46 件**を検出
  (no-future-annotations 24 / no-typing-any 10 / no-print-in-core 4 / **no-datetime-now-in-do 4** /
  no-sleep-in-tests 2 / silent-except 2)— baseline 等値 ratchet で封じ込め、解消は codex バッチへ。
- **T-C1 完了・C トラック再スコープ**: 現行 main にトレース税は存在しない(捕捉は opt-in effect +
  エラー経路のみ)。CORE-002 は「修正」から「回帰ガード」へ書き換え済み、ガード green。
  下流の 3.7x issue は旧版への計測 — pin 更新後の再検証を下流に差し戻す。
- **docs/adr 全体**: 38 green + 意図した red 2(ENFORCE-001 testpaths = flip 保留、DOMAIN-001 defdomain = E1 待ち)。

新規・変更ファイル: packages/doeff-hy/src/doeff_hy/macros.hy(guard)、packages/doeff-vm/Cargo.toml・
src/lib.rs・doeff_vm/__init__.py(oracle flag)、Makefile(sync features / test-vm-invariants)、
tests/test_vm_invariant_checks_enabled.py、tests/test_enforcement_ledger.py、tests/test_semgrep_gate.py、
docs/adr/enforcement-ledger.json、docs/adr/semgrep-baseline.json、docs/adr/conftest.py、defadr 5本、本計画。

### 追記(同日後半: Stage 0-2 全着地)

- **PR #520 merged / T-C2 完了**: run.py の traceback 行テキスト解決を
  `StackSummary.extract(walk_tb(tb), lookup_lines=False)` で遅延化(`extract_tb` に lookup_line 引数は
  存在しないため等価代替で実装)。
- **PR #522 merged / Stage 0-2 着地**: T-A1 statement bind guard、T-B3+T-B4 の VM
  invariant-checks dev 常時有効 + oracle、enforcement 台帳 ratchet、semgrep ゲート、実行可能 ADR 5本、
  docs/adr/conftest.py、docs/22-capability-classes.md が着地。
- **PR #521 merged / T-B1 完了**: root testpaths を docs/adr へ flip。defadr 収集自己検査(既定 warn /
  strict で非ゼロ終了)と `doeff-adr verify-wiring` CLI が稼働し、ADR-DOE-ADR-001 に記録済み。
  DOMAIN-001 は xfail(strict) 相当 1 件として既定ゲートを green に維持。GitHub CI には配線せず、
  tests/test_adr_wiring_gate.py が pytest 正典ゲート内で strict 検査を常時実行。
- **PR #523 merged / T-B2 違反解消**: semgrep 違反 46→0、baseline 0。datetime.now 4件を GetTime
  効果へ、sleep 2件を決定的同期へ置換し、no-future-annotations 24件、公開 API の Any 10件、
  traceback の silent except を解消。
- **現 main の既定 suite**: `uv run pytest -q` は 1149 passed / 86 skipped / 1 xfailed /
  failed 0。xfail は E1 待ちを表明する DOMAIN-001 のみ。
- **最終記帳値**: enforcement 台帳は defadr_files 16 / adr_defsemgrep_enforcements 20 /
  adr_laws 51、semgrep baseline は 0。

## Immediate next action(/goal 実行者へ)

1. frontier+人間の設計セッションで T-E1 defdomain 設計、T-C3 裁定、T-A4、T-D2 を扱う。
2. T-A2 の下流 sweep を ema / ACP / mediagen / reactor 系で実施する(リポジトリ毎に codex)。
3. 下流の doeff pin を更新し、ema ISSUE-TRD-002 を再計測する。
4. T-B2 の残作業として、個別ルールの defsemgrep fixture 化と semgrep の dev 依存化を行う。
5. T-D2 の設計後に T-D3(mediagen のパイロット deftest 移行)を実施する。
6. T-E1 の設計後に T-E2(SEDA CLI/MCP)と T-E3(適合検査 3 種)を実装する。
