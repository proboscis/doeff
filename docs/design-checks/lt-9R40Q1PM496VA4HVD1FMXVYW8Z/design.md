# 設計検証 lt-9R40Q1PM496VA4HVD1FMXVYW8Z — 設計検証の記録の検の名の写しと日次の母集団の検

## この文書の目的

読み終えた人が、次の 3 つを判断できるようにする。

- 設計検証の記録(`docs/design-checks/<依頼 id>/`)に、直した版の検や盲検の再現の写しをどんな名で置けばよいか。
- その決まりを、どの検がどこで強制しているか。
- なぜ最初の直し(除外の行)を改めたか。

依頼は lt-9R40Q1PM496VA4HVD1FMXVYW8Z(class=investigate)、card は ki-c88db93c440a。完了の範囲は**実装完了まで**。

記録の中の sha の読み方: 54a945270(盲検の前)・d3d3fa683(盲検の返答)・3fba07713(改め)は、測った時の branch wt/dc-9r40-daily-pop の commit。main の先端への載せ直しと着地の列の rebase で sha は変わる。中身は同じ題の commit で、main では題「日次の母集団の検: 設計検証の記録の dir を除外の行で外す形をやめ…」で引ける。

## 1. 全体の図(最初の直し ⇒ 改めた形)

最初の直し(77e0eb3f9): 記録の dir を除外の行で外した。

```
docs/design-checks/lt-D4D7…/evidence/scripts/test_handle_scope.revision.py   (検の名の写し)
        │
        ├─▶ tests/test_daily_test_population.py(日次の母集団の検 = M-pop)
        │     EXCLUDED["docs/design-checks/"] に当たる ─▶ 数えない ─▶ 緑
        │
        └─▶ scripts/run_changed_tests.py(make test-changed の選び)+ doeff-linter --affected-tests
              名の規則(python_files)だけを読む・EXCLUDED は読まない
              ─▶ 「変えた検」「逆依存」に選ぶ ─▶ pytest が集める時に落ちる ─▶ 赤 1・rc 1   ← 盲検 A
本物の検を記録の dir にだけ置く ─▶ M-pop は数えない ─▶ 緑(日次で 1 本も走らない) ← 盲検 B
```

改めた形(3fba07713): 記録の中の写しは、集め手の名に当たらない名にする。

```
docs/design-checks/lt-D4D7…/evidence/scripts/test_handle_scope.revision.py.txt  (集め手に当たらない名)
        │
        ├─▶ M-pop: 検の file に数えない ─▶ 緑(除外の行は要らない)
        └─▶ make test-changed の選び: 名の規則に当たらない ─▶ 選ばない
本物の検(test_*.py)を記録の dir に置く ─▶ M-pop が赤で名指し、「.py.txt に替える」と案内する
```

「何が検の file か」は名の規則の 1 点(root の ini の python_files・doeff_hy_test_files・ADR の pattern)で決まり、どの読み手も同じ規則を読む。改めた形では、記録の側が名でその規則の外に出る。規則の読み手ごとに除外を教える必要がない。

## 2. 事実(実測)

| 事実 | 証拠 |
|---|---|
| 直す前は `test_every_test_file_belongs_to_a_daily_population` が赤 1(写し 1 本を名指し) | evidence/S1-before-red.txt |
| この検は `[[tool.doeff.contract-tests]]` の接頭辞 ""(repo 全体)の行に載り、`make test-changed` はどの差分でも走らせる | root の pyproject.toml |
| 除外の行を足すと検の file 全体が 13 passed | evidence/S1-row-green.txt |
| 行を足した版でも、写しを変えた file にすると `make test-changed` が「変えた検」に選び、`ModuleNotFoundError: No module named 'test_handle_scope.revision'` で集める時に落ちて赤 1・rc 1 | evidence/A-changed-copy-red.txt |
| 行を足した版で `handlers.py` を変えると、写しが逆依存(距離 1)に選ばれる(この回は 60 秒の上限で未測) | evidence/A-reverse-selected.txt |
| 行を足した版で、本物の検を記録の dir にだけ置くと 3 つの検が 3 passed。同じ file を `docs/notes/` に置くと赤 | evidence/B-record-green.txt・evidence/B-notes-red.txt |

## 3. 盲検の前の主張

盲検の前に固定した主張は `design-before-blind.md` にある(sha256 は evidence/pre-blind.sha256・commit 54a945270)。要件 R1〜R3・責務 M-pop / M-records / M-roots / M-contract / M-seal・シナリオ S1〜S4 はそちらの定義のまま使う。

