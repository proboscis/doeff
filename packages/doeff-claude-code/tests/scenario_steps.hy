;;; 筋書きの手順の部品(公開 effect だけを使う kleisli)。fake・替え玉・本物のどの handler の下でも同じに動く。
(require doeff-hy.macros [defk <-])
(import collections.abc [Callable])
(import dataclasses [dataclass replace])
(import uuid)
(import doeff_time [GetMonotonic])
(import doeff_claude_code.values [ClaudeTurn ClaudeSessionSpec TurnInput FreshSession ResumeSession ForkSession])
(import doeff_claude_code.lines [AssistantMessage PermissionRequested])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeReadTurnEvents TurnStarted TurnEventPage])
(import tests.interpreters [ScenarioSettings Settings])

(setv PAGE-WAIT-SECONDS 5.0)


(defclass [(dataclass :frozen True)] TurnRecord []
  "読んだ行と終わり。after = 次に渡す after-seq。"
  (#^ tuple lines)
  (#^ object end)
  (#^ int after))


(defn #^ str new-id [] (str (uuid.uuid4)))

(defn #^ TurnInput typed [#^ str text [ref None]]
  (TurnInput text (or ref (new-id))))

(defn #^ bool tool-started [line]
  (and (isinstance line.kind AssistantMessage) (in "Bash" line.kind.tool-names)))

(defn kinds-of [#^ tuple lines kind-type]
  (lfor line lines :if (isinstance line.kind kind-type) line.kind))


(defk settings []
  {:pre [] :post [(: % Settings)]}
  (<- found (ScenarioSettings))
  found)

(defk start [origin #^ ClaudeSessionSpec spec #^ str text]
  {:pre [(: origin (| FreshSession ResumeSession ForkSession)) (: spec ClaudeSessionSpec) (: text str)] :post [(: % TurnStarted)]}
  (<- outcome (ClaudeStartTurn origin spec (typed text)))
  (assert (isinstance outcome TurnStarted) (repr outcome))
  outcome)

(defk read-until [#^ ClaudeTurn turn #^ Callable stop #^ float timeout #^ int after]
  {:pre [(: turn ClaudeTurn) (: stop Callable) (: timeout float) (: after int)] :post [(: % TurnRecord)]}
  "stop(page の行の列 終わり) が真になるまで読む(上限 timeout 秒)。行の seq は重複も欠落もなく増える。"
  (<- started (GetMonotonic))
  (setv lines [] end None)
  (while True
    (<- page (ClaudeReadTurnEvents turn after PAGE-WAIT-SECONDS))
    (assert (isinstance page TurnEventPage) (repr page))
    (for [line page.lines]
      (assert (> line.seq after) (.format "seq {} after {}" line.seq after))
      (setv after line.seq)
      (.append lines line))
    (setv end page.end)
    (when (stop (tuple lines) end) (return (TurnRecord (tuple lines) end after)))
    (<- now (GetMonotonic))
    (assert (< (- now started) timeout) (.format "{} 秒の内に読み終わらない: {!r}" timeout (lfor line lines line.kind)))))

(defk read-to-end [#^ ClaudeTurn turn #^ float timeout]
  {:pre [(: turn ClaudeTurn) (: timeout float)] :post [(: % TurnRecord)]}
  (<- record (read-until turn (fn [lines end] (is-not end None)) timeout -1))
  record)

(defk read-to-tool-start [#^ ClaudeTurn turn #^ float timeout]
  {:pre [(: turn ClaudeTurn) (: timeout float)] :post [(: % TurnRecord)]}
  (<- record (read-until turn (fn [lines end] (or (any (gfor line lines (tool-started line))) (is-not end None)))
                         timeout -1))
  (assert (is record.end None) (.format "道具が始まる前に手番が終わった: {!r}" record.end))
  record)

(defk read-until-permission [#^ ClaudeTurn turn #^ float timeout]
  {:pre [(: turn ClaudeTurn) (: timeout float)] :post [(: % PermissionRequested)]}
  "許可の問いの行まで読み、その問いを返す。"
  (<- record (read-until turn (fn [lines end] (or (kinds-of lines PermissionRequested) (is-not end None))) timeout -1))
  (assert (is record.end None) (.format "許可の問いの前に手番が終わった: {!r}" record.end))
  (get (kinds-of record.lines PermissionRequested) 0))
