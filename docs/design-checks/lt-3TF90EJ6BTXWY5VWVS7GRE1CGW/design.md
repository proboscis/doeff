# 設計検証(最終)— doeff-linter の事実の cache を作業木の間で分ける(card ki-9743c30d49bc・依頼 lt-3TF90EJ6BTXWY5VWVS7GRE1CGW)

## この文書の目的

読み手が次の 4 つを、この文書と `evidence/` だけで確かめられる状態にする。

1. 事実の cache の責務がどう分かれ、どの知識がどこに隠れているか(第 2 節)。
2. 将来の変更で、変わる所が予想の範囲に収まるか。事前の予想・実測・差の理由(第 3 節)。
3. 盲検 A・B が見つけた失敗例が何で、どう直し、直した後に何で拒否されるか(第 4 節)。
4. 責務の境界を、何が機械で守り、何が守れず、何が通常の変更時の経路に繋がっていないか(第 5〜7 節)。

事前の主張は `design-pre-blind.md`(sha256 `4392dca38b0637f7b180c98466b8a43088af35d5c16f5ffc1738b0642466f02c`)に、盲検を起こす前に固定した。失敗例を受けて書き換えていない。この文書の第 3 節はそれを引き、第 8 節で修正を別に書く。

## 0. 完了の範囲

- **実装完了まで**(依頼の範囲 = 直す + 受入の test)。設計の予定ではなく、着地した code と実行記録を対象にする。
- 対象の版: doeff main の `8ff9709b5730a43b265b3d345c46257a91212b39`(L2266)。この版は次の 3 つの着地の積み重ねである。
  - `c225cef6f2d688b3d2133a7d88a89a68984becfe`(L2246・第 1 版・盲検の対象)
  - `9119bb923d82386ce9c66bb83b948621fe7d78c9`(L2253・第 2 版・盲検 A・B の失敗例への直し)
  - `8ff9709b5730a43b265b3d345c46257a91212b39`(L2266・第 3 版・設計者の自己検査で見つけた境界の穴を塞ぐ名の走査の検 1 本。`facts_cache.rs` の tests に +48 行だけ)
- 実行記録の多くは第 2 版(9119bb92)で取った。第 3 版との差は検 1 本の追加だけで、最終の版でも全検を 1 回走らせた(`evidence/T-final-lib-and-integration.log.txt`・lib 660 本と統合の 4 つの file〔16・7・3・1 本〕が緑)。
- 範囲の外: card の受入の条 2(zeus の本番で 1 時間に更新される量が 3.4GB から半分以下になった事)。新しい linter が作業木の venv に届いた後でないと測れない運用の観測で、設計の主張ではない。依頼者は「新しい linter が届いた後の観測の条として配り役が数える」と判断した。

## 1. 固定した要件

| id | 内容 | 出所 |
|---|---|---|
| R1 | 同じ commit を checkout した 2 つの作業木で doeff-linter を順に走らせた時、2 本目が事実の cache の下に新しく書く byte は 1 本目の 10% 未満 | card の条 1(id c5817ce0d) |
| R2 | 答えは変えない。cache の有る無し・温かさ・壊れ・並走に依らず、同じ木から同じ出力(位置の path は今の作業木の file を指す) | 前提(`design-pre-blind.md` 第 1 節) |
| 前提 | linter は 1 回で終わる CLI。同じ機体の多数の process(editor の hook・commit の hook・land の門・日次)が同じ置き場を並走で使う。事実は file の中身・根からの path・linter の印・呼び手の鍵だけで決まる。置き場が消えても答えは変わらない(作り直すだけ) | 同上 |

## 2. 責務(最終の形)

実体は `packages/doeff-linter/src/project/` の次の 3 つの file と、呼び手。親子の依存は一方向(親 → 子。子は親を `use` しない)。

