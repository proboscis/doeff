# handle・defhandler の展開が読む名を書き手の名前空間から分ける — 設計検証の報告

依頼 lt-2SJXY98Q643WGJK2ASWJ3N8PWH(card ki-16180480129e)。対象は doeff の `packages/doeff-hy`。
基準版(直す前)= doeff main `dc3385b9a4bb105937d9dad6671545f7b34c80f2`。検証した版 = 直した版 `1410ce508e3baec632ea994184c884ba28ca0ee9`(L2217)と、設計検証の結果で直した main `fb4dd0f3b50417c54948a69ea0a0dd215e3f2829`(L2234)。
**完了の範囲 = 実装完了まで**(直しは main に着地済み。着地後に盲検で見つけた退行 1 件も直して着地済み)。設計段階の予定は「§8 残る限界」に分けて書き、`checks` には実行済みの物だけを `passed` で載せた。

## 0. 全体の構成

```
書き手の module / class / 関数                      doeff-hy の展開(この設計検証の対象)
┌──────────────────────────────┐              ┌───────────────────────────────────────────────────┐
│ Transfer / Put / Pass … 書き手が   │  名を分け合わない   │ handle・defhandler の展開が読む doeff の名は    │
│ 自分の effect に付けた名           │ ◄──────────────► │ すべて _doeff_<名> の別名だけ(R1・R2・R3)          │
└──────────────────────────────┘              └───────────────────────────────────────────────────┘
                                                                       ▲ 綴りは 1 か所の表から引く(R4)
              ┌──────────────────────────────────────────────────────┴───────────────┐
              │ expansion_names.py  alias_symbol / import_form / import_source / home   │
              └──────────────────────────────────────────────────────▲───────────────┘
        ┌───────────────┬───────────────────────┬──────────────────────┴─┬───────────────────────┐
  clause_ops.hy    handle.hy               binding_forms.py `:=`      static_view.py          tests/ (name-guards)
  CLAUSE-OPS の    WithHandler・Pass・      macros.hy `set!`           module の頭の補助 import  挙動 84 + 構造 + 位置
  rewrite の node  Get・Put・Some(session)  Put・Some                  Some・Put                 + 一般の形
```

## 1. 要件(固定)

| 記号 | 要件 |
| --- | --- |
| R1 | 書き手が `Resume`・`Transfer`・`Pass`・`WithHandler`・`Get`・`Put`・`Some` のどれかを自分の effect にした module で、module の段・defk の本体・素の defn の本体・class の本体の handle・defhandler の節が、その effect を受けて答える(節の操作 resume・transfer・reperform・:when の外れ・session var の := を通す) |
| R2 | module の段の handle・defhandler の後も、書き手の module の名は書き手の物のまま。展開が module の globals に足す名は、書き手が書いた名か展開の物(`_doeff` の頭・Hy の gensym の `_hy` の頭・`hy`・`doeff_hy`)だけ(盲検前の文は「書き手が書いていない名を置かない」だったが、検は素の 7 つの名だけを見ていた — 盲検 B の指摘で一般の形に揃えた) |
| R3 | 実行時の展開と型検査のための展開は、これらの名を素の名で読みも束ねもしない(読むのは `_doeff_` の頭の別名だけ) |
| R4 | 展開が読む名の綴りは 1 か所の表から引き、節の操作の表 CLAUSE-OPS(card ki-b4c015d8ba1f)の行の追加・退役に追従する |

## 2. 責務(module)

