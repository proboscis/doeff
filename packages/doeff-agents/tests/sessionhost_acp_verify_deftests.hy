;;; agentd が charter.kind = verify の job(定期便の検証の命令 1 つ — 会社 repo の日次の全体検証)を担う腕の焦点の検
;;; (段 12 lane 12a・agora-redesign #230・依頼者の裁定 2026-09-16・ADR-DOE-AGENTS-012 R36)。
;;;
;;; 形: k3s の CronJob が ACP に agent-job を 1 つ書き、配置が charter.place を spec.places に名乗る node(会社 Mac)に
;;; 結び、その agentd が **claude / codex を起こさず・預かり所から札も借りず**、機体の家の dotfiles の script
;;; (VERIFY-SCRIPTS-RELDIR/<jobId>.sh ちょうど — 命令の文字列は行から運ばない)を自分の session で起こし、結末(rc の
;;; file)を Ended の result に写す。ここで撃つのは
;;;   * 受け: Running + sessionHandle{stream, verify}・CommandStart の argv は sh の 1 行(pid → script → rc)・
;;;     **反例 = session を起こす形・札を借りる形が赤**(launches == [] ∧ borrowed == [])
;;;   * 結末: rc 0 も rc 3 も Ended の result(赤は結末で条件ではない)
;;;   * 知らない id(綴りの外・script が無い)は起こさず条件 VerifyScriptMissing
;;;   * 再起動: Running の行の sessionHandle.verify から組み直し、process は起こし直さない
;;;   * 消えた(rc 無し・pid 死)= VerifyCommandLost / 期限超過 = CommandStop + VerifyDeadlineExceeded
;;;   * 取り下げ(Withdrawn)= CommandStop + Interrupted(phase は書かない)
;;;   * 純関数: verify-step-of の表・verify-argv-of の形・job-kind-of の既定
;;; fake の handler で同じ program(agentd.hy)を一周させる。HTTP も subprocess も無い。

(require doeff-hy.macros [deftest])

(import dataclasses [replace])
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AGENT-JOB-KIND
  AGENT-JOB-NAMESPACE
  AGORA-KINDS-NAMESPACE
  AcpRow
  AgentdSettings
  CHARTER-KIND-TURN
  CHARTER-KIND-VERIFY
  CONDITION-INTERRUPTED
  CONDITION-VERIFY-COMMAND-LOST
  CONDITION-VERIFY-DEADLINE-EXCEEDED
  CONDITION-VERIFY-SCRIPT-MISSING
  CONDITION-VERIFY-START-FAILED
  CommandExited
  CommandGone
  CommandRunning
  NODE-KIND
  PHASE-BOUND
  PHASE-ENDED
  PHASE-RUNNING
  PHASE-WITHDRAWN
  VERIFY-SCRIPTS-RELDIR
  VERIFY-STEP-ENDED
  VERIFY-STEP-LOST
  VERIFY-STEP-OBSERVE
  VERIFY-STEP-TIMED-OUT
  VerifyPlan])
