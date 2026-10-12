# 設計検証 lt-R12TTZXKQPKRPSE5342853KM0V — docs/design/ の行が記録の模型の検を外す件

依頼 class = investigate(設計検証が自動で必須)・card acp:kanban-issue:ki-d153ab595504・対象 repo = doeff・検証した変更 = 3682f28c8b06bbd18f125619a85f35d88f337e21(着地済み・L2211)。

## この文書の目的

読み手が次の 3 つに答えられる事。

1. 行 `docs/design/` を外して模型の検 4 本を `.py.txt` に替えた判断は、盲検 A・B の失敗例に耐えたか。
2. 耐えなかった所(盲検 A)は何で、どう改め、再検証で何が確かめられたか。
3. まだ確かめていない事・範囲の外に残した事は何か。

## 1. 完了の範囲と順序

- 範囲: **実装まで済み**。3682f28c8 は着地済みで、この検証の改め(503856b64)も実装済み。着地(main への取り込み)はこの記録の登記と同時。
- 順序: 設計 → 盲検 → 実装 の順では**ない**。変更を先に着地させ、その後に設計の主張を保存して盲検へ出した(design-before-blind.md の頭に同じ事を書いた)。
  盲検の前の主張は `design-before-blind.md`(sha256 5ff42453…)に固定し、盲検の返答を受けて書き換えていない。入力の hash は `evidence/pre-blind.sha256`。

## 2. 受入条件(固定)

| id | 内容 |
|---|---|
| R1 | doeff main の EXCLUDED に dir の行 `docs/design/` が無いか、残すなら行の理由が「make test-changed の選びが記録の模型を検として走らせる事」と「R8 の抜け道」への答えを書いている |
| R2 | seat-home の model/ の test_*.py 4 本が、.py.txt への改名か日次の母集団の根への登録のどちらかに決まり、tests/test_daily_test_population.py が緑 |
| R3 | 記録の中に検の名の file を置いても日次が赤にならない一方、本物の検を記録の dir にだけ置いても緑にならない(ADR-DOE-ENFORCE-001 R8 の役目を弱めない) |

## 3. 全体の構図(今 ⇒ 改め後)

```
今(3682f28c8)                                      改め後(503856b64)
─────────────────────────────────────────────────  ─────────────────────────────────────────────────
 pyproject.toml                                     pyproject.toml
  ├ ini の python_files など = 「検の名」の定義       ├ ini の python_files など = 「検の名」の定義
  └ (記録の dir の宣言は無い)                        └ [tool.doeff] record-dirs  ← 記録の dir の 1 点の定義(新)
        │                                                  │              │
        ▼                                                  ▼              ▼
 M-pop tests/test_daily_test_population.py          M-pop(同左)         M-select scripts/run_changed_tests.py
  EXCLUDED = 1 file ごとの行(cpu-share の 2 本)      記録の dir を覆う     is_test_file: record-dirs の下は
  記録の中の検の名 → 赤(.py.txt を案内)             dir の行を禁じる(新)   名の規則に当たっても検に数えない(新)
        ▲                                           記録の中の検の名 → 赤
        │ 読まない(2 点に割れていた)
 M-select scripts/run_changed_tests.py
  is_test_file: 名の規則だけ
  → 記録の中の改名できない *_test.py が
    import 先の変更で選ばれ「集める時に落ちた」赤
```

## 4. 責務(modules)