| id | 実体 | 責務 | 所有する知識 | 隠す知識 |
| --- | --- | --- | --- | --- |
| expansion-names | `expansion_names.py`(新) | 展開が読む doeff の名の別名の綴りと import の唯一の定義元 | 別名の頭・名ごとの置き場 | Hy の import の model の組み方 |
| clause-ops-table | `clause_ops.hy` | 節の操作の表 CLAUSE-OPS と各行の書き換え | 操作の名・終わり方・必要な node の名 | handle.hy に操作の名を写さない |
| handler-expansion | `handle.hy` | handle・defhandler の展開(dispatcher・prelude の import・`WithHandler` の被せ・session の lazy 初期化) | 展開の形・置き場ごとの import の置き方 | 節の操作の node の名の一覧 |
| session-write | `binding_forms.py` の `_assign`・`macros.hy` の `set!` | session var の書き換え | 書き換えの形 | 保存先の key の作り方 |
| static-head-imports | `static_view.py` | 型検査のための展開の module の頭の補助 import | 補助の名の一覧 | — |
| session-protocol | `session.py`・ADR-DOE-HY-006・README・外の handler の検 | session の値を Get / Put で外の handler へ開く公開の契約 | key の形・Get / Put の意味 | — |
| linter-table | `packages/doeff-linter/data/clause_ops.json`・`scripts/export_clause_ops.py` | CLAUSE-OPS から書き出した、linter が読む操作の名・終わり方 | 生成物 | — |
| bytecode-guard | `doeff_hy_bytecode_guard` | macro の依存の記録と照らし(compile 済みの module の cache の有効・無効) | digest の作り方 | — |
| name-guards | `tests/test_handle_writer_names.py`・`tests/test_expansion_names.py` | 展開の綴りの衝突を機械で止める検 | 監視する名・標本の展開 | — |
| writer-namespace | 利用者の module(doeff-hy の外) | 書き手の名の束縛 | 書き手の名 | — |

## 3. 変更シナリオ — 盲検の前に固定した主張と、実測

6 軸のうち effects・storage・distribution を適用し、hardware・concurrency・simulation は不適用(`report.json` の X-…)。**主張(`claim`)と予想の範囲(`expected_scope`)は盲検の前に固定した版(`evidence/design-pre-blind.md`・sha256 86689ca8…)から変えていない**。実測は `actual_scope` に分けて書いた。

| 記号 | 軸 | 変更 | 事前の主張・予想の範囲 | 実測 | 差の原因 |
| --- | --- | --- | --- | --- | --- |
| S1 | effects | 節の操作 `(throw 例外)` を足す(`ResumeThrow`・`TransferThrow`) | clause-ops-table だけ。足した node は別名で import され、書き手の `TransferThrow` を隠さない | 盲検 B の差分(clause_ops.hy 26 行)を overlay で当てると、関連の検は 243 件が緑・赤 1 件(`tests/test_linter_clause_ops.py` — 生成物 `clause_ops.json` の書き直しが要る)。node は別名で読まれる。**同時に、書き手の関数 `throw` の呼びが節の本体で黙って操作に書き換わる**(基準では `pay(-5)` が `ValueError`、overlay では検めごと消える / log に `-5` が残り one-shot violation) | 生成物 `clause_ops.json` の書き直しは、別の card(ki-d3ef2a50cc04)が足した派生物の配線 — 契約どおり。`throw` は操作の名が節の本体の頭の名として予約される(CLAUSE-OPS の前からの性質)ことの現れで、node の名の衝突(別名で防ぐ)とは別の根。**契約の拡張(新しい予約語)であり、隠すはずの知識の漏洩ではない**。機械の検では止められない(足す前の呼びと足した後の呼びを source から見分けられない)ので、行を足す前の手順を clause_ops.hy の header に書いた(L2234)。 |
| S2 | storage | session の保存先の効果を `Get`・`Put`(+ `Some`)から別の効果の組へ替える | expansion-names・handler-expansion の lazy 初期化・session-write・static-head-imports の名の一覧(+ 検の監視の名)。clause-ops-table・置き場ごとの組み立て・writer-namespace は変えない。key の作り方は変えない | 名を読む綴りは 4 module・8 行(`evidence/s2-census.txt`)で、予想どおり。**ただし**、`Get`・`Put` と key は外の handler への公開の契約(`session.py`・README・ADR-DOE-HY-006 R8・外の handler が Get に答えて値を差し替え Put を観測する検 `test_val_var_lazy.py`)で、保存先を替えるとこの契約の側も変わる。変更そのものは実行していない(census と変異で測った) | 盲検 A の補足。S2 は保存先の**契約の変更**で、漏洩ではない。事前の予想の範囲に session-protocol が入っていなかった。session-protocol を含めて宣言し直した(`changed_modules`) |
| S3 | distribution | 版の違う process が共有の code store を使う | 古い展開(素の名の import)で作った cache は新しい macro の process で使われない。表の値を後で書き換えても冷える。bytecode guard は変えない | `handle`・`defhandler` の macro の digest(別 process・overlay)— 無変更 d740a532…・c8f7d51c…、無関係な docstring の書き換えでも同じ。`_HOMES` の値の書き換えで 1e73921f…・b22db0d3…、`PREFIX` の書き換えで a9fc7adf…・c49df7b4… と変わる(`evidence/s3-macro-digest.txt`) | 予想どおり。digest は素の値を値で、dict・list を束縛する文の source で覆う |

