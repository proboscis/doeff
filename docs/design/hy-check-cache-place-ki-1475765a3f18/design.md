# doeff-hy-check の展開の保存を、置き場の path と入口の名の違いで外さない — 設計と検証の記録

依頼 lt-TBEF3PAMAVQ8B3Z07MDSCA994C・card ki-1475765a3f18(agora-redesign)。著者 c-XTC7RB24JH38PC0PET3XV4F1EJ(claude-opus-5-5)。

## この文書の目的

読んだ人が次の 4 つを判断できる状態にする。

1. 日次の hy-type-trend が保存した展開を毎回外していた訳と、直しが変えた責務の境界。
2. 将来の変更(置き場・入口・後処理・共有)が、どの責務の中で収まるか — 盲検の前の主張と、盲検の後に実測した範囲の差。
3. その主張を、どの検査が機械で守り、どこからが守れていないか。
4. 盲検 A・B が示した穴と、その再現・修正・再検証。

## 完了の範囲

実装完了まで。

| 物 | 状態 | 版 |
|---|---|---|
| D1・D2(置き場の path と入口の名) | doeff の main に着地(L2100) | 基準の版 4ccab04ac の上の 4 commit・先端 15db080f3 |
| 日次に効かせる doeff の版の下限 | agora-controllers の main に着地(L6196) | 5456ec34(doeff-min-commit = 15db080f3) |
| D3・A2・規則の守り(盲検の穴) | この記録と同じ branch(wt/design-check-ki1475)で着地を登録する | 失敗ケース d8c82385b・直し 29ba29c9f・置き場の読みの守りの検はこの記録と同じ commit |
| 日次での秒の受入(card の受入 c5df0dcbc) | この文書の範囲の外(日次の回の観測) | — |

盲検の前の主張は `design-before-blind.md`(sha256 `8e757ccfb68e54b54d7e010e7ee98f75dacd97e67c632cb06ec4c7d49ae01826` — `design-before-blind.sha256`)に固定した。
下の「事前の主張」はその写しで、盲検の後に書き換えていない。

## 固定した要件

- R1: 日次の hy-type-trend(agora-controllers の repo 全体に `python -m doeff_hy.static_check --strict`)の秒を、自分の上限
  3,300 秒と全体の上限 4,800 秒に余裕をもって収める。
- R2: 速さの変更は、同じ負荷の前後の数を出してから。
- R3: 保存は速さのためだけの層。引いた答えは、展開し直した答えと同じでなければならない(古い展開を今に合うと判じない)。

## 現状の障害と直し

