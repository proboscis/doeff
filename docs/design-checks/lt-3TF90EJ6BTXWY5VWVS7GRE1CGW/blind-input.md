# 盲検 A・B への材料 — doeff-linter の事実の cache(版 c225cef6f2d688b3d2133a7d88a89a68984becfe)

あなたが読む対象は doeff の repo の `packages/doeff-linter/src/project/facts_cache.rs` を中心とする、linter の「file ごとの事実の cache」。
これは設計の主張と実コードの一式。設計者の自己評価・成功報告・過去のレビューは含めていない。足りない情報は不足として返すこと(推測で補わない)。

## 作業の決まり(守る)

- 共有の source を**書き換えない**。実験が要るなら、`T=$(mktemp -d)` を切り、`mkdir -p $T/packages && cp -r <repo>/packages/doeff-linter <repo>/packages/doeff-indexer $T/packages/` で写して、写しの上で行う。
- Rust の build の置き場も一時 dir にする: `CARGO_TARGET_DIR=$(mktemp -d)`。速くするため `CARGO_PROFILE_DEV_OPT_LEVEL=2 CARGO_PROFILE_DEV_DEBUG=0` を付ける。build は初回で数分かかる。
- binary を走らせる検は `DOEFF_LINTER_CHECK_SLOT=1` を付ける(機体の検の枠を待たないための印)。
- 本物の置き場(`/work/toolset/cache/doeff-linter`・`~/.cache/doeff-linter`)に触らない。linter の cache の置き場は `DOEFF_LINTER_CACHE_DIR=<一時 dir>` で替える。
- 全数検査・他の会話への連絡・network への送信はしない。python は `uv run` を通す(機体の python3 を直に呼ばない)。
- 対象の版は、読み取り専用で `<repo>` = `/home/ubuntu/.worktrees/doeff-wt-dc-lt3tf`(doeff main の c225cef6f2d688b3d2133a7d88a89a68984becfe)。

## 1. 固定した要件と前提

- R1: 同じ commit を checkout した 2 つの作業木で doeff-linter を順に走らせた時、2 本目が事実の cache の下に新しく書く byte は 1 本目の 10% 未満。
- R2: 答えは変えない。cache の有る無し・温かさ・壊れ・並走に依らず、同じ木からは同じ出力(位置の path は今の作業木の file を指す)。
- 前提: linter は 1 回の実行で終わる CLI(常駐しない)で、同じ機体の多数の process(editor の hook・commit の hook・land の門・日次)が同じ置き場を並走で使う。事実は file の中身・根からの path・linter の印・呼び手の鍵だけで決まる(呼び手が保つ)。置き場が消えても答えは変わらない(作り直すだけ)。

## 2. 責務(論理 module — 実体は facts_cache.rs の関数群と呼び手)

| id | 責務 | 所有する知識 | 隠す知識 | 公開(契約) | effects | 不変条件 |
|---|---|---|---|---|---|---|
| M-store | 事実 1 つを鍵で置く・読む | 鍵の作り方(`object_key`)・path(`object_path`)・符号化(`encode_value`/`decode_value`)・一時 file から rename・読んだ時の更新時刻の更新(`read_object`/`write_object`) | file の配置・hash の種類 | 内部(`per_file_at`・`keyed_value` だけが呼ぶ) | `objects/` の下の file の読み書き | 同じ鍵なら同じ内容・書きかけは見えない・読めない物は None |
| M-index | 作業木ごとの表(file → 大きさ・更新時刻・事実の鍵) | 行の形(`Row`)・塊の分け方(`shard_of`)・どの塊を書き直すか | 事実の中身 | 内部 | 根の dir の下の塊の読み書き | 印が違う・壊れた塊は無い物 |
| M-resolve | file 1 つの事実を引く手順 | 早い判じ(大きさ・更新時刻)→ 中身の hash → 置き場 → 作る → 置く の順・読む間に file が変わった時は覚えない・bind を返す前に当てる | 表と置き場の配置 | `per_file`・`per_file_keyed`・`per_file_compact`・`per_file_compact_bound`・`keyed_value`(公開) | file の読み・compute の呼び | 答えは cache 無しの `compute` と同じ・置く値は bind の前の形 |
| M-sweep | 置き場の寿命(上限・古い順・消えた根・一時 file) | 上限(既定 4 GiB)・使っている根の 10 分・番の間隔・事実の古い順・`objects` の使った印 | 事実・表の中身 | `cache_base()`・`mark_used` | 置き場の走査と削除・記録の file | 使っている根・10 分の内の事実は消さない・消しても答えは変わらない |
| M-callers | 事実の意味(`compute`)と、根の絶対 path を持つ値の bind | 種類ごとの事実の中身・どの欄が根の path を持つか・種類の鍵 | 置き場の形・鍵の作り方 | `compute(rel, path)`・`bind(&mut value, rel, path)` | file の読み | 事実は中身・`rel`・渡した鍵だけで決まる・置く値に根の絶対 path が無い |
| M-indexer | Hy の索引の cache の形(`CachedHyFile`)と path 欄の所有 | 索引のどの欄が絶対 path か | — | `CachedHyFile::of`・`set_path`・`into_file` | なし | `set_path` 以外の欄は中身と根からの path だけで決まる |