## 4. 強制方法と実装の証拠(main に着地済み)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 検出範囲・限界 |
| --- | --- | --- | --- | --- |
| 展開が書き手の名を隠さない(R1・R2) | 書き手が 7 つの名を自分の effect にした module を、4 つの置き場 × (handle の 2 形・defhandler の session var)で受けて答える挙動の検 + module の段の後の名が書き手の物のままの検 | `tests/test_handle_writer_names.py::test_clause_answers_the_writers_effect_named_like_an_expansion_name`(84 件)・`::test_module_level_handle_and_defhandler_*` | 通常の pytest(`make test-changed`・日次) | 標本の source の置き場 4 × 形 3 × 名 7 |
| 展開が素の名を読みも束ねもしない(R3) | **一般の形**: 展開の木が読む名は書き手の物か展開の物、束ねる名は展開の物だけ(名の一覧に依らない)+ 9 つの名を名指す回帰の検 | `::test_expansion_reads_and_binds_only_written_or_expansion_owned_names`・`::test_expansion_neither_reads_nor_binds_a_bare_expansion_name`・`::test_names_the_module_level_expansion_adds_*` | 同上 | 標本 8 本 × 実行時・型検査のための 2 つの展開。標本に無い展開の経路は外 |
| 綴りは 1 か所の表から(R4) | doeff_hy の `.hy` で `import`/`require` の外に素の Symbol として書かれた 9 つの名があれば赤 + `.py` の `Symbol("名")` | `tests/test_expansion_names.py::test_no_source_spells_an_expansion_name_bare` | 同上 | 9 つの名の一覧は手で持つ(表の外の node 名は上の一般の形が拾う) |
| 展開が作る model は位置を引き寄せない | `import_form` が返す model は位置を持たない + 型検査のための展開の別名の import が handler の行に置かれる | `tests/test_expansion_names.py::test_import_form_builds_no_positioned_model`・`tests/test_handle_writer_names.py::test_static_expansion_imports_keep_the_handlers_line` | 同上 | 標本 1 本 |
| 表の正しさ | 表の名が置き場から import できる検、`import_form`/`import_source` の形の検 | `tests/test_expansion_names.py` | 同上 | 9 つの名 |
| CLAUSE-OPS への追従(R4) | 足した行の node が別名で import され読まれる検 | `tests/test_handle_clause_ops.py` | 同上 | テストの中で足した行 |
| 型検査のための展開 | pyright strict で、書き手の `Transfer`・`Pass`・`Put` の effect を持つ module が赤 0 | `packages/doeff-hy/tests/test_static_check_defhandler.py::test_writer_effects_named_like_the_expansion_names_are_not_an_error` | 同上(pyright が PATH に在る時) | 検体 1 本・module の段 |
| 依存の方向 | 品質検査の module 契約 `doeff-hy-expansion-names` | `.agents/code-quality.d/doeff-hy-expansion-names.json` | `code-quality --scope changed`(land の門) | 契約に載せた module だけ(handle.hy・clause_ops.hy は未登録) |

実測した実行(`evidence/final-main-tests.txt`): 着地した main fb4dd0f3b で `tests/test_handle_writer_names.py tests/test_expansion_names.py tests/test_handle_clause_ops.py tests/test_handle_scope.py tests/test_linter_clause_ops.py packages/doeff-hy/tests/test_static_check_defhandler.py` が **240 passed**。

## 5. 盲検 A・B の失敗例と、設計者の再現・分類

