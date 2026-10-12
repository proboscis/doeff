## 盲検 B の答え: import の口が、今の版で読めない「古い世代」の entry を片づける

検査を全部通るのに責務を侵す変更を 1 つ見つけました。全部の検査を走らせたわけではないので、各項に「実測」か「推論」かを付けています。

### 1. 機能要求と差分

**要求(現実にありそうな形)**
- 記録の印(RECORD_TAG)を上げた後、鍵の dir に今の版では読めない古い世代の entry が残ります。
- import の口(loader_hooks)は compile の前に、それを毎回 marshal で読んでから捨てています。`CODE_ENTRIES_PER_KEY` の註にある「全部を照らす手間」で、上限 16 も埋めます。
- `records.RECORD_TAG` の註(印 2〜5)は「compile し直した code が上書きするので、古い世代の file が保存先に残らない」と書いています。鍵の印 3 の並べる形ではこの性質が失われたので、取り戻したい、という要求です。

**差分**(`packages/doeff-hy/src/doeff_hy_bytecode_guard/loader_hooks.py` の `_to_shared_store` だけ。実物は `/tmp/tmp.djdDeGE02Y/proposed.diff`)
```diff
     if records.record_of(code) is None:
         return
-    code_store.add_code(store, _store_key(fullname, path, source), code)
+    key = _store_key(fullname, path, source)
+    code_store.add_code(store, key, code)
+    _drop_older_generations(store, key)
+
+
+def _drop_older_generations(store: str, key: str) -> None:
+    """鍵 key の entry のうち、今の版の記録の形で読めない物(記録の印の古い世代 — records.record_of が None)を除く。…"""
+    from doeff_hy_bytecode_guard import code_store, records  # 保存先を使う時だけ読む
+
+    for entry in code_store.code_entries(store, key):
+        stored = code_store.stored_code(entry, touch=False)
+        if stored is not None and records.record_of(stored) is None:
+            code_store.discard_entry(entry, "今の版の記録の形で読めない古い世代の entry")
```
- 使っているのは M1 と M2 の公開の名(`code_entries`・`stored_code(touch=False)`・`discard_entry`・`record_of`)だけです。
- 並びの綴りも、使った印の誤りも、型の回避もありません。壊れた entry を「名指しの 1 行で除く」作法にも合っています。

### 2. 本来の責務の持ち主と、侵す契約

**判断が移る**
- どの entry が死んでいて消せるかは、本来 M1(上限を越えたら使った時刻の古い物から)と M5(7 日動いていない物)が持つ判断です。どちらも「誰かが使った時刻」だけで決め、書き手 1 つの視点を持ちません。
- この差分ではその判断が M3 に移ります。

**意味のすり替え**
- M2 の `record_of` が None なのは「今の環境の記録の形でない」という、環境ごとの答えです。
- M3 はそれを「もう誰も使わない」という全体の判断に読み替えます。

**入り込む知識**
- 「記録の印が自分と違う書き手は生きていない」という、書き手の集団についての仮定です。どの doeff の版の作業木や root が動いているか、という知識にあたります。
- 設計はこの知識をどこにも持たせず、使った時刻で代えていました。

**侵す契約**
- R2b「他の環境の entry は上書きしない」と、S2 の主張: 除くのは上書きと同じ結果になります。
- S3 の主張「RECORD_TAG を上げても M1・M3 は変わらず、版が違っても効く」: 印を上げた瞬間に R1・R2 が壊れます。
- M1 の除く順と M5 の「使った物は消さない」: 数秒前に使われた entry も消えます。
- `discard_entry` の契約(壊れた entry 用・呼び手は作り直す): 生きた entry を除く口に流用しています。

**起きること**
- 印 6 の書き手と印 7 の書き手が互いの entry を消し合い、毎回焼き直す。これは 2026-10-12 の zeus の上書き合戦と同じ形です。
- git log では RECORD_TAG は 9/29〜10/11 の 13 日で 5 回上がっており(5 は 10/11 10:15、6 は 10/11 21:32)、次の引き金は現実的です。

### 3. 提示された検査がこの差分を拒まない理由

| 検査 | 結果 | 実測/推論 |
|---|---|---|
| `test_code_store_entries.py`(8 件) | 改変した写しで 8 passed。code_store.py は変えておらず、除きは M1 の外にある | 実測 |
| `test_bytecode_guard.py` の `two_macro_versions`・`new_tree`×2 | 改変した写しで 3 passed。2 つの書き手は macro の中身だけが違い、doeff-hy は同じ(GUARD_ROOT 1 つ)なので RECORD_TAG も同じ。`record_of` が None にならず、除きは一度も動かない | 実測 |
| `test_bytecode_guard.py` の残り 16 件 | venv の .pth が要るので外した。どれも doeff-hy は 1 つ | 推論 |
| `test_code_prepare_content_store.py`(test_e ほか) | 書き手はすべて同じ doeff-hy。test_b が「2 回目は全部 stored」を確かめるので、M4 の entry にも記録があるはずで、除きは動かない | 推論(未実行) |
| `test_env_store.hy` の掃除の検 2 本 | tmp の file で M5 だけを見ており、loader_hooks を通らない | 推論(未実行) |
| ADR の冊(法 one-bytecode-store-entry) | `".code"` / `".imports"` の綴りを探す正規表現を写して当てると、改変後も False。ほかの deftest が読む boot.sh・code_prepare.hy・env_upkeep.hy は触っていない | 正規表現の写しで実測。deftest そのものは未実行 |
| pre-commit の semgrep(Python) | lock の 1.169.0 と repo の `.semgrep.yaml` を、同じ相対 path に写した木で走らせた。改変前も後も所見 0。基点にも loader_hooks の行は無い | 実測(hook_finding_baseline.py 自体は未実行) |
| pre-commit の semgrep-hy・semgrep-other-files・doeff-linter-doeff-cluster・enforcement-ledger | 差分は `.py` の 1 file で、どの項の files 条件にも当たらない | 設定から読んだ事実 |
| pre-commit の doeff-linter(根) | HEAD の鍵の binary がこの環境に無いので、script の作りでは「測れない」と名乗って通る。古い断面 86ecd134 で測ると error 0 → 0(info の DOEFF022 が 1 増えるだけ) | HEAD の binary では未確認 |
| code-quality(fast の段) | repo の中に定義が見つからなかった | 未確認 |

