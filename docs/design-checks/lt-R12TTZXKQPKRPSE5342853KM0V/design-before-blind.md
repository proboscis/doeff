# 設計(盲検の前に固定した版)— docs/design/ の行が記録の模型の検を外す件

依頼: lt-R12TTZXKQPKRPSE5342853KM0V(class investigate)・card acp:kanban-issue:ki-d153ab595504。
対象 repo: doeff。検証する変更: 3682f28c8b06bbd18f125619a85f35d88f337e21(doeff main に着地済み・着地 L2211)。

## 0. 完了の範囲と、この検証の順序(正直な記録)

- 完了の範囲: **実装まで着地済み**。この設計検証は、着地した後に、着地した規則を盲検で試す順で行う。変更を先に作り、設計の主張は
  その後(盲検の前)に保存した。順が「設計 → 盲検 → 実装」ではない事を、結論の重みから外さない。
- 設計の主張はこの file(盲検の前の版)に固定し、hash を evidence/pre-blind.sha256 に残す。盲検の返答を受けて書き換えない。

## 1. 受入条件(固定)

| id | 内容 |
|---|---|
| R1 | doeff main の EXCLUDED に dir の行 `docs/design/` が無いか、残すなら行の理由が「make test-changed の選びが記録の模型を検として走らせる事」と「R8 の抜け道」への答えを書いている |
| R2 | docs/design/seat-home-common-instructions-AJ8C0B/model/ の test_*.py 4 本が、.py.txt への改名か日次の母集団の根への登録のどちらかに決まり、tests/test_daily_test_population.py が緑 |
| R3 | 記録の中に検の名の file を置いても日次が赤にならない一方、本物の検を記録の dir にだけ置いても緑にならない(ADR-DOE-ENFORCE-001 R8 の役目を弱めない) |

## 2. 現状の事実(実測・evidence/ に log)

- 行 `docs/design/` が外していた検の名の file は 6 本: seat-home の model/test_{chain,counterexamples,scenario_checks,violations_are_rejected}.py と、
  worker-pod-cpu-share の evidence/scripts/{fork_test,reset_test}.py(`*_test.py`)。
- seat-home の 4 本は pytest の検ではない(`def test_*` が無く、`t_*` を __main__ から回す)。repo の設定のまま pytest で集めると 0 本・rc 5
  (evidence/collect-4-and-fork.log)。母集団の根へ入れると日次が赤になる。
- fork_test.py・reset_test.py は module の先頭で sched_setattr・fork・chrt を走らせる。pytest で集めると本体が走る(evidence/collect-fork-s.log)。
  fork_test.py は閉じた記録 docs/design/worker-pod-cpu-share/records/assessment-1bff46b3e6e1.json が path と sha256 で名指す。
- 穴 A(再現済み・evidence/A-before-selected.log): make test-changed の選びは名の規則だけを読み、記録の中の model/test_chain.py・fork_test.py を
  「変えた検」として選び、集めて「集めた検が 0 本」の未測で終わる。
- 穴 B: 行の形そのものの帰結。dir の行の下に置いた本物の検は、母集団の検にも古い行の検にも見つからない。

## 3. 責務(modules)

| id | 責務 | 所有する知識 | 隠す知識 | 公開契約 |
|---|---|---|---|---|
| M-name | 「検の file」の名の規則 | root の ini の python_files・doeff_hy_test_files・doeff-adr の既定の pattern | 各 pattern の読み方 | `_test_file_patterns(repo)`・`_is_test_file(rel, patterns)`。scripts/run_changed_tests.py も同じ ini の値から同じ判定をする |
| M-pop | 検の file が日次の母集団の根の下か、理由つきの除外の行の下に在る事の確認(R8) | 母集団の根・EXCLUDED の表(path か dir の接頭辞 → 理由) | 木の歩き方 | `_assert_every_test_file_belongs_to_a_daily_population`・`test_exclusion_table_has_no_stale_rows` |
| M-select | 変えた file から走らせる検を選ぶ(make test-changed) | 変えた検・逆依存の選び | linter の逆依存の問い | scripts/run_changed_tests.py。**名の規則だけを読み EXCLUDED を読まない** |
| M-records | 設計の記録(docs/design/<記録>/・docs/design-checks/<記録>/): その時点の証拠 | 記録の中の模型・測りの script・写し | — | 検の名の file は集め手の pattern に当たらない名(`.py.txt`)で置く。着地の後は書き換えない |
| M-seal | hash で封をした記録の file を linter の規則から外す名指し | evidence/SHA256SUMS.txt の path と hash・architecture.hy の :files | — | tests/test_sealed_records_are_named.py と packages/doeff-linter/tests/rule_population.rs |

## 4. 変更後の設計(3682f28c8)

