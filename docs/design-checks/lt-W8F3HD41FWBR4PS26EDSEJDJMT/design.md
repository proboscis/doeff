# 設計検証 lt-W8F3HD41FWBR4PS26EDSEJDJMT — doeff の main の既存の赤 3 本の直し(card ki-2320b23ba160)

## この文書の目的

読み手が次の 3 つを辿れるようにする: (1) 3 本の赤の原因と直し、(2) 直しが責務の境界をどう守ると主張し、何で強制するか、
(3) 盲検 A・B の失敗例を試した結果と、その扱い。

## 完了の範囲

実装完了まで。変更は doeff main c8fd2c26a(L2220・基底 c33b881a9)に着地済み。依頼主は報告 lt-J5AJ1MHE0C1D576NBXX7XWYCD1 を
受け入れた(lt-QPKTFCCVSX8EFQDQQ2RV6H4CY5)。この検証の証拠は、どれも c8fd2c26a の上で走らせた(evidence/ の各 log の頭に版を書いた)。

## 事前の主張(盲検の前に固定)

design-pre-blind.md(sha256 1b116db1f5613325265f4f683ad3c918f42d95399b5748189ea01ca17950f81e)。要件・原因・責務の表・
変更シナリオ S1〜S6・強制方法の表はそこにあり、この文書で上書きしない。

## 直しの要約

| 赤 | 原因 | 直し(c8fd2c26a) | 変えた責務 |
|---|---|---|---|
| test_static_cache | M-slot の名乗りが正本の `module.__file__` を読み、M-place-guard に当たった | 正本の path を `home / CANON_UNDER_HOME` から作る | M-slot の 1 行 |
| test_budget::test_report_mode… | pytester の内側の pytest が外側の警告の filter を引き継ぎ、外側の `-W ignore::pytest.PytestWarning` で BudgetWarning が消えた | 内側に `-W default::doeff_hy_pytest.budget.BudgetWarning` を渡す | M-budget-test の 1 行 |
| test_static_check_slot の 3 本 | 正本(dotfiles 142108b9f)が宣言の processesPerSlot を必須にした | 検の宣言に processesPerSlot = 1 を置く | M-slot-scene の宣言 |

## シナリオごとの実測(予想と実際)

| シナリオ | 予想の範囲 | 実際 | 正常例 | 失敗例 |
|---|---|---|---|---|
| S1 effects(外側の -W) | M-budget-test の 1 行 | 予想どおり。M-budget は変えていない | -W 無し・`ignore::pytest.PytestWarning`・`ignore` で緑 | 内側の -W を外す変異で、外側 `ignore::pytest.PytestWarning` の時に nomatch(担い手の log と同じ形) |
| S2 storage(宣言の形) | M-slot-scene の宣言 | 形の変化については予想どおり。盲検 A・B は「新しい鍵の意味に doeff が従う」機能追加では M-slot も変わる事を示した(下) | check_slot の検 7 本緑 | processesPerSlot を外す変異で 3 本赤(「processesPerSlot は正の整数(現状 None)」) |
| S3 hardware(置き場) | M-slot の 1 行 | 予想どおり | 守りの検が緑 | 名乗りを `module.__file__` に戻す変異で守りが赤 |
| S4 concurrency(xdist) | 変更なし | 予想どおり(S1 の直しで足りる) | xdist の worker 1 つ・外側 `ignore::pytest.PytestWarning` で緑 | 内側の -W を外す変異で、xdist の gw0 で nomatch |
| S5 distribution | 除外 | — | — | — |
| S6 simulation | 除外 | — | — | — |

## 盲検の失敗例と扱い

| 盲検 | 失敗例 | 再現の結果 | 成立 | 扱い |
|---|---|---|---|---|
| B | doeff-hy-check の展開の子の数を宣言の processesPerSlot に揃える実装が、take の答えの `holding.policy.processes_per_slot` を読む。提示した検査を通り、正本の方策の中身(欄の名)が M-slot へ漏れる | patch を当てて置き場の守り + check_slot の検 8 本緑(B-repro-checks-pass)。probe で width(4) = 2 を返し、doeff が宣言の値を読む事を確かめた(B-repro-violation-a) | 成立 | 今回の変更が作った穴ではなく、M-slot の既存の守り(card ki-26ebd3f636e0 の AST の検は方策の「入る向き」だけを見る)の穴。card ki-1bfe2c42dce6 に切って渡した |
| A | 同じ要求を、方策を名に束ねて正本の公開関数へ不透明に渡す形で書くと、AST の検が正しい形まで断る。口の揃いの判定が全か無か | R1 の形で 2 本赤(A-R1-opaque-policy: `['module.read_policy()']` と、口だけの正本で「take が落ちた」の素通し) | 成立 | 同上。card ki-1bfe2c42dce6 |

S2 の主張の扱い: 事前の主張は「宣言の形が変わる」場合で、実測でも M-slot-scene の宣言だけが変わった。盲検の例は「新しい鍵の意味に
doeff-hy-check が従う」機能追加で、正本の公開の口の拡張(幅を返す)と M-slot の守りの作り直しを伴う契約の拡張になる。今回の範囲
(赤を直す)に入れず、予想の範囲を後から広げてもいない。

## 強制方法と実装の証拠

| 守る責務 | 強制 | 実装箇所 | 実行経路 | 正常例 / 失敗例の証拠 | 限界 |
|---|---|---|---|---|---|
| M-slot が置き場を読まない | source の行の検査 | test_static_cache.py::test_doeff_hy_sources_do_not_read_their_place | doeff の pytest(変更時の focused・日次) | P-S3-place-guard / N-S3-module-file-back | 文字列の検査。`inspect.getfile` 等は拒まない |
| test_report_mode が外側の -W に依らない | 内側の走りの -W | test_budget.py::test_report_mode_warns_and_lists_but_stays_green | doeff の pytest | P-S1-* ・P-S4-xdist / N-S1-pin-removed・N-S4-xdist-pin-removed | 他の pytester の検は、警告の行を期待しないので測っていない |
| 検の宣言が正本の求める鍵を持つ | 正本の policy_of が欠けを断る | test_static_check_slot.py::_scene | doeff の pytest(dotfiles の在る機体) | P-S2-check-slot / N-S2-no-processesPerSlot | 正本が鍵を足すと後から赤になる(先に知らせる仕組みは無い) |
| M-slot が方策の中身を読まない | AST の検(入る向きだけ) | test_static_check_slot.py::test_the_canon_policy_goes_straight_into_take(既存) | doeff の pytest | — | 出る向き(take の答えから読む)を断らない — 盲検 B で成立。card ki-1bfe2c42dce6 |

## 測っていない範囲

- 型検査(日次)と doeff-hy の 2 つの dir の全体の走り(4 時間ごとの全体の回)。
- 変更時の品質検査は c8fd2c26a の commit の前に `code-quality --scope changed`(fast)で passed(card ki-2320b23ba160 の発言)。

## 修正の履歴

- c8fd2c26a: 3 本の直し(盲検の前)。
- 盲検の後: この直しへの追加の修正は無し。盲検が示した既存の守りの穴は card ki-1bfe2c42dce6。
