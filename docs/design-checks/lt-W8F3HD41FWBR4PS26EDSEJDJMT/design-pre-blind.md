# 設計検証 lt-W8F3HD41FWBR4PS26EDSEJDJMT — 盲検の前の主張(card ki-2320b23ba160)

この file は盲検 A・B を頼む前に固定した主張です。盲検の後に書き換えません(後の結論は design.md)。

## 完了の範囲

実装完了まで。変更は doeff main c8fd2c26a(L2220・基底 c33b881a9)に着地済み。この検証は着地した変更を対象にする。

## 要件(card ki-2320b23ba160 の受入)

- ca2a64cac: test_static_cache が check_slot.py の 1 行に当たって赤になる原因を突き止め、直して緑にする
- c194a37f8: test_budget が doeff-vm の検査つきの build に当たって赤になる原因を突き止め、直して緑にする
- c50ae0325: 直す前の main で赤・直した後で緑である事を同じ doeff の版の上で実行して card に書く

## 原因(実測)

1. test_static_cache::test_doeff_hy_sources_do_not_read_their_place — 49deba317 が check_slot.take_slot の名乗りの文で
   正本の `module.__file__` を読んだ。doeff_hy の source は module の置き場(`__file__`・`__cached__`)を読まない
   (保存した展開の記録に隣の file の中身が入らない — 設計検証 lt-TBEF3PAMAVQ8B3Z07MDSCA994C)という守りに当たった。
2. test_budget::test_report_mode_warns_and_lists_but_stays_green — 検査つきの build は訳ではない(root の conftest が test の
   session では常に検査を有効にする)。pytester.runpytest は同じ process で内側の pytest を走らせ、内側の
   `catch_warnings` は外側の警告の filter を写して引き継ぐ。外側が `-W ignore::pytest.PytestWarning` だと BudgetWarning
   (PytestWarning の子)が消え、警告の行を期待する検が赤になる。
3. (範囲の外で見つけた)test_static_check_slot の 3 本 — dotfiles の正本 machine_check_slots.py(142108b9f)の policy_of が
   宣言の processesPerSlot を必須にし、検が写す宣言にその鍵が無かった。

## 責務(module)

| id | 所在 | 責務 | 持つ知識 | 隠す知識 |
|---|---|---|---|---|
| M-slot | packages/doeff-hy/src/doeff_hy/check_slot.py | doeff-hy-check が機体の検の枠を正本から取る。正本が無い・読めない・口が揃わない時は 1 行名乗って素通し | 正本の置き場(home / CANON_UNDER_HOME)・口の名(CANON_CALLS・CANON_ERRORS) | 正本の方策の中身(read_policy の答えを読まずに take へ渡す) |
| M-place-guard | packages/doeff-hy/tests/test_static_cache.py の test_doeff_hy_sources_do_not_read_their_place | doeff_hy の source が `__file__`・`__cached__` に触れる行を表 PLACE_LINES の外で拒む | 許す行の表 | — |
| M-budget | packages/doeff-hy-pytest/src/doeff_hy_pytest/budget.py | 検の時間・歩数を測り、超過を fail の形なら赤・報告の形なら BudgetWarning と終わりの一覧で知らせる | 判定・警告の文・build の種類の見出し | 警告をどう見せるか(利用者の警告の filter)には触れない |
| M-budget-test | packages/doeff-hy-pytest/tests/test_budget.py の test_report_mode_warns_and_lists_but_stays_green | 報告の形が警告と一覧を出し赤にしない事を、pytester の内側の走りで確かめる | 内側の走りの引数(c8fd2c26 から `-W default::doeff_hy_pytest.budget.BudgetWarning`) | — |
| M-slot-scene | packages/doeff-hy/tests/test_static_check_slot.py の _scene | 機体の正本を検体の home へ写し、K = 1 の宣言を隣に置く | 宣言の JSON(c8fd2c26 から slots・processesPerSlot・dir・waitLimitSeconds) | — |
| M-canon | dotfiles agent/tests/machine_check_slots.py(別の repo) | 宣言を読み(policy_of)、枠を取る・待つ | 宣言の鍵と検め | doeff からは中を読まれない |

## 変更シナリオと主張(6 軸)

- S1(effects・適用): 外側の走り方が警告の filter を変える(日次や担い手が `-W ignore::pytest.PytestWarning`・`-W ignore` を付ける)。
  主張: test_report_mode は外側の -W に依らず緑。変わるのは M-budget-test だけで、M-budget(警告を出す判断)は変えない。
  予想の範囲: M-budget-test の 1 行。
- S2(storage・適用): 正本の宣言の形(machine_check_slots.json の鍵)が変わる(例 processesPerSlot が必須になった)。
  主張: doeff の中で変わるのは M-slot-scene の宣言だけ。M-slot は方策を読まずに take へ渡すので変わらない。
  予想の範囲: M-slot-scene の宣言の 1 か所。
- S3(hardware・適用): 機体ごとに正本の置き場(home)が違う・名乗りの文を変える。
  主張: M-slot の名乗りは引数 home と CANON_UNDER_HOME から path を作り、module の置き場を読まない。今後 M-slot に
  `__file__` を読む行を足すと M-place-guard が拒む。予想の範囲: M-slot の 1 行。
- S4(concurrency・適用): 検を pytest-xdist の worker で並べて走らせる(担い手の走りは xdist の gw0)。
  主張: xdist の worker でも S1 と同じく外側の -W に依らず緑。予想の範囲: 変更なし(S1 の直しで足りる)。
- S5(distribution・除外): 枠の分配を機体をまたいで行う変更は正本(M-canon・dotfiles)の持ち物で、doeff の M-slot は
  take を呼ぶだけ。この変更の範囲に無い。
- S6(simulation・除外): この 3 本の検は決定的な模擬の世界を使わない(test_report_mode は本物の CPU 時間で超過を作る)。
  模擬の時計へ移す変更はこの直しの範囲に無い。

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| M-slot が置き場を読まない | source の行の検査(`__file__`・`__cached__` を含む行を表の外で拒む) | test_static_cache.py::test_doeff_hy_sources_do_not_read_their_place | doeff の pytest(日次・変更時の focused) | 文字列の検査で、`getattr(m, "__fi" + "le__")` や `inspect.getfile` は拒まない |
| test_report_mode が外側の -W に依らない | 内側の走りに -W で BudgetWarning を見せる filter を渡す(pytest は cmdline の filter を後から前へ入れ、引き継いだ filter より強い) | test_budget.py::test_report_mode_warns_and_lists_but_stays_green | doeff の pytest | 他の pytester の検が外側の -W に依るかは個別に測っていない(BudgetWarning の行を期待するのはこの 1 本だけ) |
| 検の宣言が正本の求める鍵を持つ | 正本の policy_of 自身が鍵の欠けを PolicyUnreadable で断り、検が赤になる | test_static_check_slot.py::_scene と dotfiles の policy_of | doeff の pytest(機体に dotfiles が在る時だけ・無ければ skip) | 正本が鍵を足すと doeff の検が後から赤になる(先に知らせる仕組みは無い) |