起こし方・入力・返答の原文・model(要求と観測の別)は `blind/blind-meta.md`。第 1 候補の `gpt-6-astra`(codex)は Pod に資格が無く 401(`blind/codex-attempt.txt`)、第 2 候補の Opus 5.5・`xhigh` の subagent で A・B を新しい文脈で同時に起こした。

| # | 出所 | 失敗例 | 設計者の再現 | 分類・対応 |
| --- | --- | --- | --- | --- |
| A-1 | 盲検 A | 型検査のための展開で、class の本体の handler が `_doeff_Pass`・`_doeff_WithHandler` を解けない(`reportUndefinedVariable`) | 再現した(`evidence/a-static-reproduction.txt`)。**基準版 dc3385b9a でも同じ**(素の `Pass`・`WithHandler` が未定義 — 9 件が 9 件) | **既存の穴で、この変更の退行ではなく、事前の主張の外**(R3 は「別名だけを読む」で、別名が解ける事までは主張していなかった)。直さず記録(§8)。実使用の class の本体の handler は無い。別の根(class の本体の defhandler の型検査は他にも赤が 5 件残る) |
| A-2 | 盲検 A | module の段に handler が 65 本以上あると別名が Unknown(pyright の 64 宣言の限り) | 再現した(N=60 は 0 件・N=70 は 70 件の赤 — `evidence/a-many-handlers-reproduction.txt`)。**基準版でも N=70 は 70 件** | 既存の穴。実際の repo の 1 file の handler 数の最大は 28(agora-controllers)で、64 には遠い。直さず記録(§8) |
| A-3 | 盲検 A(補足 A) | S2 は漏洩でなく契約の変更 | 読んで確かめた(`session.py`・README・ADR-DOE-HY-006 R8・`test_val_var_lazy.py` の外の handler の検が公開の契約。現状 36 件が緑 — `evidence/session-behavior.txt`) | **成立**。S2 の宣言に session-protocol を足した(§3) |
| A-4 | 盲検 A(補足 B) | 検の監視する名の一覧は手で写したもの。S1 で足す node は監視されない | 変異(表の行が新しい node を素の Symbol で読む)で再現した: 9 つの名の一覧では 0 件、一般の形が `ResumeBare` を拾う(`evidence/general-guard-mutant.txt`) | **成立**。名の一覧に依らない一般の形の検を足した(L2234)。一覧の検は名指しの回帰として残した |
| A-5 | 設計者(A の probe を基準版と 1410ce508 で比べて発見) | 型検査のための展開で、別名の import(と同じ式の中の `doeff_hy.declarations`)の位置が handler の行でなく 1:1 になる | 再現した(`evidence/a-static-reproduction.txt` — 基準版 `7:6`・1410ce508 `1:1`)。原因は `import_form` が module の名を `hy.read` で作り、reader が 1 行目 1 列目の位置を付けて、位置を付ける macro が囲む式の範囲を 1 行目へ引くこと | **私の変更の退行**(R3 の範囲)。失敗する検を先に足し(5 件赤 — `evidence/position-regression-red.txt`)、`import_form` が reader を通さず位置の無い model を組むよう直した(L2234)。直した後は A の probe の位置が基準版と同じ `7:6`(`evidence/a-static-reproduction-after-position-fix.txt`) |
| B-1 | 盲検 B | S1 のとおり `throw` の行を足すと、書き手の関数 `throw` の呼びが節の本体で黙って操作に書き換わる。提示の検は全部通る | 再現した(`evidence/b-throw-reproduction.txt`・`evidence/s1-throw-overlay-tests.txt`) | **契約の拡張(新しい予約語)であって、node の名の漏洩ではない**。機械の検では止められない(§3 S1)。clause_ops.hy の header に「行を足す前に 4 repo の節の本体でその名の呼びを引く」手順を書いた(L2234)。限界として §8 に残す |
| B-2 | 盲検 B | S1 の予想の範囲に生成物 `clause_ops.json` が入っていない | 再現した(overlay で `test_linter_clause_ops.py` が赤) | 配線(別の card が足した派生物)。`actual_scope` に書いた。検が書き直しを求める形で既に強制されている |
| B-3 | 盲検 B | R2 の文と検の差: module の段の後に `_doeff_*` 12 の名・`_hy_anon_*`・`doeff_hy` が増える | 再現した(`evidence/b-preexisting-field-collision.txt`) | **成立**(文が検より広かった)。R2 の文を正しくし、module の globals に足す名を一般の形で見る検を足した(L2234)。`doeff_hy`・`hy` は名指しで許した既存の物 |
| B-4 | 盲検 B | 節の欄の名が `k` なら TypeError、`effect` で `:when` が外れると中身の effect が外の handler へ渡る(黙った誤配) | 再現した(同上) | **この変更の外の既存の衝突**(展開の局所名 `effect`・`k` と書き手の欄の名)。依頼元へ報告済みで、配り役が card にする |

