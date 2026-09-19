;;; semgrep hit fixture: doeff-agents-job-step-reads-the-stream-before-the-step
;;; (card acp:kanban-issue:ki-2bd49c68b042 — 材料を読む前に次の 1 手を決める拍は禁止形。
;;;  直す前の agentd.hy の形: 器の眺めだけで job-step-of を呼び、材料〔events〕の読みは
;;;  observe の腕の中に在った。CLI が result を出して降りた拍に tick が入ると、
;;;  「結果が器へ出たか」を読まないまま (not live-backend) で SessionLost に落ちる)。

(defk observe-job-fast [settings state job now-ms]
  (<- view (| SessionView None) (SessionGet :session-id job.session-id))
  (setv progressed True)
  (when (isinstance view SessionView)
    (<- moved bool (progressed-of job view))
    (setv progressed moved))
  ;; BAD: この拍の材料を読む前に次の 1 手を決めている
  (<- step str (job-step-of view job.turn-floor-ms progressed False))
  (setv current state)
  (when (and (= step JOB-STEP-OBSERVE) (isinstance view SessionView))
    (<- pushed InFlightJob (stream-job-read settings job view now-ms))
    (<- kept AgentdState (with-job state pushed))
    (setv current kept))
  #(current view step))
