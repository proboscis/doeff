## 責務分離の失敗例:正本が幅の受け渡しを env から外すと、doeff の検が赤になる

この失敗例は、設計の S3 の主張(正本の中で env を替えても「doeff は env の名を持たないので変わらない」)を崩します。doeff の src(M-slot・M-main)は主張どおり変わりません。一方で doeff の検(M-slot-scene)が、正本の中の知識を 2 つ写して持っています。幅を運ぶ env の名と、入れ子の規則が返す値です。そのため、dotfiles だけを変えても、pin で固定した doeff の検が赤になります。

### 1. 変更要求と、それが要る理由

**要求**:外側の入口が子へ配った小さい幅を、env を消された子孫でも失わないようにする。具体的には、正本が幅の受け渡しを env `AI_CHECK_SLOT_PROCESSES` から外し、祖先の列(/proc で辿る親の process)から読める所(保持者の枠の file など)へ移す。これは S3 の後半「env をやめる」そのものです。

**理由(実測)**:試作の正本の写しで、外側が幅 1 を配った後に、途中の道具が `AI_CHECK_SLOT_*` を通さず子を起こす形を作りました。
- doeff の `take_slot` が受ける幅は、env を消さなければ `1`、消すと `2`(宣言の値に戻る)でした。
- 枠そのものは祖先の列で「内側」と判定されるので、待ちも 2 つ目の枠も出ません。
- 正本は枠の判定では「env を消した子でも届く」ように祖先の列を使っていますが(`machine_check_slots.py` 200–209 行)、幅だけが env に頼っています。運び手を揃えたくなるのは自然な保守の要求です。

### 2. 関係する主張・前提と、予想に反して変わる所

元の主張(`design-pre-blind.md`):
- S3:「変わる所 = M-canon(と env を読む他言語の入口)/ 変わらない所 = M-slot・M-main(doeff は env の名を持たない)」
- S2:「入れ子の規則を替えても正本の中だけで終わる」
- 責務の表:M-slot-scene の「持つ知識: —」
- I2:「env の名・入れ子の規則は M-canon にだけ在る」
- §7 の予定:「正本の変更を先に着地し、次に doeff の pin を進める」

前提は守っています。変えるのは正本の中だけで、doeff は pin のまま、機体の `~/dotfiles` が先へ進む、という形です。契約の範囲内の例です。

予想に反して変わる所:
- **`packages/doeff-hy/tests/test_static_check_slot.py`(M-slot-scene)**
  - 383 行:外側の入口の役をする中継が、env の名 `AI_CHECK_SLOT_PROCESSES` を文字列で直に書いています。
  - 392–415 行:期待する値 `"2"`・`"1"` は、正本の入れ子の規則が返す値そのものです。
  - 34 行と 120 行:検は機体の正本(`Path.home()/dotfiles/…`)をその場で写して使います。このため dotfiles の先端が変わるだけで、doeff の commit の無いまま赤になります。
- **`tests/test_check_slot_opacity.py` の `CANON_OWNED`**:正本の名の写しなので、新しい名を足す必要があります。これは設計が認めた限界ですが、S3 の「変わる所」に挙がっていません。

### 3. どの知識がどこへ漏れているか(契約の拡張との区別)

漏れている知識:
- **幅を子へ配る道具(env の名)**:正本の公開の形では、`PROCESSES_ENV` は「他言語の入口のため」とされています。Python の doeff の検が文字列で直に書くのは契約の外の使い方で、漏洩にあたります。
- **入れ子の規則の答え**:「包みの中は 2」「1 を配られたら 1 を残す」は正本の規則が返す値です。doeff の検がその値を固定して確かめています。
- **原因**:正本には幅を「受ける口」(`process_width`)はありますが、Python の入口が子へ「小さい幅を配る口」がありません。`run_held` は同じ幅を配るだけです。だから外側の入口の役をする所は、どこでも env を直に書くしかありません。読んでよいコードの中で、その役をしているのは検の中継だけでした。
- **守りの検の届く範囲**:名の走査は `src/doeff_hy` だけを見ており(221–236 行)、tests の漏洩は検出されません。

