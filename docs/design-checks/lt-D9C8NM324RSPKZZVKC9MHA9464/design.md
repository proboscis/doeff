# 設計検証 lt-D9C8NM324RSPKZZVKC9MHA9464 — 結論・再現・修正(card ki-799eb0e54c56)

## この文書の目的

読み終えた人が次を答えられる状態にする。

- 環境変数で決まる値を macro が読む時、Hy の .pyc の照らし(doeff_hy_bytecode_guard)はどう古いと判じるか。どの責務がそれを持つか。
- 盲検 A・B が示した失敗例のうち、どれが成り立ち、どう直したか。
- 将来の変更(S1〜S5)が、事前の予想どおり狭い範囲に収まるか。収まらない所はどこで、なぜか。
- 何が検で縛られ、何が縛られていないか。

完了の範囲: **実装完了まで**。盲検の前の主張は [design-before-blind.md](design-before-blind.md) に保存した(sha256
266d5711e8664801de54b0b2d1f149fd77760e5c9d7371d8ddeb65475713b949・2026-10-11T18:22Z・盲検を起こす前に commit 7e2aea54a で push)。
この本文はその主張を書き換えず、盲検の後に分かった事・直した事を足す。

**順の申告**: この依頼(class investigate)では、直しを先に実装して着地し(L2095・main 38629119)、その後で設計検証の手順を始めた。
盲検の前の主張は実装の後に書いた物で、実装の前に固めた主張ではない。盲検 A の失敗例を受けて、さらに直しを足した(下の「盲検の後の
直し」)。

## 版

| 版 | 中身 |
| --- | --- |
| 4ca9fcd502928a0ffcde455f390f66bd629144b9 | 基準版(直す前の main) |
| eb83e2f52 → d793b6fde(branch wt/hy-env-dict-stale) | 1 回目: 失敗ケースだけの commit(赤 9) → 直し(照らせない印) |
| 38629119b5729e7ec310b32c579c740f59ee6fb5 | 1 回目を main へ並べ直して着地した版(L2095)— **この報告の base_revision** |
| 7e2aea54a(branch wt/hy-env-design-check・main 61f060009 の上) | 盲検の前の主張と渡す材料 |
| b9f436404 → 5c16f9fc3 | 盲検の後: 失敗ケース(赤 3・守り 2) → 直し(Hy の import を解く・辿れない値の逃げ道の印) |
| 671aa66d9 → c7ff7ed7d | 守りの検(5c16f9fc3 で赤 1) → 直し(Hy の import の form は読む名を持たない) — **実装の最後の版** |

## 要件(固定・盲検の前と同じ)

1. 不変条件 B1(packages/doeff-hy/architecture.hy): 記録の digest が依った物(macro を定義した file の中身・macro が辿る値の module の
   file の中身・環境変数など file の外で決まる値)のどれかが記録を作った時と違えば、照らしは古いと判じて展開し直させる。
2. 依頼の受入(card ki-799eb0e54c56): 付録 3 の形を写した検が直す前に赤・直した後に緑。素の文字列の形の既存の検と、照らしの回数
   (照らし・歩み・stat)が直す前と同じ事を card に書く。
3. 今の本物の macro(doeff_hy などの閉包)の使い手を、照らせない形にしない。

## 設計(最後の版 c7ff7ed7d)

| 責務 | 持つ判断 | 公開の口 | 不変条件 |
| --- | --- | --- | --- |
| W 歩み(macro_use の `_Walker`・`_remembered`・検出の関数) | 何を digest に入れるか・どの読みを照らせない読みと数えるか | `ClosureDigest(digest, modules)` — digest は 16 進・None(import の途中)・印 `records.ENVIRONMENT_READ` | 環境変数の読みに当たった歩みは印になり、覚えない |
| C 照らし(macro_use の `record_is_current`・`_row_check`) | 合う / 古い | `record_is_current` | 記録か今の digest のどちらかが印なら古い |
| R 記録(records) | 記録の形・印の定義点 | `MacroRecord`・`ENVIRONMENT_READ` | 印は 16 進とも None とも一致しない。RECORD_TAG は変えない |
| B 束縛の読み(bound_source) | source の文の読み(純な読み) | `binding_of`・`source_names` | 生きた値を覚えない。Hy の import は Python の import と同じ形に解く |
| L 読み込みの口(loader_hooks) | compile と .pyc の読み | `record_is_current_here` など | 照らしが偽なら compile し直す。印を見分けない |