| 記号 | 障害 | 直し | commit |
|---|---|---|---|
| F1 | 閉包の歩みが関数の名の表(co_names)の名を大域で引いて値を入れ、`project` の `module.__file__ = …` から static_check の大域 `__file__`(doeff の置き場の絶対 path)を後処理の digest に入れた。日次は回ごとに別の dir に doeff を置く | D1: `__file__`・`__cached__`(`_LOCATION_NAMES`)は値でなく印 `location` を入れる | 05c49ef7f(赤)・6a21fcd47 |
| F2 | 記録は POST_PROCESSING を `__name__` で名指した。`python -m` では親が `__main__`・展開の子が `__mp_main__` の名で読み、親の照らしと子の記録が別の名の project を辿った | D2: `__spec__.name`(import で引ける名)で名指す | 0dd35678c(赤)・15db080f3 |
| F3(盲検 B) | 後処理か macro の補助が `__file__` から隣の file を読んで結果を決めると、歩みはその file の中身を覆わない。D1 の後は別の dir の木どうしでも古い展開を引いた(同じ木の中では D1 の前から) | D3: code の読みに `reads_location`(`__file__`・`__cached__` の大域の読み・属性の読み・文字列の定数の名 — 書くだけの STORE_ATTR は除く)を足し、当たった digest と、置き場を読む束縛の文に当たった digest を照らせない印 `ENVIRONMENT_READ` にする。code の読みの置き場の形を 2 に上げる | d8c82385b(赤)・29ba29c9f |
| F4(盲検 A) | `static_cache._read_entry` が保存の形の版を literal の 3 で照らし、CACHE_VERSION を上げた版は自分の entry を読めない | A2: CACHE_VERSION で照らす | 同上 |
| F5(盲検 A) | 後処理が使う gensym の名の振り直しの規則は `doeff_hy_bytecode_guard`(歩みが名だけ入れる package)に在り、記録の digest が覆わない。規則を替えても古い名の展開を引く | 守り: 規則の source の sha256 を保存の形の版へ結ぶ表 `GENSYM_RULE_VERSIONS`(test_static_cache)。規則を替えて版を上げない変更・同じ版に 2 つの規則を置く変更を検が断る | 同上 |

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 | effect | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|
| walker | 閉包の digest を作る(何を digest に入れるか) | code の読み・大域/属性/文字列の定数の辿りの規則・`_LOCATION_NAMES`・`_TEXT_SKIP`・照らせない読み(環境変数・置き場の読み) | 辿りの順・process の中の覚え・code の読みの置き場 | `macro_digest`・`value_digest` → `ClosureDigest(digest \| None \| ENVIRONMENT_READ, modules)` | module の import・file の sha256・code の読みの置き場 | 覚えは process の中だけ | 同じ中身なら置き場の dir に依らず同じ digest。中身が変われば変わる。歩みが覆えない入力(環境変数・置き場から辿る file)に当たった digest は照らせない印 |
| record | 記録の形と今の環境との照らし | MacroRecord の行・指紋・照らしの判定・gensym の名の振り直しの規則 | digest の作り方 | `macro_recording(module).record(text, also=…)`・`record_is_current_here`・`value_reference`・`gensym_renaming` | 照らしの時に記録が名指す module を import | なし | 記録は path を持たず、module の名で今の環境から引き直す。照らせない印の行はいつも古い |
| post-processing | 1 つの .hy の型検査の展開と、後処理を記録に名指す | 展開の手順・POST_PROCESSING の範囲・名指す module の名(`_POST_PROCESSING_MODULE`) | 後処理の中身 | `project(root, roots, source)` → `Projection \| CompileFailure` | source の読み・Hy の compile | 1 呼びごと | 名指す module はどの入口でも同じ名 |
| store | 場所と記録の指紋の entry で保存して引く | 場所の名・entry の形・CACHE_VERSION | dir の並び・一時 file の名 | `place`・`load` → `CachedProjection \| CacheMiss`・`store` | 保存先の file の読み書き | entry は消さない | 場所の名に絶対 path を入れない。今の CACHE_VERSION で書いた entry で、記録が今に合う物だけを返す |
| expanders | 段ごとに保存を引き、引けない物を子で展開して保存する | 段の進め方・子の数・pool の寿命・保存の書きの 1 か所(親) | pool・子の起こし方 | `project_closure(root, roots, targets, cache_dir, limit)` → `Closure` | 子 process の起動・保存先の読み書き(親だけ) | pool は 1 回の実行の中 | 照らしは親・展開は親か子 — どちらが展開しても同じ記録 |
| daily-stage | 日次の処理ステージ(agora-controllers) | 命令・時間の上限・保存先の設定・doeff の版の下限 | doeff の置き場 | `hy scripts/hy_type_trend.hy --timeout-s N` | 子 process・結果の file | 1 回の日次 | doeff の版は下限ちょうど・保存先は回をまたいで残る |

## 変更シナリオ — 事前の主張と実測

