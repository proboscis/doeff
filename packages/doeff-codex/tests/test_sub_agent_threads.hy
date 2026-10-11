;; 子の agent の thread の行の振り分けの検(card ki-c09b93b72bff)— codex 0.162.1 は子の agent(collabAgentToolCall の spawnAgent)を
;; 同じ app-server の process の別の thread で走らせ、子のターンの始まりと終わり・答え・usage の行を、親の会話の行と同じ stdout に
;; 混ぜて出す(録った行 tests/recorded/codex-0.162.1/other-tools)。
;;
;; 守る事: handler は自分の会話の thread を名乗る記録だけを会話のターンへ積む。子の thread のターンの始まりを自分のターンの始まりと
;; 読むと、走っているターンが子のターンに替わり、親のターンを名乗らない行(口座の枠の知らせ・codex からの要求)が親のターンに届かない
;; (子のターンの間は子のターンへ・子のターンが終わった後はターンの外へ落ちる — 利用枠切れの事実の材料が欠け、答えの要る要求は
;; 誰にも答えられない)。子の thread の記録はターンの外(outside — 数えて見せるだけ)へ置く。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex-test" :role "foundation"})
(require doeff-hy.macros [deftest <- var])
(import json)
(import doeff_codex.lines [classify-line RateLimits TurnRecord])
(import doeff_codex.handler [SessionRuntime absorb])
(import doeff_codex.values [CodexSessionSpec CodexHome])
(import doeff_codex.rpc [ApprovalPolicy SandboxMode])
(import tests.test_lines [RECORDED])


(deftest test-a-sub-agent-thread-does-not-take-over-the-running-turn
  (val lines (lfor line (.splitlines (.read-text (/ RECORDED "other-tools.stdout.jsonl") :encoding "utf-8")) :if (.strip line) line))
  (val answers (dfor line lines :setv message (json.loads line) :if (in "result" message) (get message "id") (get message "result")))
  (val parent (get answers 2 "thread" "id"))
  (val turn (get answers 3 "turn" "id"))
  (val runtime (SessionRuntime (CodexSessionSpec :home (CodexHome :env {}) :cwd "/tmp" :approval-policy ApprovalPolicy.NEVER
                                                 :sandbox SandboxMode.DANGER-FULL-ACCESS)))
  ;; handler は thread/start の答えで会話の thread の id を持ってから turn/start を書く(handler.hy の start-turn)。
  (setv runtime.thread-id parent)
  (var limits 0)
  (for [line lines]
    (<- record (classify-line line))
    (when (isinstance record RateLimits) (:= limits (+ limits 1)))
    (<- (absorb runtime record)))
  ;; 走っているターンは親のターンのまま(子のターンの始まりで替わらない)— 答えを待つ要求と枠の知らせの行き先。
  (assert (= runtime.current-turn turn) #(runtime.current-turn turn))
  ;; ターンの記録は親のターンだけ(子のターンの列を作らない)。
  (assert (= (list runtime.turns) [turn]) (list runtime.turns))
  (val events (tuple (gfor event (. (get runtime.turns turn) events) event.record)))
  ;; 親のターンは、子のターンの間と後に届いた枠の知らせも全部持つ(録りでは 6 つ)。
  (assert (> limits 0) limits)
  (assert (= (len (lfor record events :if (isinstance record RateLimits) record)) limits) events)
  ;; 親のターンに子の thread を名乗る記録は無く、子の thread の記録はターンの外に在る。
  (val foreign (lfor record events :if (and (isinstance record TurnRecord) (!= record.thread-id parent)) record))
  (assert (= foreign []) foreign)
  (val outside (lfor record runtime.outside :if (isinstance record TurnRecord) record.thread-id))
  (assert (and outside (all (gfor thread outside (!= thread parent)))) runtime.outside))