環境変数の読みと数える物(W):

- code か束縛の文の名の並びに `environ`・`environb`・`getenv`・`getenvb` が在り、並びの名のどれかが大域で os(posix・nt)の module を指す。
- 関数の中の import の先が os で、名の並びにそれらの名が在る。
- 歩みが値として `os.environ`・`os.environb`・`os.getenv`・`os.getenvb`・posix(nt)の `environ` に当たる。
- **盲検の後に足した物**: 辿れない値を持ち主の module の file で覆う時(`_Walker._fold_opaque` — 既定値・閉包の cell・束縛を辿れない
  大域・名で辿れない読みの先・書き換えの関数でない物・import の先が引けない大域)、持ち主の source に os(posix・nt)の名と
  環境変数を読む属性の名の両方が現れる(B の `source_names` が読む)。

## 盲検 A・B

入力・未加工の返答・起動の記録は [blind/](blind/)(meta = [blind/blind-meta.md](blind/blind-meta.md))。第 1 候補(codex・gpt-6-astra)は
この Pod の資格が無く 401 で落ち、第 2 候補(Agent tool の新しい文脈の subagent・opus・effort xhigh)で起こした。実 model と effort の効きは
観測していない。

## 失敗例の再現と修正

### A の主の例(S1・要件 2): Hy の import で名を束縛した表

- **返答の主張**: `(import pkg.tables [ROWS])` で束縛した表 ROWS(tables.py が環境変数から組む)を読む macro の使い手は、環境変数を
  替えても古い展開を使う。B が Hy の import を unresolved とし、W は macro の module の file で覆うだけなので。
- **再現**: 検 `test_a_macro_reading_a_value_decided_by_the_environment_is_stale_when_it_changes[hy-import]` を足し、b9f436404(main
  61f060009 = 38629119 を含む)で赤([evidence/revision/red-at-b9f436404.txt](evidence/revision/red-at-b9f436404.txt))。**成立**。
- **原因(共同の不変条件)**: 「B が解けない束縛は W が file で覆えば安全」という W と B の共同の不変条件が、file の外の依存(環境変数)の
  下で成り立たない。1 回目の直しは検出の規則だけを足し、この逃げ道を見ていなかった。
- **修正**(5c16f9fc3): B が Hy の `(import …)` を Python の import と同じ `ImportedName` に解く(`(import a.b)` は a・`(import a.b :as n)` は
  a.b・`(import a.b [x :as y])` は a.b の x・相対の `(import ..h [x])` は段数 2)。星の import と読めない形は前と同じく unresolved。W は
  `_imported_binding` で import の先の module の束縛を辿り、tables.py の文の環境変数の読みに当たる。
- **検**: 直した版で緑([evidence/revision/env-tests-at-c7ff7ed7d.txt](evidence/revision/env-tests-at-c7ff7ed7d.txt))。変異「Hy の import を
  解かない」で狙った検だけが赤([evidence/revision/mut/mut-hy-import-left-unresolved.txt](evidence/revision/mut/mut-hy-import-left-unresolved.txt))。

### A の同じ種類の例(S1・要件 2): 既定値と閉包の cell に持つ表

- **再現**: 検 `[default]`・`[cell]` を足し、b9f436404 で赤。**成立**。
- **原因**: 大域の名で引けない辿れない値は、持ち主の module の file で覆うだけ(`_state_value` の既定の枝)。file は読み込みの時に読んだ
  環境変数を写さない。
- **修正**(5c16f9fc3): 辿れない値の逃げ道 8 か所を `_fold_opaque` にし、持ち主の source に os の名と環境変数を読む属性の名の両方が
  現れれば印にする。import の途中の module と C の関数の持ち主の覆いは変えない(本物の閉包で落ちるのは import の途中の 6 回だけ)。
- **検**: 直した版で緑。変異「逃げ道で印にしない」で `[default]`・`[cell]` だけが赤
  ([evidence/revision/mut/mut-opaque-fold-without-mark.txt](evidence/revision/mut/mut-opaque-fold-without-mark.txt))。

### A の S1 の指摘: 検出の規則を W の定数だけで広げられるか