```
呼び手  project/mod.rs(cached_hy_files)・project/signatures.rs(World::build)
  │  per_file_compact_bound(root, kind, files, compute, bind)      ← 事実の意味と、根の絶対 path の bind だけを持つ
  ▼
facts_cache.rs   M-resolve(引く手順)+ M-index(根ごとの表)
  │  object_key / read_object / write_object / LeakGuard           ← 事実を鍵で置く・読む口だけ
  ▼
facts_cache/store.rs   M-store(事実 1 つの置き場: 鍵・配置・符号化・一時名・見張り・更新時刻)
  ▲
  │  list / remove(事実を並べて消す口)・OBJECTS_DIR                 ← 片づけが使う名はこれだけ
facts_cache/sweep.rs   M-sweep(上限・古い順・10 分・消えた根・使った印)
        ▲
        └─ facts_cache.rs が呼ぶ: tend_roots(実行ごとに 1 度)・note_object_bytes(新しく置いた量)
```

| id | 実体 | 所有する知識 | 隠す知識 | 公開(契約) | effects | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|
| M-store | `facts_cache/store.rs` | 鍵の作り方(SHA-256 の頭 16 byte)・事実 1 つの置き場の path・一時 file の名と見分け・符号化(JSON / bincode)・根の絶対 path の見張り(`LeakGuard`)・最後に使われた時刻の運用(置いた時と、24 時間より古い事実を読んだ時に今にする)・事実を並べる口(配置の深さに依らない)・消す口 | file の配置・符号化・一時名・hash の種類・見張りの針 | `pub(super)`: `object_key`・`read_object`・`write_object`・`list`・`remove`・`Format`・`LeakGuard`・`ObjectKey`・`OBJECTS_DIR`。事実の path を作る `object_path` と符号化・一時名は非公開 | `objects/` の下の file の読み書き・古い一時 file の削除 | 事実は置いたら書き換えない。消えるのは片づけだけ | 同じ鍵なら同じ内容・書きかけは見えない(一時 file から rename)・読めない物は無い物・根の絶対 path を持つ値は置かない |
| M-index | `facts_cache.rs`(表の部分) | 根ごとの表(file → 大きさ・更新時刻・事実の鍵)の形・16 塊の分け方・どの塊を書き直すか | 事実の中身 | 内部 | 根の dir の下の塊の読み書き | 根の dir の寿命 | 印(identity)が違う・壊れた塊は無い物 |
| M-resolve | `facts_cache.rs`(引く手順) | 早い判じ(大きさ・更新時刻が同じなら中身を読まず前の鍵で引く)→ 違えば中身の hash → 置き場にあれば読む・無ければ作って置く、の順・読む間に file が変わった時は覚えない・返す前に bind を当てる | 表の形と置き場の配置 | 公開: `per_file`・`per_file_keyed`・`per_file_compact`・`per_file_compact_bound`・`keyed_value`・`mark_used`(再公開) | file の読み(中身の hash)・compute の呼び | 1 回の実行 | 答えは cache 無しの compute と同じ・置く値は bind の前の形(根の絶対 path を持たない) |
| M-sweep | `facts_cache/sweep.rs` | 上限(既定 4 GiB)・使っている根として守る 10 分・番の間隔・消えた根・根の dir を先に、事実は古い順・`objects` の使った印・`mark_used` | 事実と表の中身・事実の配置 | `pub(super)`: `tend_roots`・`note_object_bytes`。公開: `mark_used` | 置き場の走査と削除・記録の file | 実行ごとに 1 度(`tend_roots`)+ 新しく置いた量が 32MiB を越えた時 1 度 | 使っている根・置いてから(か更新してから)10 分の内の事実は消さない・消しても答えは変わらない |
| M-callers | `project/mod.rs`(`cached_hy_files`)・`project/signatures.rs`(`World::build`) | 事実の意味(compute)・どの欄が根の絶対 path を持つか・種類の鍵 | 置き場の形・鍵の作り方 | `compute(rel, path)` と `bind(&mut value, rel, path)` | file の読み | なし | 置く値は中身・`rel`・渡した鍵だけで決まる・根の絶対 path が無い |
| M-indexer | `doeff-indexer` の `hy_index/cache_form.rs` | Hy の索引のどの欄が絶対 path か | — | `CachedHyFile::of`・`set_path`・`into_file` | なし | なし | `set_path` 以外の欄は中身と根からの path だけで決まる |
| M-checks | `facts_cache.rs` の unit 15 本(うち `store.rs` に 4 本)・`tests/facts_cache_roots.rs` 16 本・`tests/facts_cache_shared.rs` 7 本・`.agents/code-quality.d/doeff-linter-1828.json` | 上の責務の不変条件を、箱の外(binary と一時 dir)から観測する検 | — | `cargo test` | 一時 dir で binary を走らせる | 検の 1 走り | 配置を知らない形で数える(第 4 節 A-1) |

