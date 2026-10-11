## 結論

失敗例は見つかりました。変更要求は「失敗する件のある pipeline(ex07・ex04)に進捗の表示を足す」です。主張 S2 のとおり stack に ex08 の `tqdm-progress` を足すだけ(M2 の配線の変更)だと、答えは同じですが、進捗が 3/5・4/5 で止まり、bar が閉じません。直す道は 2 つしかありません。M3 が隠すはずの「件の program の組み立て」の規則を M2 に写すか(知識の漏洩)、M3 の公開契約を広げて件の結末を外へ出すか(M3 の変更)です。共有の source は変えていません(`git status` は空で、検 `test_examples_run.py` は 12 passed)。

## 1. 変更要求と、それが要る理由

- **要求:** ex07(proboscis-ema の news pipeline の書き直し)に進捗の表示を足し、各段の bar が全件の終わりで 100% になって閉じるようにする。
- **理由:** ex08 の頭(6〜7 行)に「proboscis-ema は throttled_gather_with_progress で tqdm を手で配線している。doeff-traverse では (tqdm-progress) を stack に足すだけ」と書いてあります。元の proboscis-ema は本物の backend を呼ぶので、一部の symbol が失敗します(symbol not found・market closed)。失敗する件のある pipeline に進捗を足すのは、ex08 自身が約束している使い方です。ex04・ex07 は、どちらも失敗する件を前提に作られています。

## 2. 関係する主張・前提と、予想に反して変わる所

- **主張 S2:** 「進捗の表示を足す。変わるのは M2 の stack だけで、答えは同じ。tqdm-progress は Traverse の f を包み直して同じ k のまま reperform する」。
- **前提は守っています(実測):**
  - 変えたのは stack だけです。M1・M3・M4 は写しで 1 文字も変えていません(diff で確かめた)。
  - 仕組みは ex08 の `tqdm-progress` の写しそのものです。
  - 答えはすべての組で同じでした(ex07 は success=3 fail=2、ex04 は mean=45.0)。
  - したがって、この例は契約の適用範囲の中にあります。
- **予想に反して変わる所:**
  - 正しい進捗には「件ごとの結末」が要ります。その情報を作るのは M3 の Traverse の handler だけです(ItemResult に ok・failed・skipped を付ける所)。
  - M3 はこの情報を、最後の `Resume(k, Collection)` で一度にしか返しません。M2 は f を包んで推測するしかありません。
  - その結果、変わる所は次のどちらかになります。
    - (a) M2 の進捗の handler に M3 の内部の規則を写す
    - (b) M3(`sequential`・`parallel`・`parallel_fail_fast` の 3 か所に同じ件の loop がある。加えて effects.py)を変える
  - どちらでも「M2 の stack だけ」は保てません。
- **付随して見えた結合(実測):**
  - ex08 の handler のままだと、失敗の方針を `fail_handler` から `replace-with-zero` に替える(S1 の変更)だけで、進捗が正しくなりました(下の B1 と B2)。S1 と S2 の 2 つの handler が、M3 の件の結末を通して結合しています。
  - M1 が When の見張りを 1 行足す(M1 の正当な変更)と、M2 だけで直した版 v2 がまた壊れました(C2)。M1 の書き方が、M2 の直しを強いています。

## 3. どの知識がどこへ漏れるか(区別)

| 種類 | 中身 | 欠陥か |
|---|---|---|
| 配線の変更 | stack に `(tqdm-progress)` を足す | 主張どおりの道。ただし進捗が誤る(実測) |
| 共同の不変条件 | 「件 1 つにつき進捗が 1 回進み、全件で閉じる」には、進捗の handler の total と、Traverse の handler が f を呼ぶ件の集合が一致している必要がある | v2・v3 は、この一致を契約ではなく写しで保っている |
| 隠すはずの知識の漏洩 | M3 の「隠す知識: 件の program の組み立て」の 3 つ。① 入りが Collection なら、失敗した件は f を呼ばずに持ち越す(handlers.py 65〜69 行・226〜228 行)。② 件の失敗は f の中から例外として抜ける。③ Skip を受けた Traverse の handler は件の続きを捨てる(45〜46 行・211〜212 行)。v2 は ① を `valid_items` の数として写し、v3 はさらに内部の effect `Skip`(effects.py 170〜180 行「Internal … Not intended for direct use」)を import して横取りする | 欠陥。M3 の規則が変わると、M2 が黙って壊れる |
| 意図した公開契約の拡張 | M3 が「件の結末が決まった」effect を出すようにする(対照実験の `ItemDone`)。これなら進捗の handler は Skip も持ち越しの規則も知らずに済む | 漏洩ではなく正当な拡張。ただし M3 の変更なので、S2 の主張「M2 の stack だけ」は破れる |

- ① は ex04 の注記にも書いてある半ば公開の挙動です。ただし handler ごとに違います。`parallel_fail_fast` は持ち越しを数えずに例外を投げます(401〜405 行、読んだだけ)。
- ex08 の total の `(len all-items)`(38 行)は、`Collection.__len__`(collection.py 108〜109 行)が失敗した件も数えるため、持ち越しの件がある段で一致しません。

## 4. 最小の入力・再現手順・観測すべき結果

実験の file はすべて `/tmp/ki043e-blind-a/` に置きました。