- **返答**: S1(cwd などを足す)を W の定数だけで模すと、Python の import と module の属性の形は印になるが、Hy の import の形は印に
  ならない — 「検出は W の定数だけが持つ」主張は、B の解ける範囲と W の逃げ道に依る。
- **再現**: 盲検 A の probe を 5c16f9fc3・c7ff7ed7d で走らせた。直した版では、S1 を模すと cwd の 3 つの形が全部 cwd2 で展開し直され、模さ
  なければ cwd の形は照らせるまま([evidence/revision/s1-emulation-at-c7ff7ed7d.txt](evidence/revision/s1-emulation-at-c7ff7ed7d.txt))。
  直す前(38629119)は A の実測どおり成り立たなかった(返答の表)。**成立していた**(直した後は主張どおり)。

### 自分で見つけた後退: Hy の import の form の兄弟の名

- 5c16f9fc3 の S1 の模しで、模していない側の `u_cwd_name` まで印になった([evidence/revision/s1-emulation-at-5c16f9fc3.txt](evidence/revision/s1-emulation-at-5c16f9fc3.txt))。
  Hy の import の form の記号を全部 読む名(loads)に入れたので、歩みが同じ form の兄弟の名(環境変数から組んだ ROWS)を辿った。
- **修正**: 守りの検(同じ form で import した環境変数を読まない表 FIXED を読む macro は照らせるまま)を足し、5c16f9fc3 で赤
  ([evidence/revision/sibling-guard-red-at-5c16f9fc3.txt](evidence/revision/sibling-guard-red-at-5c16f9fc3.txt))を確かめてから、c7ff7ed7d で
  Hy の import の form を Python の import の文と同じく読む名を持たない文にした(緑)。

### A の S2 の指摘: 値で照らす変更は `_bound` だけでは済まない

- **再現**: 盲検 A の who_marks を c7ff7ed7d で走らせた([evidence/revision/s2-who-marks-at-c7ff7ed7d.txt](evidence/revision/s2-who-marks-at-c7ff7ed7d.txt))。
  印を立てる所は形ごとに `_bound`・`_state_value`・`function`(名だけの枝・code の読み)に分かれ、`both`(表 BUILT と展開の時の
  `mode-now` の両方を読む)では `mode-now` の 2 度目の訪れが `_first_visit` で畳まれ、印は「bound BUILT の下」の 1 回だけ。**成立**。
- **判断**: S2 は実装していない将来の変更。主張「W の `_bound` だけ」は偽で、値で照らすには W の印を立てる 4 か所と、同じ関数の 2 度目の
  訪れ(展開の時の読み)を分ける仕組みが要る。下の表の S2 の「実際」に書いた。

### A の S3 の指摘: 前の版の読み手

- **再現**: 基準版 4ca9fcd50 の `_row_check` の本文をそのまま走らせた([evidence/revision/s3-old-reader.txt](evidence/revision/s3-old-reader.txt))。
  印の行と完了した歩みの組は STALE、印の行と import の途中の歩み(None)の組は BY_FILES(file の行で比べる — 合うと判じうる)。
- **判断**: 主張「前の版も印の行を古いと判じる」は、import の途中の歩みについて偽。ただし前の版は自分の記録(環境変数を写さない 16 進の
  digest)でも同じく見落とすので、印の記録が前の版を悪くはしない。前の版の code は直せないので、記録の印を上げない決めは変えない
  (上げると全部の Hy の module が 1 度 compile し直される)。

### A のその他の指摘

- `record_fingerprint` は印の記録で環境に依らず同じ指紋になる: 型検査の展開の保存(doeff_hy/static_cache.py)は entry を
  `record_is_current_here` が真の時だけ使う(110 行)ので、印の記録の entry は使われず、同じ指紋の entry を上書きするだけ。
- `hy.I.os.environ` は検出しない — module の表を通す読みとして、見ない読みに名指して足した。

### B の例(S2・S5): 読み込みの口が印の行を除いて照らす

- **返答**: L の `_from_shared_store` が、印の行を除いた記録を照らし、環境変数の全部の指紋を鍵に置き場の entry を使い回す差分
  ([evidence/revision/blind-b-loader_hooks.diff.txt](evidence/revision/blind-b-loader_hooks.diff.txt))は、提示した検を全部通るのに、
  別の作業木で macro を替えた展開を、環境変数が同じだけで今に合うと判じる(C の判断が L へ移り、W の印の意味を L が読み替える)。