| id | 責務 | 所有 | 隠す | 公開契約 |
|---|---|---|---|---|
| M-name | 「検の file」の名の規則 | root の ini の python_files・doeff_hy_test_files・doeff-adr の既定の pattern | 各 pattern の読み方 | `_test_file_patterns`・`_is_test_file`(population)と `collector_patterns`・`is_test_file`(選び)が同じ ini の値から判定する |
| M-pop | 検の file が日次の母集団の根か、理由つきの除外の行の下に在る事の確認(R8) | 母集団の根・EXCLUDED の表 | 木の歩き方 | `test_every_test_file_belongs_to_a_daily_population`・`test_exclusion_table_has_no_stale_rows`・`test_exclusion_table_has_no_dir_row_over_a_record_dir`(新) |
| M-select | 変えた file から走らせる検を選ぶ(make test-changed) | 変えた検・逆依存の選び | linter の逆依存の問い | `is_test_file` は記録の dir の下を検に数えない(新) |
| M-records | 設計の記録 dir(その時点の証拠) | 模型・測りの script・検の写し | — | 検の名の file は `.py.txt` で置く。改名できない file だけ 1 file ごとの除外の行。着地の後は書き換えない |
| M-seal | hash で封をした記録の file の名指し | SHA256SUMS.txt の path と hash・architecture.hy の :files | — | tests/test_sealed_records_are_named.py・packages/doeff-linter/tests/rule_population.rs |

## 5. 盲検 A・B と設計者の試験

起動: 1 番目の候補 gpt-6-astra・low は別の会話として起こしたが 51 分たっても返答が無く、skill の 2 番目の候補(opus 階層・xhigh)へ替えた(blind/launch.md に理由・配り手への報告・先の 2 会話の識別子)。
実行した A・B の model は要求値 opus で、実際に観測できた model は**未確認**(Agent tool は名を返さない)。返答は加工せず blind/blind-a-reply.md・blind/blind-b-reply.md に置いた。

| 盲検 | 失敗例の要点 | 設計者の試験(実物の道具) | 判断 |
|---|---|---|---|
| A | 記録の中の改名できない `*_test.py`(閉じた記録が path と sha256 で名指す測りの script)が、import する生きた module の変更(関数の改名)で選びに選ばれ、「集める時に落ちた」赤・rc 1 になる。記録の側では直せない。S2 の予測(表の 1 行だけ)と、EXCLUDED の注釈「赤にはならない」が外れる | 使い捨ての作業木(3682f28c8)で、記録の中の `*_test.py` を除外の 1 file の行に載せ、doeff_adr の関数 `parse_hy_test_skips` を使い手ごと改名して、実物の doeff-linter 付きで選びを走らせた。**赤 1・rc 1・距離 1 で選ばれた**(A-real-before-red) | **成立**。設計・検査を改めた(§6) |
| B | 記録の模型 `.py.txt` を glob で日次に走らせる shim の検を足す。母集団の検は緑のまま、検の本体は記録の dir にしか無い。選びには見えず、日次で初めて赤になる | 使い捨ての作業木で shim と記録の模型(実在の doeff_adr を import)を置いた。母集団の検 3 本は緑・shim は 4 passed。import 先を壊すと shim は直に走らせて赤(lt-NEWREC が FAILED)で、選びの選んだ検に shim は入らない(B-real-select・B-real-shim-direct) | **観測は成立・責務違反としては不成立**(§7) |

## 6. 改め(A への対応・503856b64)

漏れた知識は「この検の名の file は検ではない」で、M-pop の EXCLUDED にしか無く、名の規則だけを読む M-select に届かなかった。

| 変更 | 内容 |
|---|---|
| pyproject.toml | `[tool.doeff] record-dirs = ["docs/design/", "docs/design-checks/"]` — 記録の dir の定義を 1 点に置く |
| scripts/run_changed_tests.py | `collector_patterns` が値を読み(形が違えば止める)、`is_test_file` は record-dirs の下を名の規則に当たっても検に数えない。変えた検の選びと逆依存の答えの絞りは、どちらもこの 1 判定を通る |
| tests/test_daily_test_population.py | 記録の dir を覆う dir の行を禁じる検 `test_exclusion_table_has_no_dir_row_over_a_record_dir`・記録の dir ごとに赤→緑を見る検・赤の文を元の名に `.txt` を足す形へ(盲検 A の補足 ii の `.hy` に合わせた)・注釈の誤りを直した |
| tests/test_run_changed_tests.py | 記録の dir の下の検の名の file は、変えても・逆依存の答えに載っても選ばない検(宣言が無い対照では選ばれて赤) |

