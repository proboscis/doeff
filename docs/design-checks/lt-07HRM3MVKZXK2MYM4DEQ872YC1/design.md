# 設計検証 — 関数の中の require の macro を、bytecode の包みの記録に入れる(依頼 lt-07HRM3MVKZXK2MYM4DEQ872YC1)

## この文書の目的

読み終えた人が、次の 3 つを判断できる状態になる事:

1. 関数の中の require の macro を記録する包みが、どの責務に何を置き、どの検がそれを守るか。
2. 盲検 A・B が見つけた失敗例と、その直しが実測で効いた事。
3. 残る限界。

## 全体の図(直した後)

```
Hy の module を compile する(import・解析器の展開・型検査の展開)
   │
   ▼ loader_hooks.macro_recording(module)  ── guard-hooks(口の包み)
   ▼
macro_use.MacroRecording(guard-recording)
   ├─ module の表 _hy_macros を RecordingTable に替える(引いた名・表に無かった名を覚える)
   ├─ _watch_local_tables(): HyASTCompiler.new_local_state を process で 1 度だけ lock の下で包む
   │     compiler が局所の状態を開くたび、その macro の表を compiler.module の RecordingTable.local_tables に覚える
   └─ record(): 局所の表の他の module の macro のうち、展開で実行された物を使った macro に入れる
   ▼
records.MacroRecord(印 RECORD_TAG = doeff-hy/macro-dependencies/7)── records-names
   │  .pyc の定数と共有の置き場の entry。印の違う記録(6 = 局所の macro を覆わない世代)は記録なしと同じ → compile し直す
   ▼
照らし record_is_current(記録に在る行だけを今の環境と比べる)
```

## 完了の範囲と時系列

- 実装完了まで。直しは盲検の前に main へ着地した(L2238 — 95751e88a・b6390f52d・a4abf5218)。終わりの検査が設計検証を求めたので、
  着地した版 a4abf5218 を基準版に、盲検の前の主張(design-pre-blind.md)を固定してから盲検 A・B を起こした。順が逆(実装の後の検証)
  である事を記録する。実装の前の非盲検の構成のレビュー(Fable 5.1・README.md の節)は盲検に数えない。
- 盲検を受けた直し(この branch): 記録の印を 7 へ上げる(盲検 A)・盲検 B の形を拒む検 2 本を足す。

## 要件(固定)

依頼の文の要約: 「関数の中の require で入れた macro も記録し、macro の変更で使う側を展開し直す」。再現は Hy 1.3.1 で 11 → 11 → 11。

## 責務と公開の契約

| id | 責務 | 持つ知識 | 公開の形 |
| --- | --- | --- | --- |
| guard-recording | 展開が使った macro を拾い記録を作る(macro_use.py) | Hy が module の表と局所の表を引く口・局所の状態を開く口・実行された code の拾い | `MacroRecording` の with → `record(source)` |
| records-names | Hy の内部の名と鍵・記録の印の定義元(records.py) | MACRO_TABLE・READER_TABLE・LOCAL_MACRO_TABLE・RECORD_TAG(記録が覆う範囲の世代) | 定数 |
| guard-hooks | SourceFileLoader の口を包む(loader_hooks.py) | 口の包み方・compile の口 | `install`・`source_to_code_as_import`・`macro_recording` |
| guard-check | 包みを uv.lock の版の Hy で動かして測る(test_bytecode_guard.py) | 見本・使い捨ての環境 | pytest の検 |
| guard-detail-tests | 記録の細部を venv の Hy で測る(test_macro_use_record.py) | 見本 | pytest の検 |
| adr-record | ADR-DOE-HY-008 の R7 の文 | どの検が包みの依りを測るか | 設計記録 |

## 変更シナリオ — 盲検の前の主張と実測

