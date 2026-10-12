# 設計検証(盲検の前の主張)— doeff-linter の事実の cache を作業木の間で分ける(card ki-9743c30d49bc)

この file は盲検 A・B を起こす**前**に固定した主張。失敗例を受けて書き換えない(修正は design.md に別に書く)。

## 0. 完了の範囲

- **実装完了まで**(依頼の範囲 = 直す + 受入の test)。対象の版 = doeff main の `c225cef6f2d688b3d2133a7d88a89a68984becfe`(着地済み・L2246)。
- 範囲の外: card の受入の条 2(本番の 1 時間の測り)は、新しい linter が作業木の venv に届いた後でないと測れない。設計の主張ではなく運用の観測。

## 1. 固定した要件

- R1: 同じ commit を checkout した 2 つの作業木で doeff-linter を順に走らせた時、2 本目が事実の cache の下に新しく書く byte は 1 本目の 10% 未満。
- R2: 答えは変えない。cache の有る無し・温かさ・壊れ・並走に依らず、同じ木からは同じ出力(位置の path は今の作業木の file を指す)。
- 前提: linter は 1 回の実行で終わる CLI(常駐しない)で、同じ機体の多数の process(editor の hook・commit の hook・land の門・日次)が同じ置き場を並走で使う。事実は file の中身・根からの path・linter の印・呼び手の鍵だけで決まる(呼び手が保つ)。置き場が消えても答えは変わらない(作り直すだけ)。

## 2. 責務(論理 module — 実体は `packages/doeff-linter/src/project/facts_cache.rs` の関数群と呼び手)

| id | 責務 | 所有する知識 | 隠す知識 | 公開(契約) | effects | 寿命 | 不変条件 |
|---|---|---|---|---|---|---|---|
| M-store | 事実 1 つを鍵で置く・読む | 鍵の作り方(`object_key`)・置き場の path(`object_path`)・符号化(`encode_value`/`decode_value`)・一時 file から rename・読んだ時の更新時刻の更新(`read_object`/`write_object`) | file の配置・hash の種類 | 内部(`per_file_at`・`keyed_value` だけが呼ぶ) | `objects/` の下の file の読み書き | 事実は書き換えない。消えるのは片づけだけ | 同じ鍵なら同じ内容・書きかけは見えない(rename)・読めない物は None |
| M-index | 作業木ごとの表(file → 大きさ・更新時刻・事実の鍵) | 行の形(`Row`)・塊の分け方(`shard_of`)・どの塊を書き直すか | 事実の中身 | 内部 | 根の dir の下の塊の読み書き | 根の dir の寿命(作業木が消えれば片づく) | 印(identity)が違う・壊れた塊は無い物 |
| M-resolve | file 1 つの事実を引く手順 | 早い判じ(大きさ・更新時刻)→ 中身の hash → 置き場 → 作る → 置く の順・読む間に file が変わった時は覚えない・bind を返す前に当てる | 表と置き場の配置 | `per_file`・`per_file_keyed`・`per_file_compact`・`per_file_compact_bound`・`keyed_value`(公開) | file の読み(中身の hash)・compute の呼び | 1 回の実行 | 答えは cache 無しの `compute` と同じ・置く値は bind の前の形 |
| M-sweep | 置き場の寿命(上限・古い順・消えた根・一時 file) | 上限(既定 4 GiB)・使っている根の 10 分・番の間隔・事実の古い順・`objects` の使った印 | 事実・表の中身 | `cache_base()`・`mark_used` | 置き場の走査と削除・記録の file | 実行ごとの 1 度(`tend_roots`)+ 新しく置いた量が 32MiB を越えた時 1 度 | 使っている根・10 分の内の事実は消さない・消しても答えは変わらない |
| M-callers | 事実の意味(`compute`)と、根の絶対 path を持つ値の bind | 種類ごとの事実の中身・どの欄が根の path を持つか・種類の鍵(`failure-types` の設定の指紋・`program-params` の defk の名の指紋) | 置き場の形・鍵の作り方 | `compute(rel, path)`・`bind(&mut value, rel, path)` | file の読み | なし | 事実は中身・`rel`・渡した鍵だけで決まる・置く値に根の絶対 path が無い |
| M-indexer | Hy の索引の cache の形(`CachedHyFile`)と path 欄の所有 | 索引のどの欄が絶対 path か | — | `CachedHyFile::of`・`set_path`・`into_file` | なし | なし | `set_path` 以外の欄は中身と根からの path だけで決まる |