漏洩ではない物(区別):
- **公開契約の変更(欠陥ではない)**:agora の `node-test.mjs` の `CONCURRENCY_ENV` が変わるのは、他言語の入口向けの公開契約の変更で、S3 も挙げています。
- **配線**:doeff の src の配線は変わりません。改名後も、受ける側の検(`test_the_width_follows…`)は緑でした。
- **共同の不変条件**:I1(1 枠の木の並走 ≤ 宣言)は正本と入口が共同で守る物です。ただし、入れ子の時の期待値は正本の検に既に在ります(`test_a_nested_wrapper_keeps_the_smaller_width_its_caller_handed_down`)。doeff が持つべきなのは「正本の答えを変えずに受けて使う」ことだけで、その検は口だけの正本(幅 7)と `test_the_entry_hands_the_slot_width_to_the_expanders` で既に足りています。

### 4. 最小の再現と観測

`uv run` は doeff の木に `.venv` を作るので使っていません。代わりに次の形で走らせました。
- python は別の木 `doeff-wt-check-slot-ppslot/.venv` の物を使い、`PYTHONPATH` を盲検の木の `packages/doeff-hy/src` に向ける。
- 検の file を `/tmp/ppslot-blindA-sep/t/` に写す。
- `PYTHONDONTWRITEBYTECODE=1` と `-p no:cacheprovider` を付け、`-k "width_follows or smaller_width"` の 2 本だけを選ぶ。

正本の 3 つの写しで比べました。

| 正本の写し(HOME の下) | 変更 | `…width_follows…` | `…handed_down` |
|---|---|---|---|
| H0 | 試作 4b9b243b9 のまま | 緑 | 緑 |
| H1 | 73 行を `PROCESSES_ENV = "AI_CHECK_SLOT_WIDTH"` に改名(S3 の最小の代わり) | 緑 | **赤**(stdout `2`・期待 `1`) |
| H2 | 463 行を `return max(1, int(text) - 1)` に変更(S2 の例。0 にならないよう 1 で止めた) | **赤**(`1`・期待 `2`) | 緑 |

そのほかの観測:
- H1 の正本の側:正本の検の ⑹ と同じ論理を、名を `slots.PROCESSES_ENV` で引く形で /tmp に写して走らせると緑でした(配った 1 がそのまま届く)。dotfiles では緑、doeff だけが赤、という形です。
- 守りの検:`canon_owned_names('X = "AI_CHECK_SLOT_WIDTH"')` は `[]` でした。新しい名は捕まりません。
- 共有の 2 つの木の `git status` は空のままです。

### 5. 推測と未確認

- **実測**:上の表、env を消した時の幅の比較、守りの名の写しの確認。
- **推測**:
  - 「env をやめる」本体は実装していません。中継の env が無視されるのは改名と同じ理屈なので、同じく赤になると見ています。
  - 木全体の並走が宣言を越える(1 + 2 > 2 など)ことは、幅の答えから推した物で、process の数は測っていません。
- **未実行**:
  - dotfiles の本物の pytest(agentcli の環境が要るので、論理の写しで代えました)
  - 盲検の木での `uv run pytest`
  - code-quality の検査
- **不足している情報**:
  - doeff の日次の pytest が、dotfiles の先端を追う機体で走るか。
  - agora-controllers の gate や hy-type-trend など、実際の Python の外側の入口が幅を分けて配っているか(読んでよい範囲の外)。
  - 実際の経路(uv・poe・long-command など)に env を絞る道具があるか。
  - 使った venv の doeff と doeff_core_effects は試作 0dfe61d56 の木の物です。盲検の木との差は文だけと聞いているので結果は変わらないと見ていますが、推測です。
- **見たが詳しく調べなかった候補**:
  - `_positive` は正本の答え 0 を「形の誤り」として None にし、展開の子が既定の 4 本に上がります。幅の値の範囲が、誰も書いていない共同の約束になっています。
  - S1 の「別の機体の種類で json を 1 行変える」は、宣言が全機体で共有の 1 file なので、そのままでは成り立ちません。ただし直しは正本の中で済み、doeff は変わりません。
  - 入口ごとに違う幅(node と doeff)は、整数 1 つの口では表せません。契約を広げるか、正本が入口の名を知るか、のどちらかになります。

実験に使った物は `/tmp/ppslot-blindA-sep/`(正本の写し H0・H1・H2、写した検)と `/tmp/ppslot-blindA-sep-scripts/`(`canon_side.py`・`scrub_probe.py`・`patch_s2.py`)にあります。

WAIT: work — 依頼元(設計検証の会話)の次の依頼