既存の組み立て点から無くなった判断: 呼び手(`cached_hy_files`・`World::build`)は、置き場の形・更新時刻の判じ・書き直す塊の判断を持たない(以前から)。新しく呼び手が持つのは、値に根の絶対 path が在る種類の bind だけ。

## 3. 変更シナリオ — 事前の予想・実測・差

6 軸(hardware・storage・effects・distribution・concurrency・simulation)をすべて扱った。S1〜S6 は盲検の前に固定した(`design-pre-blind.md` 第 3 節)。S7・S8 は盲検 A・B の失敗例から起こした追加で、事前の主張は同じ file の責務表(M-store が配置を隠す・M-sweep が寿命を持つ)に当たる。

| id | 軸 | 変える要求 | 事前の予想(変わる / 変わらない) | 実測の波及 | 差と理由 |
|---|---|---|---|---|---|
| S1 | storage | 事実の符号化を替える(先頭に版の 1 byte を付け、無ければ読めない物として作り直す) | M-store だけ / 他は変わらない | 写しとの差分は `store.rs` の `encode_value`・`decode_value` の 2 関数だけ(`S1-p3`)。lib 14・roots 16・shared 7 が全部緑(`S1-p1`・`S1-p2`)。他の file から符号化・配置・一時名の非公開の名を使う試みは 10 通りとも E0603 で拒否(`L-S1`) | 予想どおり |
| S2 | effects | 新しい種類の file ごとの事実を足す。値は根の絶対 path を持つ | 呼び手(compute と bind)と値の型だけ / 置き場・表・引く手順・片づけは変わらない | 既存の 2 種類(Hy の索引・signature の事実)は呼び手と `set_path` だけで分け合えている(shared の 7 本が緑)。呼び手が約束を破る変種は、Hy の索引の「path を空にし忘れる」が shared の 2 本で赤(`L-U1`)、signature の「bind し忘れる」が lib の 2 本で赤(`L-U2b`) | **差あり**: 事前は置き場の側に手を入れない予想だったが、盲検 B の別候補(新しい種類が約束を忘れる)を受けて、置く所の見張り `LeakGuard` を M-store に足した(第 4 節 B-2)。新しい種類の追加で変わる範囲は呼び手のままで、見張りは種類に依らない |
| S3 | concurrency | 同じ置き場へ多数の linter が並走し、片づけが事実を消し、実行が途中で殺される | コードの変更は要らない / 全部 | 答えは全部 cache 無しと同じ: 3 つの種(各 30 round × 根 8 本)で確かめた実行 600 本(完走した実行と、途中死の後の 1 本ずつ)の食い違い 0(`C3-landed`)。ただし**主張の言い方は直した**: 「10 分の内の事実は消さない」は、読んだだけでは更新時刻が動かない事実には成り立たなかった(盲検 A の実験 2・`A3`) | **差あり(言い方と定数)**: 答えの正しさに変更は要らなかった(予想どおり)。M-sweep の守りの言い方を「置いてから(か更新してから)10 分」に絞り、M-store の読んだ時の更新の間隔を 24 時間に整理した(第 4 節 A-2)。着地版でも A3 と同じ観測(`L-A3`)が、絞った主張どおりである |
| S4 | simulation | 置き場の状態(空・温・一部欠け・壊れ・表だけ欠け・事実だけ欠け)と thread の数(1・2・8)を替えて、同じ木を再現する | コードの変更は要らない / 全部 | 8 状態 × 3 thread × 2 本の 48 本、答えの種類は木ごとに 1 つ(2 つの木で 2 つ)・食い違い 0(`C4-landed`) | 予想どおり |
| S5 | hardware | 置き場を書けない・読み専用・dir を作れない・更新時刻を持てない場所へ移す | `DOEFF_LINTER_CACHE_DIR` の値だけ / 全部 | 温かい読み専用・空の読み専用・途中が file・objects だけ読み専用・まだ無い置き場・更新時刻が全部 1970 年・木の更新時刻が全部 1970 年の 9 本(書けなかった事の確かめを含む)で、答えが同じ・失敗しない(`C5-landed`) | 予想どおり。限界は第 7 節(本物の別の FS は試していない) |
| S6 | distribution | 単一機体から cluster へ(置き場を機体間で共有) | **不適用**: 置き場は機体の局所(linter の印は binary の大きさと更新時刻なので機体をまたいで安定しない)。要求に無く、先回りして作らない | — | — |
| S7 | storage | 事実の置き場の配置を変える(1 事実 1 file の fan の段を 1 つ増やす。本命は種類ごとにまとめた file) | M-store の `object_path` だけ(M-store は配置を隠す) | **修正前**: 1 行の変更で提示された検 34 本が全部通ったまま、片づけが事実を 0 byte と数え、30 日前の事実 30 個と 2 時間前の一時 file が残った(`A1`・`A2`)。M-store と M-sweep の両方・片づけの検が変わる必要があった。**修正後**: 同じ 1 行を着地版へ当てて、lib 659・roots 16・shared 7・focused 3・hy_index 1 が緑、30 日前の事実の残り 0・一時 file の残り 0(`L-A1`・`L-A2`)。直していない版との対(`L-A2` の unchanged)も同じ数 | **差あり(修正前)**: 配置の知識(深さ・一時 file の名)が M-sweep とその検へ漏れていた = 隠すはずの知識の漏洩。修正後は予想の範囲に収まる |
| S8 | concurrency / effects | 引く手順が、表を書き直す場で前の事実を消す(editor の保存ごとに増える事実を抑えたい) | 寿命の判断は M-sweep だけ。引く手順に消す口は無い | **修正前**: 提示された検 34 本が全部通ったまま、他の作業木が使う事実 4 個を消し、R1 を破った(別の commit へ移った作業木の後に元の commit の木を作ると 95.9% を書き直す)(`B1`・`B2`)。**修正後**: 同じ差分は E0603(事実の path を作れない・`L-B1`)。非公開を緩めた木に同じ差分を当てても shared の 2 本が赤(`L-B2`)。親が `list`・`remove` を呼ぶ変種は名の走査が赤(`N-S8` の M1) | **差あり(修正前)**: 寿命の判断と削除の effects が M-sweep の外へ移れた = 責務の漏洩。修正後は拒否される |

