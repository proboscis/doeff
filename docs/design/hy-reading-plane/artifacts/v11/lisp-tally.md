file 158 本・本体の行 12264・lisp の目印が残る行 3524

| 頭の form | 行 | 例 |
|---|---:|---|
| `式の中の島: if` | 342 | `wake-at=(if wakes (min wakes) None),` |
| `setv` | 260 | `(setv (get located r.id) #(owner rows r))))` |
| `<-` | 235 | `(<- body str (card-document-of card.body))` |
| `do` | 159 | `(do (val card (get by-key candidate.card))` |
| `True` | 129 | `True` |
| `=` | 125 | `(= verdict VERDICT-NO-RULE) (.append no-rule target.card)` |
| `:=` | 112 | `(:= posted (+ posted 1)))` |
| `when` | 111 | `(when (not-in state REQUEST-OPEN-STATES)` |
| `cond` | 110 | `(cond` |
| `if` | 87 | `(if (= missing #()) "" f"(足りない証拠の族 {(list missing)})")` |
| `式の中の島: fn` | 87 | `val ? asks = sorted(mine.keys(), key=(fn [ask] (if (in ask located) #((. (get (get located` |
| `continue)` | 78 | `(continue)` |
| `.append` | 74 | `(.append actions (Submit :request-id candidate.request-id :name INTENT-POST-MESSAGE :paylo` |
| `raise` | 71 | `(raise (ValueError f"契約 {KIND-HEALTH} の status.{HEALTH-STATUS-RESOURCES}[].{HEALTH-RESOURC` |
| `isinstance` | 67 | `(isinstance (. (get rows ask) routed-to) str))` |
| `式の中の島: do` | 65 | `Submit    → (do (<- answer (| IntakeSubmitted IntakeKeyTaken IntakeSubmitUnreachable)` |
| `val` | 63 | `(val since card-view.waiting-since)` |
| `式の中の島: dfor` | 60 | `FrozenMap((dfor card judged.cards card.key card))` |
| `for` | 60 | `(for [#(reason keys) (sorted (.items found.skipped))]` |
| `式の中の島: cut` | 60 | `tuple(((cut tag (len prefix) None) for tag in words if tag.startswith(prefix) and (cut tag` |
| `and` | 60 | `(and (.startswith by CONVERSATION-BY-PREFIX) (> (len by) (len CONVERSATION-BY-PREFIX))) (c` |
| `;;` | 34 | `;; 差し戻しの性質 = 中身の class(列の翻訳が盤の型 KanbanDoneSendBack へ解いた値 — 欄の無い中身・形の合わない中身は既定の性質)。` |
| `式の中の島: gfor` | 33 | `val ? found = next((gfor #(repo work-dir) (.items policy.work-dir-of-repo) :if (in repo re` |
| `is-not` | 32 | `(is-not unmet None) (.append (.setdefault skipped SKIP-DEPENDS []) (+ card.key "(" unmet "` |
| `+` | 30 | `(+ f"。積み残し(担い手待ち)は全体で {sample.undelivered} 件"` |
| `try` | 30 | `(try` |
| `while` | 30 | `(while (is reason None)` |
| `:if` | 29 | `:if (and (in row.parent NO-PARENT) (in row.request-class COLUMN-BY-REQUEST) (in row.state ` |
| `return` | 29 | `(return (TickOutcome :copy copy :wake-at None :decided False))))` |
| `except` | 28 | `(except [e re.error]` |
| `式の中の島: cond` | 27 | `val ? reason = (cond (is-not sample.oldest-reason None) sample.oldest-reason` |
| `is` | 26 | `(is priority None) (.append (.setdefault skipped SKIP-NO-PRIORITY []) card.key)` |
| `in` | 25 | `(in policy.hold-tag words) (.append (.setdefault skipped SKIP-HELD []) card.key)` |
| `not` | 18 | `(not (isinstance by str)) None` |
| `match` | 12 | `(match answer` |
| `cut` | 12 | `(cut rest 0 cut-at)` |
| `式の中の島: match` | 12 | `reason=(match stock` |
| `!=` | 12 | `(!= current.sha verdict.sha)` |
| `tuple` | 12 | `(tuple placeables))` |
| `_` | 11 | `_ "在庫の口が契約どおりに答えなかった"))))` |

| file | 本体の行 | lisp の行 |
|---|---:|---:|
| controllers/screen/core/board.hy | 714 | 151 |
| controllers/screen/core/cache.hy | 434 | 129 |
| controllers/screen/core/request_history.hy | 361 | 105 |
| controllers/scheduling/core/attend.hy | 175 | 103 |
| controllers/screen/core/slice.hy | 203 | 100 |
| controllers/screen/core/view_eval.hy | 197 | 97 |
| controllers/screen/core/queue.hy | 389 | 93 |
| controllers/screen/core/record.hy | 275 | 89 |