## 6. 修正履歴

| commit(doeff main) | 内容 | 着地 |
| --- | --- | --- |
| d92097bab | 検: 書き手の名の effect を節が受けて答える 84 件(直す前は赤) | L2217 |
| 4f7dd1d49 | 直し: 展開が読む名を `_doeff_` の別名にし、表 `expansion_names.py` から引く | L2217 |
| 1410ce508 | 品質検査の module 契約を足す | L2217 |
| d179ad049 | 検: 位置(5 件赤)と名の一覧に依らない一般の形(盲検 A・B を受けて) | L2234 |
| fb4dd0f3b | 直し: `import_form` が reader を通さない(位置の退行)・clause_ops.hy に行を足す前の手順 | L2234 |

## 7. 事前の予測と実測の差

| 項目 | 予測 | 実測 | 説明 |
| --- | --- | --- | --- |
| S1 の波及 | clause_ops.hy(+ 検) | clause_ops.hy + 生成物 `clause_ops.json` | 派生物の配線。node の名の漏洩は無い |
| S1 の意味 | 書き手の名は隠れない | 書き手の `TransferThrow` は隠れないが、書き手の `throw` は操作になる | 操作の名の予約は別の性質(契約の拡張) |
| S2 の波及 | 4 module の綴りと検の名 | 4 module・8 行に加えて公開の契約(session-protocol) | 契約の変更 |
| S3 | 表の値を書き換えれば冷える | 冷える(digest が変わる)・無関係な行では変わらない | 予想どおり |
| 実装の退行 | 無い | 型検査の診断の位置が 1:1 になる退行が 1 件(L2217 に在り、L2234 で直した) | 設計検証前に私の検が位置を見ていなかった。盲検 A の probe を基準版と比べて発見 |

## 8. 残る限界(未解決の条件ではなく、記録する限界)

- **型検査のための展開の別名の束ね方**(盲検 A-1・A-2): 別名は handler ごとに置き場で import する。class の本体では解けず、handler が 65 本以上の module では Unknown になる。基準版からの既存の穴で、実使用は無い(handler の最大は 28)。直すなら、他の会話が足した投影の後処理 `static_check.with_module_level_imports` に別名の import(表から判じる)を集めさせる形が見える — 本 card の外。
- **操作の名の予約**(B-1): 行を足す瞬間に書き手の同名の関数の呼びが操作に書き換わる。機械の検では止められない。足す前の手順(4 repo の節の本体を引く)で防ぐ。
- **名の一覧の検**: `tests/test_expansion_names.py` の 9 つの名は手で持つ。表の外の名は一般の形の検が拾うが、一般の形は標本の展開だけを見る(新しい macro の経路・文字列から `hy.read` する綴りは外)。
- **展開の局所名**(B-4): 節の関数の引数 `effect`・`k` と書き手の欄の名の衝突は、この設計の外。
- **品質検査**: handle.hy・clause_ops.hy は module 契約に未登録。`code-quality --scope changed` は macros.hy の既存の指摘 20 件で failed のまま(基底と同数 20 = 20)。
- **model の観測**: 盲検の subagent の model・effort は要求した値だけで、観測していない(§5 の冒頭)。
- **測っていない範囲**: repo 全体の検と型検査の全 module(日次の回)。agora-controllers は doeff の pin を上げるまで反映されない。