再検証: 同じ再現(同じ作業木・同じ linter・同じ改名)で**赤 0・rc 0**(linter の答え 3 本のうち記録の script が除かれ、足した検は 2 本 — A-real-after-green)。
変異(dir の行 `docs/design/` を戻す)で記録用の検と構造の検の 2 本が赤(mutation2-dir-row-red)。実物の選びは 17 + 15 + 26 本が緑(fix-test-changed-green)。

## 7. 盲検 B の判断

B の観測のうち、母集団の検が緑のまま・shim が `.py.txt` を日次の形で走らせる・選びに shim が入らない・import 先を壊すと shim だけが直に赤、は実物の木で再現した。
封の検・linter の層に拒否されない点は、B の写しの木の実測と読みによる(shim は封の file にも architecture.hy にも触れないので推論で、私は再現していない)。判断は次のとおり。

- **R3 の趣旨は保たれる**: R3 の失敗は「本物の検が日次で黙って走らない」事。shim は本物の検を日次で走らせるので、走らない検を緑で隠す形ではない。R3 の文の後半(「緑にならない」)を字面どおり読むと、shim の形で破れる — 文言は盲検の前に固定したので書き換えず、ここに記録する。
- **設計の契約に反する物は見つからなかった**: M-records の契約(検の名の file は `.py.txt`・着地の後は書き換えない)・M-name の契約(pytest が集める名の規則)・S3 の主張(「走る検にしたいなら母集団の根へ置く」)のいずれも、shim は破っていない。
  shim は「集め手の外の glob で別 process に走らせる」第 3 の道で、設計の外にある。
- **限界として残す**: (a) shim は子 process で走るので import の辺が無く、make test-changed の選びに見えない(日次で初めて赤)— subprocess で走る検に共通の性質。
  (b) `.py.txt` は linter の対象の外で、3682f28c8 は seat-home の層 counterexamples の禁じる import(doeff を import しない)を外した。shim が日次で走らせる記録の模型の import は linter が見ない。
  (c) shim に別の除外の表(B の `MACHINE_BOUND`)を持たせると、理由つきの表の検(古い行の検)が効かない。
  `.py.txt` を日次で走らせる検を足す時は、これらを持つ別の設計検証を要する — この依頼では足していない。

## 8. 変更範囲: 予測と実測

| 項目 | 盲検の前の予測(§7 の design-before-blind) | 実測 |
|---|---|---|
| 3682f28c8 の 10 file | 4 本の改名・封の表・architecture.hy・RECOVERY.md・rule_population.rs・test_daily_test_population.py・test_sealed_records_are_named.py。M-name・M-select は変えない | 同じ 10 file。予測どおり(M-select は変えていない) |
| S2(改名できない file が入る) | M-pop の表の 1 行だけ | **外れた**: 盲検 A の例では M-select の判定と、記録の dir の 1 点の定義(pyproject)も要った(知識の漏洩・§6)。改めて 503856b64 の 4 file |

## 9. 変更シナリオ(6 軸)

