# S-dist の失敗例(素朴な形・盲検 B の差分)と main

## digest を file の状態で覚える素朴な形(着地させない branch wt/ki-3850-naive-red 3d37e76ce)— 条 1〜3
```
E       AssertionError: 別の木で macro の本体が違うのに、2 つ目の process が古いと判じない
E       assert True is False
E       AssertionError: macro が返す別の module の値を替えたのに、2 つ目の process が古いと判じない
E       assert True is False
E       AssertionError: macro が返す環境変数の値が違うのに、2 つ目の process が古いと判じない
E       assert True is False
FAILED packages/doeff-hy/tests/test_macro_use_record.py::test_a_macro_changed_in_another_tree_is_stale_in_a_second_process
FAILED packages/doeff-hy/tests/test_macro_use_record.py::test_a_changed_value_from_another_module_is_stale_in_a_second_process
FAILED packages/doeff-hy/tests/test_macro_use_record.py::test_a_changed_environment_value_is_stale_in_a_second_process
3 failed, 1 passed, 45 deselected, 1 warning in 17.26s
```
## 盲検 B の差分(照らしの答えを覚える)— b1_probe(ce9c19488 と差分の写し)
```
base(ce9c19488):
B の置き場の .verdict の数: 0
C3 check(同じ package の STEP 5→50) {"current": false, "walked": 3}
要約 {"A3_current": false, "B3_value": [30, 5], "C3_current": false}
差分の写し:
B の置き場の .verdict の数: 4
C3 check(同じ package の STEP 5→50) {"current": false, "walked": 3}
要約 {"A3_current": true, "B3_value": [3, 5], "C3_current": false}
```
## 新しい検を盲検 B の差分の写し(84072f2bb + verdict.diff)で撃つ
```
E       AssertionError: macro が返す別の top の package の値を替えたのに、次の process が古いと判じない
E       assert True is False
FAILED packages/doeff-hy/tests/test_macro_use_record.py::test_a_changed_value_from_another_top_package_is_stale_after_an_unchanged_check
1 failed, 4 passed, 46 deselected, 1 warning in 16.58s
```
## main(cf7ea9cc0)
```
test_a_macro_changed_in_another_tree_is_stale_in_a_second_process PASSED [ 22%]
test_a_changed_value_from_another_module_is_stale_in_a_second_process PASSED [ 33%]
test_a_changed_environment_value_is_stale_in_a_second_process PASSED [ 44%]
test_a_changed_value_from_another_top_package_is_stale_after_an_unchanged_check PASSED [ 55%]
================= 9 passed, 46 deselected, 1 warning in 9.74s ==================
```
