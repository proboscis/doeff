## 結論

検査を通るのに責務を侵す実装を 1 つ作り、実際に確かめました。示された検はすべて緑のまま、枠が埋まっている間も linter が枠の外で走ります(R1 違反)。正本だけが持つはずの「入れ子かどうかの判定」が linter 側(M-gate)へ移ります。

## 1. 機能要求と差分

**要求(想定)**: 枠を持つ pytest の中で doeff-linter を撃つ検は、1 回ごとに `uv python find` と python の起動(註の実測で 0.2〜0.6 秒)を払っています。それなのに正本は「入れ子だから 2 つ目を取らない」と判じて、そのまま走らせるだけです。正本は枠を持つ process の子へ `AI_CHECK_SLOT_HOLDER` という env を継ぐので、これが在れば python を通さず直に走らせて速くしたい、という要求です。

**差分**(`src/check_slot.rs`。ほかに註 2 行を足しています):
```rust
+/// 保持者の印 — 正本(machine_check_slots.py の HOLDER_ENV)が、枠を持つ process の子へ継ぐ env。値は保持者の pid。
+pub const HOLDER_ENV: &str = "AI_CHECK_SLOT_HOLDER";
+/// 枠を持つ process の内側か(保持者の印が在るか)— 内側なら正本は 2 つ目を取らないので、包みへ渡さずに走る。
+fn inside_holder() -> bool {
+    std::env::var_os(HOLDER_ENV).is_some_and(|pid| !pid.is_empty())
+}
 ...
-    if std::env::var_os(GATED_ENV).is_some() {
+    if std::env::var_os(GATED_ENV).is_some() || inside_holder() {
```
実装者が足しそうな検も 1 本 `tests/check_slot.rs` に加えました。`a_linter_inside_a_slot_holder_runs_without_the_canon` は、偽の正本の下で `AI_CHECK_SLOT_HOLDER=4242` を付けた linter が正本を通らないことを確かめます。既にある検は 1 行も変えていません。

## 2. 本来の持ち主と、破られる契約

- **持ち主は M-canon**: 入れ子の判定(/proc の祖先の列 → それが読めない時だけ env の印 → その枠が今も lock されている時だけ内側と読む)は正本のものです。表でも「隠す知識: 祖先の判定」とされています。正本の公開の口は `run` と `holders` だけです。`AI_CHECK_SLOT_HOLDER` は、/proc の無い機体のための正本内部の印です(正本 L17-18・L183-192)。
- **移るもの**:
  - 知識: 印の名が M-gate に移ります。
  - 判断: 「印が在れば枠の内側」という判断が M-gate に移ります。
  - 状態: 「誰が今枠を持つか」の正しい答えは lock の file にあります。写しは lock を見ず、印だけで決めます。
- **写しは正本と食い違います**: 正本の検 `test_a_stale_marker_or_a_dead_holder_does_not_count_as_inside` は、「印の指す pid が枠を持っていなければ内側と読まずに枠を取る」ことを契約にしています。写しはこの逆をします。
- **崩れる主張**:
  - R1: 枠が埋まっている間は待つ。
  - S3: 取り方を変えても `run --label -- cmd` と終了コードが保たれる限り、M-gate は変わらない。正本が印の名・意味・継ぎ方を変えると、M-gate が黙ってずれます。
  - S5 の前提: 子孫は /proc の祖先の列で辿る。
  - `enter(Weight)` の意味(重い mode は正本を通る)に、正本内部の印による例外が入ります。
- **印が古いまま残る道**: `run` の命令が残した長命の孫(正本の検 `test_a_long_lived_grandchild_does_not_keep_the_slot` が扱う形)、背景に回した子、pytest の検が起こした daemon。どれも保持者が退いた後も印を継いだまま残ります。

## 3. 示された検が拒まない理由

cargo test の欄は、印 env の無い環境で走らせた実測です。

| 検 | 結果 | 拒まない理由 |
|---|---|---|
| `a_linter_beyond_the_slots_waits_…` | ok | `linter_under` が消すのは `DOEFF_LINTER_CHECK_SLOT` だけで、印 env はもともと無いので近道が働かない |
| `a_repo_scan_hands_itself_…` | ok | 同上 |
| `cheap_and_caller_bounded_modes_…` | ok | 軽い mode は近道より前に戻る |
| `a_linter_inside_the_gate_or_without_a_canon_…` | ok | 再入の印・正本の無い HOME の挙動は変わらない。「印は在るが保持者がいない」場合は検に無い |
| `the_linter_keeps_no_copy_of_the_slot_values` | ok | 禁じる綴りは置き場の dir の値・`machine_check_slots.json`・`waitLimitSeconds`・`slot-`・数 8 と 1200 だけ。値の写しを見る検で、判断や印の名の写しは見ない |
| 単体 4 本(`--lib check_slot::`) | 4 passed | weight・gate_command・label は変えていない |
| dotfiles の `test_machine_check_slots.py` | 走らせていない | 正本の module だけを検める。読んだ範囲では doeff-linter を起動せず、差分は dotfiles に触れない(読んだ上での推論) |