| id | 軸 | 主張(盲検の前・design-pre-blind.md) | 実測 | 結論と直し |
| --- | --- | --- | --- | --- |
| S1 | storage | Hy が局所の状態を口を通さずに開く・鍵を替えると guard-check の使い捨ての環境の検が赤。直すのは guard-recording と records-names | s1-variant-localstate.log: 代役の Hy で before-hy・after-hy の 2 本が赤。s1-variant-base.log: 替えない Hy で 3 本緑 | 成立 |
| S2 | effects | 局所の表への入り方が増えても、口 1 か所で覚えるので記録は変えない | 入れ子の関数(guard-check の見本)・入れ子の関数の中の `*` の require(guard-detail-tests)で記録に入る(b-check-without-diff.log)。直す前の包みでは赤(before-fix.log) | 成立 |
| S3 | concurrency | 口は 1 度だけ lock の下で包み、局所の表は compile している module の記録にだけ入るので、別の module の展開は混ざらない | 盲検 B の形(包みを展開ごとに入れて外す)は、入れ子の compile で外の記録に混ざり、別 thread の展開が先に終わると漏れた(b-check-with-diff.log)。今の形では混ざらず漏れない(b-check-without-diff.log) | 主張は今の形で成立。盲検 B の形を提示した検が拒まなかった — 検 2 本を guard-detail-tests に足した |
| S4 | distribution | 記録の形(RECORD_TAG)を変えず、配っても作り直しは起きない | 盲検 A: 直す前の包みの .pyc と置き場の entry(局所の macro の行が無い)を、直した包みが今に合うと判じ、macro を 12 へ変えても 11(repro-a-real-before.log・直す前の実物の包み 5454330f5 で再現) | **不成立**。記録の覆う範囲が変わったのに印を上げなかった(records-names の知識 — 「印は記録の覆う範囲の世代」— を予想に入れていなかった)。RECORD_TAG を 7 へ上げ、6 の記録を記録なしと同じに扱う(repro-a-real-after.log: 12・置き場は新しい entry を足す)。費用: 全部の Hy の module が 1 度 compile し直される |
| X-hardware | hardware | 不適用: 包みは source と Hy の compiler だけを読み、機体に依らない | — | — |
| X-simulation | simulation | 不適用: 決定的な模擬の対象の振る舞いを持たない | — | — |

## 盲検 A・B と、再現・修正

起動の記録は evidence/blind-meta.md、入力は blind-input.md・blind-a-prompt.md・blind-b-prompt.md、返答は evidence/blind-a-reply.md・
blind-b-reply.md(加工なし)、盲検の script と log は evidence/blind-a-files/・blind-b-files/。

### 盲検 A — 直した包みを配っても、直す前の記録を信じ続ける

| A の主張 | 設計者の再現 | 結論と直し |
| --- | --- | --- |
| 直す前の包みの .pyc の記録は `macros=()` で、直した包みはそれを今に合うと判じ、macro を 12 へ変えても 11 | A の代役(今の木の包みで局所の表を覚えない)で repro-a-before.log、直す前の実物の包み(5454330f5 の作業木・印 6)で repro-a-real-before.log — どちらも .pyc の道 11・11、置き場の道 11 | 成立。RECORD_TAG を 7 へ(records.py の注釈に 7 の訳)。repro-a-real-after.log で .pyc の道 12・12、置き場の道 12 |
| 直し方の候補(a)〜(d)のうち印を上げる(a)は全部の module の作り直しを招く | — | (a)を採る。(b)は形を変えるので結局印を上げる。(c)は Hy の知識を照らしへ写す。(d)は配備の道具に包みの知識を要する |
| 「同じ印の記録は同じ範囲を覆う記録者が作った」という共同の不変条件が責務表に無い | — | records-names の持つ知識に「RECORD_TAG = 記録が覆う範囲の世代」を書き足した(この文書の責務表)。強制は静的に無い — 限界へ |

### 盲検 B — 包みを展開の with の間だけ入れる形が、提示した検を全部通る

