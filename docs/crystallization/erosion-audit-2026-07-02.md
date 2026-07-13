# 侵食監査 2026-07-02 — 宣言済み不変量 ⇔ 機械検査ギャップ(doeff)

> 4リポジトリ横断監査の doeff 分。宣言済み不変量・核・法則を全数列挙し、機械検査の存在と
> 「CI/デフォルト実行で実際に走るか」を突き合わせた(read-only、Fable 5 監査エージェント)。
> 横断まとめ: `~/repos/erosion-audit-2026-07-02-cross-repo.md`
> 対応 issue: `ci-wire-enforcement-layer`(CI 配線・codex)/ `doeff-adr-wiring-selfcheck`(根本対処設計・frontier)

**結論: ENFORCED 7 / PARTIAL 4 / EXISTS-NOT-WIRED 10 / DOC-ONLY 6。**
最重要発見: **ポストモーテムの教訓そのもの(VM conformance oracle)が実装済みなのに CI で一度も走っていない。**

---

## 読了した宣言ソース

- `docs/crystallization/invariants.md` — VM 実行時不変量カタログ I1–I8。enforcement として
  `packages/doeff-vm-core/src/vm/invariants.rs`(cargo feature `invariant-checks`)を名指し。
- `docs/crystallization/decision-records.md` — D1–D24。enforceable: K1 single-location(D19)、
  state/continuation 共有(D9/S6)、Var 廃止(D15)、委譲 issue 法則(D20–D24)。
- `docs/crystallization/postmortem.md` — 3度の rebuild の記録。§3 に R3 の核違反リスト、
  §5 が `check_invariants`(I1–I8)を検知単位として指名。
- `docs/crystallization/algebra-draft.md` — effect 代数法則群(A/S/W/AW/CC/CS/GH/CAN/selective/TR/ST/O)。
- `docs/crystallization/constraint-graph.md` — 結合核 watchlist 本体。核 **K1(所有権)/ K2(capture)**、
  routing 規則、:69 で `check_invariants`(I1–I8)を K1/K2 の機械ガードに指定。
- `docs/adr/defadr_doeff_agents_001_await_result_public_boundary.hy` — 唯一のドッグフード実行可能 ADR。
- `docs/crystallization/adr-0003-handler-stack-syntax.md` — with-handler の Invariant-TDD 表6行。

## エントリ表(要点)

