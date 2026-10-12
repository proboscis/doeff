# 盲検の前に固定した設計と主張 — bytecode の包みの Hy の内部への依りを uv.lock の版で測る(依頼 lt-JYZ9N64ZE70GCBXPP33QDG3WF7・card ki-eafa369600cd)

## 完了の範囲

実装完了まで。実装 = doeff の branch `wt/hy-bytecode-guard-internals` の commit 0de52de264f40cbe43e01d4638e54e0cfa9f9af1
(基準版 = 分かれ目の origin/main d406b28914b20875e8b452c0b384adf8a0390ea7)。main への着地は、この設計検証の後に `ai land request` で行う(予定)。

## 要件(固定)

card ki-eafa369600cd の受入の条(逐語): 「bytecode の包みの Hy の内部への依りを R7 の母集団に足すか、uv.lock の変更で包みの検が選ばれる形に
して、Hy の表の引き方を .get に替えた失敗ケースが赤になる事を doeff main で確かめる」。

依頼の本文の名指す依り: module の macro の表の名 `_hy_macros`・`_hy_reader_macros`、`hy.macros.macroexpand` がその表を「名 in 表」と「表[名]」で
引く事(RecordingTable が引いた名を覚える前提)、gensym と get_anon_var の名の形。

利用の状況: doeff の Hy の版は uv.lock が固定する(今 1.3.1)。Hy を上げる便は uv.lock と doeff-hy の Hy の上限(ADR-DOE-HY-008 R6)を変え、
登記の前に `make test-changed` を走らせる。`make test-changed` は作業木の venv を sync せずに使う(`uv run --no-sync`)ので、uv.lock を上げて
sync していない作業木では venv の Hy は古い版のまま。着地の門は検を走らせない。日次の全体の回は make sync の後に全部の検を走らせる。

## 現状(直す前 = 基準版の検の file)の実測

Hy 1.3.1 の 1 か所だけを替えた wheel(evidence/scripts/build_hy_variants.py)を uv の find-links で入れ、包みの検の file を走らせた:

| 変種 | 替えた所 | 結果(基準版の test_bytecode_guard.py・20 本) |
| --- | --- | --- |
| get | `hy.macros.macroexpand` が module の表を `.get(fn)` で引く | 9 本赤(macro を変えても使う側が 11 のまま)— before-get.log |
| table | `_hy_macros` の名を Hy の全部で `_hy_macro_table` へ | 9 本赤 — before-table.log |
| reader | `_hy_reader_macros` の名を Hy の全部で `_hy_reader_macro_table` へ | 20 本緑 — before-reader.log |
| gensym | gensym の名を `_hy_gs<数え>_<base>` へ | 20 本緑 — before-gensym.log |
| anon | get_anon_var の名を `_hy_<種><数><元の名>` へ | 20 本緑 — before-anon.log |

検の file は依頼の後に card ki-33d2441b7e28(cd305fbd0)で uv.lock の契約の組に入っていた。

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
| --- | --- | --- | --- | --- |
| guard-recording | 展開が使った macro を拾う(macro_use.RecordingTable・MacroRecording・record) | Hy が module の表を引く口(`__contains__`・`__getitem__`)・表の名(records.MACRO_TABLE・READER_TABLE)・reader macro は表に在る他の module の物を全部記録 | 表の差し替えと戻し・拾った code の絞り | `MacroRecording`(with の中が展開)→ `MacroRecord` |
| guard-canonicalization | compile の済んだ code の gensym の名を module の中の順へ振り直す(records.canonical_gensyms) | gensym の名の形 GENSYM_NAME・let が改めた束縛の名の形 RENAMED_BINDING | 名の集め方・振り直しの対応 | `canonical_gensyms(code) -> code` |
| guard-hooks | SourceFileLoader の口を包み、compile の直後に正準化と記録を足す(loader_hooks) | 口の包み方・compile の口(source_to_code_as_import) | Hy の importer との重なり | `install()`・`source_to_code_as_import` |
| guard-check | 包みを uv.lock の版の PyPI の Hy の使い捨ての環境で動かして測る(packages/doeff-hy/tests/test_bytecode_guard.py) | 見本の package・使い捨ての環境の作り方(`uv run --no-project --isolated --with hy==<版>`)・確かめの script(`_probe_on_pypi_hy`) | — | pytest の検 |
| version-source | uv.lock の hy の版を読む(scripts/semgrep_locked.py の locked_version) | uv.lock の読み方 | — | `locked_version(repo, "hy") -> str` |
| selection | uv.lock を変えた便が払う検(root の pyproject.toml の `[[tool.doeff.contract-tests]]` の `prefix = "uv.lock"`・tests/test_contract_table_follows_packages.py) | 表の行 | — | 表 |
| adr-record | ADR-DOE-HY-008 の R7 の文・事実・解釈 — 包みの依りをどの検が測るかの記録 | — | — | 設計記録 |
| hy-version-pin | uv.lock の hy の行と doeff-hy の Hy の上限 | — | — | uv.lock・pyproject.toml |