| id | 軸 | 事前の主張(design-before-blind.md) | 予想した範囲 | 実測した範囲 | 差の訳と修正 |
|---|---|---|---|---|---|
| S1 | storage | 同じ中身の doeff なら置き場が変わっても保存を引く。置き場の変更は daily-stage の設定だけで済む | daily-stage | 置き場を替えるのは daily-stage だけ(別の dir の doeff で 593 本すべて引けた — C05)。盲検 B: 後処理が置き場から data を読む形では、D1 の後は別の dir どうしで data が違っても引き、R3 を破った | walker の知識の漏れ(`__file__` を「置き場を名指すだけ」と判じたが、値として読む code では入力への道)。D3 で照らせない印にして再検証(C02) |
| S2 | concurrency | どの入口・子の数でも同じ記録を書き、次の実行の親が引く | daily-stage | daily-stage だけ(C03・C04・C05)。盲検はこの軸の失敗例を返さなかった | 修正なし。根拠 = `__main__` の名の入口で 2 度目に全部引く検と、展開の子 4 の測り |
| S3 | effects | 後処理を変えたら記録が古くなり展開し直す。保存・照らし・並べ方は変えない | post-processing | static_check の中の後処理は post-processing だけで古くなる(C06・C08)。盲検 A: 後処理が使う gensym の振り直しの規則(record が持つ — 歩みが名だけ入れる package)を替えると、記録は古くならず、store の CACHE_VERSION を上げる要が出た。さらに `_read_entry` の literal の版のせいで、上げても自分の entry を読めなかった | 共同の不変条件(展開の結果を変える code は、記録の digest か保存の版で覆う)に持ち主と検が無かった。規則の source を保存の版へ結ぶ守りの表と検を置き(post-processing の検の file)、A2 で版の照らしを定数にした。この変更をする時は record・store・post-processing の 3 つが変わる(意図した共同の不変条件) — C07 |
| S4 | distribution | 中身が同じなら他の機体の entry を引き、違えば引かない。store の既存の書きで足りる | daily-stage | 別の dir の doeff の entry を引く(C05)。盲検 B の実験 2: 置き場ごとに data だけが違う木で、D1 の後は違う方の木が古い答えを引いた | S1 と同じ漏れ。D3 の後は違う木で外れ、保存なしと同じ答え(C09) |
| S5 | hardware | 対象外 — digest と記録に機体に依る入力が無い(CPU の数は子の数だけ — S2) | — | — | — |
| S6 | simulation | 対象外 — 決定的な模擬は doeff-hy-check の保存を使わない | — | — | — |

## 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| walker: 置き場に依らない digest | 契約の検(別の木に同じ中身を写して照らす) | `test_macro_use_record.py::test_a_macro_naming_the_place_of_its_module_is_current_in_another_tree`・`…_changed_macro_naming_the_place_of_its_module_is_stale_in_another_tree` | doeff の pytest(doeff-hy の検 — 日次の doeff の区画) | 置き場から作った素の文字列の大域(`HERE = os.path.dirname(__file__)`)を関数が `HERE` で読む形は、歩みが値(path)を入れるだけで束縛の文を辿らない — 隣の file を替えても digest は変わらず、別の置き場では変わる(`evidence/C-place-read-guard.log` の実測)。doeff_hy の中は下の守りの検で持ち込ませない。doeff_hy の外の macro の module は見ない(L4) |
| doeff_hy の macro と後処理は置き場を読まない | 守りの検(doeff_hy の source の `__file__`・`__cached__` の行を、許した 1 行 = static_check の書きと照らす) | `test_static_cache.py::PLACE_LINES`・`test_doeff_hy_sources_do_not_read_their_place` | 同上 | 行の綴りで判じる(注記の行は除く・docstring の中の綴りも数える — 偽の赤に倒れる)。doeff_hy の外は見ない |
| walker: 歩みが覆えない入力に当たった digest を信じない | 契約の検(置き場から隣の file を読む macro・後処理) | `test_macro_use_record.py::test_a_changed_file_a_macro_reads_beside_its_module_is_stale`・`test_static_cache.py::test_a_changed_file_the_post_processing_reads_beside_its_module_expands_again` | 同上 | `__file__` を通さない file の読み(絶対 path の定数・引数で渡された path)は覆わない — 閉包の digest は code と値を覆い、I/O の中身は覆わない(後処理の effect の欄に file の読みを足さない事は文書の決まりで、静的な規則は無い) |
| store + post-processing: 同じ中身の doeff を別の dir に置いて引く | 契約の検 | `test_static_cache.py::test_the_same_doeff_hy_in_another_place_keeps_the_stored_expansion` | 同上 | probe が使う macro(defk)と POST_PROCESSING の閉包だけを測る |
| post-processing + expanders: 入口の名に依らない記録 | 契約の検(`__main__` の名で 2 度撃つ) | `test_static_check_parallel.py::test_the_entry_run_as_main_reads_the_store_its_expansion_children_wrote` | 同上 | console script の入口は測らない(`__name__` = import の名なので前から同じ) |
| post-processing: 後処理を変えたら展開し直す | 契約の検 | `test_static_cache.py::test_a_changed_type_check_post_processing_expands_again` | 同上 | 名だけ入れる package の code は覆わない — gensym の規則だけを下の守りで覆う |
| record + store: gensym の振り直しの規則を替えたら保存の版を上げる | 規則の source の sha256 → 版の表と検 | `test_static_cache.py::GENSYM_RULE_VERSIONS`・`test_a_changed_gensym_renaming_rule_comes_with_a_new_store_version` | 同上 | 表に入れた関数(gensym_renaming・gensym_renames・_gensym_match・renamed・2 つの正規表現)の外の、規則が呼ぶ物は覆わない。表を書き直す時に版を上げる事は検が強制する(同じ版に 2 行を断る) |
| store: 今の版の entry を読む | 契約の検 | `test_static_cache.py::test_an_entry_of_the_current_store_version_is_read_back` | 同上 | — |
| 型・import の層 | 既存の pyright・semgrep・doeff-linter | — | doeff の commit の hook | 「digest に何を入れるか」は型で表せない(値の意味の契約)— 上の検で補う |

