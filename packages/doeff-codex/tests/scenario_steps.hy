;;; 筋書きの手順 — 公開 effect だけを撃つ(handler を知らない)。fake と stub の両方で同じ手順が走る。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "codex-test" :role "program"})
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import doeff_codex.values [CodexTurn CodexEvent])
(import doeff_codex.lines [TextDelta])
(import doeff_codex.effects [CodexReadTurnEvents TurnEventPage BackendLost])
(import doeff_codex.lines [TurnEnded])
(import tests.interpreters [ScenarioSettings Settings])

;; 1 回の読みで待つ上限(秒)。筋書きの全体の上限は Settings.turn-timeout。
(val READ-WAIT-SECONDS 1.0)


(defrecord ReadSoFar
  "読んだ出来事の全部(seq の順)と終わり(まだなら None)。"
  {:tags {:context "codex-test" :role "type"}}
  (#^ (get tuple #(CodexEvent ...)) events)
  (#^ (| TurnEnded BackendLost None) end))


(defk settings []
  {:pre [] :post [(: % Settings)] :tags {:context "codex-test" :role "program"}}
  "筋書きの宣言を読むため。"
  (<- found (ScenarioSettings))
  found)


(defk read-until [#^ CodexTurn turn enough #^ float timeout]
  {:pre [(: turn CodexTurn) (: enough Callable) (: timeout float)] :post [(: % ReadSoFar)] :tags {:context "codex-test" :role "program"}}
  "enough(読んだ出来事・終わり) が真になるか、終わりが来るか、読みの回数の上限まで、ターンの出来事を読み進めるため。"
  (var events #())
  (var end None)
  (var after 0)
  (for [_ (range (int (/ timeout READ-WAIT-SECONDS)))]
    (<- page (CodexReadTurnEvents turn after READ-WAIT-SECONDS))
    (assert (isinstance page TurnEventPage) page)
    (:= events (+ events page.events))
    (:= after page.next-seq)
    (:= end page.end)
    (when (or (is-not end None) (enough events end))
      (break)))
  (ReadSoFar :events events :end end))


(defk read-to-end [#^ CodexTurn turn #^ float timeout]
  {:pre [(: turn CodexTurn) (: timeout float)] :post [(: % ReadSoFar)] :tags {:context "codex-test" :role "program"}}
  "ターンの終わりまで出来事を読むため。"
  (<- so-far (read-until turn (fn [events end] False) timeout))
  so-far)


(defk read-to-first-delta [#^ CodexTurn turn #^ float timeout]
  {:pre [(: turn CodexTurn) (: timeout float)] :post [(: % ReadSoFar)] :tags {:context "codex-test" :role "program"}}
  "答えの文字の途中が 1 つ届くまで出来事を読むため(止めの筋書きが、途中で止める拍を作る)。"
  (<- so-far (read-until turn (fn [events end] (any (gfor event events (isinstance event.record TextDelta)))) timeout))
  so-far)


(defk records-of [#^ ReadSoFar so-far kind]
  {:pre [(: so-far ReadSoFar) (: kind type)] :post [(: % tuple)] :tags {:context "codex-test" :role "program"}}
  "読んだ出来事から、ある種類の記録だけを出来事の順に取り出すため。"
  (tuple (gfor event so-far.events :if (isinstance event.record kind) event.record)))
