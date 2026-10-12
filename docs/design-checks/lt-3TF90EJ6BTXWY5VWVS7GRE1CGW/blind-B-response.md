## 盲検の失敗例: 寿命の判断と事実の削除が片づけ(M-sweep)の外へ移る(提示の検査は通過)

結論です。下の差分は、提示された検査を写しの上で実行して全部通りました(実測)。それでいて M-sweep の不変条件と R1 を破ることも、同じ写しで確かめました(実測)。答え(R2)は変わらないので、出力の比較では見つかりません。

前提として、`<repo>` の HEAD は 41b33a3d9 で、`git diff c225cef6f HEAD -- packages/` は空でした。blind-input.md の sha256 も一致しています(どちらも実測)。

### 1. 機能要求と差分

**要求:** editor の保存ごとの hook では、1 file の中身の版ごとに事実が `objects/` に 1 つずつ増え、4 GiB の上限で片づけが走るまで溜まり続ける。根の表の行が新しい鍵に替わった時(中身が変わった・file が消えた)、前の鍵の事実はもうその根から読まれないので、その場で消して置き場の伸びを抑えたい。

**差分:** `facts_cache.rs` の `per_file_at` で、`dirty` を計算した直後に足します。写しは `/tmp/dc-mod.Mp43FS/packages/doeff-linter/src/project/facts_cache.rs` です。
```diff
@@ -691,6 +691,17 @@
     for rel in known.keys().filter(|rel| !present.contains(rel.as_str())) {
         dirty[shard_of(rel)] = true;
     }
+    // 行の鍵が替わった file(中身が変わった)と、表から消える file の前の事実は、この根からはもう読まれない。保存ごとに走る editor の
+    // hook では中身の版ごとに事実が 1 つずつ増え、上限の片づけ(4 GiB)まで溜まり続けるので、表を書き直すこの場で前の事実を消す。
+    let current: HashMap<&str, Option<ObjectKey>> =
+        resolved.iter().filter_map(|one| one.row.as_ref()).map(|row| (row.rel.as_str(), row.object)).collect();
+    for (rel, row) in &known {
+        let replaced = row.object.filter(|old| current.get(rel.as_str()).copied().flatten() != Some(*old));
+        if let Some(old) = replaced {
+            // 消せなくても答えには関わらない(上限の片づけがいずれ消す)
+            let _ = std::fs::remove_file(object_path(&place.objects, &old));
+        }
+    }
```

### 2. 本来の所有者と、侵す契約

- **本来の所有者:** 置き場の寿命は M-sweep が持ちます。上限・古い順・10 分の保護という知識、「置き場の走査と削除」という effects、不変条件「使っている根・10 分の内の事実は消さない」がそこにあります。
  - M-resolve の effects は「file の読み・compute の呼び」、M-index は「根の dir の下の塊の読み書き」です。どちらも `objects/` の削除を持ちません。
  - M-store は、置いたら書き換えない書き手と読み手で、削除の口を持ちません。
- **移るもの:** 「この事実はもう要らない」という判断と削除の effects が、M-sweep から `per_file_at`(M-resolve と M-index の境)へ移ります。
  - しかも判断の材料は、1 つの根の表だけです。事実は中身の鍵で根をまたいで分け合うので、他の根が同じ鍵を指しているかはこの場所では分かりません。
  - この知識は表で M-sweep 以外に隠されています。寿命を「古い順＋10 分」に預けた理由もここにあります。
- **壊れる契約(どれも実測):**
  - M-sweep の「10 分の内の事実は消さない」。他の作業木が数秒前に使った事実 4 個が消えました。
  - R1。作業木 A が別の commit へ移った後、A が最初にいた commit の新しい作業木が 95.9% を書き直しました。
  - 「変えていない作業木の 2 回目は事実を書かない」。複数の作業木があると崩れます。
- **保たれるもの:** R2(答え)は保たれます(実測)。M-store の「同じ鍵なら同じ内容」も保たれますが、置いた鍵が片づけ以外の経路で消えます。

### 3. 各検査が拒否しない理由