### 4. 検査を通ることと責務違反の両方を確かめる手順

置き場は `/tmp/tmp.djdDeGE02Y`(自分で作った dir で、残してあります)。
- A6 = 27f354830 の写しのまま、B6 = 1 の差分を入れた写しです。
- A7・B7 はそれぞれの RECORD_TAG を `.../7` にだけ変えた物です(S3 の印上げだけ)。
- package は helpers.hy・macros.hy・user.hy で、macro は展開のたびに `expansions.log` へ 1 行書きます。両方の世代で中身は同じです。
- 毎回新しい木を作り、1 つの保存先で 6 と 7 を交互に 3 巡読みます。

```
T=/tmp/tmp.djdDeGE02Y
cd $T && DOEFF_HY_CODE_STORE=off uv run --no-project python driver.py $T s3    # 世代 6/7 の交互(A と B)
cd $T && DOEFF_HY_CODE_STORE=off uv run --no-project python driver.py $T same  # 同じ世代・macro の中身 1 と 5(既存の検の筋書き)を B で
cd $T/repoB && DOEFF_HY_CODE_STORE=off PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$T/repoB/packages/doeff-hy/src \
  uv run --no-project --isolated --python 3.14 --with pytest python -m pytest -p no:cacheprovider --basetemp=$T/pytest-tmp -q \
  packages/doeff-hy/tests/test_code_store_entries.py packages/doeff-hy/tests/test_bytecode_guard.py -k "code_store_entries or two_macro_versions or new_tree"
```

**実測の結果**(展開の回数。並びは 1 巡目 6, 7 → 2 巡目 6, 7 → 3 巡目 6, 7)

| 筋書き | 結果 |
|---|---|
| A(元のまま)・世代 6/7 | 1, 1, 0, 0, 0, 0。S3 の主張どおり 2 巡目から焼き直さない |
| B(差分)・世代 6/7 | 1, 1, 1, 1, 1, 1。毎回の読みが相手の世代の entry を 4 つ除き、「今の版の記録の形で読めない古い世代の entry」の名指しの行が出る。どちらの世代も、直前に使われたばかりの entry を除いている |
| B・同じ世代(既存の検の筋書き) | 1, 1, 0, 0, 0, 0。名指しの行は 0。既存の検はこの違いを見分けない |
| pytest の 11 件 | 11 passed, 16 deselected |

見分けに要る最小の入力は「RECORD_TAG の違う 2 つの書き手が、同じ鍵を交互に読む」形です。提示された検はどれも書き手の doeff-hy が 1 つなので、この入力を持っていません。

注意: この環境の `DOEFF_HY_CODE_STORE` は本番の共有の保存先 `/work/state/doeff-hy-code-store` を指しています。改変した loader_hooks を env を上書きせずに走らせると、その保存先の entry を実際に消します。上の手順はどれも上書きしてあります。

### 5. 確かめていない前提と、足りない情報

- **世代 7 は模擬です。** RECORD_TAG の文字列だけを変えた物で、本物の印上げは欄や digest も変えます。合戦が起きる仕組みは「`record_of` は tag が自分の RECORD_TAG と同じ時しか記録を返さない」(records.py 155〜167 行)だけに依るので、結論は変わらないと見ていますが、これは推論です。
- **本番の準備の道具への波及は推論です。** M4 自体は除きませんが、別の世代の作業木の M3 が準備の entry を除くので、準備が焼き直します。2 世代の準備の道具を回す実験はしていません。
- **今日すでに起きるかは未確認です。** 古い版の root(印 5)の準備が、今も印 3 の鍵へ entry を足しているかは確かめていません。足しているなら、RECORD_TAG を上げなくても印 5 と印 6 の間で合戦が起きます。
- **実行の環境が本番と違います。** pytest は uv の isolated の環境と写しで走らせました。repo の .venv、根の conftest、3.14t ではありません。
- **未実行の検査:** doeff-cluster の 2 つの検、ADR の deftest そのもの、HEAD の鍵の doeff-linter、code-quality の 4 つです。上の表の「推論」と「未確認」はこれを指します。
- **探した範囲:** 却下した候補が 3 つあります。
  - add_code の中で印の違う entry を除く形: `test_writers_with_different_records_keep_their_own_entry_under_one_key` が捕まえます。
  - 記録に plain でない欄を足す形: 2 つの書き手の検が捕まえると読みました。
  - 呼び手が `stored_code` の既定(touch=True)で全部の entry の時刻を進める形: 通りそうですが、詰めていません。

WAIT: work — 依頼元(設計者)の次の依頼
