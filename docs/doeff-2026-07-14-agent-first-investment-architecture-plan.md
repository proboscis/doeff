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
>
> 裁定済み(2026-07-17 maintainer + frontier):
> - **C3 = GetTraceback / GetExecutionContext は VM 内省命令のまま現状維持**
> - **E1 = `packages/doeff-domain` に完全 opt-in で配置**。導入 1 / 包含 ∞、handler 処理集合は二層導出、
>   適合検査 (a) 被覆・(c) 孤児禁止を E1 で出荷する

## Source of truth

| 種別 | パス | 内容 |
|---|---|---|
| defadr | `docs/adr/defadr_doeff_hy_001_statement_bind_guard.hy` | ADR-DOE-HY-001: statement 位置 bind guard(Track A) |
| defadr | `docs/adr/defadr_doeff_enforce_001_pytest_canonical_gate.hy` | ADR-DOE-ENFORCE-001: pytest 正典ゲート(Track B) |
| defadr | `docs/adr/defadr_doeff_core_002_lazy_creation_trace.hy` | ADR-DOE-CORE-002: 生成トレース遅延化 + VM 内省命令の現状維持裁定(Track C) |
| defadr | `docs/adr/defadr_doeff_hy_002_deftest_expressiveness.hy` | ADR-DOE-HY-002: deftest 表現力契約(Track D) |
| defadr | `docs/adr/defadr_doeff_domain_001_vocabulary_cohesion.hy` | ADR-DOE-DOMAIN-001: defdomain / SEDA / 適合検査(Track E) |
| defadr | `docs/adr/defadr_doeff_hy_003_bang_evaluation_position.hy` | ADR-DOE-HY-003: bang(!) evaluation-position 意味論(Track A4) |
| defadr | `docs/adr/defadr_doeff_enforce_002_semgrep_p0_fixtures.hy` | ADR-DOE-ENFORCE-002: P0 Semgrep 規則の fixture 生存証明(Track B2) |
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
| F-4 | ~~semgrep 手動のみ~~ **解消(T-B2、2026-07-16 までに完了)**: dev 依存化 + 1.169.0 pin(#528)、229 規則の棚卸し(#529)、死亡 8 規則の修理 + 46 tombstone 注記(#532)、P0 16 規則の hit/clean fixture 常設(#535)。現 HEAD は 235 規則、baseline 0 | pyproject.toml / uv.lock / docs/semgrep-rules-inventory-2026-07-14.md / ADR-DOE-ENFORCE-002 |
| F-5 | deftest は最大 Python 消費者(mediagen)で使用 0 / 260。**:env 経路は doeff 側では機能する**と実証済み(HY-002 roundtrip green)— 残ギャップは消費側配線と語彙拡張(D2) | mediagen conftest / docs/adr/conftest.py |
| Gap-1 | ~~docs/adr に conftest.py が無い~~ **解消済み(Stage 0)**: `docs/adr/conftest.py` を新設(:env を reader で反映・エラー再送出。ADR-DOE-HY-002 R3 の参照実装) | 2026-07-14 実装 |
| Gap-2 | T-A1/B3 は green、root testpaths の flip 済みで ADR 検査と wiring 自己検査が既定ゲートに常駐。残る designed-red は DOMAIN-001 の xfail(strict) 相当 1 件のみ。E1 の設計は確定し、実装 issue `doeff-domain-e1-defdomain` が進行中(実装で green になったら xfail 解除と記帳を強制) | tests/test_adr_wiring_gate.py / `doeff-domain-e1-defdomain` |
| Gap-3 | ~~CORE-002 enforcement blocked / C3 未裁定~~ **解消(T-C1/T-C3、2026-07-17)**: 現行 main に per-effect 捕捉は存在しないと確定し、回帰ガード deftest green。GetTraceback / GetExecutionContext は effect dispatch を通らない VM 内省命令として現状維持を裁定。下流 TRD-002 も旧版限定として resolved 化済み(ema PR #612) | C1 spike / CORE-002 R3 / ema PR #612 |
| Gap-4 | `doeff-main-with-handler-stack`(489MB 姉妹 checkout)は 2026-07-11 に main へ完全マージ済みの残骸と判明 — 計画対象外、削除は maintainer 判断 | git merge-base 検証 2026-07-14 |
| Gap-5 | ~~T-A4 の bang(!) が制御境界を越えて hoist~~ **解消(2026-07-16 PR #533)**: evaluation-position を保存する in-place yield 展開へ修正。ADR-DOE-HY-003 は fresh bytecode で 10 tests collected(probe 9 + ADR contract 1) | ADR-DOE-HY-003 / PR #533 |

## 依存関係(ASCII)

```
                 [B: pytest 正典ゲート]  ←—— ゲートが無ければ全 enforcement は飾り
                  B1 testpaths+自己検査
                  B2 ✅ fixture 常設     B3 ✅ VM oracle 配線
                  B4 台帳 ratchet
                     │ (ゲート成立)
      ┌──────────────┼──────────────────┬───────────────┐
      ▼              ▼                  ▼               ▼
[A: bind/bang]   [C: trace(再スコープ)] [D: deftest 契約] [E: defdomain/SEDA/適合]
  A1 ✅ guard      C1 ✅ 税は不存在      D1 ✅ 大半完了     E1 ✅ 設計確定
  A2 ✅ 主要部     C2 ✅ 行解決遅延化    D2 シム語彙吸収    └ 実装 issue 進行中
  A3 ✅ 裁定       C3 ✅ 現状維持裁定    D3 mediagen 移行   E2 SEDA CLI/MCP
  A4 ✅ ! 意味論                         (下流)            E3 残る適合検査(b)
      │
      ▼
[将来(本計画外): class 2 新プリミティブ — rate-limit scheduler / batching / hedging]
```

## Completion gates(計画全体の完了条件)

1. `uv run pytest`(既定 testpaths)が docs/adr の全 defadr enforcement を収集・実行する(ENFORCE-001 law)。
2. 本計画で追加した red enforcement 4 本が green(HY-001 guard / ENFORCE-001 testpaths+oracle / DOMAIN-001 defdomain)。
3. ~~ADR-DOE-CORE-002 に実測に基づく enforcement を追加~~ **達成(2026-07-14)**: 回帰ガード green、T-C2 は PR #520 で完了。下流 TRD-002 も現行 pin の再計測で旧版限定と確認し、ema PR #612 で resolved 化済み。
4. ADR-DOE-HY-002 の :env roundtrip が green、かつ mediagen の run_test.py が deftest 語彙で置換可能なことを下流で実証。
5. 本計画の全 ADR の :status が "accepted" に昇格(enforcement green が条件)。
6. `.semgrep.yaml` 全 235 規則(現 HEAD 実測)+VM oracle が「バイナリ/feature 不在 = hard fail」で既定ゲートに常駐。

## Master TODO

| ID | 出典 ADR | 作業 | red 反例(現状) | green 機構 | 状態 |
|---|---|---|---|---|---|
| T-B1 | ENFORCE-001 R2 | testpaths に docs/adr 追加 + defadr 収集自己検査を doeff-adr に実装 | test-…-docs-adr-in-default-testpaths が red | root testpaths の flip 実施。`pytest_collection_finish` で全 defadr 候補と session.items を照合する自己検査(既定 warn / strict で非ゼロ終了)と `doeff-adr verify-wiring` CLI を実装し、ADR-DOE-ADR-001 に記録。DOMAIN-001 は xfail(strict) 相当で既定ゲートを green 維持 | **完了(2026-07-14 PR #521)** |
| T-B2 | ENFORCE-001 R3 / ENFORCE-002 | .semgrep.yaml を既定 pytest ゲートで実行し、重要規則の生存も証明 | 手動 make のみ・skip 可能。一括 green では規則死亡を検出不能 | baseline 0 の fail-closed gate に加え、dev 依存 + pin(#528)、229 規則棚卸し(#529)、死亡 8 規則修理 + 46 tombstone(#532)、P0 16 規則の hit/clean fixture(#535、installed-rule runner の極性別 temp tree 対応) | **完了**。P1 fixture 拡張は P0 運用経験待ちで保留 |
| T-B3 | ENFORCE-001 R4 | dev ビルド invariant-checks 常時有効 + oracle の pytest 起動 | test-…-vm-oracle-wired が red | **完了(2026-07-14)**: doeff-vm に feature 転送、make sync に --features、PyO3 で invariant_checks_enabled() 公開(doeff_vm/__init__.py 再輸出込み)、tests/test_vm_invariant_checks_enabled.py が hard-fail 常駐(実測 True)。oracle は全 VM 実行で常時演習される。make test-vm-invariants 追加 | **完了** |
| T-B4 | ENFORCE-001 R5 | enforcement 台帳 ratchet メタテスト | 台帳の黙減が検出されない | **完了(2026-07-14)**: docs/adr/enforcement-ledger.json(defadr 15 / semgrep 229 / deftest 9 / defsemgrep 19 / law 49)+ tests/test_enforcement_ledger.py 等値 ratchet green | **完了** |
| T-A1 | HY-001 R1-R4 | statement 位置 runtime guard 実装(修正プロンプト形式メッセージ) | test-…-bare-statement-program-raises が red | **完了(2026-07-14)**: macros.hy に _guard-statement-value / _wrap-statement-guard を実装、defk(_build-fn-with-contracts)/do!/defp(_build-defp)/deftest の4 emit サイトに適用。HY-001 green、既定スイート 1094 passed 無回帰。**同日後半**: 下流洗い出しで class-level defk の guard ヘルパー解決欠陥を発見し、PR #525 で生成関数の `__wrapped__` チェーンを辿って module `__globals__` へ `setdefault` 注入する根本修正を実施。ADR-DOE-HY-001 に class-level probe を追加 | **完了** |
| T-A2 | HY-001 R5 | 下流(ema/hypha/ACP/mediagen)で guard 有効化 → 休眠 discard 全数洗い出し・修正 | 休眠 bare form 数 未知 | 各リポジトリのスイート green + 洗い出し報告。**⚠️ Hy バイトコード罠**: マクロ変更はソース mtime に映らないため、掃引は必ず `PYTHONPYCACHEPREFIX=<fresh>` か touch で全再コンパイルして走らせること(doeff 自身の検証でこの罠を実踏) | **主要部完了(2026-07-14 ema PR #613 / hypha PR #5)**。発火 0 件 = 休眠 discard 不在の証明。残: mediagen / ACP(小、ACP は ADR のみ)。reactor / proboscis-reactor は deprecated と裁定され GitHub アーカイブ済みのため対象外 |
| T-A4 | HY-003 | bang(!) の evaluation-position 意味論を保存し、保存不能位置は明示エラー化 | hoist による分岐脱出・短絡無視・評価順逆転・例外文脈喪失 | in-place yield 展開 + 保存不能位置の修正プロンプト付き展開時エラー。ADR-DOE-HY-003 は 10 tests collected(probe 9 + contract 1)。Fable/codex A/B の交差検証後 #533 を採用し #534 を close、#534 の Semgrep 2 規則 + README 節は #533 へ移植 | **完了(2026-07-16 PR #533)** |
| T-C1 | CORE-002 R3 | profiling spike: 現行 main の捕捉サイト・コスト内訳の確定、ADR facts 更新 | Gap-3(サイト未特定) | **完了(2026-07-14)**: 現行 main に per-effect 捕捉は存在しない(opt-in VM 内省命令 + エラー経路のみ、linecache は描画時限定。100k Ask 実測で linecache/stack-walk/整形 = 0 呼び出し)。ADR facts 更新済み + 回帰ガード deftest green。下流 TRD-002 は現行 pin の再計測で旧疑惑 5 系統がすべて 0 calls となり、旧版限定として resolved 化済み(ema PR #612)。副産物: @do の generator 再構築グルーが Python 側時間の ~39%(別 issue 種、本 ADR 対象外) | **完了** |
| T-C2 | CORE-002 R2(再スコープ) | run.py の traceback 行テキスト解決を `StackSummary.extract(walk_tb(tb), lookup_lines=False)` で遅延化 | 例外1回につき linecache 先読み | `extract_tb` に存在しない lookup_line 引数を使わず等価代替で実装 + 既存テスト green | **完了(2026-07-14 PR #520)** |
| T-C3 | CORE-002 R3(再スコープ) | GetTraceback/GetExecutionContext の VM 組み込み vs observability handler 化 | — | DoCtrl 変種・yield 分類時の直接処理を根拠に VM 内省命令(GetHandlers 族)と分類。実需要のない handler 化コストは Evidence Rule により不採用 | **裁定済み(2026-07-17、現状維持)** |
| T-D1 | HY-002 R2/R3 | deftest params 忠実受け渡し + docs/adr conftest fixture(:env 反映の参照実装) | — | :env roundtrip **green 済み(Stage 0 実測)**。残作業 = fixture 契約の文書化 + 未対応 params の hard fail | 大半完了 |
| T-D2 | HY-002 R1 | mediagen run_test.py の要求(部分スタック+差し替え)を deftest 語彙へ吸収 | シムの存在自体 | 語彙追加 + 等価性デモテスト | T-D1 後 |
| T-D3 | HY-002 R1 受入 | mediagen テストのパイロット deftest 移行(下流 dogfood) | mediagen deftest 0/260 | 下流 PR(mediagen ADR-008 residual 消化) | T-D2 後 |
| T-E1 | DOMAIN-001 R1/R3(a,c) | defdomain マクロ設計・実装 | test-…-defdomain-exists が red | `packages/doeff-domain` に完全 opt-in で配置。導入 1 / 包含 ∞。handler 処理集合は defhandler の `__doeff_body__` 節構造から自動導出、生 Python handler は `@handles` 注釈 + 将来 SEDA 照合。(a) 被覆・(c) 孤児禁止を同時出荷 | **設計確定(2026-07-17)**。実装 issue `doeff-domain-e1-defdomain` 進行中 |
| T-E2 | DOMAIN-001 R2/R3(b) | SEDA を CLI/MCP としてエージェント照会可能に(WIP 完成) | 照会面なし | SEDA CLI/MCP + Program 使用 effect 集合・interpreter 処理集合の照会テスト | T-E1 の domain/handler 契約着地後、T-E3 と共同実装 |
| T-E3 | DOMAIN-001 R3(b) | Program 使用 effect 集合 ⊆ interpreter 処理集合の検査 | 未処理 effect が実行時まで不明 | SEDA と連携した部分集合検査 green | T-E1 後、E2 と共同実装 |

## 段階計画

- **Stage 0(完了 / 本セッション)**: 価値評価・理論文書(22 番)・本計画・5 defadr(red enforcement 4 本 + blocked 1 件)作成。A3/B3 裁定記録。
- **Stage 1(着地済み)**: T-B1/T-B2/T-B3/T-B4/T-C1 完了、T-D1 大半完了。T-B2 の P1 fixture 拡張は P0 運用経験待ちで保留。
- **Stage 2**: T-A1/T-A4 完了。T-A2 は ema / hypha の主要部完了。残: mediagen / ACP の小規模 sweep。
- **Stage 3**: T-C2 完了。残: T-D2 → T-D3(下流 PR)。
- **Stage 4(設計セッション完了、2026-07-17)**: T-E1 defdomain 設計確定 + T-C3 現状維持裁定。残: T-E1 実装 issue → T-E2/T-E3、T-D2 設計。
- **Stage 5(本計画外・次期計画)**: class 2 新プリミティブ(rate-limit token-bucket scheduler → batching/dedup/hedging)。前提 = 本計画の Gate 1-6。

## Subagent spawn strategy(初期計画・履歴)

完了・裁定済みの行は実施履歴として保持する。再発注対象は末尾の Immediate next action を正典とする。

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
- 本 docs-only 更新内での新規サブパッケージ追加(T-E1 実装 issue が `packages/doeff-domain` を所有)、Haskell client の拡張、multi-shot 方向の検討。
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
- **T-C1 完了・C トラック再スコープ**: 現行 main にトレース税は存在しない(捕捉は opt-in VM 内省命令 +
  エラー経路のみ)。CORE-002 は「修正」から「回帰ガード」へ書き換え済み、ガード green。
  下流 TRD-002 も現行 pin の再計測で旧版限定と確認し、ema PR #612 で resolved 化済み。
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
- **現 main の既定 suite**: `uv run pytest -q` は 1150 passed / 1 xfailed。xfail は E1 待ちを
  表明する DOMAIN-001 のみ。
- **最終記帳値**: enforcement 台帳は defadr_files 16 / adr_defsemgrep_enforcements 20 /
  adr_laws 51、semgrep baseline は 0。

### 追記2(同日夜: 下流展開一巡)

- **T-A2 主要部完了**: proboscis-ema は PR #613 で doeff を 54417d7c に pin し、fresh
  バイトコードで root suite 843 passed。休眠 discard の発火は 0 件で、SlogEffect wire-type 分離に
  伴う test-only handler 2 件を修正した。hypha は PR #5 で editable path 依存を git rev pin 化して
  guard を点検し、発火 0 件を確認。editable が隠していた公開 API ズレ 4 点
  (WithHandler→with_handlers、slog の Listen 化、scheduler 明示、SessionHandle opaque 化追随)も
  修正した。reactor / proboscis-reactor は deprecated と裁定され GitHub アーカイブ済みのため
  sweep 対象外。残る mediagen / ACP は小規模で、ACP は Haskell アプリ、`.hy` は executable ADR
  のみ。
- **T-A1 class-level defk 根本修正**: 下流洗い出しで、defclass 内 defk の guard ヘルパー import が
  class スコープに落ち、メソッド実行時に NameError になる欠陥を発見。doeff PR #525(main =
  fd849ca0)で生成関数の `__wrapped__` チェーンを辿り、module `__globals__` へ `setdefault` 注入する
  ゼロランタイムコストの修正を実施した。旧 defp 展開の return-color ヘルパーも同じ機構へ統合し、
  ADR-DOE-HY-001 に class-level probe deftest を追加(台帳 deftest 8→9)。既定 suite は 1150 passed /
  1 xfailed。
- **ema の応急処置除去**: PR #614 で doeff pin を fd849ca0 へ更新し、応急 guard-helper import を
  7 ファイルから除去。suite 843 passed を維持した。
- **ISSUE-TRD-002 解消**: ema PR #612 の現行 pin 再計測で、旧疑惑 5 系統
  (capture_creation_context / stack walk / linecache.getline / sys._getframe / traceback extract)が
  すべて 0 calls と確認し、issue を「現行 doeff では 3.7倍減速は旧版限定」に改稿して resolved 化。
  残る主成分は Rust VM dispatch と `@do` generator グルーで、C1 spike と整合する。`@do` グルー
  ~39% は別 issue 種という記述は引き続き有効。

## 進捗記録 2026-07-17(Stage 1–4 更新)

- **T-A4 完了**: bang(!) を文前へ hoist せず、出現位置で in-place yield 展開する意味論へ修正した
  PR #533 を merge(main `678c32b7`)。Fable/codex A/B の交差検証で機能等価を確認して #533 を採用、
  #534 を close し、#534 の Semgrep guard 2 規則と README 節は #533 へ移植済み。
  ADR-DOE-HY-003 は fresh bytecode で 10 tests collected(probe 9 + ADR contract 1)。
- **T-B2 完了**: Semgrep を dev 依存化して 1.169.0 に pin(#528)、当時の全 229 規則を棚卸し
  (#529)、死亡 8 規則を修理して 46 規則へ tombstone 根拠を注記(#532)。P0 16 規則は
  ADR-DOE-ENFORCE-002 の hit/clean fixture として常駐(#535)。同 PR は installed-rule runner を
  hit/clean の極性別一時 tree へ分離し、同一 exact path の fixture も検査可能にした。P1 拡張は保留。
- **T-C3 裁定済み**: GetTraceback / GetExecutionContext は DoCtrl の VM 内省命令であり、effect
  dispatch を通らず handler スタックから不可視。perf 根拠も拒否可能性の実需要もないため、
  observability handler へ載せ替えず現状維持とした。sandbox の需要が出たら別 ADR を起こす。
- **T-E1 設計確定**: `packages/doeff-domain` に core 非侵襲・完全 opt-in で配置し、effect の正典導入は
  ちょうど 1 domain、`includes` による参照合成は無制限(導入 1 / 包含 ∞)。handler 処理集合は
  defhandler の `__doeff_body__` 節構造から自動導出し、生 Python handler は `@handles` 注釈を使う
  (将来 SEDA で照合)。(a) 被覆・(c) 孤児禁止は E1、(b) Program/interpreter 部分集合は E2/E3 で
  出荷する。実装 issue `doeff-domain-e1-defdomain` は起票済みで進行中。

## Immediate next action(/goal 実行者へ)

1. `doeff-domain-e1-defdomain` で確定済み T-E1 設計を実装し、(a) 被覆・(c) 孤児禁止を出荷する。
2. T-A2 の残りとして mediagen / ACP の小規模 sweep を実施する。
3. T-D2 を設計し、その後 T-D3(mediagen のパイロット deftest 移行)を実施する。
4. T-E1 着地後に T-E2(SEDA CLI/MCP)と T-E3((b) Program/interpreter 部分集合検査)を実装する。
5. T-B2 の P1 fixture 拡張は、P0 の運用経験が蓄積するまで保留する。