訂正: `design-before-blind.md` §2 の「直す前の版 e71995dfa で実測」は不正確だった。赤の再現は 74873132f(da660e83e を含み、77e0eb3f9 の基になった e71995dfa の 2 つ前)の作業木で取った(evidence/S1-before-red.txt の頭)。写しの file は両方の版で同じなので、結果は変わらない。

## 4. 盲検の結果と再現

起動の記録は blind/launch.md、返答は blind/blind-a-reply.md・blind/blind-b-reply.md(加工なし)。

| 盲検 | 主張された失敗例 | 再現 | 結論 |
|---|---|---|---|
| A | `make test-changed` の選び(`scripts/run_changed_tests.py` の `select_targets`・`extend_with_reverse`、doeff-linter の `--affected-tests`)は名の規則だけを読み、`EXCLUDED` を読まない。記録の写しを検として選び、赤にする | 成立。写しを変えた file にした実行で赤 1・rc 1(A-changed-copy-red.txt)。逆依存に選ばれる事も確かめた(A-reverse-selected.txt) | S1 の主張「変えるのは記録の dir だけ」は M-pop についてしか成り立たない。「名の規則に当たるが検ではない」という知識が `EXCLUDED` 1 か所にしかなく、名の規則を読む他の読み手へ漏れていた |
| B | 本物の回帰の検を記録の dir にだけ置くと、提示した 3 つの検がどれも緑のまま。R3(ADR-DOE-ENFORCE-001 R8)を弱める | 成立。3 passed(B-record-green.txt)、対照の `docs/notes/` では赤(B-notes-red.txt) | 除外の行は「写しか、唯一の守りか」を区別できず、記録の dir を R8 の抜け道にしていた |

## 5. 改め(3fba07713)

1. lt-D4D7YPF2BHEVKEHH2JJ7GT0W8W の `evidence/scripts/test_handle_scope.revision.py` を `test_handle_scope.revision.py.txt` に改名した。中身は同じ。記録の本文・報告 JSON・log はこの名を参照しない(`git grep` で確かめた)。前例は cfa45c4d9(lt-9TQ4 の probe)と lt-AAN9 の `.py.txt`。
2. `EXCLUDED` から `docs/design-checks/` の行を外し、表の頭に理由を書いた。
3. 母集団の検の赤の文に「設計検証の記録(docs/design-checks/)の検の写しは、集め手の pattern に当たらない名(`<名>.py.txt`)に替える」を足した。
4. 模型の木の検 `test_test_named_copy_in_a_design_check_record_is_red_until_renamed` を足した。記録の中の `test_*.py` は赤、`.py.txt` に替えると緑。

## 6. 予測と実際の差

| シナリオ | 盲検の前の予測 | 実際(改める前) | 差の原因 | 改めた後 |
|---|---|---|---|---|
| S1 記録が検の名の写しを置く | 記録の dir だけが変わる | M-pop は緑。ただし `make test-changed` の選びが写しを検として選び、赤 1 | 知識の漏洩: 「名が当たるが検ではない」が `EXCLUDED` にしかない。名の規則を読む読み手は 3 つある | 写しを `.py.txt` で置く形では、どの読み手も選ばない(A-after-not-selected.txt)。記録の dir だけが変わる |
| S2 新しい種類の記録の dir | `EXCLUDED` に 1 行。足すまで赤 | 予測どおり | — | 予測どおり(S2-new-kind.txt)。M-seal の `RECORD_KINDS` も変わるのは封を使う種類だけ(盲検 A の副次の指摘。封の種類を足すのは意図した契約の拡張) |
| S3 並行して記録を足す | M-pop を触らない | 行の形では写しのある記録の着地が赤になり、並ぶ会話の `make test-changed` に赤が広がった(依頼の症状そのもの) | S1 と同じ | 2 つの記録が同時に `.txt` の写しを足しても緑・`tests/` の差 0 行(S3-two-records.txt) |
| S4 記録の外の検の名の file | 赤のまま | 予測どおり(B-notes-red.txt) | — | 記録の中の本物の検も赤になる(B-after-red.txt) |

予想の範囲は広げていない。S1 の予測は改めた形で初めて成り立った。

