# 盲検の前の主張 — 関数の中の require の macro の記録(依頼 lt-07HRM3MVKZXK2MYM4DEQ872YC1)

## この文書の目的

盲検 A・B を起こす前に、設計者の主張・前提・予想する波及の範囲を固定する。盲検の後に結論を書き換えない。

## 完了の範囲と時系列

- 実装完了まで。直しは盲検の前に main へ着地した(L2238・95751e88a・b6390f52d・a4abf5218)。この設計検証は着地した版
  a4abf5218 を基準版とし、盲検の失敗例で直しを測り直す(終わりの検査が求めた設計検証を、実装の後に行う — 順が逆である事を記録する)。
- 実装の前に行った非盲検の構成のレビュー(Fable 5.1)は、盲検の代わりに数えない。

## 要件(固定)

依頼の文(逐語の要約): 「関数の中の require で入れた macro も記録し、macro の変更で使う側を展開し直す」。
再現: 関数の中の require の macro を 11 → 12 に変えても、使う側の値が 11 → 11 → 11(Hy 1.3.1・uv.lock の版)。

## 責務と公開の契約

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 |
| --- | --- | --- | --- | --- |
| guard-recording | 展開が使った macro を拾い記録を作る(macro_use.py の RecordingTable・MacroRecording・_watch_local_tables・_whole_requires) | Hy が module の表と局所の表を引く口・局所の状態を開く口 `HyASTCompiler.new_local_state`・実行された code の拾い | 表の差し替えと戻し・局所の表の覚え方・code の絞り | `MacroRecording(module, hy_version, sha256_of)` の with → `record(source) -> MacroRecord` |
| records-names | Hy の内部の名と鍵の定義元(records.py の MACRO_TABLE・READER_TABLE・LOCAL_MACRO_TABLE) | 名と鍵の字 | — | 定数 |
| guard-hooks | SourceFileLoader の口を包む(loader_hooks.py) | 口の包み方・compile の口 | 記録の作り方(macro_use に任せる) | `install`・`source_to_code_as_import`・`macro_recording` |
| guard-check | 包みを uv.lock の版の Hy で動かして測る(test_bytecode_guard.py) | 見本・使い捨ての環境 | — | pytest の検 |
| guard-detail-tests | 記録の細部を venv の Hy で測る(test_macro_use_record.py) | 見本 | — | pytest の検 |
| adr-record | ADR-DOE-HY-008 の R7 の文 | どの検が包みの依りを測るか | — | 設計記録 |

## 変更シナリオと主張(盲検の前)

| id | 軸 | 変更 | 主張 | 予想する波及 |
| --- | --- | --- | --- | --- |
| S1 | storage | Hy を上げ、局所の状態を `new_local_state` を通さずに開く・局所の表の鍵を替える(.pyc と共有の置き場の古い展開を黙って使う形) | guard-check の使い捨ての環境の検(test_a_macro_required_inside_a_function_recompiles_the_user_on_pypi_hy)が uv.lock の変更で選ばれて赤になり、直すのは guard-recording と records-names だけ | guard-recording・records-names。guard-hooks・guard-check・adr-record は変えない |
| S2 | effects | 他の module の macro が局所の表へ入る道が増える(局所の require の別の形・macro が展開で局所の require を書く・入れ子の関数・class の本体・内包) | 局所の表は compiler が局所の状態を開く口 1 か所で覚えるので、入り方が増えても記録は変えずに拾う。記録の判定は record の中だけ | なし(増えた入り方の検を guard-check か guard-detail-tests に足すだけ) |
| S3 | concurrency | free-threaded の Python で、別々の module の展開が thread で並ぶ | 口の包みは process で 1 度だけ lock の下で入れ、局所の表は展開している module の記録の表にだけ覚えるので、別の module の展開は互いの記録に混ざらない(前提: 同じ module の import は import の lock で直列) | なし |
| S4 | distribution | 直した包みを worker・作業木へ配る(共有の置き場と .pyc は直す前の包みが作った記録を持つ) | 記録の形(RECORD_TAG)を変えず、関数の中の require を持たない module の記録は直す前と同じなので、配っても作り直しは起きない。関数の中の require を持つ module だけ記録が変わる | なし(RECORD_TAG を上げない) |
| X-hardware | hardware | — | 不適用: 包みは source と Hy の compiler だけを読み、機体に依らない | — |
| X-simulation | simulation | — | 不適用: 決定的な模擬の対象の振る舞いを持たない(検そのものが Hy の版上げの模擬 — 代役の wheel) | — |

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 局所の状態を開く口と鍵が uv.lock の版の Hy で効く | 振る舞いの契約テスト(関数の中の require の macro を変えたら使う側が作り直される。包みなしは古いまま) | test_bytecode_guard.py の test_a_macro_required_inside_a_function_recompiles_the_user_on_pypi_hy[none-11/before-hy-12/after-hy-12] | uv.lock の変更 → 契約の組([[tool.doeff.contract-tests]] prefix = "uv.lock")→ 使い捨ての環境 | 見本は入れ子の関数 1 つ |
| 使った局所の macro だけを記録し、局所の全部の require も古いと判じる | 記録の契約テスト | test_macro_use_record.py の local の 3 本 | 触った file の検・日次 | venv の Hy(file の fixture は uv.lock の版の一致を見ない) |
| 名と鍵の定義元は records に 1 か所 | macro_use は records の定数を読む | records.LOCAL_MACRO_TABLE | — | 静的な禁止は置いていない |
