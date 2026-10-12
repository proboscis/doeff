## 失敗例: resume と同じ扱いの `(throw 例外)` を足すと、clause-rewrite(handle.hy)の変更が要る

結論として、表に 1 行足すだけでは済みませんでした。末尾の throw を handler を外す形にするには、主張では「変えない」はずの handle.hy の `_tail-resume-to-transfer` を変える必要があります。加えて、行の書き換え関数が handle.hy の中の変数名を知らないと、実行の時の検めが誤って止めます。どちらも作業木で再現しました。

### 1. 変更要求とその理由
- **要求:** Hy の handler に `(throw 例外)` を足す。扱いは resume と同じにする。
  - 末尾でない所では `ResumeThrow` にする。続きへ例外を投げ、続きが終わったら節へ戻る。handler は残る。
  - 末尾では `TransferThrow` にして handler を外す。
- **理由:**
  - VM は `Resume`/`Transfer` と対で `ResumeThrow`/`TransferThrow` を出しています(`doeff/__init__.py:36-38`)。
  - Python の handler はすでに `yield ResumeThrow(k, exc)` を使っています(設計検証 lt-H07C2RG4T2BM59EENGQWQHSJKH の failure_budget)。
  - `doeff/do.py:162` は ResumeThrow を「末尾でないと handler の frame を残す」物として扱い、TransferThrow を勧めています。
  - 続きの `try` へ例外を返したい要求(retry や時間切れ)は、Hy の handler でも自然に出てきます。

### 2. 元の主張・前提と、予想に反して変わる所
- **前提を守っているか:**
  - S1 の前提「`(名 引数 …)` の形で、終わり方は 4 つの Ending のどれか」は守っています。throw は `(throw 例外)` の形で、終わり方は `Ending.RESUMES` です。
  - R4(既存の展開を変えない)も守っています。
  - S1 の文言は TransferThrow だけの版です。この例はそれと並ぶ別の版なので、S1 の文言そのものには当たりません。ただし R1・R2 と、S1 の一般化した主張(「表の 1 行と rewrite だけ。clause-rewrite と clause-endings は変えない」)の範囲には入ります。
- **予想に反して変わる所:**
  - clause-rewrite の `_tail-resume-to-transfer`(`handle.hy:374-375`)。ここに `"resume"` を `"transfer"` にする対応が、名前で直に書かれています。
  - 実行の時の検めの約束(`handle.hy:592-594` の `_doeff_clause_resumed`)。表の行の書き換え関数がこれを知っている必要があります。

### 3. 漏れている知識
- **(a) 末尾の形の対応が表の外にある**
  - 「resume の末尾の形は transfer」という操作の性質が、表ではなく handle.hy に在ります。R1(名と性質は表 1 か所)に反します。
  - 表の `ClauseOp` には「末尾の形」の欄がありません。そのため直し方は 2 つしかありません。
    - handle.hy に名を直に足す。これは漏れを広げる直し方です。
    - `ClauseOp` に末尾の形の欄を足し、`_tail-resume-to-transfer` がその欄を読むようにする。これは意図した公開契約の拡張で、一度で済む正当な直し方です。それでも、S1 の「clause-rewrite は変えない」には反します。
- **(b) 実行の時の検めが、宣言されていない約束に頼っている**
  - 実行の時の検め(節が resume も finish もせずに抜けたら RuntimeError)は、次の約束に頼っています: 「節へ戻る操作の書き換えは、handle.hy の局所の名 `_doeff_clause_resumed` を立てる」。
  - この約束は、表と handle.hy が共に守る不変条件です。ところが `ClauseOp` の欄にも Ending にも書かれていません。`Ending.RESUMES` は「節へ戻る(Resume / ResumeThrow)」と「戻らない(Transfer / TransferThrow)」を区別しないからです。
  - その結果、handle.hy が隠すはずの名が、表の行の書き換えへ漏れています。`_resume` の docstring には書いてあるので、`_resume` を写す人なら気づける、と推測します。
  - 検の fixture `_rewrite_throw` は戻らない TransferThrow なので、この約束を一度も試しません。