## 7. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 検の名の file は母集団の根の下か、理由つきの除外の行の下に在る(R8) | 契約の検 | `tests/test_daily_test_population.py::test_every_test_file_belongs_to_a_daily_population` | `make test-changed`(接頭辞 "" の行・どの差分でも)と日次の root の段 | 名の規則に当たらない file は数えない。`.py.txt` の写しの中身が「写しか」は見ない |
| 記録の dir を行で外す形に戻さない | 模型の木の契約の検。行を戻す変異で赤(DID NOT RAISE)を確かめた | 同 `test_test_named_copy_in_a_design_check_record_is_red_until_renamed` | 同上 | 別の dir の接頭辞の行(例 `docs/`)で覆う形は、この検ではなく行の理由の review が止める |
| 除外の行は古くない | 契約の検 | 同 `test_exclusion_table_has_no_stale_rows` | 同上 | — |
| 写しの置き方を書き手が知る | 赤の文の案内 | `_assert_every_test_file_belongs_to_a_daily_population` の文 | 同上 | 着地の列は検を走らせないので、書き手が `make test-changed` の赤を見ずに着地させると、main で赤になる(今回の 25ddfddff と同じ経路)。これはこの検ではなく着地の経路の性質 |

## 8. 検(正常例・失敗例)

| id | シナリオ | 種類 | 期待 | 観測 | 証拠 |
|---|---|---|---|---|---|
| c-s1-before | S1 | 失敗例 | 直す前の版で写しを名指して赤 | 1 failed・写し 1 本 | evidence/S1-before-red.txt |
| c-a-changed | S1・S3 | 失敗例 | 行の形では写しが `make test-changed` で赤 | 赤 1・集める時に落ちた | evidence/A-changed-copy-red.txt |
| c-revision | S1・S4 | 正常例 | 改めた版で検の file 全体が緑 | 14 passed | evidence/revision-green.txt |
| c-a-after | S1・S3 | 正常例 | 改めた版で写しと `handlers.py` を変えても、記録の下は選ばれない | `design-checks` を含む行 0 | evidence/A-after-not-selected.txt |
| c-mutation | S1 | 失敗例 | 行を戻す変異で新しい検が赤 | DID NOT RAISE AssertionError | evidence/mutation-row-back-red.txt |
| c-b-after | S4 | 失敗例 | 本物の検を記録の dir に置くと赤で名指し | 1 failed・案内つき | evidence/B-after-red.txt |
| c-b-notes | S4 | 失敗例 | 記録の外の検の名の file は赤 | 1 failed | evidence/B-notes-red.txt |
| c-s3 | S3 | 正常例・失敗例 | 2 つの記録の `.txt` の写しは緑・`tests/` の差 0。片方が `test_x.py` なら赤 | 3 passed・0 行 / 1 failed | evidence/S3-two-records.txt |
| c-s2 | S2 | 失敗例・正常例 | 行の無い新しい種類の記録は赤。理由つきの行 1 つで緑 | 1 failed / 2 passed | evidence/S2-new-kind.txt |

## 9. 未確認と引き継ぎ

- 盲検のモデル: 1 番目の候補 gpt-6-astra は試していない。2 番目の opus・xhigh(Agent tool)で起こした。訂正(着地の後): 最初は「この Pod に起こす手段が無い」と書いたが、agora の会話は codex の枝で gpt-6-astra のターンを走らせられる(card ki-f63f65c153b0・done)ので、`agora conv open --agent model=gpt-6-astra effort=low` で別の会話として起こせた見込みがある。Agent tool に無い事だけを見て判断し、その口を試さなかった。fallback の理由は「利用不可」ではなく「試していない」。実際に動いたモデルの名は Agent tool の返りに出ないため、未確認(blind/launch.md)。
- c-a-after は機体の load 100 超の時に走り、選ばれた検は 60 秒の上限で 1 本も走らなかった(ran=0)。確かめたのは「記録の下の file が選ばれない事」で、他の検の緑ではない。改めた版の触った検は c-revision で直に走らせた。
- doeff-linter はこの Pod の PATH に無く、uv の cache に在った 0.2.0(a40add2d9+dirty)を `--linter` で渡した。
- 同じ形が残る所: `docs/design/` の行は今も残り、`docs/design/seat-home-common-instructions-AJ8C0B/model/test_*.py` の 4 本を外している。この 4 本にも盲検 A・B と同じ 2 つの穴(`make test-changed` の選びが検として選ぶ・本物の検を隠せる)がある。この依頼の要件(設計検証の記録)の外なので、ここでは直さず card acp:kanban-issue:ki-d153ab595504 に渡した(ki-c88db93c440a と relates-to)。