- **再現と修正**: 守りの検 2 本を足した(b9f436404)— 別の作業木で macro の本体を替えた後、同じ環境の process が置き場の路で使い手を
  compile し直す検と、印を records.py・macro_use.py の外で読まない事を構文木で縛る検。B の差分を最後の版(5c16f9fc3 の上で当てた)に
  当てると、この 2 本だけが赤([evidence/revision/mut/mut-blind-b-loader-hooks-diff.txt](evidence/revision/mut/mut-blind-b-loader-hooks-diff.txt))。
  外すと緑。**成立していた**(提示した検は通っていた — B の返答の実測)。

## 強制方法(実装後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| B1(環境変数で決まる値が違えば古い) | 失敗ケースの検 — 12 の形を子の process で環境変数を替えて照らす | `test_a_macro_reading_a_value_decided_by_the_environment_is_stale_when_it_changes[形]`(tests/test_macro_use_record.py) | doeff-hy の pytest(着地の前の触った file の検・日次の全体) | 検の形の外の読みは縛らない。どの読みが process の状態かは意味の判断で、静的には表せない |
| 照らせない形の広がりを狭く保つ(要件 3) | 守りの検 — 環境変数を読まない macro(body)・素の値の macro(plain)・同じ import の form の兄弟の表(fixed)が照らせるまま。本物の doeff_hy の macro の使い手が process をまたいで照らせる既存の検(RealTree) | `test_macros_that_do_not_read_the_environment_stay_checkable_beside_ones_that_do` ほか | 同上 | 本物の閉包の数え(印 0・逃げ道 0)は実測で、検ではない |
| 印は記録の側でも今の側でも古い | 行の照らしの検 | `test_a_row_of_an_environment_read_is_stale_even_when_the_walk_now_compares_files` | 同上 | 前の版の読み手は縛れない(S3) |
| 印の判断は W・C が持ち、L は印を見分けない | 置き場の路の振る舞いの検と、印を読む module を構文木で縛る検 | `test_an_environment_reading_user_is_compiled_again_after_its_macro_changed_in_another_tree`・`test_the_environment_mark_is_read_only_by_the_walk_and_the_check` | 同上 | 構文木の検は doeff_hy_bytecode_guard の Python の file だけを読む。印の綴りを literal で写す形は振る舞いの検が拾う |
| 辿れない値の逃げ道は環境変数を見落とさない | 既定値・閉包の cell の形の検・変異 | `[default]`・`[cell]` | 同上 | 判じは source の名の並び(どこで読むかを分けない — 安全側) |
| Hy の import を辿る | Hy の import の形の検・変異 | `[hy-import]` | 同上 | 星の import は辿らない |

正常例と違反の例の実行記録は下の証拠の索引。違反の形は 1 回目の変異 7 つ([evidence/first-fix/mut/](evidence/first-fix/mut/))と、盲検の後の
変異 3 つ([evidence/revision/mut/](evidence/revision/mut/) — B の差分を含む)。どれも狙った検だけが、狙った文で赤になった。

## 実際の波及と事前の予想

| シナリオ | 予想(盲検の前) | 実際 | 差の訳 |
| --- | --- | --- | --- |
| S1 effects(検出の規則を広げる) | W の検出の関数と定数・検 | 38629119 では、W の定数だけでは Hy の import と辿れない値の形を覆えなかった(盲検 A)。直した後(c7ff7ed7d)は、W の定数だけで cwd の 3 つの形が全部 印になる(模しで実測) | W と B の共同の不変条件(解けない束縛と辿れない値は file で覆えば安全)が、file の外の依存の下で偽だった。B の公開の口を広げ(Hy の import を解く・`source_names`)、W の逃げ道に判じを足した — 共同の不変条件の変更で、隠す知識の漏れではない |
| S2 effects(値で照らす) | W の `_bound`・検 | 実装していない。印を立てる所は W の 4 か所に分かれ、展開の時の読みは `_first_visit` で畳まれる(実測)ので、`_bound` だけでは足りない。W の中で閉じる見込み(C・R・L・B は変わらない) | 主張が W の中の分け方を見誤った |
| S3 storage(前の版と読み合う) | 変わらない | 変わらない(R の形と RECORD_TAG を変えていない)。ただし前の版は、印の行と import の途中の歩みの組を file の行で比べる(実測) | 前の版の行の判じの BY_FILES の枝を見落とした。前の版が自分の記録で見落とすのと同じ穴で、悪くはしない |
| S4 concurrency | 変わらない | 変わらない(同じ process で os.environ を書き換えた後も別の thread でも、印の使い手は古く、印の無い使い手は覚えて今に合う — 実測) | — |
| S5 distribution | 変わらない | 変わらない。ただし L で印を読み替える実装(盲検 B)を拒む検が無かった — 振る舞いの検と構文木の検を足した | 「印の判断は W・C が持つ」を縛る検が無かった |
| S6 hardware | 対象外 | — | 照らしは機体の種類を読まない。機体ごとの環境変数は S5 |
| S7 simulation | 対象外 | — | 照らしは時刻・乱数・外の service を読まない |