- **(c) 検が (a) を見逃す**
  - handle.hy に `"throw"` を `"transfer-throw"` にする対応を直に書いても、R3 の検 `test_readers_name_operations_only_through_the_table` は赤になりません。名がどちらも表に在るからです。
- **(参考)知識の漏れだが、複数 module の変更ではない例**
  - S3 自身の例「型検査のための展開の resume の node を替える」は、resume の行だけ替えても末尾の resume には効きません。末尾の resume は transfer の行が展開するからです。
  - 直すのは表の中の 2 行で済みます。ただ、clause-rewrite の末尾の変換を知らないと、直す行を間違えます。

### 4. 再現手順と観測した結果(すべて実測)
作業木で `uv run python -I /tmp/blind-a-b4c0/exp_throw.py` を実行します。表への行の追加は、検の fixture と同じく `CLAUSE_OPS` を差し替えて行います。

| 区 | 何をしたか | 観測した結果 |
| --- | --- | --- |
| 0 | 既存の resume を展開した | 末尾の resume は `Transfer` / 型検査のための展開では `typed_transfer`。末尾でない resume は `Resume` / `typed_resume` |
| 1 | S1 の形の書き換え(印を立てない)で throw を ResumeThrow にした | 続きの `try` が例外を受けて終わった後に、`RuntimeError: handle の節 Bump が resume / transfer / finish / reperform のどれにも届かずに抜けた`。節は実際には続きを再開している |
| 2 | `_resume` を写して印を立てた | 走るが、末尾の throw は `ResumeThrow` のまま。effect 10 / 100 / 400 回で、生きた生成器が +12 / +102 / +402 と増え続けた。末尾の resume は +2 で一定 |
| 3 | `/tmp/blind-a-b4c0/hardcoded_tco.patch` を当てた(throw と transfer-throw の行を足し、handle.hy に名で直に対応を書いた) | `tests/test_handle_clause_ops.py` は 14 passed(赤にならない)。`/tmp/blind-a-b4c0/exp_after.py` では、末尾の throw が +2 で一定になった。`packages/doeff-hy/tests/test_tail_resume_tco.py` は 10 passed |

- 区 2 の積もり方は、agora-redesign #3530 と同じ形です。
- 試した後、patch は `git apply -R` で戻しました。作業木の `git diff` は空です(未追跡は BLIND_INPUT.md だけ)。

### 5. 未確認の前提と不足する情報
- throw を ResumeThrow で入れる要求が実際に計画されているかは知りません。VM の対と Python の handler の使用例から、自然な要求だと推測しています。設計者が throw を TransferThrow だけに限るつもりなら、この例は S1 の文言の外です。それでも R1・R2 の範囲には入ります。
- 生成器の数は gc で数えた実測です。メモリの量や、長く動かした時の実害は測っていません。
- `doeff/do.py` の「末尾でない」警告が .hy の handler では出ない事は、コードを読んだ上での推測です(`co_filename` が `.py` でなければ解析しない)。実測していません。
- 型検査のための展開で `ResumeThrow` が pyright を通るかは確かめていません。`ClauseRun = Generator[object, object, object]` なので通ると推測します。typed 版の throw が要るかも確かめていません。
- 続きが例外を受けずに抜ける時の動きは測っていません。
- 走らせた検は `test_handle_clause_ops.py`(元の状態で 12 passed、patch 後 14 passed)と `test_tail_resume_tco.py` だけです。ADR の検、`test_handle_macro.py`、全体の検は走らせていません。
- S4(bytecode-guard)は探していません。
- 以上は失敗例の報告です。この設計が合格・安全だという結論ではありません。

WAIT: work — 呼び手の次の依頼
