# 設計検証 lt-9R40Q1PM496VA4HVD1FMXVYW8Z — 盲検の前の主張(版 77e0eb3f9)

依頼 lt-9R40Q1PM496VA4HVD1FMXVYW8Z(class=investigate・card ki-c88db93c440a)。完了の範囲 = **実装完了まで**(直しは doeff main 77e0eb3f9 = L2158 で着地済み)。この file は盲検 A・B を起こす前に固定した主張で、盲検の後に書き換えない。

## 1. 要件(固定)

- R1: doeff main で `tests/test_daily_test_population.py::test_every_test_file_belongs_to_a_daily_population` が緑。
- R2: 設計検証の記録(`docs/design-checks/<依頼 id>/`)に `test_*.py`・`test_*.hy` の名の file(盲検の再現・直した版の検の写し)を置いても、この検が赤にならない形を決める。
- R3: 日次の母集団の検の本来の役目(検のつもりの file が日次のどの母集団にも属さず、黙って走らない形を赤にする — ADR-DOE-ENFORCE-001 R8)を弱めない。

## 2. 事実(直す前の版 e71995dfa で実測)

- 25ddfddff が `docs/design-checks/lt-D4D7YPF2BHEVKEHH2JJ7GT0W8W/evidence/scripts/test_handle_scope.revision.py` を置き、この検が「どの母集団にも除外の表にも無い検の file が 1 本」で赤(1 failed・12 passed)。
- この検は root の pyproject の `[[tool.doeff.contract-tests]]` の接頭辞 ""(repo 全体)の行に載り、`make test-changed` はどの差分でも赤 1・rc 1。
- repo は記録の dir を `docs/design/<記録>` と `docs/design-checks/<記録>` の 2 種と定める(`tests/test_sealed_records_are_named.py` の `RECORD_KINDS`)。除外の表 `EXCLUDED` は `docs/design/` だけを持っていた。

## 3. 直し(77e0eb3f9)

`EXCLUDED` に行 `"docs/design-checks/"`(dir の接頭辞・理由「設計検証の記録の証拠(盲検の再現・直した版の検の写し。その時点の記録で、日次の母集団ではない)」)を足した。記録の側の file の名は替えない。

## 4. 責務(module)

| id | 実体 | 持つ知識・判断 | 隠す物 | 契約 |
|---|---|---|---|---|
| M-pop | `tests/test_daily_test_population.py` | 「検の file」の定義(`_test_file_patterns` = root の ini の python_files・doeff_hy_test_files・ADR の pattern)、母集団の根の読み(root の testpaths・`packages/<p>/tests`・Makefile の PACKAGE_EXTRA_TEST_ROOTS)、除外の表 `EXCLUDED`(path か `/` で終わる dir の接頭辞 → 理由) | 木の歩き方(`_NOT_WALKED`) | 検の file は母集団の根の下か除外の行の下に在る。除外の行は今の木の検の file を 1 本以上覆う(古い行は赤) |
| M-records | `docs/design-checks/<依頼 id>/`(design-change-validation の記録) | その時点の証拠(本文・報告 JSON・盲検の入出力・再現の script と log) | — | 着地の後は書き換えない。日次の母集団ではない |
| M-roots | root の pyproject の testpaths・Makefile の PACKAGE_EXTRA_TEST_ROOTS | 日次に走る根 | — | 根の下の検の file は日次で走る |
| M-contract | root の pyproject の `[[tool.doeff.contract-tests]]` | どの差分でどの検を必ず走らせるか | — | 接頭辞 "" の行の検はどの差分でも走る |
| M-seal | `tests/test_sealed_records_are_named.py` の `RECORD_KINDS` | 封の記録の dir の種類 | — | 封の名指しは記録の封の表に在る |

## 5. 変更シナリオと主張(盲検の前)

| id | 軸 | 変更要求 | 主張(変える / 変えない) | 局所化できる理由・前提 |
|---|---|---|---|---|
| S1 | storage | 新しい設計検証の記録が `evidence/` や `blind/` の下に `test_*.py`・`test_*.hy` の名の証拠を置く | 変えるのは M-records の新しい dir だけ。M-pop・M-roots・M-contract・M-seal は変えない。検は緑のまま | 除外は dir の接頭辞で、記録ごとの行を要しない。前提: 記録の中の検の名の file は写しで、日次で走らせる物ではない |
| S2 | storage | 新しい種類の記録の dir(例 `docs/design-reviews/`)を作り、検の名の file を置く | 変えるのは M-pop の `EXCLUDED` の 1 行(理由つき)。足すまでは赤 | 除外の表が「日次でない」の宣言の 1 点。黙って外れる経路を作らない |
| S3 | concurrency | 複数の会話が同時に別々の設計検証の記録を足し、マージの列へ並べる | どの会話も M-pop を触らないので、同じ行での rebase の衝突が起きない | 記録ごとの行・名の付け替えを要しない |
| S4 | effects | 記録の外(母集団の根の外の任意の dir、例 `docs/notes/`・`scripts/`)に検の名の file を置く | 今までどおり赤(M-pop は変えない) | 除外は `docs/design-checks/` の接頭辞ちょうどで、他の dir を覆わない |
| — | hardware | 対象外: file system を歩く静的な検で、機体の資源に依らない | | |
| — | distribution | 対象外: 検は repo ごとに 1 つの Pod の作業木で走り、分散の状態を持たない | | |
| — | simulation | 対象外: 時刻・乱数・外の I/O を読まない決定的な検(模型の木 tmp_path で既に確かめられる) | | |

## 6. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 検の file は母集団か除外の行の下 | 契約の検(pytest) | `tests/test_daily_test_population.py::test_every_test_file_belongs_to_a_daily_population` | `make test-changed`(接頭辞 "" の行・どの差分でも)と日次の root の段 | 名の pattern に当たらない file は数えない |
| 除外の行は古くない | 契約の検 | 同 `test_exclusion_table_has_no_stale_rows` | 同上 | 行が覆う file が 1 本でも在れば緑 |
| 除外の理由 | 表の値(文) | `EXCLUDED` の値 | 書き手が読む | 理由の意味は機械で確かめない |

## 7. 予定する検(正常例・失敗例)

- S1 正常: 模型の記録 `docs/design-checks/lt-MODEL/evidence/test_x.py`・`blind/test_y.hy` を足しても検が緑。
- S1・S4 失敗: 同じ file を `docs/notes/`・`docs/design-checksX/` に置くと赤で名指す。
- S2 正常 / 失敗: `docs/design-reviews/r1/test_z.py` は行が無ければ赤、行を足すと緑。
- S3: 2 つの模型の記録を同時に足しても `tests/test_daily_test_population.py` の差は 0 行で緑。
- 直す前の版(e71995dfa)で赤 1 本を名指す事(実測済み)。