## 盲検 A・B と再現

起動の記録は `blind/blind-meta.md`、入力は `blind/blind-input.md`・`blind/change.patch`、返答は `blind/blind-a-return.md`・`blind/blind-b-return.md`(加工なし)。

### 盲検 A(gensym の名の振り直しの規則を広げる変更)

- 主張: 規則(records.py)を替えても、POST_PROCESSING の digest も記録の指紋も変わらず、保存から古い名の展開を引く。RECORD_TAG を上げても効かず、
  CACHE_VERSION を上げると `_read_entry` の literal の版のせいで自分の entry を読めない。
- 再現(`evidence/repro-a/repro-A.log`・直した版 29ba29c9f の写し 3 つ — 元・規則を替えた・規則を替えて CACHE_VERSION 4):
  - 守りの検: 元の写しで 1 passed、規則を替えた写しで「gensym の名の振り直しの規則(a441bc1a…)を替えたら static_cache.CACHE_VERSION を上げ、
    GENSYM_RULE_VERSIONS に 新しい規則の行を足す」の AssertionError(意図した拒否)。版 4 の写しで、新しい版の行を足した形は通り、版 3 のまま
    行を足した形は「同じ版に 2 つの規則の行がある」で断られる(2 passed)。
  - 駆動(盲検 A の drive2.py・同じ保存先 cache-y を順に): 元の規則で温める(外れ 2)→ 規則を替え版 3 のまま: 引けた 2・保存の文
    `'bound _hy_gensym_v_2'` ≠ 展開し直した文 `_v_1`(守りの検が無ければ起きる古い答え)→ 規則を替え版 4: 外れ 2・同じ → 版 4 の 2 回目: 引けた 2・
    同じ(A2 の後は今の版の entry を読む)。
- 成立: 成立した。修正 = 守りの表と検・A2。
- 範囲の外として残す物: 元の規則の写しでも、保存の文 `_v_2` と展開し直した文 `_v_3` が違った(盲検 A の E1)。文字列に埋まった gensym の名は
  元の規則が振り直さず、番号が同じ process で先に何を展開したかで変わる — 展開そのものの非決定性で、保存の層の責務ではない。doeff-hy の macro は
  既存の検(`tests/test_gensym_canonical.py` の読めない埋まり方を名指して落とす検)が守る。型検査の答えの識別子は匿名の名の番号を畳む
  (`doeff_hy.static_baseline` の comparable_message)ので、日次の比べには効かない。

### 盲検 B(所見の重さの表を doeff_hy の中の JSON に置く変更)

- 主張: 後処理が `Path(__file__).with_name("static_findings.json")` を読むと、提示した 5 本の検は通り、表を替えた後も保存から古い重さを引く
  (同じ木・別の置き場の木の両方)。表を module の直下で読む形では、束縛の文の読みから path が digest に入り、別の置き場の検が赤になる。