予想と実測の差は、S2(見張りの追加)・S3(主張の言い方)・S7・S8(修正前の漏洩)の 4 件。どれも盲検 A・B の失敗例か、設計者の変種で見つけた。合格させるために予想の範囲を後から広げていない。

## 4. 盲検 A・B が見つけた事と、その扱い

盲検の起動の記録は `blind-meta.md`、返答は `blind-A-response.md`・`blind-B-response.md`(加工なし)。

| 番号 | 見つけた事(出所) | 設計者の再現 | 成立 | 直し | 直した後の確かめ |
|---|---|---|---|---|---|
| A-1 | 配置の知識(深さ・一時 file の名)が M-sweep の `object_files` とその検へ漏れている。1 行で上限が黙って無効になる(A) | 元の木 + 1 行: 34 本緑、30 日前の事実 30 個が残る(`A1`・`A2`) | 成立 | 配置・一時名・列挙を M-store に集約。列挙は配置の深さに依らない `list` に。片づけは `list` が返した物を `remove` するだけ(事実の path を作れない)。置く側と並べる側が一時 file の名の決まりを共有。検を足した | S7(`L-A1`・`L-A2`)。列挙の深さを固定に戻す変種は unit が赤(`N-S7`) |
| A-2 | 「10 分の内の事実は消さない」が、読んだだけでは更新時刻が動かない事実に成り立たない。更新時刻の運用(読んだ時の付け直し 6 時間)と片づけの保護(10 分)の食い違い(A の実験 2) | 元の binary: 2 時間前に置いた事実 30 個を読んでも古いまま、数秒後に別の根の片づけで全部消えた(`A3`) | 成立(言い方が不正確) | 主張を「置いてから(か更新してから)10 分」に絞って註と設計に書いた。読んだ時の付け直しは 24 時間に整理した。答えは作り直されるので変わらず、書き込みが増えるだけ | `L-A3` は絞った主張どおり。unit `reading_an_old_fact_refreshes_its_last_used_time` |
| B-1 | 引く手順が表を書き直す場で前の事実を消す差分は、提示された検が全部通る。他の作業木が使う事実を消し、R1 を破る(B) | 元の木 + 差分: 34 本緑・再現の検 2 本が赤(`B1`・`B2`) | 成立 | 事実の path を作れるのを store だけにした(非公開)。動作の検(別の commit へ移る木・1 つの木での書き換えが他の木の事実を消さない)を shared に 2 本足した | `L-B1`(E0603)・`L-B2`(緩めても 2 本が赤) |
| B-1 の残り | 上の直しは「事実の path を作る口」を塞いだだけで、事実を**並べて消す口**(`list`・`remove`)は `pub(super)` のため親(引く手順)にも見える。rustc は兄弟の module どうしの見え方を限れない。前の版の commit の題と `facts_cache.rs` の頭の註は「事実を消せるのは片づけだけ」と書いたが、強制していたのは path の作成までだった | 設計者が見つけた(盲検の指摘ではない)。親が `list`+`remove` を呼ぶ変種はコンパイルが通る(`N-S8` の M1・コンパイルの失敗 0 行) | 成立(強制の穴) | 第 3 版(L2266): unit `each_file_uses_only_the_names_of_the_knowledge_it_owns` が、3 つの file の本体(コメントと tests を除く)が store から使う名を表と突き合わせる | `N-S8`: 親が list/remove・片づけが read_object・store が IN_USE_SECONDS の 3 変種はコンパイルが通った上で意図した理由で赤。コメントに名が出るだけの場合は通る |
| B-2 | 新しい種類が「path を空にして置き、読んだ後に bind する」約束を忘れると、作業木の絶対 path を持つ事実が置き場に溜まる。path を探す検は有効にした規則の種類しか通らない(B の別候補・読んだだけ) | 設計者が変種で試した | 成立しうる(検の死角) | 置く所に `LeakGuard`(根の絶対 path を置く前に探し、当たれば置かず作り直す。1 つの実行で 1 度 stderr に知らせる)を足した。答えは作り直した値のままで正しい | unit 3 本。実在の呼び手の変種は `L-U1`(shared 2 本が赤)・`L-U2b`(lib 2 本が赤) |
| 読んだだけの候補 | 扱いは第 6 節 | | | | |