| id | 軸 | 適用 | 主張(盲検の前) | 実測 | 変えた責務 | 変えない責務 |
|---|---|---|---|---|---|---|
| S1 | storage | 適用 | 新しい記録が `test_*.py` の模型を置いても、直すのは記録の file の名だけ | 母集団の検が赤になり `.py.txt` を案内する。M-pop・M-select・M-seal は変えず、封の表の検算は改名前後で同じ(19 行一致・7 行は元から不一致) | M-records | M-name・M-pop・M-select・M-seal |
| S2 | storage | 適用 | 改名できない `*_test.py` が入る時は EXCLUDED に 1 file の行を足すだけ | **外れた**(盲検 A)。選びが記録の script を選んで赤になった。M-select と記録の dir の定義(pyproject)を改めて解消 | M-pop・M-select(改め) | M-name・M-seal |
| S3 | effects | 適用 | 本物の検を記録の dir にだけ置くと赤。`.py.txt` で緑になるがその file は走らない | 赤→緑を実測。dir の行を戻す変異で 2 本が赤。B の shim は走る道だが、設計の外の第 3 の道(§7) | なし | 全部 |
| S4 | concurrency | 適用 | `.py.txt` の置き方なら共有の file を触らず、S2 の行だけが表で衝突し得る | 同じ表に 2 つの行を足すと衝突(rc 1)、別々の記録に `.py.txt` を足すと衝突なし(rc 0)を実測 | M-records(各自の dir) | M-pop |
| S5 | hardware | 不適用 | — | 名の規則の検は file の名と中身を読むだけで、機体の資源に依らない | — | — |
| S6 | distribution | 不適用 | — | 日次は atlas の Pod の git の作業木、make test-changed は手元の作業木で走るが、どちらも同じ pyproject の値と同じ名の規則を読む | — | — |
| S7 | simulation | 不適用 | — | 検は file の名・中身・make の出力を読む決定的な検で、時刻・乱数を持たない | — | — |

## 10. 強制方法と実行経路(実装済み)

| 責務 | 強制 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| M-pop | 検の file が根か除外の行の下に在る | `test_every_test_file_belongs_to_a_daily_population` | root の契約の表の接頭辞 ""(repo 全体)→ make test-changed がどの差分でも走らせる・日次の root 段 | 検の名でない名で置いた本物の検は見えない |
| M-pop | 記録の dir を覆う dir の行が無い | `test_exclusion_table_has_no_dir_row_over_a_record_dir`(新) | 同上 | — |
| M-records | 記録の中の検の名は赤・`.py.txt` で緑・名指しの行の隣の新しい検も赤 | `test_test_named_file_in_a_design_record_is_red_until_renamed`・`test_test_named_copy_in_a_design_check_record_is_red_until_renamed` | 同上 | tmp の模型の木で見る |
| M-select | 記録の dir の下は選ばない | `is_test_file`(新)・tests/test_run_changed_tests.py の新しい検 | make test-changed(変えた検として走る) | EXCLUDED の記録の外の行(`packages/doeff-agentic/examples/` など)は対象外 — §11 |
| M-seal | 封の名指しが封の表に在り hash が一致する | tests/test_sealed_records_are_named.py・rule_population.rs(cargo test) | make test-changed・日次 | 封の表の元から不一致の 7 行は対象外 |

## 11. 限界と、この依頼の範囲の外(未変更)

- 記録の外の EXCLUDED の行(`packages/doeff-agentic/examples/`・`tools/test_python_versions.py` など)は、同じ形の穴(名の規則に当たるのに選びが選ぶ)を持つ。盲検 A が読んで指摘した(`examples/` の 2 本は doeff_agentic を import し `def test_*` を持つ — 走らせてはいない)。
  この依頼は設計の記録の dir が対象で、触っていない。
- seat-home の封の表 evidence/SHA256SUMS.txt は、元から 7 行が不一致(部分復元の log 4 本・implementation-request.md・report.json・model/check_body_contract.py)で、RECOVERY.md の表の「一致 21」と合わない(改名の前後で同じ 19 行一致・同じ 7 行不一致 — seal-table-before-after)。
  盲検 B は model/check_body_contract.py の不一致を別に見つけた(その file は :files に無い)。原因は調べていない。
- linter の逆依存の測りは、この機体の debug binary(cargo test が作った物)で、修正の検証と盲検 A の再現にだけ使った。本番の linter の snapshot での実測ではない。
- 型検査は未実行(code-quality の fast 段のみ)。盲検の model は要求値のみで観測していない(§5)。
- 盲検の入力は設計の本文と証拠の dir の外に置いたが、同じ Pod で動くため、その dir を読めなくしてはいない(指示した)。

## 12. 未解決

なし(§11 は範囲の外・限界で、この依頼の受入条件の未達ではない)。
