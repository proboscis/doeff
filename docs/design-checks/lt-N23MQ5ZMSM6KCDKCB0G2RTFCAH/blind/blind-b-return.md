# ADR-DOE-AGENTS-012 R49 の巡回の検査を通るのに責務を破る反例(盲検 B)

反例は見つかりました。巡回は一覧の行で判断し、閉じる status も一覧の行から組みます。鍵での読み直しは、書く直前に新しい generation を得るためだけに使います。この形では提示された 2 本の検査が通り、R49 の振る舞いの検査 7 本も通ります。一方、別の書き手と競合すると、ended の記録への二重の書きと usage の消去が実際に起きました。どちらも作業樹で実行して確かめ、作業樹はその後元に戻しました(`git status` は空・版は b7f836b0)。

## 1. 機能要求と差分

**要求:** 巡回が候補ごとに記録と対の agent-job を 2 回 GET しています。走っている記録の大半は生きている別の機体のもので、判断は skip です。一覧の行は `status.state=running` で引いた直後の image なので、判断と閉じる status はそれで組み、閉じると決めた記録だけを書く直前に鍵で読み直したい、という性能改善です。読み直しの目的は「一覧の generation は古く Conflict が続くので、新しい generation で CAS する」とします。これはよく知られた「書く直前に版を取り直す」形です。`AcpRunningTurnRecords` の docstring に「読んだ行は判断の材料で、書く相手ではない」とあるので、書き手は許されていると読みやすいです。

**差分**(W = `agentd.hy` の `sweep-turn-records` だけ。M1 と M1c は変えない):

```hy
-      (.append candidates listed-id)))
+      (.append candidates row)))
 ...
-    (for [job-id candidates]
-      (<- key str (turn-record-key-of job-id))
-      (<- record (| AcpRow None) (AcpGetRow :key key))
+    (for [listed-row candidates]
+      (setv job-id (.get listed-row.spec "agentJobId"))
       (<- pair (| AcpRow None) (AcpGetRow :key f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{job-id}"))
-      (when (isinstance record AcpRow)
-        (<- verdict str (turn-record-sweep-verdict record pair settings.node-name live-nodes mine))
+      (<- verdict str (turn-record-sweep-verdict listed-row pair settings.node-name live-nodes mine))
+      (setv record-node (.get listed-row.spec "node"))
       (if (= verdict TURN-RECORD-SWEEP-END)
         (do ... pair-conditions は今日のまま ...
-          (<- record-status dict (status-object-of record))
+          (<- record-status dict (status-object-of listed-row))
           (<- ended dict (turn-record-ended-status record-status None #() pair-conditions))
+          ;; CAS は鍵で読み直した行の generation で(一覧の generation は古く Conflict が続く)
+          (<- key str (turn-record-key-of job-id))
+          (<- record (| AcpRow None) (AcpGetRow :key key))
+          (when (isinstance record AcpRow)
             (<- wrote (| Written Conflict Refused) (AcpPutStatus :row record :status ended))
             ... 数え・log・計器は今日のまま ...)))
```

## 2. 本来の責務所有者と破る契約

- **所有者:** W(巡回の腕)は、読み直した行を唯一の材料にして J(判断・status の組み方)へ渡し、その行の generation で CAS する責務を持ちます。変更後は次のように移ります。
  - 判断の材料は、正本の行から一覧の image(E の `AcpRunningTurnRecords` の答え)へ移る。
  - 書く中身も一覧の image から組まれる。
  - 読み直しは、判断より後に取る CAS 用の generation だけになる。その結果 ifGeneration が何も守らない。
- **破る契約:**
  - R49 (a) の "reads the row by key before writing, and writes with ifGeneration so two agentd never write twice"。
  - 固定要件 1 の「一覧の image で書かない」。
  - J の判断の条件 1「ended の行は触らない — 冪等」。
  - 巡回の docstring の「二重の書きは無い」。
  - usage の書き手は手番の終わりの 1 回だけ、という所有。status は丸ごと書かれるので、巡回が usage 欄を消すことも usage への書きです。
- **M1 の事前の主張への反証:** M1 は「一覧の image で書く変更は赤」と主張していましたが、今回は一覧の image が中身として書かれ、M1 は緑でした。

## 3. 各検査がこのコードを拒否しない理由

