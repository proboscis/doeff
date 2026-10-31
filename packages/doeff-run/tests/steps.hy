;;; 筋書きの手順の部品(公開 effect だけを使う kleisli)。fake・本物のどの handler の下でも同じに動く。
(require doeff-hy.macros [defk <- val var])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import uuid)
(import doeff_time [GetMonotonic])
(import doeff_run [RunSpec RunHandle RunEvent RunStart RunStatus RunReadEvents RunAccepted RunLive RunStopped RunGone])
(import tests.interpreters [ScenarioSettings Settings])

;; 1 回の読みの待ち(秒)。終わりまで読む手順は、この待ちを繰り返す。
(setv PAGE-WAIT-SECONDS 1.0)


(defclass [(dataclass :frozen True)] Seen []
  "読んだ出来事の全部と、最後に読んだ時の状態。after = 次に渡す after-seq。"
  (#^ tuple events)
  (#^ object state)
  (#^ int after))


(defn #^ str new-name []
  "この検だけの run の名(名は小文字・数字・- の 1〜63 字)。"
  (+ "contract-" (cut (. (uuid.uuid4) hex) 0 12)))

(defn #^ bool terminal? [state]
  (isinstance state #(RunStopped RunGone)))

(defn #^ list kinds-of [#^ tuple events]
  "出来事の状態の型の並び(RunLive・RunStopped・RunGone)。"
  (lfor event events (type event.state)))


(defk settings []
  {:pre [] :post [(: % Settings)]}
  (<- found (ScenarioSettings))
  found)

(defk begin [#^ RunSpec spec]
  {:pre [(: spec RunSpec)] :post [(: % RunHandle)]}
  "名を新しく付けて走らせ始め、受け付けられた取っ手を返す。"
  (<- outcome (RunStart (new-name) spec))
  (assert (isinstance outcome RunAccepted) (repr outcome))
  outcome.handle)

(defk read-until [#^ RunHandle handle #^ Callable stop #^ float timeout]
  {:pre [(: handle RunHandle) (: stop Callable) (: timeout float)] :post [(: % Seen)]}
  "stop(それまでに読んだ出来事の列 状態)が真になるまで読む(上限 timeout 秒)。出来事の seq は 1 から欠けずに増える。"
  (<- began (GetMonotonic))
  (var events #())
  (var after 0)
  (while True
    (<- page (RunReadEvents handle after PAGE-WAIT-SECONDS))
    (:= events (+ events page.events))
    (:= after page.next-seq)
    (assert (= (lfor event events event.seq) (list (range 1 (+ (len events) 1)))) (repr events))
    (when (stop events page.state)
      (return (Seen events page.state after)))
    (<- now (GetMonotonic))
    (assert (< (- now began) timeout) (.format "{} 秒の内に条件が満ちない: {!r}" timeout events))))

(defk read-until-live [#^ RunHandle handle]
  {:pre [(: handle RunHandle)] :post [(: % Seen)]}
  (<- seen (read-until handle (fn [events state] (or (terminal? state) (isinstance state RunLive))) 60.0))
  (assert (isinstance seen.state RunLive) (repr seen))
  seen)

(defk read-to-end [#^ RunHandle handle #^ float timeout]
  {:pre [(: handle RunHandle) (: timeout float)] :post [(: % Seen)]}
  (<- seen (read-until handle (fn [events state] (terminal? state)) timeout))
  seen)
