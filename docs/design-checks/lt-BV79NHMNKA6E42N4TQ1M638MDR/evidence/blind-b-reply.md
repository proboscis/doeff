責務を侵したまま §5 の検をすべて通る失敗例を 1 つ作り、検の通過と責務違反の両方を実際に走らせて確かめました。作業木には差分を当てたままにしてあり、同じ差分の写しを `/tmp/blind-b-b4c0/failure.diff` に置いています。commit・push はしていません。

## 1. 機能の要求と差分

**要求**: 同じ節の中で続き k を 2 回使う書き方を、展開の時に断る。非末尾の `(resume …)` は続きが終わると節へ戻るので、その後に resume・transfer・reperform を書くと k を 2 回使うことになります。今は実行の時に VM の内部の誤り `continuation already consumed (one-shot violation)` で落ち、どの節が悪いのか分かりません(`/tmp/blind-b-b4c0/probe_double.py` で実測)。ADR R15 の「展開の時に止める」と同じ方向の改善です。

**差分**(`packages/doeff-hy/src/doeff_hy/handle.hy` に 57 行を足すだけ。説明の文は省略):
```hy
;; 続き k を使う操作(再開する・外の handler へ渡す)。k は 1 度しか使えない
(setv _K-USING-OPS #{"resume" "transfer" "reperform" "pass"})
;; 続きが終わると節へ戻ってくる操作
(setv _RETURNING-OPS #{"resume"})
(setv _NOT-CLAUSE-STEPS #{"quote" "quasiquote" "fn" "fn/a" "defn" "defn/a" "lfor" "gfor" "dfor" "sfor"})

(defn _uses-op? [form names] ...)          ; form の中に頭の名が names の式があるか
(defn _resume-statement? [form] ...)       ; 値を捨てる (resume …) の文か(when / unless / if の枝も)
(defn _check-one-shot [where forms]        ; その文より後の文が k をまた使えば SyntaxError
  ...)
(defn _check-nested-one-shot [where form] ...)  ; do / when / unless / let / with の本体の列も見る
;; _build-clause の中、_check-clause-terminates の直後に
  (_check-one-shot where cbody)
```
既存の `_tail-resume-to-transfer` も `(= hname "resume")` と名を直に書いており、普通の実装者はこれに倣う、という想定です。

## 2. 本来の持ち主と、侵す契約

- **持ち主**は clause-ops-table(`clause_ops.hy`)です。操作の名と性質(Ending)はこの表だけが持ちます。handle.hy の termination-check と clause-rewrite は「どの名が操作か」を表に聞く側です。
- **handle.hy へ移る知識は 2 つ**です。
  - 「k を使う操作はどれか」の判断。表の Ending の RESUMES と FORWARDS から導けるのに、名の集まり `_K-USING-OPS` として写しています。前に消された写し `RESUMING-OPS` の作り直しです。
  - 「続きの後に節へ戻る操作は resume だけ」という新しい性質。表の欄にせず、名 `_RETURNING-OPS` で handle.hy に置いています。
- **侵す契約**:
  - R1(名と性質は表 1 か所)
  - R2 と S1 の主張(足すのは表の 1 行と rewrite だけ)
  - S3 の主張(消した操作はどの読み手からも操作でなくなる)
  - R3(読み手へ表を介さずに名を足すと検が赤になる)
  - clause_ops.hy の頭の書き「操作の名の一覧をこの表の外に写さない」

## 3. 提示された各検が拒否しない理由(すべて実測)

| 検 | 結果 | 拒否しない理由 |
|---|---|---|
| `test_readers_name_operations_only_through_the_table` | 通る | 新しく書いた綴りは候補として拾われています(実測)。ただし検が照らすのは `_terminates`・`clause_endings.clause_ops`・`_rewrite_ops` の 3 つだけで、新しい読み手 `_check-one-shot` は見ません。書いた名は全部表に在るので、3 つの読み手は表どおりに振る舞います。一時的に `"throw"` という表に無い名を集まりに足しても、この検は通りました(実測・元に戻し済み) |
| `test_added_operation_*` 4 件 | 通る | throw を使う節はどれも操作が 1 つだけです。値を捨てる resume の後に throw を書く形が無いので、新しい検めの写しの古さを踏みません |
| `test_every_operation_in_the_table_is_checked_by_its_ending`・`test_table_names_are_unique` | 通る | 節に操作が 1 つだけなので、新しい検めが一度も働きません |
| 既存の展開の形(4 file) | 92 passed | 最初の版は `(<- r (resume x))` も数えたため、`test_tail_resume_tco.py` の 2 件が赤になりました(実測)。値を捨てる文の resume だけを数えるように絞った今の版で通ります |
| ADR R15(`-k "clause_must_end_explicitly or clause_endings_checked"`) | 2 passed | — |

`tests/test_handle_clause_ops.py` は 12 passed でした。

## 4. 検の通過と責務違反を確かめる手順(この作業木で)

1. `uv run pytest -q tests/test_handle_clause_ops.py` → 12 passed(実測)。ADR と既存の展開の検は上の表のとおり。
2. `uv run python -I -W ignore::DeprecationWarning /tmp/blind-b-b4c0/demo.py` を走らせる。実測の出力は次のとおりです。
   - **今の表(機能は動く)**: `(resume 10) (resume 20)`・`(resume 10) (transfer 20)`・`(resume 10) (reperform effect)` は、どれも展開の時に「続き k を 2 度使う」で断られます。
   - **S1**(検の `_THROW` と同じ行を表に足し、読み手は直さない):
     - `(resume 10) (throw (ValueError "x"))` は展開を通り、実行の時に `continuation already consumed (one-shot violation)` で落ちます。
     - 比べると、表を引く `(Raise [reason] (throw …))` の検めはちゃんと追従して断ります。
     - 追従しないのは新しい検めだけで、盲検 A と同じ「検めが黙って抜ける」形です。
   - **S3**(表から pass の行を消す):
     - `named-op('pass')` は None、`_terminates((pass))` は False で、表の上では操作でなくなります。
     - それでも `_check_one_shot((resume 10) (pass))` は「k を使う」として SyntaxError を出します。
     - この状態で `test_readers_name_operations_only_through_the_table` は通りました。

## 5. 確かめていない前提と足りない情報

- **推測**: reperform と pass が k を外の handler へ渡すので、resume の後に書けば k を 2 回使うことになる、という理解。展開の時の拒否は実測しましたが、`(resume 10) (reperform effect)` を実行した時の動きは測っていません。
- **走らせていない物**: 全体の検、bytecode-guard の検(S4)、doeff-hy-check や pyright で repo を読む検、他の ADR の law や semgrep。これらが拒否するかは分かりません。
- **型検査のための展開**: 新しい検めの動きを、この展開について単独では確かめていません(`test_static_check_defhandler.py` が通ったことだけ)。
- **正しい形の実装は作っていません**: 表に欄を足すか、`ops-ending` で Ending から引く形が本来の在り方ですが、それが §5 を通るかは確かめていません。
- **作業木の状態**: 失敗例の差分を当てたまま残しています。`/tmp/blind-b-b4c0/` には次を置きました。
  - `demo.py`(手順 2 の確かめの script)
  - `probe_double.py`(2 回目の resume を実行した時の誤りを見る)
  - `probe_bind_resume.py`(`(<- r (resume x))` の後の transfer の動きを見る)
  - `handle.hy.diffed`(差分を当てた handle.hy の写し)