| 検査 | 結果 | 拒否しない理由 |
|---|---|---|
| `cargo test --locked --lib facts_cache`(9 本) | 実測 9 passed | 削除が起きるのは、根 1 つの中で中身が変わる・file が消える場合だけです。そこで消える前の中身の事実は、誰も読み直しません。検が見るのは compute を呼んだ回数と答えだけです |
| `facts_cache_shared`(5 本) | 実測 5 passed | 2 本目の作業木は初回の実行で、自分の表に前の行を持たないので何も消しません。1 file を書き換える検は作業木 1 本だけで、他の作業木が古い事実を指す状況を作りません。path を探す検は、この変更と関係しません |
| `facts_cache_roots`(16 本) | 実測 16 passed | どの根も、新しく走るか変えずに走るだけです。検が確かめるのは、片づけが何を選んで消すかです。片づけの外のコードが消さないことは、どの検も見ていません |
| `hy_index_cache`(1 本)・`focused_run_reads`(3 本) | 実測 1 passed・3 passed | 根は 1 つで、答えは変わりません |
| rustc | 実測で compile が通った | 写しの間で、lib test の警告は 18 件ずつ同じでした |
| code-quality の module 契約 | 実行していない(読んだだけ) | `doeff-linter-1828.json` では、`facts_cache.rs` と呼び手は全部 1 つの module(`doeff-linter-project`)に入っています。M-store・M-index・M-resolve・M-sweep は同じ file の中の関数で、この差分は新しい依存を足しません。拒否されないと見込みますが、推測です |

### 4. 再現の手順と結果(実測)

- **手順 1:** `/tmp/dc-orig.EglefR`(元の写し)と `/tmp/dc-mod.Mp43FS`(差分を当てた写し)に、`packages/doeff-linter` と `packages/doeff-indexer` を写しました。
- **手順 2:** 差分を当てた写しで、提示の 2 つの命令を実行しました。`CARGO_TARGET_DIR` は一時 dir、`DOEFF_LINTER_CHECK_SLOT=1`、`DOEFF_LINTER_CACHE_DIR` も一時 dir です。結果は §3 の表のとおり全部通りました。
- **手順 3:** 再現の検 `tests/repro_object_lifetime.rs` を、両方の写しに置きました。木の作り方は `facts_cache_shared.rs` の写しです。片づけを除くため、上限は `DOEFF_LINTER_CACHE_MAX_BYTES=2^40` にしました。実行は `cargo test --locked --test repro_object_lifetime -- --nocapture --test-threads=1` です。
  - **筋書き 1:** 作業木 A と B を走らせ、A で mod3.hy に 1 行足して A を走らせ、変えていない B をもう一度走らせます。
  - **筋書き 2:** A を走らせ、全 module に 1 行ずつ足して A を走らせ、元の中身の作業木 C を新しく作って走らせます。

| | 元の写し | 差分を当てた写し |
|---|---|---|
| 筋書き 1: A の実行が消した事実 | 0 個 | 4 個(`hy-index.bin`・`signature-facts.bin`・`defk-names.json`・`program-params.json`。全部 10 分の内) |
| 筋書き 1: 変えていない B の 2 回目が `objects` に書いた byte | 0 | 19,688 |
| 筋書き 2: C が書いた byte / A の 1 本目 | 17,171 / 517,863(3.3%) | 496,783 / 517,863(95.9%) |
| 答えは cache 無しと同じか | 同じ | 同じ |
| 再現の検 | 2 passed | 2 failed |

### 5. 確かめていない前提と、足りない情報

- **R1 の読み方:** R1 の文が「1 本目の作業木が間に別の commit で走る」場合を含むかは、材料からは決められません。筋書き 2 は R1 の意図として示したものです。筋書き 1 は M-sweep の不変条件に直接当たるので、この解釈に依りません。
- **実際の規模:** zeus の数百の作業木と editor の hook でどれだけ書き直しが増えるかは、測っていない推測です。
- **並走:** 削除と別の process の読みが重なる場合は試していません。Linux では開いた file は消しても読めるので、答えは変わらないと見込みますが、推測です。
- **元の写しでの提示の検査:** 元の写しで実行したのは build と再現の検だけで、提示の検査そのものは走らせていません。
- **後始末:** build の置き場(2.4G)は消しました。もう一度走らせるには、初回の build(約 5 分)がかかります。本物の置き場の `root.path` に、私の一時の木は記録されていませんでした(確かめ済み)。

**読んだだけで検証していない別の候補:**
- JSON の形には bind つきの口がありません。また、path を探す検は `CONFIG` で有効にした規則の種類しか通りません。そのため、`DOEFF142` の `handler-argument-classes` に絶対 path の欄を足すような変更は、すり抜けると見込みます。
- `KeyedFacts::of` は `kind == Defk` しか hash しません。推論が deff の種類なども読むように変えると、`keyed_value` が古い答えを返すと見込みます。

WAIT: work — 依頼元の次の指示