(import doeff_agents.sessionhost.acp.fake [FakeAcp FakeCustody FakeLocal FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [
  job-kind-of
  verify-argv-of
  verify-plan-of
  verify-step-of])
(import doeff_agents.sessionhost.acp.runtime [initial-state run-tick])


(setv NODE "CA-20038667")
(setv HOME "/home/mac")
(setv RUNS "/state/acp-agentd/verify-runs")
(setv VERIFY-ID "land-verify-mediagen")
(setv RUN-KEY "land-verify-mediagen-29396010")
(setv SCRIPT f"{HOME}/{VERIFY-SCRIPTS-RELDIR}/{VERIFY-ID}.sh")


(defn #^ AcpRow verify-row [#^ str job-id #^ str verify-id #^ int deadline #^ str phase]
  "配置が結んだ verify の行(binding は node・attempt・at だけ — profile / account は無い)。"
  (AcpRow :namespace AGENT-JOB-NAMESPACE
          :key f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{job-id}"
          :kind AGENT-JOB-KIND :resource-id job-id :version "v1" :generation 2 :created-at-ms 500
          :labels {} :payload {}
          :spec {"subject" verify-id "inputs" []
                 "charter" {"kind" CHARTER-KIND-VERIFY "place" "company"
                            "jobId" verify-id "runKey" RUN-KEY "deadlineSeconds" deadline}}
          :status {"phase" phase "binding" {"node" NODE "attempt" 1 "at" 0} "conditions" []}))


(defclass World []
  "fake の 4 handler + 値の宣言 + Node の行(verify の腕を撃つ最小の世界)。会社 Mac の形: 預かり所を宣言し、
   company と personal の両方を名乗る。"
  (defn #^ None __init__ [self]
    (setv self.settings (AgentdSettings :node-name NODE :homes-root "/homes" :home HOME :verify-runs-dir RUNS
                                        :node-capacity 6 :custody-declared True :places #("company" "personal")))
    (setv self.acp (FakeAcp :births {}))
    (.put-row self.acp (AcpRow :namespace AGORA-KINDS-NAMESPACE
                               :key f"{AGORA-KINDS-NAMESPACE}:{NODE-KIND}:{NODE}"
                               :kind NODE-KIND :resource-id NODE :version "v1"
                               :generation 1 :created-at-ms 0 :labels {} :payload {}
                               :spec {"name" NODE "labels" {} "places" ["company" "personal"] "capacity" 6 "streamCapability" "events"}
                               :status {"state" "joined"}))
    (setv self.custody (FakeCustody :tokens {"acct" "tok"}))
    (setv self.sessions (FakeSessions))
    (setv self.local (FakeLocal :now-ms 1000))
    (.add self.local.existing-files SCRIPT)
    (setv self.state (initial-state)))

  (defn #^ None tick [self #^ int advance-ms]
    (setv self.local.now-ms (+ self.local.now-ms advance-ms))
    (setv self.state
          (run-tick self.settings self.state
                    [self.acp.dispatch self.custody.dispatch
                     self.sessions.dispatch self.local.dispatch]))
    None)

  (defn #^ AcpRow job [self #^ str job-id]
    (get self.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{job-id}"))

  (defn #^ dict status [self #^ str job-id]
    (setv row (.job self job-id))
    (if (isinstance row.status dict) row.status {}))

  (defn #^ list conditions [self #^ str job-id]
    (setv found (.get (.status self job-id) "conditions"))
    (if (isinstance found list) found []))

  (defn #^ (| dict None) verify-handle [self #^ str job-id]
    (setv handle (.get (.status self job-id) "sessionHandle"))
    (if (isinstance handle dict) (.get handle "verify") None))

  (defn #^ str rc-path [self #^ str job-id]
    f"{RUNS}/{job-id}.rc")

  (defn #^ None finish [self #^ str job-id #^ int rc]
    "sh の 1 行が rc を書いた(process は終わった)。"
    (setv (get self.local.files (.rc-path self job-id)) f"{rc}\n")
    (for [command self.state.commands]
      (when (and (= command.job-id job-id) (is-not command.pid None))
        (.discard self.local.alive-pids command.pid)))
    None))


;; ---------------------------------------------------------------------------
;; 受け: script を 1 つ走らせる — session も札も無い(反例)
;; ---------------------------------------------------------------------------

(deftest test-verify-job-runs-the-script-without-a-session-or-a-lease
  ;; R36 (1)(2): claude / codex を起こさない・預かり所から借りない・Running + sessionHandle{stream, verify}・
  ;; CommandStart の argv は sh の 1 行(pid → script → rc・path は位置引数)。
  (setv world (World))
  (.put-row world.acp (verify-row "vj-1" VERIFY-ID 9000 PHASE-BOUND))
  (.tick world 0)
  (assert (= world.sessions.launches []) "verify の job が session を起こした(反例)")
  (assert (= world.custody.borrowed []) "verify の job が札を借りた(反例)")
  (assert (= (get (.status world "vj-1") "phase") PHASE-RUNNING))
  (setv handle (.verify-handle world "vj-1"))
  (assert (isinstance handle dict))
  (assert (= (get handle "jobId") VERIFY-ID))
  (assert (= (get handle "runKey") RUN-KEY))
  (assert (= (get handle "scriptPath") SCRIPT))
  (assert (= (get handle "rcPath") (.rc-path world "vj-1")))
  (setv stream (get (get (.status world "vj-1") "sessionHandle") "stream"))
  (assert (= stream {"owner" "agentd" "name" "vj-1"}) "running-on-me の鍵(stream.owner)が無い")
  ;; binding は触らない(書き手は配置)
  (assert (= (get (.status world "vj-1") "binding") {"node" NODE "attempt" 1 "at" 0}))
  (assert (= (len world.local.commands) 1))
  (setv argv (get world.local.commands 0))
  (assert (= (cut argv 0 2) #("/bin/sh" "-c")))
  (assert (= (get argv 3) f"{RUNS}/vj-1.pid"))
  (assert (= (get argv 4) SCRIPT))
  (assert (= (get argv 5) f"{RUNS}/vj-1.log"))
  (assert (= (get argv 6) (.rc-path world "vj-1")))
  (assert (= (get world.local.command-cwds 0) HOME))
  (assert (in RUNS world.local.made-dirs) "結末の置き場を作っていない")
  (assert (= (len world.state.commands) 1))
  (assert (= (. (get world.state.commands 0) verify-id) VERIFY-ID))
  ;; 2 拍目: まだ走っている(rc 無し・pid 生)— 何も書かない・memory に残る
  (.tick world 1000)
  (assert (= (get (.status world "vj-1") "phase") PHASE-RUNNING))
  (assert (= (len world.state.commands) 1))
  (assert (= (len world.local.commands) 1) "走っている命令を起こし直した"))


(deftest test-verify-job-ends-with-the-exit-code-as-its-result
  ;; R36 (3): rc の file が在れば Ended・result に rc(赤 = rc != 0 も結末で条件ではない)・memory から外す。
  (setv world (World))
  (.put-row world.acp (verify-row "vj-1" VERIFY-ID 9000 PHASE-BOUND))
  (.tick world 0)
  (.finish world "vj-1" 0)
  (.tick world 2000)
  (setv status (.status world "vj-1"))
  (assert (= (get status "phase") PHASE-ENDED))
  (setv result (get status "result"))
  (assert (= (get result "rc") 0))
  (assert (= (get result "kind") CHARTER-KIND-VERIFY))
  (assert (= (get result "jobId") VERIFY-ID))
  (assert (= (get result "runKey") RUN-KEY))
  (assert (= (get result "log") f"{RUNS}/vj-1.log"))
  (assert (>= (get result "durationMs") 2000))
  (assert (= (.conditions world "vj-1") []) "緑の結末に条件が付いた")
  (assert (= world.state.commands #()))
  ;; 赤の結末(rc 3)も Ended の result — 条件は付けない(赤の判断は ai land verify が台帳に書いている)
  (setv red (World))
  (.put-row red.acp (verify-row "vj-2" VERIFY-ID 9000 PHASE-BOUND))
  (.tick red 0)
  (.finish red "vj-2" 3)
  (.tick red 10)
  (assert (= (get (.status red "vj-2") "phase") PHASE-ENDED))
  (assert (= (get (get (.status red "vj-2") "result") "rc") 3))
  (assert (= (.conditions red "vj-2") []))
  (assert (= red.sessions.launches [])))


(deftest test-unknown-or-missing-verify-id-is-refused-without-starting-anything
  ;; R36 (2): 綴りの外の id(path の要素にできない)と、綴りは正しいが script が機体に無い id は起こさず
  ;; 条件 VerifyScriptMissing で Ended(知らない id は loud に落とす — D0626 決定 2)。
  (setv world (World))
  (.put-row world.acp (verify-row "vj-bad" "../evil" 9000 PHASE-BOUND))
  (.put-row world.acp (verify-row "vj-none" "land-verify-nowhere" 9000 PHASE-BOUND))
  (.tick world 0)
  (assert (= world.local.commands []) "起こしてはいけない id で命令を起こした")
  (assert (= world.sessions.launches []))
  (for [job-id ["vj-bad" "vj-none"]]
    (assert (= (get (.status world job-id) "phase") PHASE-ENDED) job-id)
    (setv last (get (.conditions world job-id) -1))
    (assert (= (get last "type") CONDITION-VERIFY-SCRIPT-MISSING) job-id))
  (assert (in "pattern" (get (get (.conditions world "vj-bad") -1) "reason")))
  (assert (in "land-verify-nowhere" (get (get (.conditions world "vj-none") -1) "reason")))
  (assert (= world.state.commands #()))
  ;; 起こせない拍(handler の断り)は VerifyStartFailed
  (setv refused (World))
  (setv refused.local.refuse-commands "exec failed: ENOENT /bin/sh")
  (.put-row refused.acp (verify-row "vj-3" VERIFY-ID 9000 PHASE-BOUND))
  (.tick refused 0)
  (assert (= (get (.status refused "vj-3") "phase") PHASE-ENDED))
  (assert (= (get (get (.conditions refused "vj-3") -1) "type") CONDITION-VERIFY-START-FAILED)))


;; ---------------------------------------------------------------------------
;; 再起動・消失・期限・取り下げ
;; ---------------------------------------------------------------------------

(deftest test-running-verify-is-recovered-from-its-row-after-a-restart
  ;; R36 (4)(R7): memory を失っても Running の行の sessionHandle.verify と pid の file から組み直し、process は
  ;; 起こし直さない。結末が付けば同じく Ended。
  (setv world (World))
  (.put-row world.acp (verify-row "vj-1" VERIFY-ID 9000 PHASE-BOUND))
  (.tick world 0)
  (setv pid (. (get world.state.commands 0) pid))
  (assert (is-not pid None))
  ;; 再起動 = memory を捨てる(行と file はそのまま)
  (setv world.state (initial-state))
  (.tick world 1000)
  (assert (= (len world.local.commands) 1) "拾い直しで命令を起こし直した")
  (assert (= (len world.state.commands) 1))
  (assert (= (. (get world.state.commands 0) pid) pid) "pid の file から pid を読み戻していない")
  (assert (= (get (.status world "vj-1") "phase") PHASE-RUNNING))
  (assert (any (gfor line world.local.logs (in "recovered running verify job vj-1" line))))
  (.finish world "vj-1" 0)
  (.tick world 1000)
  (assert (= (get (.status world "vj-1") "phase") PHASE-ENDED))
  (assert (= (get (get (.status world "vj-1") "result") "rc") 0))
  ;; sessionHandle.verify が無い Running の行は組み直せない — 結末なしで VerifyCommandLost
  (setv bare (World))
  (setv row (verify-row "vj-9" VERIFY-ID 9000 PHASE-RUNNING))
  (setv status (dict row.status))
  (setv (get status "sessionHandle") {"stream" {"owner" "agentd" "name" "vj-9"}})
  (.put-row bare.acp (replace row :status status))
  (.tick bare 0)
  (assert (= (get (.status bare "vj-9") "phase") PHASE-ENDED))
  (assert (= (get (get (.conditions bare "vj-9") -1) "type") CONDITION-VERIFY-COMMAND-LOST)))


(deftest test-lost-command-and-exceeded-deadline-close-the-job-with-a-condition
  ;; 消えた(rc 無し・pid 死)= VerifyCommandLost(result なし)/ 期限超過 = CommandStop + VerifyDeadlineExceeded。
  (setv world (World))
  (.put-row world.acp (verify-row "vj-1" VERIFY-ID 9000 PHASE-BOUND))
  (.tick world 0)
  (setv pid (. (get world.state.commands 0) pid))
  (.discard world.local.alive-pids pid)
  (.tick world 1000)
  (setv status (.status world "vj-1"))
  (assert (= (get status "phase") PHASE-ENDED))
  (assert (not-in "result" status) "消えた命令に結末を発明した")
  (assert (= (get (get (.conditions world "vj-1") -1) "type") CONDITION-VERIFY-COMMAND-LOST))
  (assert (= world.state.commands #()))
  ;; 期限: deadlineSeconds 10・11 秒後の拍で止める
  (setv slow (World))
  (.put-row slow.acp (verify-row "vj-2" VERIFY-ID 10 PHASE-BOUND))
  (.tick slow 0)
  (setv pid2 (. (get slow.state.commands 0) pid))
  (.tick slow 9000)
  (assert (= (get (.status slow "vj-2") "phase") PHASE-RUNNING) "期限の前に止めた")
  (.tick slow 2000)
  (assert (= slow.local.stopped-pids [pid2]) "期限超過の命令を止めていない")
  (assert (= (get (.status slow "vj-2") "phase") PHASE-ENDED))
  (assert (= (get (get (.conditions slow "vj-2") -1) "type") CONDITION-VERIFY-DEADLINE-EXCEEDED))
  (assert (= slow.state.commands #()))
  ;; 期限 0(宣言なし)は止めない
  (setv open (World))
  (.put-row open.acp (verify-row "vj-3" VERIFY-ID 0 PHASE-BOUND))
  (.tick open 0)
  (.tick open 100000000)
  (assert (= (get (.status open "vj-3") "phase") PHASE-RUNNING))
  (assert (= open.local.stopped-pids [])))


(deftest test-withdrawn-verify-stops-the-command-and-marks-interrupted
  ;; 取り下げ(Withdrawn — 書き手は作った側): process を止め、条件 Interrupted・phase は書かない・観測をやめる。
  (setv world (World))
  (.put-row world.acp (verify-row "vj-1" VERIFY-ID 9000 PHASE-BOUND))
  (.tick world 0)
  (setv pid (. (get world.state.commands 0) pid))
  (setv row (.job world "vj-1"))
  (setv status (dict row.status))
  (setv (get status "phase") PHASE-WITHDRAWN)
  (.put-row world.acp (replace row :status status :generation (+ row.generation 1)))
  (.tick world 1000)
  (assert (= world.local.stopped-pids [pid]))
  (setv after (.status world "vj-1"))
  (assert (= (get after "phase") PHASE-WITHDRAWN) "取り下げの phase を書き換えた")
  (assert (in CONDITION-INTERRUPTED (lfor c (get after "conditions") (get c "type"))))
  (assert (= world.state.commands #()))
  ;; 同じ行に撃ち直さない
  (.tick world 1000)
  (assert (= world.local.stopped-pids [pid])))


;; ---------------------------------------------------------------------------
;; 純関数
;; ---------------------------------------------------------------------------

(deftest test-verify-judgments-are-pure
  ;; job-kind-of: 無い = turn / verify-step-of の表 / verify-plan-of の置き場と綴りの検 / verify-argv-of の形。
  (setv turn (replace (verify-row "t" VERIFY-ID 0 PHASE-BOUND)
                      :spec {"subject" "c-1" "inputs" ["m1"] "charter" {"agent_type" "claude" "prompt" "x"}}))
  (assert (= (run (job-kind-of turn)) CHARTER-KIND-TURN))
  (assert (= (run (job-kind-of (verify-row "v" VERIFY-ID 0 PHASE-BOUND))) CHARTER-KIND-VERIFY))
  (assert (= (run (verify-step-of (CommandExited :rc 0) 0 5000 10)) VERIFY-STEP-ENDED))
  (assert (= (run (verify-step-of (CommandExited :rc 0) 0 50000 10)) VERIFY-STEP-ENDED) "終わった命令を期限で止めた")
  (assert (= (run (verify-step-of (CommandGone) 0 5000 10)) VERIFY-STEP-LOST))
  (assert (= (run (verify-step-of (CommandRunning :pid 1) 0 5000 10)) VERIFY-STEP-OBSERVE))
  (assert (= (run (verify-step-of (CommandRunning :pid 1) 0 10000 10)) VERIFY-STEP-TIMED-OUT))
  (assert (= (run (verify-step-of (CommandRunning :pid 1) 0 10000000 0)) VERIFY-STEP-OBSERVE) "期限 0 で止めた")
  (setv plan (run (verify-plan-of (verify-row "vj-1" VERIFY-ID 9000 PHASE-BOUND) HOME RUNS)))
  (assert (isinstance plan VerifyPlan))
  (assert (= plan.script-path SCRIPT))
  (assert (= plan.deadline-seconds 9000))
  (assert (= plan.rc-path f"{RUNS}/vj-1.rc"))
  (setv bad (run (verify-plan-of (verify-row "vj-1" "Land Verify" 9000 PHASE-BOUND) HOME RUNS)))
  (assert (isinstance bad str))
  (assert (in "pattern" bad))
  (setv homeless (run (verify-plan-of (verify-row "vj-1" VERIFY-ID 9000 PHASE-BOUND) "" RUNS)))
  (assert (isinstance homeless str))
  ;; 負の・偽の deadline は 0(期限なし)に落ちる — 発明しない
  (setv odd (replace (verify-row "vj-1" VERIFY-ID 9000 PHASE-BOUND)
                     :spec {"subject" VERIFY-ID "inputs" []
                            "charter" {"kind" "verify" "place" "company" "jobId" VERIFY-ID "deadlineSeconds" True}}))
  (setv odd-plan (run (verify-plan-of odd HOME RUNS)))
  (assert (isinstance odd-plan VerifyPlan))
  (assert (= odd-plan.deadline-seconds 0))
  (assert (= odd-plan.run-key ""))
  (setv argv (run (verify-argv-of plan)))
  (assert (= (len argv) 7))
  (assert (= (get argv 0) "/bin/sh"))
  (assert (in "echo $$ > \"$0\"" (get argv 2)))
  (assert (in "\"$1\" >> \"$2\" 2>&1" (get argv 2)))
  (assert (in "echo $? > \"$3\"" (get argv 2)))
  ;; path は位置引数で運ぶ — 文字列に埋めない
  (assert (not-in SCRIPT (get argv 2))))
