# 設計(盲検の前に固定した版)— DOEFF002 の初期化の口と、doeff-linter の基点の鍵

依頼 lt-JAMKZ0NP8ZTQ52AHNGW67SGKPP(class investigate)・card ki-9da0a5c71947。

## この文書の目的

読み手が、次の 3 つを判じられるようにする。

1. 日次の lint の DOEFF002 の 2 行が、本物の所見か linter の誤検出か。
2. どの責務(module)がどの判断を持ち、将来の変更がどこに収まると主張するか。
3. その主張を何が機械で守るか。

## 完了の範囲

実装完了まで。直しは doeff の main に入っている(L2099・着地の sha 4ccab04ac)。

| 版 | sha |
|---|---|
| 基準の版(直す前の main) | 61f0600093debe4c04c6550985bc4b26a3b6e4dc |
| linter の commit | 02fca1e25 |
| 着地 | 4ccab04aca929df800e6d33d36b8e31c8f87fcea |

## 固定した要件

- R1(card の受入 c51f88e5f): 2026-10-12 00:00 JST より後の doeff の日次の lint で、次の 2 つに戻る。
  - program_effects.py の DOEFF002 が基点 1 件
  - macro_use.py の DOEFF002 が 0 件
- R2: 誤検出なら linter を直して戻す。基点に足して通さない。
- R3: 直しが、日次の lint に新しい赤の行を足さない。

## 調べの事実

| 所見 | 置かれた所 | 判定 |
|---|---|---|
| macro_use.py `self.missed = True`(a5d666e74) | `CodeReadings.facts()` の中 | 本物。54f1cdfa3 が `_mut_missed` に直していた |
| program_effects.py `self._held = f"_{name}"`(646a3e986) | descriptor `_ClauseEmits.__set_name__` の中 | 誤検出 |
| program_effects.py `self._absent = _absent_receiver()`(e3a391c71・日次の後) | `_Reader.absent_receiver()` の中・遅れて作る値 | 本物 |

- 規則の実装は、2025-11-25 の誕生から値の型を見ない。`__init__` / `__post_init__` の外で `self.<名>` に代入する属性を数える。
- 説明文(rule_info.rs・report.rs)だけが「list, dict, set」と書き、実装と食い違っていた。
- `__set_name__` は、owner の class を作る時に Python が 1 度だけ呼ぶ、descriptor の初期化の口(PEP 487)。

## 責務(module)

### M1 DOEFF002 の規則

- path: packages/doeff-linter/src/rules/doeff002_mutable_attribute_naming.rs
- 持つ判断:
  - 初期化の method の集合(`is_init_method` = `__init__`・`__post_init__`・`__set_name__`)
  - 「初期化の外の代入」を数える事
  - 名の決まり(`mut_` / `_mut`)
- 隠す事: 値の型を問わない事。呼び手は所見の列だけを受ける。
- 口: `LintRule::check(&RuleContext) -> Vec<Violation>`
- effect: なし。AST を読むだけ。
- 不変条件: 初期化の集合の外の method で `self.X` に代入すれば、値の型に依らず所見にする。

### M2 規則の説明文

- path: packages/doeff-linter/src/rule_info.rs・packages/doeff-linter/src/report.rs
- 持つ物: 人と道具に見せる説明。
- 不変条件: M1 の判断と食い違わない。

### M3 基点の比べ

- path:
  - scripts/hook_finding_baseline.py
  - scripts/hook_finding_baseline/doeff-linter.json
  - scripts/doeff_linter_locked.py
  - scripts/gate_tools.sh の `linter`
- 持つ物: 規則 × file の基点の数と、その数を数えた linter の組み立ての入力の鍵(`version`)。
- 不変条件:
  - 基点は下がる向きにだけ動く(`lower` は上げない・新しい組を足さない)。
  - 日次は、基点の file を最後に書いた commit の linter で測る。
  - 鍵の合う binary が無ければ「測れない」(rc 3)。黙って別の版で測らない。

### M4 tools の層の宣言

- path: tools/architecture.hy
- 持つ物: 規則の母集団から外す名指しの module と、その理由。
  - build = doeff_cargo_backend
  - release = rust_wheel_release
- 不変条件: 外すのは DOEFF004 だけ・名指しの module だけ。tools のほかの file には今までどおり当たる。

### M5 閉じの解析の reader