合格させるために予想を後から広げていない(上の「予想」は design-before-blind.md の表のまま)。

## 費用(要件 2・3)

- 本物の閉包: doeff の Hy の module 298 個を全部 compile し直し、歩み 1,209 回のうち印は 0 回(d793b6fde と 5c16f9fc3 の両方)。
  `_fold_opaque` に落ちた数 0・file の覆いは import の途中の doeff_hy.outcome_forms の 6 回だけ
  ([evidence/revision/reach-at-5c16f9fc3-a.txt](evidence/revision/reach-at-5c16f9fc3-a.txt)・`-b`)。agora-controllers で macro を定義する file は
  2 つで、どちらも環境変数を読まない(grep)。
- 照らしの回数(test_emulated_screen の読み込み・agora-controllers 0c9579aec): 基準 4ca9fcd50 と d793b6fde、着地した 38629119 と最後の
  c7ff7ed7d で、温まった後は同じ(照らし 802・歩み 17・stat 20・読み込みの覚え 4)。直した版の 1 回目だけ code の読み 3,145〜3,147 回 —
  macro_use.py の中身が変わり、code の読みの置き場の鍵が替わった 1 度きりの読み直し。

## 未確認事項・限界

- 盲検の subagent の実 model と effort の効きは観測していない([blind/blind-meta.md](blind/blind-meta.md))。
- 見ない読み(契約の外 — docstring と B1 に書いた): 標準 library の関数が中で読む環境変数(`os.path.expanduser`・`tempfile` など)・
  文字列で引く読み(`getattr(os, "environ")`)・module の表を通す読み(`sys.modules["os"]`・`hy.I.os`)・星の import で束縛した表・
  unchecked-hash の .pyc。
- 環境変数を読む macro の使い手は、同じ環境でも process ごとに展開し直す(値で照らす S2 は実装していない)。
- 前の版の読み手は、印の行と import の途中の歩みの組を file の行で比べる(S3 — 前の版の code は直せない)。
- 型検査(pyright)は日次の段で、盲検の後の直しでは走らせていない(1 回目の直しの 2 file は前から在る 3 件だけ — 足した行に 0)。
- 触った検の file 2 つの全体は、5c16f9fc3 で 82 passed・2 本は負荷 50〜60 の機体で子の process の 25 秒の時間切れ(単独で緑)、
  c7ff7ed7d の source で 84 passed(守りの検の fixed を足す前の検の file)。fixed を足した後は環境変数の節 17 本だけを走らせた。
- 前の設計検証(lt-9TQ4)と同じ 9 file の focused は 1 回目の直し(d793b6fde)で 135 passed。盲検の後の直しでは走らせていない
  (触った file の検だけ)。

## 証拠の索引

各 file の版と命令。命令の cwd は doeff の作業木(1 回目は wt/hy-env-dict-stale、盲検の後は wt/hy-env-design-check)。pytest は
`uv run --frozen pytest -q -p no:cacheprovider …`(cwd packages/doeff-hy)。「の source」は、その版と同じ中身の未 commit の木で走らせた事を言う。