## 5. 強制方法・実行経路・限界

| 守る責務 | 強制方法(選んだ理由) | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 配置・符号化・一時名・事実の path は store だけが持つ | rustc の可視性(非公開)。コンパイラで表せる契約を優先した | `store.rs` の `object_path`・`encode_value`・`decode_value`・`temporary_path`・`is_temporary`・`StoredObject.path` | コンパイル(`cargo build`・`cargo test`・commit hook の code-quality は型検査を撃たない) | `pub(super)` の `list`・`remove`・`read_object` などは親と兄弟に見える |
| 事実を並べて消す口の使い手は sweep だけ・sweep は事実を置かず読まない・store は寿命の決まりを持たない | 使う名の走査の unit。rustc では表せない兄弟の見え方を補う | `facts_cache.rs` の tests の `each_file_uses_only_the_names_of_the_knowledge_it_owns` | `cargo test`(`make test-rust`・`.agents/land-queue.toml` の日次の full 段 rust) | **名だけ**を見る。別名の関数を挟む差分・親が `std::fs` で `objects/` を直に歩く差分は見えない(後者は配置の知識を自分で持つ必要があり、shared の動作の検が補う)。行末の `//` で行を切るので、文字列の中の `//` の後ろは見ない |
| 配置の深さに依らない数え方 | unit(深い配置を置いて数える)+ 配置を替えた写しで提示の検と実験 | `store.rs` の `every_object_written_is_listed_and_a_deeper_layout_is_still_counted`・`a_temporary_made_by_the_writer_is_recognised_by_the_lister` | `cargo test` | 配置を実際に替える差分の検は設計時の実験(`L-A1`・`L-A2`)で、通常の経路には無い |
| 根の絶対 path が置き場に残らない | 置く所の見張り(`LeakGuard`)+ 統合検(置き場の全 file を根の path で検索) | `store.rs` の `LeakGuard`・`write_object`、`tests/facts_cache_shared.rs::no_working_tree_path_is_left_in_the_shared_facts`、lib の unit | `cargo test` | 針は根の path の 2 綴り(与えた形と正規化した形)・4 byte 以上だけ。根の外の絶対 path・作業木に固有の別の情報(host 名など)は見ない。JSON の形(`per_file`・`per_file_keyed`)には bind の口が無く、path を持つ種類は compact の形を使う必要がある(見張りは置かずに作り直すだけ) |
| 答えは cache の状態・並走・書けない置き場に依らない | 差分検(cache あり・無しの出力を比べる)統合検・壊れた事実の unit | `tests/facts_cache_shared.rs`・`facts_cache.rs` の `a_broken_index_or_fact_is_read_as_absent` | `cargo test` | **並走・途中死・書けない置き場の確かめ(`C3`〜`C5`)は設計時の実験の script(`evidence/tools/stress.py`)で、`cargo test` に入っていない = 通常の変更時の経路に未接続**。変種 3 つ(stamp を信じ過ぎる・書き失敗で止まる・壊れた事実で止まる)は script では全部赤になる(`N-C3`〜`N-C5`)が、通常の検は壊れた事実と答えの比べまで |
| 親子の依存が一方向 | code-quality の fast 段の `dependency-cycle`・`dependency-uncontracted` | `.agents/code-quality.d/doeff-linter-1828.json`(facts_cache.rs・store.rs・sweep.rs を `doeff-linter-project` に入れ、公開は facts_cache.rs だけ) | commit の hook・登記前の `code-quality --scope changed`(L2253・L2266 の前に passed) | 契約は 1 つの module で、file どうしの細かい許可は表せない。型検査は fast 段で撃たない |
| 通常の経路への接続 | 着地ごとの門(L2253 の log)は衝突印の確認と `uv sync` だけで、`cargo test` を走らせていない。`cargo test` は `make test-rust`(`.agents/land-queue.toml` の日次の台帳の full 段 `rust`)が全 package で走らせる | — | 日次 | **着地の時点では cargo の検は走らない**。日次の full 段が今日までに緑かは確かめていない |