| file | 中身 |
|---|---|
| `progress_lib.hy` | bar を記録する tqdm の子 class・ex08 の `tqdm-progress` の写し(29〜52 行)・M2 だけの直し `tqdm-progress-v2`(Try で失敗も数え、Collection の有効な件だけを total にする)・`tqdm-progress-v3`(v2 に加え、内部の Skip を横取りする) |
| `exp1_ex07_progress.hy` | ex07 の 19〜175 行を写し、stack だけを変える |
| `exp2_ex04_progress.hy` | ex04 の 21〜166 行を写し、stack だけを変える |
| `exp3_ex04_when_progress.hy` | ex04 の写しに M1 の変更を 3 か所(Skip の import・stage 1 の `(When (not (.get item "excluded")))`・除外の印の付いた件 frank)足す |
| `exp4_ex04_when_itemdone.hy`・`m3_itemdone.py` | 対照の実験。handlers.py の `sequential` の写しに、件の結末が決まる 4 か所で `ItemDone` を出す行を足し、ItemDone だけを読む進捗の handler を外側に置く。写しと差分は `_seq_orig.txt`・`_seq_patched.txt` |

走らせ方(どれも `cd /home/ubuntu/.worktrees/doeff-wt-ki-043e-dc/packages/doeff-traverse` の後に `uv run python -m hy /tmp/ki043e-blind-a/<file>.hy`)と結果です。終了状態はすべて rc=0 でした。

| 組 | 答え | bar(n/total・閉じたか) |
|---|---|---|
| A1 ex07 + ex08 の tqdm-progress(sequential) | success=3 fail=2(A0 と同じ) | index 5/5 閉、signal 5/5 閉、**price 3/5 閉じない** |
| A2 同じ組で parallel(3) | 同じ | **price 3/5 閉じない** |
| A3・A4 v2(sequential・parallel(3)) | 同じ | price 5/5 閉 |
| B1 ex04 base + ex08 の handler | mean=45.0 | **extract 4/5・normalize 4/5、どちらも閉じない** |
| B2 ex04 replace-zero + ex08 の handler | mean=36.0 | 5/5・5/5 閉(失敗の方針しだいで進捗の正しさが変わる) |
| B3・B4 v2 | mean=45.0 | extract 5/5・normalize 4/4 閉 |
| C1 When を足した ex04 + ex08 の handler | mean=45.0、events=[ok ok failed ok ok skipped] | 4/6・4/6、閉じない |
| C2 同じ pipeline + v2 | 同じ | **extract 5/6 閉じない**(Skip で件の続きが捨てられる) |
| C3・C4 v3(内部の Skip を使う) | 同じ | 6/6・4/4 閉 |
| D1・D2 M3 の写しの ItemDone + ItemDone だけを読む進捗 | C と同じ・replace-zero 版も同じ | 6/6・6/6 閉(Skip も持ち越しの規則も知らない) |

- **観測すべき点:**
  - 答えは変わらず、bar の n が total に届かない。
  - 閉じない bar は終了の時に「price: 60% 3/5」「extract: 80% 4/5」と出る。
  - 閉じない bar が行の位置を持ち続けるので、後の bar がずれて出る。stderr に cursor を上げる escape `[A` が出ていた(`exp1.stderr` など)。
- **その他に走らせた命令:**
  - 元の 3 本の例 `uv run python -m hy examples/ex04_traverse_macro.hy` など: rc=0。
  - `uv run python /tmp/ki043e-blind-a/m3_itemdone.py`(継ぎ足した source を出す): rc=0。
  - `uv run pytest packages/doeff-traverse/tests/test_examples_run.py -q`: 12 passed。
  - M5 は rc しか見ないので、この欠陥は検に出ません。どの組も rc=0 でした。

## 5. 未確認の前提と不足する情報(推測と実測の区別)

- **実測:**
  - 上の表のすべて(sequential・parallel(3) の組、答えが同じこと、bar の n/total と閉じたか)。
  - 共有の木が変わっていないこと。
- **推測(読んだだけで走らせていない):**
  - `parallel_fail_fast` は持ち越しの件があると例外を投げるので、v2 が写した規則は handler ごとに違う。
  - M3 側を本当に直すなら、件の loop を持つ 3 つの handler のそれぞれに、4 か所ずつ同じ追加が要る。対照の実験で直したのは `sequential` の写しだけです。
  - for/do に From を重ねると、`tqdm-progress` は件の program の中へ入れ直されるので(handlers.py 71〜79 行)、外の件ごとに内側の bar が 1 本ずつ出る。effects.py 86〜89 行は「handler は内側の From かどうかを推し量らない」と決めているので、段ごとにまとめた bar は M2 だけでは作れない、という推測です。
- **前提として仮定した事:** 「進捗の表示が全件で閉じる」ことを要件とみなしました。R1 には書かれていません。根拠は ex08 の頭の注記と、元の proboscis-ema との同等の主張です。設計者が「進捗は答えではないので誤ってよい」とするなら、S2 は字面の上では保たれます。その場合でも、正しくするには知識の漏洩か M3 の変更が要る、という点は変わりません。
- **不足する情報:**
  - 本物の backend での振る舞い(mock でしか試していない)。
  - 端末(TTY)での見え方(stderr を file に取っただけ)。
  - 進捗の handler を M2 の部品として他の pipeline にも使い回す意図があるか。
- **探していない似た候補:** HistoryEntry には `event="retried"` と `attempt` の欄がありますが、書く handler がありません(collection.py 28〜30 行)。S1 の retry を Inspect の履歴に残すには、M3 の契約の拡張が要りそうです。調べておらず、推測です。

WAIT: work — 呼び出し元の次の指示