| path | 版 | 命令・中身 |
| --- | --- | --- |
| evidence/first-fix/forms-probe-at-4ca9fcd50.txt | 4ca9fcd50 | `uv run --frozen python probes/env_variants.py.txt <一時>` — 形ごとの再現(8 形が 'plain') |
| evidence/first-fix/red-at-4ca9fcd50.txt | 4ca9fcd50 + eb83e2f52 の検 | `tests/test_macro_use_record.py -k environment` — 9 failed |
| evidence/first-fix/env-tests-at-d793b6fde.txt | d793b6fde の source | 同じ — 12 passed |
| evidence/first-fix/mut/ | d793b6fde | `probes/mutate_env.py.txt` — 変異 7 つ(summary.txt と各 file の頭に変異の名・差分・終了の code) |
| evidence/first-fix/reach-names-at-4ca9fcd50.txt | 4ca9fcd50 | `probes/env_reach.py.txt`(歩みの名と値の検出を包んで数える)— 298 module・当たり 0 |
| evidence/first-fix/reach-marks-at-d793b6fde-a.txt・-b.txt | d793b6fde | `probes/env_reach_fixed.py.txt <一覧> 0 150`・`150 400` — 印 0 |
| evidence/first-fix/walk-count-4ca9fcd50-vs-d793b6fde.txt | 2 版 | `probes/walk_count_ab.sh.txt`(walk_count を版ごとに 3 回) |
| evidence/first-fix/focused-at-d793b6fde.txt | d793b6fde | 9 file の focused — 135 passed |
| evidence/first-fix/code-quality-at-d793b6fde.txt | d793b6fde の source | `code-quality --scope changed --base 4ca9fcd50` — passed |
| evidence/revision/red-at-b9f436404.txt | b9f436404 | `-k environment` — 3 failed・14 passed |
| evidence/revision/env-tests-at-5c16f9fc3.txt | 5c16f9fc3 の source | 同じ — 17 passed |
| evidence/revision/touched-tests-at-5c16f9fc3.txt・touched-tests-rerun-at-5c16f9fc3.txt | 5c16f9fc3 の source | `tests/test_macro_use_record.py tests/test_bound_source_store.py` — 82 passed・2 時間切れ/単独で 2 passed |
| evidence/revision/touched-tests-at-c7ff7ed7d-source.txt | c7ff7ed7d の bound_source・5c16f9fc3 の検 | 同じ 2 file — 84 passed |
| evidence/revision/sibling-guard-red-at-5c16f9fc3.txt | 671aa66d9 の検・5c16f9fc3 の bound_source | `-k stay_checkable` — 1 failed |
| evidence/revision/env-tests-at-c7ff7ed7d.txt | c7ff7ed7d の source | `-k environment` — 17 passed |
| evidence/revision/mut/ | 5c16f9fc3 | `probes/mutate_env2.py.txt` — B の差分・Hy の import を解かない・逃げ道で印にしない |
| evidence/revision/reach-at-5c16f9fc3-a.txt・-b.txt | 5c16f9fc3 | `probes/reach_v2.py.txt` — 印 0・逃げ道の数 |
| evidence/revision/s1-emulation-at-5c16f9fc3.txt・-at-c7ff7ed7d.txt | 各版 | `probes/s1_run.py.txt`(盲検 A の run.py・child.py の置き場の path だけを替えた写し) |
| evidence/revision/s2-who-marks-at-c7ff7ed7d.txt | c7ff7ed7d | 盲検 A の who_marks.py |
| evidence/revision/s3-old-reader.txt | 4ca9fcd50 の `_row_check` | `probes/s3_old_reader.py.txt` |
| evidence/revision/s4-inprocess.txt | 5c16f9fc3 | `probes/s4_inprocess.py.txt` |
| evidence/revision/walk-count-final.txt | 38629119・c7ff7ed7d | walk_count を版ごとに 3 回(1 回目の温めは別に 150 秒) |
| evidence/revision/code-quality-at-5c16f9fc3.txt | 5c16f9fc3 の source | `code-quality --scope changed --base 61f060009` — passed |
| evidence/revision/blind-b-loader_hooks.diff.txt | — | 盲検 B の差分の写し |
| evidence/blind-probes/ | — | 盲検 A・B の probe の写し |
| evidence/checks/ | — | JSON の各確かめが指す記録(参照する file の sha256 と観測) |

evidence/probes/ の Python の script は走らせた中身のまま `.py.txt` の名で置いた(repo の Python の lint の対象に入れない)。
