# 盲検への入力 — doeff の bytecode の包みが依る Hy の内部を、uv.lock の版の PyPI の Hy で測る検

## 対象の版と読む所

- repo: doeff。版: 0de52de264f40cbe43e01d4638e54e0cfa9f9af1(この作業木はその版で切ってある)。git の歴史と commit の文は読まないでください
  (対象は今の木の中身です)。
- 読む file(この作業木の中。書き換えない):
  - 包み(bytecode guard): `packages/doeff-hy/src/doeff_hy_bytecode_guard/` — 特に `macro_use.py`(`RecordingTable`・`MacroRecording`・
    `MacroRecording.record`)・`records.py`(`MACRO_TABLE`・`READER_TABLE`・`GENSYM_NAME`・`RENAMED_BINDING`・`canonical_gensyms`)・
    `loader_hooks.py`(`_recording_source_to_code`・`source_to_code_as_import`・`install`)。
  - 包みの検: `packages/doeff-hy/tests/test_bytecode_guard.py`(`_LOADER`・`_IMPORT_USER`・`_COMPILE_TWICE`・`_run_on_pypi_hy`・
    `_probe_on_pypi_hy` と検の全部)。
  - venv の Hy で包みを測る他の検: `packages/doeff-hy/tests/test_gensym_canonical.py`・`test_macro_use_record.py`。
  - 選び方: root の `pyproject.toml` の `[[tool.doeff.contract-tests]]`(`prefix = "uv.lock"` の行)・`scripts/run_changed_tests.py`
    (`make test-changed` の本体。`select_targets`)・`tests/test_contract_table_follows_packages.py`・`scripts/semgrep_locked.py`
    (`locked_version`)。
  - 設計記録: `docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy`(R2・R4・R7 と事実・解釈)。
  - 入っている Hy 1.3.1 の source: `.venv/lib/python3.14t/site-packages/hy/`(`macros.py` の `macroexpand`・`require`・`require_reader`、
    `core/util.hy` の `gensym`、`compiler.py` の `get_anon_var`、`scoping.py`、`reader/hy_reader.py`)。

## 要件(固定)

doeff の bytecode の包み(doeff_hy_bytecode_guard — macro の変更で古い bytecode を使わない)は、import でない形で Hy の内部に依る:
module の macro の表の名 `_hy_macros`・`_hy_reader_macros`、Hy の展開(`hy.macros.macroexpand`)がその表を「名 in 表」と「表[名]」で引く事
(`RecordingTable` が引いた名を覚える前提)、gensym の名と let が改めた束縛の名(`get_anon_var`)の形(compile の口の正準化が読む)。

受入の条: これらの依りを、Hy の版を上げる便(uv.lock の hy の行を変える便)が登記の前に測る形にし、「Hy が表を `.get` で引く」失敗ケースが
赤になる事を確かめる。

利用の状況: doeff の Hy の版は uv.lock が固定する(今 1.3.1)。Hy を上げる便は uv.lock と doeff-hy の Hy の上限(ADR-DOE-HY-008 R6)を変え、
登記の前に `make test-changed` を走らせる。`make test-changed` は作業木の venv を sync せずに使う(`uv run --no-sync`)。着地の門は検を
走らせない。日次の全体の回は make sync の後に全部の検を走らせる。

## 責務と公開の契約