追加した検を含め、integration は 6 passed でした。

**補足(実測)**: 同じ差分で `AI_CHECK_SLOT_HOLDER=999999` を付けて cargo test を走らせると、R1 の検が 60 秒後に FAILED(「linter が待ちを名乗らない」)になります。つまり R1 の検がこの差分を捕まえるのは、cargo test 自体が保持者の内側で走る時だけです。

## 4. 確かめた手順と結果(実測)

**作業の場所**: `mktemp -d` で作った `/tmp/tmp.IGTlSGT4Bk` に `packages/doeff-linter` と `packages/doeff-indexer` を写しました。共有の作業木は `git status` で変更なしを確かめています。ビルド成果物(約 3.5GB)は確認後に消しました。残っているのは差分の source(`src/`)と再現 script だけです。

**検の通過**:
1. 着地の版をビルドし、binary を取っておく。
2. 差分を当て、印 env の無い環境で次を走らせる。
   - `CARGO_TARGET_DIR=<tmp>/target cargo test --locked --test check_slot` → 6 passed
   - 同じく `cargo test --locked --lib check_slot::` → 4 passed

**違反の再現**: `/tmp/tmp.IGTlSGT4Bk/scenario/run.sh <作業 dir> <linter>` で行います。
- 準備: 一時の HOME に正本(f333bd2f)の写しと宣言 `{"slots":1,"dir":<一時>/slots,"waitLimitSeconds":30}` を置く。
- (a) 包み W1 が枠を取る。命令の sh は「3 秒後に `doeff-linter --no-log .` を撃つ背景の子」を残して終わり、W1 も枠を解いて退く。子は W1 の pid を印に継いでいる(その pid は既に死んでいる)。
- (b) 別の保持者 H(`run --label long-check -- python -c sleep(12)`)がただ 1 つの枠を取る。
- (c) 背景の子の linter が撃たれる。

**結果の比べ**:

| | 着地の版 | 差分の版 |
|---|---|---|
| 撃って 2.5 秒後 | 未終了 | 終了 1 |
| stderr | 「埋まっているので待つ — 保持 pid … long-check・待つのは doeff-linter lint repo」→「枠 0 を取った(待ち 8 秒)」 | 名乗り無し |
| 所要 | 9.6 秒(H が退くのを待った) | 0.8 秒 |
| 走っていた間の `holders` | (待っていた) | long-check の 1 件だけ |

差分の版は、K = 1 が埋まっている間に枠の外で lint しました。

## 5. 未確認の前提と不足

- **要求の効果**: 速くなる量(pytest の中で linter が何回撃たれるか、合わせて何秒縮むか)は測っていません。要求そのものも想定です。
- **本番での頻度**: 印を持ったまま枠の外で linter を撃つ process が実運用でどれだけ在るかは調べていません(long-command の task・daemon・会話の Bash が印を継ぐか)。再現は人工的に作った孤児の子です。
- **日次の rust の段(atlas)**: land-queue.toml の rust の段は、文面上 `make test-rust` を `run` で包んでいません。ただし日次の task 全体の env に印が在るかは読んでいません。在れば、R1 の検がこの差分を捕まえます。atlas の Pod に `$HOME/dotfiles` が在るかも不明です(無ければ、差分と関係なく `real_canon()` で赤になります)。
- **着地の版の検**: 示された cargo test は差分の版でだけ走らせました。着地の版の binary は再現の場面でだけ使いました。
- **dotfiles の pytest**: 走らせていません。「拒まない」は読んだ上での推論です。
- **/proc の無い機体(Mac)**: 正本も印で判じますが、lock の確認はするので、死んだ保持者の場合は同じく食い違うはずです。ただしこれは推論で、確かめていません。
- **ほかの書き方**: lock まで見る「丁寧な」写し(`slot-<i>.lock` を読む)にすると、`slot-` の綴りで値の写しの検に捕まるはずです。これも推論で、ビルドはしていません。

WAIT: work — 依頼した側の次の指示