1. 検の名の file を記録に置く時は `<名>.py.txt` にする(M-records が名で M-name の外へ出る)。M-pop の dir の行は置かない。
2. 改名できない file(閉じた記録が path と sha256 で名指す)だけを、1 file ごとの path の行で EXCLUDED に載せる(dir の行は置かない — 新しい検は隠せない)。
3. M-select は変えない。記録の中の file が名の規則に当たらないので、選びの入力に入らない(穴 A を、選びではなく**置く名**で閉じる)。
4. 赤の文と表の頭の注釈が、置き方(`.py.txt`)を案内する。

## 5. 変更シナリオと事前の主張

6 軸を扱う。

| id | 軸 | 適用 | 変更 | 主張(盲検の前) | 変える責務 | 変えない責務 |
|---|---|---|---|---|---|---|
| S1 | storage | 適用 | 新しい設計の記録が model/ や evidence/ に `test_*.py` の模型や再現の写しを置く | 母集団の検が赤になり、赤の文が .py.txt を案内する。直すのは記録の file の名だけで、M-pop・M-select・M-seal は変えない | M-records | M-name・M-pop・M-select・M-seal |
| S2 | storage | 適用 | 閉じた記録が path と sha256 で名指す `*_test.py` の script が新しい記録に入る(改名できない) | EXCLUDED に 1 file の path の行を 1 つ足す。M-select・M-seal・M-name は変えない。その行の隣に置いた新しい検は赤のまま | M-pop(表の 1 行) | M-name・M-select・M-seal |
| S3 | effects | 適用 | 本物の回帰の検を記録の dir にだけ置く(`docs/design/<記録>/` の下の `test_*.py`) | 母集団の検が赤になる(dir の行が無いので隠れない)。.py.txt に替えると赤は消えるが、その file は走らない — 走る検にしたいなら母集団の根へ置く | なし(赤が出る) | 全部 |
| S4 | concurrency | 適用 | 2 つの会話が同時に別々の記録を足す | .py.txt の置き方なら共有の file を触らない。S2 の行だけが M-pop の表で衝突し得る(稀) | M-records(各自の dir) | M-pop |
| S5 | hardware | 不適用 | — | 名の規則の検は file の名と中身を読むだけで、機体の資源に依らない | — | — |
| S6 | distribution | 不適用 | — | 日次は atlas の Pod の git の作業木で直に走り(R10)、make test-changed は手元の作業木で走る。どちらも同じ ini と同じ名の規則を読む | — | — |
| S7 | simulation | 不適用 | — | 検は file の名・中身・make の出力を読む決定的な検。時刻・乱数を持たない | — | — |

前提: 「検の file」の定義は root の ini が 1 点で持つ。記録の中の file は、読み手が名の規則だけで走らせる物(M-select・日次の pytest)から外れていれば足りる。
未知の将来の分類・新しい種類の記録の dir の追加は予測しない(新しい検の名の規則や集め手の追加は M-name の変更になる)。

## 6. 強制方法(実装した物)

| 責務 | 強制 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| M-pop | 検の file が根か除外の行の下に在る事 | tests/test_daily_test_population.py::test_every_test_file_belongs_to_a_daily_population | root の contract-tests 表の接頭辞 ""(repo 全体)の組 → make test-changed がどの差分でも走らせる・日次の root 段 | 「検の名でない名で置いた本物の検」は見えない |
| M-pop | 除外の行が今の木の file を覆う | test_exclusion_table_has_no_stale_rows | 同上 | — |
| M-records | docs/design/ の中の test_*.py・*_test.py は赤・.py.txt で緑・名指しの行の隣の新しい検も赤 | test_test_named_file_in_a_design_record_is_red_until_renamed(新規) | 同上 | tmp の模型の木で見る |
| M-records | docs/design-checks/ の同じ契約 | test_test_named_copy_in_a_design_check_record_is_red_until_renamed(3f3fc4b53) | 同上 | — |
| M-seal | 封の名指しが封の表に在り hash が一致 | tests/test_sealed_records_are_named.py・packages/doeff-linter/tests/rule_population.rs | make test-changed(変えた検)・日次 | 封の表の元から不一致の 7 行は対象外(復元で不一致と記録済み) |
| M-select | 名の規則に当たらない file は選ばない | 変更なし(scripts/run_changed_tests.py) | make test-changed | EXCLUDED の行の file(cpu-share の 2 本)は選ぶ — 残る穴 A |

## 7. 予測する変更範囲(実測と比べる)

3682f28c8 の変更は 10 file: 4 本の改名・封の表(SHA256SUMS.txt)・architecture.hy・RECOVERY.md・rule_population.rs・test_daily_test_population.py・
test_sealed_records_are_named.py(下限 4 → 3)。主張: これは「改名の連鎖(封の名指しを持つ 3 責務 — M-seal の 3 か所)」と
「M-pop の表と検」で、M-name・M-select は変わらない。

## 8. 未確認(盲検の前に分かっている物)

- 逆依存の選び(`doeff-linter --affected-tests`)は、この機体に linter が無く測っていない。
- 型検査は未実行(code-quality の fast 段のみ)。
- cpu-share の 2 本の行は穴 A を残す(変えると選ばれて未測)。
