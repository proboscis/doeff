;; 閉じた会話が重い turn の記録を持ち続けない事の検 — 本番の handler + 替え玉の app-server。
;;
;; 守る事: 会話の状態は「続きを起こすのに要る小さな記録」(Continuation — thread-id・spec・next-seq・launches)と「重い turn の記録」
;; (SessionRuntime.turns の TurnLog)に分かれ、重い方の寿命は読み手が読み終えるまでに結ばれる。閉じた会話は、読み手が終わりまで
;; 読んだ turn の記録を持たず、持つのは読み終えていない turn だけ。記録が無くなった閉じた会話は CodexHost に Continuation だけを残す。
;; 続き(ResumeThread)は Continuation の上で新しい process を起こし、出来事の seq と起こした数を続ける。
;; これが崩れると、turn-host の本体の memory は codex の会話の数だけ重くなる(閉じた会話が 16 turn 分の道具の出力の全文を持ち続ける —
;; zeus の turn-host の RSS の観測 card ki-94d58a92665a の残り 3 件の 3 つ目)。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "codex-test" :role "program"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])
(import json)
(import sys)
(import threading)
(import pathlib [Path])
(import doeff [with_handlers])
(import doeff_core_effects [scheduled])
(import doeff_time [sync-time-handler])
(import doeff_codex [FreshThread ResumeThread CodexInput CodexTurn CodexSessionSpec CodexHome])
(import doeff_codex [TurnEnded TurnStatus])
(import doeff_codex [CodexHost codex-handler]
        doeff_codex._handler [KEPT-TURNS on-line])
(import doeff_codex [CodexStartTurn CodexCloseSession CodexLaunchCount CodexReadTurnEvents TurnEventPage UnknownTurn SessionClosed BackendLost TurnStarted])
(import tests.interpreters [STUB-PATH CODEX-HOME-DIR settings-for])
(import tests.scenario_steps [read-to-end])

(val THREADS 3)
(val TURN-WAIT-SECONDS 30.0)