- path: packages/doeff-effect-analyzer/python/doeff_effect_analyzer/program_effects.py の `_Reader`
- 持つ物: 遅れて作る `:absent` の受け手(`_mut_absent`)。
- 不変条件: 初めて問われた時に作り、reader ごとに 1 つ。

### M6 子の process を起こす検

- path:
  - packages/doeff-hy/tests/test_dataclass_code_store.py
  - packages/doeff-hy/tests/test_import_gc.py
  - tests/test_hook_finding_baseline.py
  - tests/test_wheel_remote_store.py
- 持つ物: 子へ渡す環境。
- 口: pytest の `monkeypatch.setenv` で検の process の環境に置く。子はそれを受け継ぐ。検の後に戻る。
- 不変条件: 検の code は `os.environ` を参照しない(DOEFF004)。

## 変更シナリオと事前の主張

### S1(axis concurrency・適用)

- 変更: thread をまたいで共有される object に、後から作って持つ状態を足す(例: `_Reader.absent_receiver`)。
  - あるいは、descriptor が自分の名を `__set_name__` で覚える。
- 主張:
  - M1 は、後から代入する状態を型に依らず `_mut` の名に強いる。
  - `__set_name__` だけで代入する descriptor の状態は強いない。
  - 初期化の集合を変えるのは M1 の `is_init_method` 1 か所だけ。
- 予想の範囲: M1(と、その検・M2 の文)。
  - M3 は `version` の 1 行だけ。
  - M4・M5・M6 の code は変わらない。

### S2(axis storage・適用)

- 変更: linter の規則の意味を変える(今回の `__set_name__`)。日次が新しい linter で測るよう、基点の鍵を替える。
- 主張:
  - 鍵の替えは、M3 の基点の file の `version` の 1 行と、下がる向きの数だけを変える。M3 の code(比べ・組み立て・探し)は変えない。
  - 鍵の合わない状態では、日次は黙って測らず「測れない」と名乗る。
  - 鍵の替えで判定に入る所見は、基点に足さず直す。
- 予想の範囲:
  - M3 の data 1 行
  - 判定に入る所見の持ち主(M4・M5・M6)

### S3(axis effects・適用)

- 変更: 検が子の process に、もう 1 つ環境変数を渡す。
  - あるいは、tools の下に、環境変数を読む道具を足す。
- 主張:
  - 検は `monkeypatch.setenv` で置く(M6 だけを変える)。DOEFF004 は鳴らず、除外は要らない。
  - tools の新しい道具は、M4 に名指して理由を書かない限り DOEFF004 が当たる。
  - 名指しは module 単位で、層のほかの file へ広がらない。
- 予想の範囲: M6 か、M4 の 1 項目。

### 外したシナリオ

| ID | axis | 外した理由 |
|---|---|---|
| S4 | hardware | 静的な解析で、機体の資源に依らない |
| S5 | distribution | linter の binary を機体ごとに組む・写す仕組み(linter_snapshot.py・land-arm)は変えていない。この変更はその仕組みを使うだけ |
| S6 | simulation | AST の読みは決定的で、時間と乱数を使わない |

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| M1: 初期化の外の代入は型に依らず `_mut` | doeff-linter DOEFF002(severity error) | doeff002_mutable_attribute_naming.rs<br>検 `test_descriptor_set_name_is_initialization`・`test_value_kept_on_first_use_is_mutated` | commit の hook と日次の lint の段(基点の比べ)<br>cargo の検は日次の rust の段 | 継承元の framework の属性への書きは見分けない(card ki-71204291060d)<br>`self` 以外の名・`setattr`・`__dict__` への書きは見ない |
| M2 = M1 | 人の読みだけ | rule_info.rs・report.rs | なし | 機械では照らしていない |
| M3: 基点は下がる向き・鍵の一致 | hook_finding_baseline.py の `check --strict` / `lower`<br>doeff_linter_locked.py の鍵 | scripts/ | 日次 lint の段<br>commit の hook | 鍵を替える便が判定に入る所見を直すのは人の規律(`lower` が名指すだけ) |
| M4: 名指しの module だけを外す | defarchitecture の `:modules`・`:exempt`(理由が必要) | tools/architecture.hy<br>linter の検 tests/rule_population.rs | 日次の lint・rust の段 | 外す理由の妥当さは人が判じる |
| M6: 検は os.environ を参照しない | DOEFF004(os.environ への参照そのもの) | linter | 同上 | `os.getenv` の別名・`subprocess` の `env=None` の受け継ぎ自体は見ない |