| ID | エントリ | 判定 | 根拠 / ギャップ |
|---|---|---|---|
| I1–I7 | VM 実行時不変量(arena 衛生、segment 生存、親鎖非循環、handler 境界、detached 鎖、**I6=single-location(K1核の機械化、SPEC-VM-021:282)**、EvalReturn 生存) | **EXISTS-NOT-WIRED** | checker は invariants.rs に実装済みだが cargo feature `invariant-checks`+`python_bridge` ゲート(invariants.md:9-17)。**どのワークフローも Makefile ターゲットも `cargo test --features "python_bridge invariant-checks"` を実行しない**。CI の cargo は bench のみ(python-compatibility.yml:50、Makefile:168) |
| I8 | Var cell owner 生存(tension) | DOC-ONLY | report-only。Var サブシステムごと廃止予定(D15、issue #461) |
| R3-ARCH | R3 機構の再導入禁止(ContId/clone_handle/…) | ENFORCED | tests/test_architecture_violations.py:21-32 + test_vm_architecture_ocaml5_source.py:12。testpaths 内、CI 実行(python-compatibility.yml:42)。ただし source-shape のみ |
| K1-MOVE | move-only Continuation(D19) | ENFORCED | test_move_semantics_architecture.py(testpaths 内)。source-shape のみ — 動的対応物は I6(未配線) |
| ALG-* | Ask A1–A4 / State S1–S5(S6別)/ Writer W1–W3 / CC1–CC5, CS1 | ENFORCED | tests/laws/test_generator_laws.py(CI 実行) |
| ALG-AWAIT | Await AW1/AW2 | PARTIAL | AW2 multi-task 反例が `@pytest.mark.skip`(:335)、sim driver 待ち(issue #463) |
| CTRL-CORE | 制御核(deep-handler/線形性/single-location) | PARTIAL | 静的形は CI、動的 single-location は I6(未配線) |
| LAW-GH/CAN/SEL/TR/ST | GH1–3, CAN1–4, selective S1–4, TR1–2, ST1 | DOC-ONLY | 委譲 issue 済(laws-gh-can-mechanization / skip-selective-reformulation / settime-real-handlers-fail-fast、D20–D24)— 既知・追跡中 |
| DEAD-RM | 廃止 API の再導入禁止 | PARTIAL | semgrep `doeff-no-removed-api`(.semgrep.yaml:2886)+ `_Removed` stub。**semgrep が CI 外** |
| ADR3-RT | with_handlers 実行時規則 | ENFORCED | tests/test_with_handlers_helper.py(CI) |
| ADR3-MACRO | with-handler マクロ規則 | EXISTS-NOT-WIRED | packages/doeff-hy/tests/test_with_handler_macro.py — testpaths 外。`make test-packages` はどのワークフローも呼ばない |
| ADR3-SG | 公開 `doeff.WithHandler` 禁止 | PARTIAL | ルール `.semgrep.yaml:2422` — semgrep CI 外、fixture 発火テストのみ |
| ADR-AGENTS-001 | ドッグフード実行可能 ADR | EXISTS-NOT-WIRED | 三重に不活性: (1) `docs/adr` が testpaths(pyproject.toml:81-86)外で plugin(pytest_plugin.py:16-21)が到達不能 (2) defsemgrep は semgrep バイナリ必須で CI 未インストール (3) 根拠テストが packages/doeff-agents/tests(testpaths 外) |

## semgrep 層の全体状況(orphan 含む)

- `.semgrep.yaml` は **222 ルール**(vm-dispatch-*、vm-ocaml5-*、k4-* 等、多くは宣言文書に対応行なし)。
  **semgrep は dev 依存になく、どのワークフローもインストール・実行しない**(pyproject.toml:92 は
  pytest marker としての言及のみ; `make lint-semgrep` は開発者ローカル; pre-commit は CI で未起動)。
- tests/semgrep/test_vm_failfast_semgrep_rules.py はバイナリ不在時 skip(:18-19 — CI の常態)。
  存在時も fixture 発火の確認のみで、実ツリーのクリーン性は未検査。
- doeff-hy / doeff-agents ほか packages/* のテストスイート(doeff-adr/doeff-vm/doeff-vm-core 以外)は
  testpaths 外で、`make test-packages` 経由のみ(CI 未使用)。

## ドッグフード判定

**現状、デフォルト実行で自分の invariant を defadr 機構で守れている箇所はゼロ。**
doeff-adr 機構自体は synthetic fixture で動作実証済み
(packages/doeff-adr/tests/test_defadr_macros.py::test_pytest_plugin_collects_defadr_hy_files — これは CI で走る)
だが、実宣言には一度も適用されていない。constraint-graph.md:69 が K1/K2 の守りに指定した
Rust checker は、CI にも Makefile にも有効化経路がない。

## 上位3ギャップ(VM conformance 最優先)

1. **I1–I7 の VM 実行時不変量 checker が CI で走らない(最大重み)。** ポストモーテムが語る故障
   そのもの。I6(single-location 法則)は R3 全面書き直しの原因となった K1 所有権核の機械化。
   CI 上の守り(R3-ARCH/K1-MOVE)は source-shape grep で、動的違反は素通り。
2. **semgrep 層 222 ルールが CI で走らない。** 守りが開発者ローカルの任意実行に依存。
3. **ドッグフード ADR と ADR-0003 マクロ不変量がどの CI にも収集されない。**
   Invariant-TDD 表6行のうち2行の red テストがパイプラインで一度も実行されない。