## 変更シナリオと主張(盲検の前に固定)

| id | 変える要求 | 変える所(予想) | 変えない所 | この設計で収まる理由 | 前提 |
| --- | --- | --- | --- | --- | --- |
| S1 | Hy を上げ、新しい版が module の macro の表を `.get` で引く(か表の名を替える・reader の表の名を替える) | hy-version-pin。uv.lock の変更で包みの検が選ばれて赤になり、直すのは guard-recording(RecordingTable が新しい引き方も拾う・表の名) | guard-check・version-source・selection・adr-record・guard-canonicalization・guard-hooks | 検は包みの振る舞い(macro・reader macro を変えたら使う側が作り直されるか)を測り、引き方や名の写しを持たない。版は uv.lock から読む | 新しい版でも見本の macro・reader macro の書き方(`defmacro`・`defreader`・`require :readers`)が通る |
| S2 | Hy を上げ、新しい版の gensym の名か let が改めた束縛の名の形が変わる | hy-version-pin。uv.lock の変更で gensym の数えの検が赤になり、直すのは guard-canonicalization(GENSYM_NAME・RENAMED_BINDING と振り直し) | guard-check・version-source・selection・adr-record・guard-recording・guard-hooks | 検は「同じ source の code が process の gensym の数えに依らない」性質を測り、名の形の写しを持たない | 見本の macro の展開が gensym を let で束ねる |
| S3 | 包みに Hy の内部への新しい依りを足す(例: Hy の別の表を読む・Hy の生成名の別の形を振り直す) | guard-recording か guard-canonicalization と、その振る舞いを測る検 1 本を guard-check(test_bytecode_guard.py)へ | version-source・selection・adr-record・hy-version-pin | guard-check の file は uv.lock の組に在るので、file に足した検は表を変えずに uv.lock の変更で選ばれ、同じ使い捨ての環境で測られる | 依りを足す便が、その振る舞いの検を test_bytecode_guard.py に置く |

不適用の軸: hardware(検は source と PyPI の Hy を読むだけ)・simulation(模擬の対象の振る舞いを持たない — 検そのものが Hy の版上げの模擬)・
concurrency(thread の並走の正準化は test_gensym_canonical.py が venv の Hy で持ち、この便の範囲外)。storage(.pyc・共有の置き場の
古い展開)は S1、distribution(worker・作業木をまたぐ code の一致)は S2 の結果として扱う。

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| guard-recording の表の名と引き方が uv.lock の Hy で効く | 振る舞いの契約テスト(macro・reader macro の変更で使う側が作り直される・包みなしは古いまま) | test_bytecode_guard.py の test_a_macro_change_recompiles_the_user_on_pypi_hy ほか 9 本・test_a_reader_macro_change_recompiles_the_user_on_pypi_hy[none-10/before-hy-15] | uv.lock の変更 → `make test-changed` の契約の組 → 使い捨ての環境の PyPI の Hy(uv.lock の版) | 登記の前の `make test-changed` は書き手が走らせる約束(着地の門は走らせない)。日次は全部 |
| guard-canonicalization の名の形が uv.lock の Hy で効く | 振る舞いの契約テスト(同じ使う側を 2 度 compile し、間で gensym の数えを 100 進めても code が `==` で同じ) | test_bytecode_guard.py の test_the_code_does_not_depend_on_how_many_gensyms_the_process_made_on_pypi_hy | 同上 | marshal の bytes は object の参照の数で揺れるので比べない(code の `==`) |
| uv.lock の Hy の版で測る検が uv.lock の組に在る | 表の検(読み手を呼ぶ検の file を source から数え、表が uv.lock の変更で選ぶ事を確かめる) | tests/test_contract_table_follows_packages.py(ki-33d2441b7e28) | repo 全体の行で毎回 | — |

## 限界(盲検に渡さない)

- 包みに新しい依りを足す便が、その検を venv の Hy で測る file(test_gensym_canonical.py・test_macro_use_record.py)に置くと、登記の前には
  uv.lock の版で測られない(日次では測られる)。
- 見本は小さい(macro 1 つ・reader macro 1 つ・gensym 1 つ)。Hy が一部の形だけ引き方を替える(例: 入れ子の展開だけ `.get`)と見逃しうる。
- `hy.R` の 1 回の require(表を通らない)は source の文から読み、この便の検は測らない。