| 責務 | 持つ知識 | 公開の形 |
| --- | --- | --- |
| 使った macro を拾う(guard-recording — `macro_use.py`) | Hy が module の表を引く口(`__contains__`・`__getitem__`)・表の名(`records.MACRO_TABLE`・`READER_TABLE`)・reader macro は使う側の module の reader の表に在る他の module の物を全部記録する | `MacroRecording`(with の中が展開)→ `MacroRecord` |
| gensym の名を振り直す(guard-canonicalization — `records.canonical_gensyms`) | gensym の名の形 `GENSYM_NAME`・let が改めた束縛の名の形 `RENAMED_BINDING` | `canonical_gensyms(code) -> code` |
| 口を包む(guard-hooks — `loader_hooks.py`) | SourceFileLoader の口の包み方・compile の口(`source_to_code_as_import`) | `install()`・`source_to_code_as_import` |
| 包みを uv.lock の版の Hy で測る(guard-check — `test_bytecode_guard.py`) | 見本の package・使い捨ての環境(`uv run --no-project --isolated --with hy==<uv.lock の版>`)・確かめの script | pytest の検 |
| 版を決める(version-source — `scripts/semgrep_locked.py`) | uv.lock の hy の行の読み方 | `locked_version(repo, "hy")` |
| いつ走るか(selection — root の `pyproject.toml` の表) | uv.lock を変えた便が払う検の組 | 表の行 |
| 設計記録(adr-record — ADR-DOE-HY-008) | 包みの依りをどの検が測るか | 設計記録 |
| Hy の版(hy-version-pin — uv.lock・doeff-hy の Hy の上限) | — | — |

## 将来の変更についての主張

| id | 変える要求 | 変える所 | 変えない所 | 前提 |
| --- | --- | --- | --- | --- |
| S1 | Hy を上げ、新しい版が module の macro の表を `.get` で引く(か表の名・reader の表の名を替える) | hy-version-pin。検が赤になり、直すのは guard-recording | guard-check・version-source・selection・adr-record・guard-canonicalization・guard-hooks | 検は包みの振る舞い(macro・reader macro を変えたら使う側が作り直されるか)を測り、引き方や名の写しを持たない。版は uv.lock から読む。新しい版でも見本の書き方(`defmacro`・`defreader`・`require :readers`)が通る |
| S2 | Hy を上げ、新しい版の gensym の名か let が改めた束縛の名の形が変わる | hy-version-pin。検が赤になり、直すのは guard-canonicalization | guard-check・version-source・selection・adr-record・guard-recording・guard-hooks | 検は「同じ source の code が process の gensym の数えに依らない」性質を測り、名の形の写しを持たない。見本の macro の展開が gensym を let で束ねる |
| S3 | 包みに Hy の内部への新しい依りを足す(例: Hy の別の表を読む・Hy の生成名の別の形を振り直す) | guard-recording か guard-canonicalization と、その振る舞いを測る検 1 本を test_bytecode_guard.py へ | version-source・selection・adr-record・hy-version-pin | test_bytecode_guard.py は uv.lock の組に在るので、足した検は表を変えずに uv.lock の変更で選ばれ、同じ使い捨ての環境で測られる。依りを足す便がその振る舞いの検を test_bytecode_guard.py に置く |

## 検の走らせ方(読み取りと、この作業木の中の一時の変更・自分の作業 dir だけ)

```
uv run --no-sync pytest packages/doeff-hy/tests/test_bytecode_guard.py -q -m 'not e2e'
uv run --no-sync pytest packages/doeff-hy/tests/test_bytecode_guard.py -q -k "reader_macro or gensyms"
uv run --script <script> …   # 選び方を確かめる時は scripts/run_changed_tests.py の select_targets を呼べる
```

- 機体の検の枠が埋まっていると pytest は `check-slot: … 待つ` を出して順を待つ(止まってはいない)。
- 包みの検は、uv.lock の hy の版の PyPI の Hy を使い捨ての環境に入れて測る。uv の環境変数(`UV_FIND_LINKS`・`UV_NO_INDEX`・`UV_NO_CACHE`)で、
  その環境に入る Hy を手元で作った wheel に差し替えられる(Hy は funcparserlib に依る — 差し替える時は同じ dir に funcparserlib の wheel も
  要る。venv の site-packages に両方が在る)。Python は `uv run` を通して走らせ、機体の python3 は使わない。
- 全部の検(repo 全体)は走らせない。共有の checkout(~/repos/doeff)と他の作業木は書き換えない。