## 3. 将来の変更についての主張(事前の予測)

| id | 変える要求 | 主張 | 予想の波及(変わる) | 変わらない |
|---|---|---|---|---|
| S1 | 事実の符号化を替える(圧縮・版の印・形式) | 符号化は M-store の `encode_value`/`decode_value` に閉じる | M-store | M-index・M-resolve・M-sweep・M-callers・M-indexer |
| S2 | 新しい種類の file ごとの事実を足す。値は根の絶対 path を持つ | 変わるのは呼び手(新しい種類の compute と bind)と値の型だけ | M-callers(+ 値の型) | M-store・M-index・M-resolve・M-sweep |
| S3 | 同じ置き場へ多数の linter が並走し、片づけが事実を消し、実行が途中で殺される | 正しさは置き場の中身に依らない(読めない・無い・壊れた事実は作り直し・書きは一時 file から rename)。コードの変更は要らない | なし | 全部 |
| S4 | 置き場の状態(空・温・一部欠け・壊れ)と thread の数を替えて決定的に再現する | 答えは置き場の状態と thread の数の関数ではない | なし | 全部 |
| S5 | 置き場を別の disk・FS(書けない・読み専用・mtime を変えられない)へ移す | 変わるのは `DOEFF_LINTER_CACHE_DIR` の値だけ。書けない時は黙って作り直し、答えは同じ | なし | 全部 |
| S6 | 単一機体から cluster へ | 不適用(置き場は機体の局所。機体間の共有は要求に無い) | — | — |

前提: 将来の変更を全部は予測しない。S1〜S5 は今の要求から具体化した物。

## 4. 提示する検査(強制方法) — これらがそのまま通る実装は「通る」とみなす

| 守る責務 | 強制方法 | 実装箇所 |
|---|---|---|
| 答えは cache 無しと同じ・同じ中身は分け合い置き直さない(R1・R2) | cache あり・無しの出力の比較・書いた byte を数える検 | `packages/doeff-linter/tests/facts_cache_shared.rs`(5 本)・`facts_cache.rs` の `mod tests`(9 本) |
| 置き場に根の絶対 path を残さない | 置き場の全 file を根の path で検索する検・`FileFacts::set_path`(signatures.rs)の欄の分解(欄を足すと compile が止まる) | `facts_cache_shared.rs::no_working_tree_path_is_left_in_the_shared_facts` |
| 読む間に変わった file を覚えない | unit 検 | `facts_cache.rs` の `a_file_changed_while_its_fact_is_made_is_not_remembered` |
| 上限・古い順・10 分・一時 file | 一時の置き場で binary を走らせて dir を見る検 | `packages/doeff-linter/tests/facts_cache_roots.rs`(16 本) |
| 責務の module 契約 | `.agents/code-quality.d/doeff-linter-1828.json`(doeff-linter-project の paths・public)と code-quality の速い規則(dependency-uncontracted など) | 同 JSON(あなたは code-quality を走らせなくてよい — 名指しの検は cargo test) |
| 型 | rustc(`cargo build`・`cargo test` の compile) | — |

実行の命令(`<T>` は写しの dir・`<G>` は一時の target dir):
`cd <T>/packages/doeff-linter && DOEFF_LINTER_CHECK_SLOT=1 CARGO_TARGET_DIR=<G> CARGO_PROFILE_DEV_OPT_LEVEL=2 CARGO_PROFILE_DEV_DEBUG=0 cargo test --locked --lib facts_cache` と
`… cargo test --locked --test facts_cache_shared --test facts_cache_roots --test hy_index_cache --test focused_run_reads`

## 5. 読む場所

- `<repo>/packages/doeff-linter/src/project/facts_cache.rs`(中心・約 1000 行)
- 呼び手: `<repo>/packages/doeff-linter/src/project/mod.rs` の `cached_hy_files`(Hy の索引)・`defk_names_for`・`program_fields_for`・`program_params_for`・`failure_types_for`、`signatures.rs` の `World::build`・`FileFacts::set_path`・`file_facts`、`affected_tests.rs` の `affected_tests`、`handler_arguments.rs` の `class_index`
- `<repo>/packages/doeff-indexer/src/hy_index/cache_form.rs`(`CachedHyFile`)
- 検: `<repo>/packages/doeff-linter/tests/facts_cache_shared.rs`・`facts_cache_roots.rs`
- 設計の主張の元: この file の 1〜4
