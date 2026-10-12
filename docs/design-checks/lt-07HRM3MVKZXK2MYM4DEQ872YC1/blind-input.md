# 盲検の入力 — doeff-hy の bytecode の包みが、関数の中の require の macro を記録する形

## 対象

- repo: doeff。対象版: a4abf5218(この作業木の HEAD)。git の歴史と commit の文、docs/design-checks/ の下は読まないでください。
- 対象の code: packages/doeff-hy/src/doeff_hy_bytecode_guard/macro_use.py(RecordingTable・_watch_local_tables・MacroRecording・
  _whole_requires)・records.py(MACRO_TABLE・READER_TABLE・LOCAL_MACRO_TABLE・RECORD_TAG)・loader_hooks.py(macro_recording・
  source_to_code_as_import)。
- Hy の実物(uv.lock の版 1.3.1): .venv/lib/python3.14t/site-packages/hy/(compiler.py の new_local_state・local_state、
  macros.py の macroexpand と require、core/result_macros.py の require と defmacro の局所の扱い)。

## 要件(固定)

bytecode の包みは、Hy の module を compile する時に展開が使った macro を .pyc の記録に入れ、macro が変わったら使う側を compile し
直させる。関数の中の require で入れた macro も記録し、その macro を変えたら使う側を展開し直す(Hy 1.3.1 の macroexpand は compiler の
局所の状態の表を module の表より先に引く)。

## 責務と公開の契約

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
| --- | --- | --- | --- | --- |
| guard-recording | 展開が使った macro を拾い記録を作る(macro_use.py) | Hy が module の表と局所の表を引く口・局所の状態を開く口 HyASTCompiler.new_local_state・実行された code の拾い(called_code.py) | 表の差し替えと戻し・局所の表の覚え方・code の絞り | MacroRecording(module, hy_version, sha256_of) の with → record(source) -> MacroRecord |
| records-names | Hy の内部の名と鍵の定義元(records.py) | 名と鍵の字 | — | 定数 |
| guard-hooks | SourceFileLoader の口を包む(loader_hooks.py) | 口の包み方・compile の口 | 記録の作り方 | install・source_to_code_as_import・macro_recording |
| guard-check | 包みを uv.lock の版の Hy で動かして測る(packages/doeff-hy/tests/test_bytecode_guard.py) | 見本・使い捨ての環境(uv run --no-project --isolated --with hy==<uv.lock の版>) | — | pytest の検 |
| guard-detail-tests | 記録の細部を venv の Hy で測る(packages/doeff-hy/tests/test_macro_use_record.py) | 見本 | — | pytest の検 |
| adr-record | docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy の R7 の文 | どの検が包みの依りを測るか | — | 設計記録 |

## 変更シナリオと主張

| id | 変更 | 主張 | 予想する波及 |
| --- | --- | --- | --- |
| S1 | Hy を上げ、局所の状態を new_local_state を通さずに開く・局所の表の鍵を替える | guard-check の使い捨ての環境の検(test_a_macro_required_inside_a_function_recompiles_the_user_on_pypi_hy)が uv.lock の変更で選ばれて赤になり、直すのは guard-recording と records-names だけ | guard-recording・records-names |
| S2 | 他の module の macro が局所の表へ入る道が増える(局所の require の別の形・macro が展開で局所の require を書く・入れ子の関数・class の本体・内包) | 局所の表は局所の状態を開く口 1 か所で覚えるので、入り方が増えても記録は変えずに拾う。記録の判定は record の中だけ | なし(検を足すだけ) |
| S3 | free-threaded の Python で、別々の module の展開が thread で並ぶ | 口の包みは process で 1 度だけ lock の下で入れ、局所の表は展開している module の記録の表にだけ覚えるので、別の module の展開は互いの記録に混ざらない(前提: 同じ module の import は import の lock で直列) | なし |
| S4 | 直した包みを worker・作業木へ配る(共有の置き場と .pyc は直す前の包みが作った記録を持つ) | 記録の形(RECORD_TAG)を変えず、関数の中の require を持たない module の記録は直す前と同じなので、配っても作り直しは起きない | なし |

## 検査(実体と実行の仕方)

| 検査 | 実行 | 選ばれ方 |
| --- | --- | --- |
| test_bytecode_guard.py | `uv run --no-sync pytest packages/doeff-hy/tests/test_bytecode_guard.py -q -m 'not e2e'`(使い捨ての環境の検と、venv の Hy が uv.lock の版である事を確かめた後の同じ process の検) | root の pyproject.toml の [[tool.doeff.contract-tests]] prefix = "uv.lock" の行(uv.lock の変更で選ばれる)と、file を触った時 |
| test_macro_use_record.py | `uv run --no-sync pytest packages/doeff-hy/tests/test_macro_use_record.py -q -m 'not e2e' -k local`(local の 3 本は数秒。file の全部は約 6 分 — 全部は走らせないでください) | file を触った時・日次 |
| Hy の代役 | Hy 1.3.1 の 1 か所を替えた wheel を uv の find-links で使い捨ての環境へ入れられる(`UV_NO_INDEX=1 UV_FIND_LINKS=<dir> UV_NO_CACHE=1`)。wheel の元は .venv の site-packages | 手で |