## 6. 盲検の「読んだだけの候補」の扱い

| 候補(出所) | 確かめ | 扱い |
|---|---|---|
| bincode の知識が M-indexer の検と M-index の塊の読み書きにも在る(A・読んだだけ) | S1 の符号化の変更(`S1-p1`〜`p3`)は `store.rs` の 2 関数だけ。表の塊は store を通らず M-index が自分で読み書きする | 事実の符号化と表の符号化は別の持ち主で、事実の側を替えても表の側は変わらない事を S1 で実測した(表の側を替える変更は試していない)。M-indexer の検が古い形式を確かめ続ける点は、cache の外の索引の形式の検で、今回の対象外 |
| 1 process で根は 1 つという前提が隠れている。`tend_roots` は process ごとに 1 度(A・読んだだけ) | 読んだだけ。`tend_roots` の `OnceLock` は第 1 版の 1 つ前の版(`c225cef6f~2`)にも在る | 元からの前提で今回の差分の外。2 つ目の根の dir は片づけの「使っている根」の印が付かないが、消えても答えは変わらない(作り直し)。変更はしなかった |
| JSON の形に bind の口が無く、path を探す検は有効にした規則の種類しか通らない(B・読んだだけ) | 前者は B-2 の見張りが置く所で補う。後者の統合検の死角は、実在の呼び手の変種(`L-U1`・`L-U2b`)で検の効きを確かめた | 見張りは種類に依らず置く所で効く。統合検の `CONFIG` の死角は残る(第 7 節) |
| `KeyedFacts::of` が `kind == Defk` しか hash しない。推論が他の種類を読むようになると `keyed_value` が古い答えを返す(B・読んだだけ) | `git diff c225cef6~1..HEAD -- signatures.rs` に `Defk` の変更は無い = 元からの鍵の意味 | 今回の差分の外の既存の前提。将来の変更の注意として残す(第 7 節) |

## 7. 設計者の自己検査で分かった事・限界