- 再現(`evidence/repro-b/repro-B.log`・`repro-B2.log`・直した版 29ba29c9f の写し 2 つに盲検 B の差分を当てた):
  - 実験 1(同じ木): 表 warning(外れ 1・warning・exit 0)→ 表を error に替えた(外れ 1・error・exit 1)= 保存なしの答え(error・exit 1)と同じ。
  - 実験 2(置き場 a は warning・b は error・保存先を共有): b は外れ 1・error・exit 1 = 保存なしと同じ。
  - 副次の形(module の直下で読む): 同じ木で表を替えた回も、別の木の回も外れ・保存なしと同じ答え。
  - 盲検 B が直す前の版で測った数(返答の表): 同じ木で表を替えた回は引けた 1・warning・exit 0、別の置き場の回は引けた 1・warning・exit 0(R3 違反)。
- 成立: 成立した。修正 = D3(置き場を値として読む code と束縛の文の digest を照らせない印にする)。代わりに、置き場を値として読む閉包を使う
  展開は毎回展開し直す(今の後処理と doeff-hy の macro の閉包には無い — C05 の 3 回目以降で 593 本すべて引けた)。

## 同じ負荷の前後の数(R2)

条件: agora-controllers 55a539330 の木・`controllers/screen`(.hy 233 本・依存を含め 593 本)・展開の子 4・static_check を `__main__` の名で撃つ
(日次と同じ)・pyright は診断の無い偽物(展開の秒だけ)・doeff を 2 つの dir(A・B)に置き保存先は共有・Hy の展開の置き場は温めて共有・
bytecode の置き場は回ごと。命令は `evidence/measure/run2.sh.txt`・数え方は `evidence/measure/count_m.py.txt`・全部の行は `evidence/measure/results.txt`。

| 回 | 直す前(doeff 60dfc1059 = 基準の src) | D1・D2 の後(8df18c7ef) | D3 の後(29ba29c9f) |
|---|---|---|---|
| A・空の保存 | 176.0 秒・外れ 593 | 167.0 秒・外れ 593 | 143.3 秒・外れ 593 |
| A・2 回目 | 182.5 秒・外れ 592・引けた 1 | 20.7 秒・引けた 593 | 65.4 秒・外れ 109・引けた 484(注) |
| B(別の dir) | 179.9 秒・外れ 593 | 21.1 秒・引けた 593 | 20.7 秒・引けた 593 |
| A・もう 1 回 | — | 21.9 秒・引けた 593 | 21.2 秒・引けた 593 |

注: D3 の列は、展開の置き場を 1 回だけ温めた直後に測った(前の列は何回も温めた置き場)。2 回目の外れ 109 は、展開の置き場が冷えた状態で書いた
記録の一部が次の回に合わない別の揺れ(D1・D2 の版でも controllers/kanban の 335 本で 222 本 — `results.txt` の k-after の行)で、外れは
展開し直すだけ(R3 は破らない)。3 回目以降は全部引ける。

## 残る限界(要件の外・未解決の必須条件ではない)

- L1 展開そのものの非決定性(文字列に埋まった gensym の名): 上の盲検 A の「範囲の外」。保存の層の外。
- L2 展開の置き場が冷えた回の記録の揺れ: 上の注。doeff-hy の Hy の macro の source が替わった次の 1 回で、一部を余分に展開し直す(答えは正しい)。
  単独の process では再現しない(defeffect の digest が冷えた回 eeeaa6c9…・温まった回 e6cd27b9…)。続きの仕事の候補。
- L3 `__file__` を通さない file の読み: 閉包の digest は code と値を覆い、I/O の中身は覆わない。後処理の effect は source の読みと Hy の compile
  だけと決める(文書の決まり — 静的な規則は無い)。
- L4 doeff_hy の外の macro の module(agora-controllers などの使い手の macro)が、置き場から作った素の文字列の大域を通して隣の file を読む形:
  歩みは覆わない(上の実測)。D1 の前から同じ木の中で覆っていなかった形で、doeff_hy の中は守りの検が持ち込ませない。使い手の macro に
  この形は今のところ見つかっていない(探していない — 全数の検は日次の範囲)。