| B の主張 | 設計者の再現 | 結論と直し |
| --- | --- | --- |
| 差分(proposal.diff)を当てても test_bytecode_guard.py 37 本・local の 3 本が緑 | b-check-with-diff.log: 足す前の local の 3 本は緑のまま | 成立(提示した検が拒まなかった) |
| 入れ子の compile で、外の記録に内の module の局所の macro が混ざる | demo_b.py nested(盲検 B の demo の写し): 差分ありで外の記録に `(<pkg>.m, answer)`・使わない macro の変更で古いと判じる。差分なしは `[(<pkg>.b, wrap)]` で今に合う | 成立。検 test_a_local_macro_of_a_module_compiled_inside_another_expansion_stays_out_of_its_record を足した |
| 別 thread の展開が先に終わると、後の展開の局所の macro が漏れ、古い展開を今に合うと判じる | demo_b.py threads: 差分ありで local の記録 `[]`・answer を替えても今に合う(True)。差分なしは `[(<pkg>.m, answer)]`・古い(False) | 成立。検 test_a_local_require_expanded_while_another_expansion_ends_on_another_thread_is_recorded を足した。足した 2 本は差分ありで赤・差分なしで緑 |

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 局所の状態を開く口と鍵が uv.lock の版の Hy で効く | 振る舞いの契約テスト(使い捨ての環境) | test_bytecode_guard.py の test_a_macro_required_inside_a_function_recompiles_the_user_on_pypi_hy[none-11/before-hy-12/after-hy-12] | uv.lock の変更(契約の組)・file を触った時 | 見本は入れ子の関数 1 つ |
| 使った局所の macro だけを記録し、局所の全部の require も古いと判じる | 記録の契約テスト | test_macro_use_record.py の local の 3 本 | file を触った時・日次 | venv の Hy |
| 局所の表は compile している module の記録にだけ入る(入れ子の compile・thread の並び) | 記録の契約テスト(盲検 B の形で赤) | test_macro_use_record.py の 2 本(入れ子・thread) | 同上 | thread の検は順を Event で 1 通りに決めた形だけ。同じ module を 2 つの thread が同時に展開する時の局所の表の足し(tuple の読み替え書き)は守らない(前提: 同じ module の import は import の lock で直列) |
| 記録の覆う範囲を変えたら印を上げる | 注釈(records.py の RECORD_TAG の世代の表)と設計記録 | records.py | 書き手の判断 | 静的にも検でも強制しない — 覆う範囲を変える便が印を上げ忘れても、どの検も赤にならない(盲検 A の形)。補う物: この文書と RECORD_TAG の注釈 |
| 名と鍵の定義元は records に 1 か所 | macro_use は records の定数を読む | records.LOCAL_MACRO_TABLE | — | 静的な禁止は置いていない |

## 測った事(evidence/)

| log | 中身 | 結果 |
| --- | --- | --- |
| repro-a-before.log | 盲検 A の代役で再現(基準版) | .pyc の道 11・11、置き場の道 11 |
| repro-a-real-before.log | 直す前の実物の包み(5454330f5)→ 基準版の包み | .pyc の道 11・11、置き場の道 11 |
| repro-a-real-after.log | 直す前の実物の包み → 印 7 の包み | .pyc の道 12・12、置き場の道 12(entry を足した) |
| b-check-without-diff.log | 印 7 の版で local の 3 本と盲検 B の検 2 本・demo_b nested / threads | 5 passed・混ざらず漏れない |
| b-check-with-diff.log | 盲検 B の差分を当てた版で同じ | 2 failed(足した 2 本)・3 passed・混ざる / 漏れる |
| s1-variant-base.log / s1-variant-localstate.log | 替えない Hy / 局所の状態を口を通さずに開く代役の Hy で、関数の中の require の検 | 3 passed / 2 failed |
| final-guard-tag7.log | 印 7 の版で test_bytecode_guard.py と test_code_store_entries.py | 48 passed |
| final-gensym-tag7.log | 印 7 の版で test_gensym_canonical.py(前の印の記録を扱う検を含む) | 8 passed |
| before-fix.log ほか(README.md の表) | 着地の前の直す前と直した後の測り | README.md |

測っていない事: test_macro_use_record.py の全部(約 6 分 — 足した 2 本と local の 3 本だけ走らせた)・repo 全体の検と型検査(全体の回)。

## 残る限界

- 記録の覆う範囲を変えたら印を上げる、は書き手の判断で、検は無い(上の強制方法の表)。
- 同じ module を 2 つの thread が同時に展開する時の局所の表の足しは守らない。
- 印 7 への上げで、配った後に全部の Hy の module が 1 度 compile し直される(前の世代の entry は置き場の上限か 7 日の掃除で消える)。