(defk handlers-for [#^ CodexHost host]
  {:pre [(: host CodexHost)] :post [(: % list)] :tags {:context "codex-test" :role "program"}}
  "本番の handler の組(時計と codex の handler)を作るため。host は検が持って、持つ記録を読めるようにする。"
  [(sync-time-handler) (codex-handler host)])

(defk new-host []
  {:pre [] :post [(: % CodexHost)] :tags {:context "codex-test" :role "program"}}
  "替え玉の app-server を起こす CodexHost を作るため。"
  (CodexHost #(sys.executable STUB-PATH) :launch-timeout 30.0))

(defk spec-in [#^ Path tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % CodexSessionSpec)] :tags {:context "codex-test" :role "program"}}
  "検の会話の宣言(作業の dir は検ごとの tmp の dir・元の CODEX_HOME はその下)を作るため。"
  (.mkdir (/ tmp-path CODEX-HOME-DIR) :exist-ok True)
  (<- settings (settings-for "stub" tmp-path))
  settings.spec)

(defk spec-numbered [#^ CodexSessionSpec spec #^ int index]
  {:pre [(: spec CodexSessionSpec) (: index int)] :post [(: % CodexSessionSpec)] :tags {:context "codex-test" :role "program"}}
  "宣言を、index 番目の会話の物にするため(替え玉は process ごとに id を 1 から数えるので、同じ host に開いた会話が同じ thread の id に
   衝突しないよう、会話ごとに替え玉の id の接頭辞を変える)。"
  (replace spec :home (CodexHome :env (| spec.home.env {"STUB_ID_PREFIX" (str index)}))))

(defk turns-held [#^ CodexHost host #^ str thread-id]
  {:pre [(: host CodexHost) (: thread-id str)] :post [(: % list)] :tags {:context "codex-test" :role "program"}}
  "会話が今持つ重い turn の記録の turn の id(昇順 — 状態が CodexHost から外れていれば空)を読むため。"
  (val runtime (.runtime host thread-id))
  (if (is runtime None) [] (sorted runtime.turns)))

(defk finished-turn [#^ str thread-id #^ CodexSessionSpec spec #^ str text]
  {:pre [(: thread-id str) (: spec CodexSessionSpec) (: text str)] :post [(: % CodexTurn)] :tags {:context "codex-test" :role "program"}}
  "会話の次の turn を始めて終わりまで読むため(thread-id が空なら新しい会話)。"
  (<- started (CodexStartTurn (if thread-id (ResumeThread :thread-id thread-id) (FreshThread)) spec (CodexInput :text text)))
  (assert (isinstance started TurnStarted) started)
  (<- done (read-to-end started.turn TURN-WAIT-SECONDS))
  (assert (and (isinstance done.end TurnEnded) (= done.end.status TurnStatus.COMPLETED)) done.end)
  started.turn)


(defrecord Closed
  "走っている turn を残したまま閉じた会話: turn = 閉じた時に走っていた turn / held = 閉じた直後に持つ turn の記録 / last-seq = 閉じる前に
   読み手へ渡した最後の seq。"
  {:tags {:context "codex-test" :role "type"}}
  (#^ CodexTurn turn)
  (#^ list held)
  (#^ int last-seq))

(defk closed-mid-turn [#^ CodexHost host #^ CodexSessionSpec spec #^ int completed]
  {:pre [(: host CodexHost) (: spec CodexSessionSpec) (: completed int)] :post [(: % Closed)] :tags {:context "codex-test" :role "program"}}
  "新しい会話を開き、completed 本の turn を終わりまで走らせ、次の turn が走っている間に閉じるため(終わりは読ませない)。"
  (<- opened (finished-turn "" spec "say hello"))
  (for [_ (range (- completed 1))]
    (<- (finished-turn opened.thread-id spec "say hello")))
  (<- running (CodexStartTurn (ResumeThread :thread-id opened.thread-id) spec (CodexInput :text "SLOW please")))
  (assert (isinstance running TurnStarted) running)
  (<- before (CodexReadTurnEvents running.turn -1 1.0))
  (<- closed (CodexCloseSession opened.thread-id "検の終わり"))
  (assert (= closed (SessionClosed :was-running True)) closed)
  (<- held (turns-held host opened.thread-id))
  (Closed :turn running.turn :held held :last-seq before.next-seq))


(defrecord EndRead
  "閉じた会話の終わりを読み手が読んだ結果: page = 読んだ頁 / held = 読んだ後に持つ turn の記録。"
  {:tags {:context "codex-test" :role "type"}}
  (#^ (| TurnEventPage UnknownTurn) page)
  (#^ list held))

(defk end-read [#^ CodexHost host #^ Closed closed]
  {:pre [(: host CodexHost) (: closed Closed)] :post [(: % EndRead)] :tags {:context "codex-test" :role "program"}}
  "閉じた会話の走っていた turn の終わりを、閉じる前に読んだ所の続きから読むため(読み手がするのと同じ読み方)。"
  (<- page (CodexReadTurnEvents closed.turn closed.last-seq 5.0))
  (<- held (turns-held host closed.turn.thread-id))
  (EndRead :page page :held held))


(defrecord Fleet
  "会話を何本も開いて閉じた観測: closed = 閉じた直後の会話ごとの記録 / read = 終わりを読ませた後の会話ごとの読み。"
  {:tags {:context "codex-test" :role "type"}}
  (#^ tuple closed)
  (#^ tuple read))

(defk fleet-closed-then-read [#^ CodexHost host #^ CodexSessionSpec spec #^ int count]
  {:pre [(: host CodexHost) (: spec CodexSessionSpec) (: count int)] :post [(: % Fleet)] :tags {:context "codex-test" :role "program"}}
  "会話を count 本開き、それぞれ turn を重ねて走っている turn を残したまま閉じ、全部閉じてから終わりを読ませるため。"
  (var closed #())
  (for [index (range count)]
    (<- numbered (spec-numbered spec index))
    (<- one (closed-mid-turn host numbered 3))
    (:= closed (+ closed #(one))))
  (var read #())
  (for [one closed]
    (<- seen (end-read host one))
    (:= read (+ read #(seen))))
  (Fleet :closed closed :read read))


(deftest test-a-closed-session-holds-no-turn-log-once-the-reader-has-read-its-end [tmp-path]
  ;; 会話を何本も開き、turn を重ねて、走っている turn を残したまま閉じる。閉じた直後は読み手がまだ読んでいない終わり(BackendLost)を
  ;; 持つ turn の記録だけ(重ねた turn の記録は読み手が終わりまで読んだので持たない)。終わりを読ませると、どの閉じた会話も記録を
  ;; 1 本も持たない。
  (<- host (new-host))
  (<- spec (spec-in tmp-path))
  (<- stack (handlers-for host))
  (<- fleet (scheduled (with_handlers stack (fleet-closed-then-read host spec THREADS))))
  (for [one fleet.closed]
    (assert (= one.held [one.turn.turn-id]) one.held))
  (for [seen fleet.read]
    (assert (isinstance seen.page TurnEventPage) seen.page)
    (assert (isinstance seen.page.end BackendLost) seen.page.end)
    (assert (= seen.held []) seen.held))
  (var total 0)
  (for [one fleet.closed]
    (<- left (turns-held host one.turn.thread-id))
    (:= total (+ total (len left))))
  (assert (= total 0) total))


(defk finished-then-closed [#^ CodexHost host #^ CodexSessionSpec spec]
  {:pre [(: host CodexHost) (: spec CodexSessionSpec)] :post [(: % tuple)] :tags {:context "codex-test" :role "program"}}
  "会話の 1 本の turn を終わりまで読ませてから、走っている turn の無いまま閉じるため。答え = 閉じた会話の thread の id と閉じた答え。"
  (<- turn (finished-turn "" spec "say hello"))
  (<- closed (CodexCloseSession turn.thread-id "検の終わり"))
  #(turn.thread-id closed))

(deftest test-a-session-closed-after-its-last-turn-was-read-holds-nothing [tmp-path]
  ;; 最後の turn が終わり、読み手が終わりまで読んだ後に閉じる会話(いちばん多い道)は、閉じた時点で記録を 1 本も持たない
  ;; (閉じた後に読む終わりが無いので、読み手の読みを待たずに外れる)。
  (<- host (new-host))
  (<- spec (spec-in tmp-path))
  (<- stack (handlers-for host))
  (<- #(thread-id closed) (scheduled (with_handlers stack (finished-then-closed host spec))))
  (assert (= closed (SessionClosed :was-running False)) closed)
  (<- held (turns-held host thread-id))
  (assert (= held []) held))


(deftest test-a-turn-the-reader-never-reads-is-the-only-record-a-closed-session-keeps [tmp-path]
  ;; 読み手が終わりを読まないまま消えても、閉じた会話が持つのは読み終えていない turn の記録 1 本だけ(読み終えた turn の記録は
  ;; 閉じた時に外れる)— 持ち続ける量は turn の数に比例しない。
  (<- host (new-host))
  (<- spec (spec-in tmp-path))
  (<- stack (handlers-for host))
  (<- closed (scheduled (with_handlers stack (closed-mid-turn host spec 5))))
  (assert (= closed.held [closed.turn.turn-id]) closed.held))


(defrecord Resumed
  "記録が外れた閉じた会話を続けた観測: read = 閉じた後に読んだ終わり / resumed-seqs = 続きの turn の出来事の seq / launches = 起こした数。"
  {:tags {:context "codex-test" :role "type"}}
  (#^ EndRead read)
  (#^ (get tuple #(int ...)) resumed-seqs)
  (#^ int launches))

(defk closed-then-resumed [#^ CodexHost host #^ CodexSessionSpec spec]
  {:pre [(: host CodexHost) (: spec CodexSessionSpec)] :post [(: % Resumed)] :tags {:context "codex-test" :role "program"}}
  "会話を閉じて終わりを読ませ、記録が外れた後に ResumeThread で続きの turn を走らせるため。"
  (<- closed (closed-mid-turn host spec 2))
  (<- read (end-read host closed))
  (<- resumed (CodexStartTurn (ResumeThread :thread-id closed.turn.thread-id) spec (CodexInput :text "say hello")))
  (assert (isinstance resumed TurnStarted) resumed)
  (<- done (read-to-end resumed.turn TURN-WAIT-SECONDS))
  (assert (isinstance done.end TurnEnded) done.end)
  (<- launches (CodexLaunchCount closed.turn.thread-id))
  (Resumed :read read :resumed-seqs (tuple (gfor event done.events event.seq)) :launches launches))

(deftest test-a-resumed-session-continues-the-seq-and-the-launch-count-after-its-logs-are-gone [tmp-path]
  ;; 記録が外れた閉じた会話の続き(ResumeThread)は、続きの記録の上で新しい process を起こし、出来事の seq と起こした数を続ける。
  (<- host (new-host))
  (<- spec (spec-in tmp-path))
  (<- stack (handlers-for host))
  (<- seen (scheduled (with_handlers stack (closed-then-resumed host spec))))
  (assert (= seen.read.held []) seen.read.held)
  (assert (isinstance seen.read.page TurnEventPage) seen.read.page)
  ;; 閉じる前に読み手へ渡した最後の seq より後ろから数え続ける(seq は会話の中で単調に増える)。
  (assert (and seen.resumed-seqs (> (min seen.resumed-seqs) seen.read.page.next-seq)) #(seen.read.page.next-seq seen.resumed-seqs))
  ;; 起こした process は、閉じる前の 1 つと続きの 1 つで 2 つ。
  (assert (= seen.launches 2) seen.launches))


(deftest test-a-fully-read-closed-session-leaves-only-its-continuation-on-the-host [tmp-path]
  ;; 記録が無くなった閉じた会話は、CodexHost に続きの記録(thread-id・spec・next-seq・起こした数)だけを残す(生きた状態は外れる)。
  (<- host (new-host))
  (<- spec (spec-in tmp-path))
  (<- stack (handlers-for host))
  (<- fleet (scheduled (with_handlers stack (fleet-closed-then-read host spec 1))))
  (<- numbered (spec-numbered spec 0))
  (val thread-id (. (get fleet.closed 0) turn thread-id))
  (assert (is (.runtime host thread-id) None) (.runtime host thread-id))
  (val continuation (.continuation host thread-id))
  (assert (is-not continuation None) continuation)
  (assert (= continuation.thread-id thread-id) continuation.thread-id)
  (assert (= continuation.spec numbered) continuation.spec)
  (assert (= continuation.launches 1) continuation.launches)
  (assert (>= continuation.next-seq (. (get fleet.read 0) page next-seq)) continuation.next-seq))


(defk read-then-straggler-line [#^ CodexHost host #^ CodexSessionSpec spec]
  {:pre [(: host CodexHost) (: spec CodexSessionSpec)] :post [(: % tuple)] :tags {:context "codex-test" :role "program"}}
  "走っている turn を残して閉じ、終わりを読ませて状態が外れた後に、降ろし中だった process の最後の行が届く場面を作るため。
   答え = 外れた状態・その process の世代・走っていた turn。"
  (<- opened (finished-turn "" spec "say hello"))
  (<- running (CodexStartTurn (ResumeThread :thread-id opened.thread-id) spec (CodexInput :text "SLOW please")))
  (assert (isinstance running TurnStarted) running)
  (val runtime (.runtime host opened.thread-id))
  (val generation runtime.generation)
  (<- before (CodexReadTurnEvents running.turn -1 1.0))
  (<- closed (CodexCloseSession opened.thread-id "検の終わり"))
  (assert (isinstance closed SessionClosed) closed)
  (<- ended (CodexReadTurnEvents running.turn before.next-seq 5.0))
  (assert (isinstance ended TurnEventPage) ended)
  #(runtime generation running.turn))

(deftest test-a-last-line-of-the-closed-process-does-not-bring-a-released-turn-log-back [tmp-path]
  ;; 閉じる時に降ろし始めた process の最後の行(turn/completed など)が、読み手が終わりを読んで状態が外れた後に届いても、外れたターンの
  ;; 記録を作り直さず、続きの記録の seq も進めない(作り直すと閉じた会話が外れず、外れた状態が続きの記録を古い物から書き換える)。
  (<- host (new-host))
  (<- spec (spec-in tmp-path))
  (<- stack (handlers-for host))
  (<- #(runtime generation turn) (scheduled (with_handlers stack (read-then-straggler-line host spec))))
  (assert (is (.runtime host turn.thread-id) None) (.runtime host turn.thread-id))
  (val continuation (.continuation host turn.thread-id))
  (val seq-before continuation.next-seq)
  (val straggler (json.dumps {"method" "turn/completed"
                              "params" {"threadId" turn.thread-id "turn" {"id" turn.turn-id "status" "interrupted"}}}))
  (val reader (threading.Thread :target on-line :args [runtime generation straggler]))
  (.start reader)
  (.join reader)
  (assert (= runtime.turns {}) runtime.turns)
  (assert (= continuation.next-seq seq-before) #(continuation.next-seq seq-before)))


(defk turns-then-count [#^ CodexHost host #^ CodexSessionSpec spec #^ int count]
  {:pre [(: host CodexHost) (: spec CodexSessionSpec) (: count int)] :post [(: % CodexTurn)] :tags {:context "codex-test" :role "program"}}
  "同じ会話で count 本の turn を終わりまで走らせるため(答え = 最後の turn)。"
  (<- opened (finished-turn "" spec "say hello"))
  (var last opened)
  (for [_ (range (- count 1))]
    (<- next-turn (finished-turn opened.thread-id spec "say hello"))
    (:= last next-turn))
  last)

(deftest test-a-live-session-keeps-only-the-last-few-turn-logs [tmp-path]
  ;; 開いた会話が持つ turn の記録は最近の KEPT-TURNS 本だけ(読み手は今の turn と続きの turn しか読まない — 読み手の
  ;; doeff-agents の headless_codex の pull は、走っている turn だけを名指して読む)。
  (assert (<= KEPT-TURNS 4) KEPT-TURNS)
  (<- host (new-host))
  (<- spec (spec-in tmp-path))
  (<- stack (handlers-for host))
  (<- last (scheduled (with_handlers stack (turns-then-count host spec (+ KEPT-TURNS 3)))))
  (<- held (turns-held host last.thread-id))
  (assert (= (len held) KEPT-TURNS) held)
  (assert (in last.turn-id held) #(last held)))