- **等価な変種を穴と誤認しかけた**: signature の compute から `set_path("")` を外した変種が全部の検を通ったが、compute は元から根を空で呼んでいて、その行は重複だった(等価な変種)。本物の変種は Hy の索引側(`index_file(root, path)` が本物の根を使う)で、shared の 2 本が赤になる(`L-U1`)。
- **統合検は signature の位置の path を観測しない**: 位置の path が出力に出るのは editor が stdin で file を渡す経路の `signatures` の欄で、統合検の binary 呼びは通らない。bind の付け忘れは lib の 2 本(`bindings_carry_types_or_null_when_unknown`・`effects_are_inferred_transitively_with_absent_and_raise`)だけが拾う(`L-U2b`)。統合検の層だけでは見えない。
- **盲検 A・B の再検は 1 回ずつ**: 直した後に盲検を新しい文脈で起こし直してはいない。直した後の確かめは設計者が、盲検の失敗例と同じ手順(`evidence/tools/`)と変種で行った。
- **実験の範囲**: 検は 60 module の合成の木(実物は Hy 2864 file)・ローカルの FS だけ。並走は 8 本・殺す時刻は乱数。本物の別の FS(NFS・overlay)・仮想の更新時刻は 1970 年の固定までしか試していない。
- **殺した実行が残す一時 file**: 殺した実行は `.<名>.<pid>.tmp` を残す(`C3` で 0〜11 本)。答えには影響せず、1 時間より古ければ片づけが消す(unit `a_temporary_made_by_the_writer_is_recognised_by_the_lister`・roots の検)。
- **盲検の写しの欠け**: 盲検の写しに `packages/doeff-hy` が無かったので、`class_fields_for_doeff204_match_the_shared_case_table` が 1 本、表の file を読めずに赤になる(修正とは無関係)。`evidence/intermediate/` の記録に残る。着地版での再検(`L-A1`)は欠けの無い写しで 659 本全部緑。
- **`facts_cache/mod.rs` の配置を試して戻した**: `facts_cache/mod.rs` にすると code-quality が Rust の module 解決を曖昧(`x.rs` と `x/mod.rs` の両方が在る)として検査を走らせなかったので、`facts_cache.rs` + `facts_cache/` の形にした。
- **今回の差分の外で将来効く前提**: `KeyedFacts::of` が Defk しか hash しない(第 6 節)・`tend_roots` が process ごとに 1 度(同上)。

## 8. 修正履歴

| 版 | 内容 | 根拠 |
|---|---|---|
| 第 1 版 `c225cef6` | 事実を 1 file 1 事実の内容アドレス(鍵 = linter の印 + 根からの path + 中身の SHA-256)にし、根ごとには file → 鍵の小さな表だけを置く。根の絶対 path を持つ種類は path を空にして置き、読んだ後に bind。上限の片づけを事実込みに拡張 | card の条 1(`a_second_checkout_of_the_same_tree_writes_less_than_a_tenth_of_the_first`)。zeus の実際の作業木の再演: 同じ commit の 2 本目 211.8MB → 1.9MB(0.9%)・答えの hash は同じ(card ki-9743c30d49bc の note) |
| 第 2 版 `9119bb92` | 盲検 A・B への直し: 事実の置き場の知識を `store.rs` に、寿命を `sweep.rs` に分ける。列挙は配置の深さに依らない。一時名を共有。事実の path を作れるのを store だけに(非公開)。`LeakGuard`。更新時刻の運用を 24 時間に整理し、10 分の主張を絞る。`Format` を store へ・`mark_used` を sweep へ移して親子の循環を解く。module 契約と shared の検を足す | 第 4 節。`dependency-cycle` を code-quality が検出した(親 ↔ 子の `use super::`)ので、移動で解いた |
| 第 3 版 `8ff9709b` | 使う名の走査の unit 1 本(`facts_cache.rs` の tests)を足す | 第 4 節の B-1 の残り。設計者が、前の版の「事実を消せるのは片づけだけ」の主張が強制できていなかった事に気づいた |

## 9. 未解決・範囲の外

- 未解決として報告 JSON の `unresolved` に載せる事項は無い。ただし次は、通過としてではなく限界として第 5・7 節に書いた: 並走・書けない置き場の確かめが通常の経路に未接続・統合検の死角・盲検の再検をしていない事・本物の別の FS を試していない事。
- 範囲の外: card の条 2(本番の 1 時間の測り)。測り方は card に書いてある(出生時刻で数える方が mtime より正確 — `find -newerBt`)。
