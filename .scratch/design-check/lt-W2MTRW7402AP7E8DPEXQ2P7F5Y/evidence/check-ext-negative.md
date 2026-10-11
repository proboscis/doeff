# S-storage-ext の失敗例(盲検 A)の再現と直し

## 直す前(doeff ce9c19488)— 盲検 A の再現 script(run_a.sh・probe_a.py の写し)で setup → first → edit(factor 3→30)→ second
```
== hy
{"variant": "hy", "phase": "edit", "edited": "macros.hy"}
{"variant": "hy", "phase": "second", "patched": [], "pyc_record_macros": [["sepx_pkg.macros", "scaled", "774c995b487c"]], "pyc_record_is_current_here": false, "user.value": 30, "macros.__file__": "macros.hy", "scaled_digest_now": "f744158cbd47", "macro_dependencies(user)": ["macros.hy"], "binding_of(macros, TABLE)": ["'(setv TABLE {\"a\" 1})", "'(defmacro looks [] (get TABLE \"a\"))"]}
== hym-defpoint
{"variant": "hym-defpoint", "phase": "edit", "edited": "macros.hym"}
{"variant": "hym-defpoint", "phase": "second", "patched": [], "pyc_record_macros": [["sepx_pkg.macros", "scaled", "437dc7f45958"]], "pyc_record_is_current_here": true, "user.value": 3, "macros.__file__": "macros.hym", "scaled_digest_now": "437dc7f45958", "macro_dependencies(user)": [], "binding_of(macros, TABLE)": null}
```
## 失敗ケースの commit 84072f2bb 単独(直す前の macro_use)— uv run pytest test_macro_use_record.py -k 'unlisted_extension or top_package'
```
E       AssertionError: 拡張子 .hym の module の class の method の本体を替えても digest が変わらない
E       assert '5a53630f0434f8ad368069ea409c93237e6790a2d7e71d7cf7ba60922fa37f3f' != '5a53630f0434f8ad368069ea409c93237e6790a2d7e71d7cf7ba60922fa37f3f'
E        +  where '5a53630f0434f8ad368069ea409c93237e6790a2d7e71d7cf7ba60922fa37f3f' = <function test_a_class_in_a_source_with_an_unlisted_extension_is_covered_by_its_body.<locals>.digest at 0x3d65c6b3300>(3)
E        +  and   '5a53630f0434f8ad368069ea409c93237e6790a2d7e71d7cf7ba60922fa37f3f' = <function test_a_class_in_a_source_with_an_unlisted_extension_is_covered_by_its_body.<locals>.digest at 0x3d65c6b3300>(30)
1 failed, 1 passed, 49 deselected, 1 warning in 2.81s
```
## 直した後(doeff cf7ea9cc0 = L2061)
```
== hy
{"variant": "hy", "phase": "second", "patched": [], "pyc_record_macros": [["sepx_pkg.macros", "scaled", "774c995b487c"]], "pyc_record_is_current_here": false, "user.value": 30, "macros.__file__": "macros.hy", "scaled_digest_now": "f744158cbd47", "macro_dependencies(user)": ["macros.hy"], "binding_of(macros, TABLE)": ["'(setv TABLE {\"a\" 1})", "'(defmacro looks [] (get TABLE \"a\"))"]}
== hym-defpoint
{"variant": "hym-defpoint", "phase": "second", "patched": [], "pyc_record_macros": [["sepx_pkg.macros", "scaled", "774c995b487c"]], "pyc_record_is_current_here": false, "user.value": 30, "macros.__file__": "macros.hym", "scaled_digest_now": "f744158cbd47", "macro_dependencies(user)": [], "binding_of(macros, TABLE)": null}
packages/doeff-hy/tests/test_macro_use_record.py::test_a_class_in_a_source_with_an_unlisted_extension_is_covered_by_its_body PASSED [ 66%]
```