既存の組み立て点から無くなる判断: 呼び手(`cached_hy_files`・`World::build`)は、置き場の形・更新時刻の判じ・書き直す塊の判断を持たない(以前から持たない)。新しく呼び手が持つ判断 = 値に根の絶対 path が在る種類の bind だけ。

## 3. 変更シナリオと事前の予測

| id | 軸 | 変える要求 | 主張 | 予想の波及(変わる) | 変わらない |
|---|---|---|---|---|---|
| S1 | storage | 事実の符号化を替える(圧縮・版の印を足す・形式を替える) | 符号化は M-store の `encode_value`/`decode_value` に閉じ、表・引く手順・片づけ・呼び手は変わらない | M-store | M-index・M-resolve・M-sweep・M-callers・M-indexer |
| S2 | effects | 新しい種類の file ごとの事実を足す。値は根の絶対 path を持つ | 変わるのは呼び手(新しい種類の compute と bind)と、その値の型だけ。置き場・表・引く手順・片づけは変わらない | M-callers(+ 値の型) | M-store・M-index・M-resolve・M-sweep |
| S3 | concurrency | 同じ置き場へ多数の linter が並走し、片づけが事実を消し、実行が途中で殺される | 正しさは置き場の中身に依らない。読めない・無い・壊れた事実は作り直し、書きは一時 file から rename、同じ鍵の事実は同じ内容。コードの変更は要らない | なし | 全部 |
| S4 | simulation | 置き場の状態(空・温・一部欠け・壊れ)と thread の数を替えて、同じ木を決定的に再現する | 答えは置き場の状態と thread の数の関数ではない | なし | 全部 |
| S5 | hardware | 置き場を別の disk・別の FS(書けない・読み専用・mtime を変えられない)へ移す | 変わるのは `DOEFF_LINTER_CACHE_DIR` の値だけ。書けない時は黙って作り直し、答えは同じ | なし(設定の値だけ) | 全部 |
| S6 | distribution | 単一機体から cluster へ(置き場を機体間で共有) | **不適用**: 置き場は機体の局所(`/work` の下・linter の印は binary の大きさと更新時刻なので機体をまたいで安定しない)。機体間の共有は要求に無く、先回りして作らない | — | — |

主張の前提: 将来の変更を全部は予測しない。S1〜S5 は今の要求から具体化した物。

## 4. 強制方法(責務ごと)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 |
|---|---|---|---|
| M-resolve: 答えは cache 無しと同じ | 差分検(cache あり・無しの出力を比べる)・unit 検 | `tests/facts_cache_shared.rs`(`a_second_checkout…`・`editing_one_file…`・`module_dependencies…`)・`facts_cache.rs` の `mod tests` | `cargo test`(`make test-rust`) |
| M-store・M-resolve: 同じ中身は分け合い、置き直さない(R1) | 書いた byte を数える検 | `tests/facts_cache_shared.rs::a_second_checkout_of_the_same_tree_writes_less_than_a_tenth_of_the_first`・unit `a_second_root_with_the_same_contents…` | 同上 |
| M-callers: 置き場に根の絶対 path を残さない | 置き場の全 file を根の path で検索する検・値の型側は `FileFacts::set_path` の欄の分解(欄を足すと compile が止まる) | `tests/facts_cache_shared.rs::no_working_tree_path_is_left_in_the_shared_facts`・`signatures.rs` の `FileFacts::set_path` | 同上・compile |
| M-resolve: 読む間に変わった file を覚えない | unit 検 | `a_file_changed_while_its_fact_is_made_is_not_remembered` | 同上 |
| M-sweep: 上限・古い順・10 分・一時 file | 一時の置き場で binary を走らせて dir を見る検 | `tests/facts_cache_roots.rs`(既存 11 + 新 5) | 同上 |
| 責務の module 契約 | `.agents/code-quality.d/doeff-linter-1828.json`(doeff-linter-project の paths・public)と code-quality の fast 段(dependency-uncontracted など) | 同 JSON | code-quality `--scope changed` |