**検査 1(M1 を呼ぶ 1878〜1886 行)**
- `turn-record-ended-status` の呼びは usage=None、entries=`#()`、responses は既定の None なので、どの失敗文も出ない。呼びも 1 つ以上ある。
- `AcpPutStatus` の row は記号 `record` で、`bindings-of` が見る束ねは `(<- record … (AcpGetRow :key key))` だけ。`key` の束ねは `(turn-record-key-of job-id)`。よって「読み直した image」と「記録の鍵」の両方を満たす。
- M1 が読まないものは 4 つ:
  - `turn-record-ended-status` の `status` 役の出所
  - `AcpPutStatus` の `:status` の出所
  - `turn-record-sweep-verdict` の引数
  - 読み直しと判断の順序(`bindings-of` は順序も入れ子も見ない)
- 同じ検査の他の行(綴りの固定)も変わりません。`(defk sweep-turn-records [settings state now-ms]`、`(<- listed tuple (AcpRunningTurnRecords))`、拍からの呼びはそのまま残し、effects と judgment には触れていません。

**検査 2(M1c)**
- `synthetic-sweep-forms` の作り物の本体だけを読み、本物の本体は読まない。W の変更は届かず、12 例とも期待どおりでした(実測)。

## 4. 再現の手順と期待結果(実測)

1. 変更前に指定のコマンドを実行: 2 passed(初回は環境づくりを含めて 214.83 秒)。
2. 差分を当てて同じコマンドを実行: 2 passed(5.82 秒)。M1c の出力は正常例 5 件が緑、違反例 7 件が意図した文の赤。
3. 読み込まれた本体が変更後のものかを確認: `body-of sweep-turn-records` に `(turn-record-sweep-verdict listed-row` と `(status-object-of listed-row)` があることを確かめた。
4. 振る舞いの検査を実行: 課題にある `.hy::名前` の指定は収集されず "not found" でした。収集の入口は `packages/doeff-agents/tests/test_sessionhost_acp_turn_events.py` です。そこから R49 の 6 本と `test_the_sweep_copies_the_conditions_of_the_pair_and_writes_none_without_one` を実行し、7 passed。
5. 競合の再現(一時スクリプト。実行後に削除済み):
   - 記録 j-2 は自分の node で running、対の agent-job は Ended。
   - `FakeAcp` を継承し、`_running_turn_records` が一覧を返した直後に別の書き手の ended を 1 度だけ `_put_status` で着地させる。
     - S1: `{"state":"ended","usage":{…}}`(停止した前の書き手や、lease の切れた生きている機体による手番の終わりの書き)。
     - S2: `{"state":"ended"}`(別の agentd の巡回)。
   - その状態で `run_tick` を 1 回実行する。

   | 場面 | 変更前 | 変更後 |
   | --- | --- | --- |
   | S1 | 記録への書き 1 回(別の書き手のみ)、巡回の ended の計器 0、usage は残る | 書き 2 回、最終 status `{'state': 'ended', 'entries': []}`、generation 3、**usage が消えた** |
   | S2 | 書き 1 回 | 書き 2 回(ended の記録への**二重の書き**) |

   再現の核は次の継承クラスです:

```python
class RacingAcp(FakeAcp):
    def _running_turn_records(self) -> tuple[AcpRow, ...]:
        listed = super()._running_turn_records()
        if not self.raced:
            self.raced = True
            self._put_status(self.rows[RECORD_KEY], self.other_status)  # 一覧の後・読み直しの前に着地
        return listed
```

## 5. 未確認の前提と不足(推測と実測の区別)

**推測:**
- 本物の engine がこの二重の書きを断るかどうか。欄ごとの書き手判定は、両方とも agentd なので通ると推測しています。fake には status の欄の所有者の判定がありません。
- 本番での競合の頻度。死んだ node の記録は生きている全 agentd が閉じてよいので、複数の agentd が同時に END と判じ得ます。変更後は競合の窓が「一覧から読み直しまで」に広がり、候補を順に処理する時間も含みます。頻度は測っていません。
- 性能改善の効果(GET の削減量)も測っていません。
- 別の形(判断だけ一覧で行い、status は読み直した行から組む)は実行していません。二重の書きは起きるが、usage は `dict(status)` の写しで残ると推測しています。

**コードを読んだだけで実行していない、M1 の他の穴:**
- `status` 役の出所を見ないので、`(| record-status {"usage" u})` のように渡すと usage が書かれる。
- `AcpPutStatus :status` を追わないので、`(| ended {...})` のように後から足す加工が通る。
- 禁止の役の表に `cache-observation` が無い。
- 既定値つきの新しい役に消費を載せても、M1 自身の正常例の設計どおり緑になる。
- `bindings-of` は `<-` と `setv` しか見ず、`for` / `let` / 分割での束ねは見えない。
- 本体から呼ぶ補助の defk の中は読まない。

**実行していないもの:** 全数の pytest、pyright、semgrep、doeff-linter。いずれも提示された検査ではないため、通るかどうかは述べません。
