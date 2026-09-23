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
;;;   * 同じ機体の verify は上限 N 本(card acp:kanban-issue:ki-9b728780cfac): 判定 verify-claim-verdict の性質(全組み合わせ)・
;;;     09-23 の 4 本が作成時刻の古い順に 1 本ずつ起きる(鍵の順ではない)・上限 2 なら 2 本・再起動で Running の行を数える・
;;;     置き直された行の生きている命令は引き取る(2 本目を起こさない)・判定の呼び手は受け口の 1 か所・上限は宣言の 1 点
;;; fake の handler で同じ program(agentd.hy)を一周させる。HTTP も subprocess も無い。

(require doeff-hy.macros [deftest])

(import os)
(import dataclasses [replace])
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects :as effects-module)
(import doeff_agents.sessionhost.acp.join :as join)
(import doeff_agents.sessionhost.acp.effects [
  AGENT-JOB-KIND
  AGENT-JOB-NAMESPACE
  AGENT-JOB-WORK-DIR-ENV
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
  CONDITION-WORK-DIR-MISSING
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
  VERIFY-CONCURRENCY-ENV
  JoinArgv
  JoinDeclaration
  METRIC-VERIFY-CLAIM-HELD
  METRIC-VERIFY-COMMAND-ADOPTED
  VerifyClaimVerdict
  VerifyPlan])
(import doeff_agents.sessionhost.acp.fake [FakeAcp FakeCustody FakeLocal FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [
  job-kind-of
  plan-with-node-home
  verify-argv-of
  verify-claim-verdict
  verify-env-of
  verify-plan-of
  verify-step-of])
(import doeff_agents.sessionhost.acp.runtime [initial-state run-tick settings-from-env])


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


(deftest test-verify-job-runs-on-a-node-that-declares-no-agent-kind
  ;; ADR-DOE-AGENTS-012 R61: 起動前の検査(AgentKindUnavailable)は agent を起こす job だけの門 — verify は agent を起こさない
  ;; ので、host が実行ファイルを 1 つも見つけない(申告が空の)node でも今日どおり script を走らせる。
  (setv world (World))
  (setv world.sessions.driver-paths {"claude" None "codex" None})
  (.put-row world.acp (verify-row "vj-7" VERIFY-ID 9000 PHASE-BOUND))
  (.tick world 0)
  (assert (= world.state.agent-kinds #()) world.state.agent-kinds)
  (assert (= (get (.status world "vj-7") "phase") PHASE-RUNNING) (.status world "vj-7"))
  (assert (= (.conditions world "vj-7") []))
  (assert (= (len world.local.commands) 1) "申告の空な node で verify を走らせなかった"))


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
  ;; 消えた(rc 無し・pid 死)= VerifyCommandLost(結末 rc を発明しない — result は終端の cause だけ)/ 期限超過 = CommandStop +
  ;; VerifyDeadlineExceeded。result の cause は ADR-DOE-AGENTS-012 R47(c39d033e — 全部の Ended の書きが result.cause を運ぶ・
  ;; 命令の族は command-cause-of = failed / 先頭の条件の型)。
  (setv world (World))
  (.put-row world.acp (verify-row "vj-1" VERIFY-ID 9000 PHASE-BOUND))
  (.tick world 0)
  (setv pid (. (get world.state.commands 0) pid))
  (.discard world.local.alive-pids pid)
  (.tick world 1000)
  (setv status (.status world "vj-1"))
  (assert (= (get status "phase") PHASE-ENDED))
  (assert (= (get status "result") {"cause" {"category" "failed" "reason" CONDITION-VERIFY-COMMAND-LOST}})
          "消えた命令に結末を発明した(result は cause だけ)")
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


;; ---------------------------------------------------------------------------
;; 同じ機体の verify は上限 N 本(card acp:kanban-issue:ki-9b728780cfac・依頼 lt-A3ST0CMSHSTP2PBBBA38YVQTZD・設計
;; agent-control-plane docs/design-checks/lt-3CXH09FC999PXC6D12RZ9EXZCG)
;; ---------------------------------------------------------------------------
;;
;; 2026-09-23 08:16 の実弾の 4 本(runKey そのまま)。**鍵の順**は acp < mediagen < orch < proboscis-ema、**作成時刻(予定時刻)の順**は
;; proboscis-ema 02:20 < acp 03:00 < mediagen 04:30 < orch 06:10 — 2 つの順を分けて置く(判定を行ごとに候補 1 本で呼ぶ形は
;; 鍵の順で起きるので、下の振る舞いの検が「最古から 1 本だけ起こしていない」で赤になる — 設計 6.1 盲検 B・反例の実行は commit 本文)。

(setv BURST #(#("land-verify-acp-29835000" "land-verify-acp" 1790103600000)
              #("land-verify-mediagen-29835090" "land-verify-mediagen" 1790109000000)
              #("land-verify-orch-29835190" "land-verify-orch" 1790115000000)
              #("land-verify-proboscis-ema-29834960" "land-verify-proboscis-ema" 1790101200000)))
(setv BURST-BY-CREATION ["land-verify-proboscis-ema-29834960" "land-verify-acp-29835000"
                         "land-verify-mediagen-29835090" "land-verify-orch-29835190"])
(setv BURST-SCRIPTS-BY-CREATION ["land-verify-proboscis-ema.sh" "land-verify-acp.sh" "land-verify-mediagen.sh" "land-verify-orch.sh"])


(defn #^ AcpRow burst-row [#^ str run-key #^ str verify-id #^ int created-ms]
  "実弾の 1 本: job の id = runKey(k8s の Job 名)・charter.jobId = script の名・created-at-ms = 予定時刻。"
  (setv base (verify-row run-key verify-id 9000 PHASE-BOUND))
  (setv spec (dict base.spec))
  (setv (get spec "charter") (| (get spec "charter") {"jobId" verify-id "runKey" run-key}))
  (replace base :spec spec :created-at-ms created-ms))


(defn #^ None put-burst [world rows]
  (for [[run-key verify-id created-ms] rows]
    (.add world.local.existing-files f"{HOME}/{VERIFY-SCRIPTS-RELDIR}/{verify-id}.sh")
    (.put-row world.acp (burst-row run-key verify-id created-ms)))
  None)


(defn #^ list started-scripts [world]
  "起こした命令の script の名(argv の $1)— 起こした順。"
  (lfor argv world.local.commands (get (.split (get argv 4) "/") -1)))


(defn #^ list held-metrics [world]
  (lfor m world.local.metrics :if (= (.get m "metric") METRIC-VERIFY-CLAIM-HELD) m))


(defn #^ None re-place-as-bound [world #^ str job-id]
  "配置(ACP Decide.lostRunner)の形: 走っていた行を Bound(attempt 2)に置き直す(sessionHandle は消える・binding は配置が書く)。"
  (setv row (.job world job-id))
  (setv status (dict row.status))
  (setv (get status "phase") PHASE-BOUND)
  (setv (get status "binding") {"node" NODE "attempt" 2 "at" 900})
  (.pop status "sessionHandle" None)
  (.put-row world.acp (replace row :status status :generation (+ row.generation 1)))
  None)


(deftest test-verify-claim-verdict-is-pure-and-orders-by-creation-time
  ;; 性質: 候補 0〜6 本 × 走っている 0〜2 本 × 上限 1〜3 の全組み合わせで、上限を超えない・claim ∩ hold = ∅・claim ∪ hold = 候補・
  ;; claim は (作成時刻, id) の順の先頭から。作成時刻は id の順と**逆**に置き、同時刻の対も入れる(同時刻は id の順)。
  (for [n-candidates (range 0 7) n-running (range 0 3) limit (range 1 4)]
    (setv in-flight (frozenset (gfor j (range n-running) f"running-{j}")))
    (setv candidates (tuple (gfor i (range n-candidates)
                                  (replace (verify-row f"cand-{i}" VERIFY-ID 9000 PHASE-BOUND)
                                           :created-at-ms (* 1000 (// (- (+ n-candidates 1) i) 2))))))
    (setv verdict (run (verify-claim-verdict in-flight candidates limit)))
    (assert (isinstance verdict VerifyClaimVerdict))
    (setv claim-ids (lfor row verdict.claim row.resource-id))
    (setv hold-ids (lfor row verdict.hold row.resource-id))
    (setv label f"候補 {n-candidates}・走行中 {n-running}・上限 {limit}")
    (when (<= n-running limit)
      (assert (<= (+ n-running (len claim-ids)) limit) f"{label}: 上限を超えて起こす {claim-ids}"))
    (when (> n-running limit)
      (assert (= claim-ids []) f"{label}: 上限を越えているのに起こす"))
    (assert (= (& (set claim-ids) (set hold-ids)) (set)) f"{label}: claim ∩ hold ≠ ∅")
    (assert (= (+ (len claim-ids) (len hold-ids)) n-candidates) f"{label}: 候補が増減した")
    (assert (= (| (set claim-ids) (set hold-ids)) (sfor row candidates row.resource-id)) f"{label}: claim ∪ hold ≠ 候補")
    (setv expected (lfor row (sorted candidates :key (fn [row] #(row.created-at-ms row.resource-id))) row.resource-id))
    (setv room (max 0 (- limit n-running)))
    (assert (= claim-ids (cut expected 0 room))
            f"{label}: claim が (作成時刻, id) の順の先頭ではない: {claim-ids} ≠ {(cut expected 0 room)}")
    (assert (= hold-ids (cut expected room None)) f"{label}: hold の順が作成時刻の順ではない")
    ;; 純関数: 同じ入力は同じ答え・候補の並びに依らない
    (assert (= (run (verify-claim-verdict in-flight candidates limit)) verdict))
    (assert (= (run (verify-claim-verdict in-flight (tuple (reversed candidates)) limit)) verdict)
            f"{label}: 候補の並びで答えが変わる")))


(deftest test-burst-of-four-verify-rows-runs-one-at-a-time-oldest-first
  ;; 09-23 08:16 の形: 停止の間に溜まった 4 本が同じ拍に Bound。1 拍目は**最古**(proboscis-ema — 鍵の順では最後)だけ起き、
  ;; 他 3 本は Bound のまま・generation 不変・条件なし(hold の行には書かない)。log と計器 verify-claim-held は待たせ始めた拍に
  ;; 1 行ずつ(次の拍は増えない)。終わらせると次に古い acp、以後 1 本ずつ。
  (setv world (World))
  (put-burst world BURST)
  (setv before (dfor [run-key _ _] BURST run-key (. (.job world run-key) generation)))
  (.tick world 0)
  (assert (= (started-scripts world) ["land-verify-proboscis-ema.sh"]) f"最古から 1 本だけ起こしていない: {(started-scripts world)}")
  (assert (= (get (.status world "land-verify-proboscis-ema-29834960") "phase") PHASE-RUNNING))
  (for [run-key ["land-verify-acp-29835000" "land-verify-mediagen-29835090" "land-verify-orch-29835190"]]
    (assert (= (get (.status world run-key) "phase") PHASE-BOUND) run-key)
    (assert (= (. (.job world run-key) generation) (get before run-key)) f"hold の行に書いた: {run-key}")
    (assert (= (.conditions world run-key) []) f"hold の行に条件を足した: {run-key}"))
  (assert (= (sorted (lfor m (held-metrics world) (get m "agentJobId")))
             ["land-verify-acp-29835000" "land-verify-mediagen-29835090" "land-verify-orch-29835190"]))
  (assert (= (len (lfor line world.local.logs :if (in "held" line) line)) 3))
  (assert (= world.state.held-verify-ids
             (frozenset ["land-verify-acp-29835000" "land-verify-mediagen-29835090" "land-verify-orch-29835190"])))
  ;; 2 拍目(まだ走っている): 起こさない・待ちの log と計器は増えない(待たせ始めた拍だけ)
  (.tick world 1000)
  (assert (= (len world.local.commands) 1) "走っている間に 2 本目を起こした")
  (assert (= (len (held-metrics world)) 3) "待ちの計器を毎拍書いた")
  (assert (= (len (lfor line world.local.logs :if (in "held" line) line)) 3) "待ちの log を毎拍書いた")
  ;; 終わらせると次に古い acp(鍵の順では最初・作成時刻の順では 2 番目)
  (.finish world "land-verify-proboscis-ema-29834960" 0)
  (.tick world 1000)
  (.tick world 1000)
  (assert (= (started-scripts world) ["land-verify-proboscis-ema.sh" "land-verify-acp.sh"]) f"次に古い 1 本を起こしていない: {(started-scripts world)}")
  (assert (= (get (.status world "land-verify-proboscis-ema-29834960") "phase") PHASE-ENDED))
  (assert (= (get (.status world "land-verify-acp-29835000") "phase") PHASE-RUNNING))
  (assert (= world.state.held-verify-ids (frozenset ["land-verify-mediagen-29835090" "land-verify-orch-29835190"])))
  (assert (= (len (held-metrics world)) 3) "待ち続ける行に計器を書き直した")
  ;; 全部終わるまで作成時刻の順に 1 本ずつ
  (.finish world "land-verify-acp-29835000" 0)
  (.tick world 1000)
  (.tick world 1000)
  (.finish world "land-verify-mediagen-29835090" 0)
  (.tick world 1000)
  (.tick world 1000)
  (assert (= (started-scripts world) BURST-SCRIPTS-BY-CREATION) (started-scripts world))
  (assert (= world.state.held-verify-ids (frozenset)))
  (assert (= world.sessions.launches []) "verify の直列化が session を起こした(反例)")
  (assert (= world.custody.borrowed []) "verify の直列化が札を借りた(反例)"))


(deftest test-declared-verify-concurrency-two-runs-two-oldest-first
  ;; 上限は node の宣言(AgentdSettings.verify-concurrency)の 1 値: 2 なら最古の 2 本(proboscis-ema と acp)が同じ拍に起き、
  ;; 残り 2 本は Bound のまま。
  (setv world (World))
  (setv world.settings (replace world.settings :verify-concurrency 2))
  (put-burst world BURST)
  (.tick world 0)
  (assert (= (started-scripts world) ["land-verify-proboscis-ema.sh" "land-verify-acp.sh"]) (started-scripts world))
  (for [run-key ["land-verify-mediagen-29835090" "land-verify-orch-29835190"]]
    (assert (= (get (.status world run-key) "phase") PHASE-BOUND) run-key))
  (assert (= (len (held-metrics world)) 2))
  (.finish world "land-verify-acp-29835000" 0)
  (.tick world 1000)
  (.tick world 1000)
  (assert (= (started-scripts world) ["land-verify-proboscis-ema.sh" "land-verify-acp.sh" "land-verify-mediagen.sh"]))
  (assert (= (len world.state.commands) 2)))


(deftest test-restart-counts-the-running-verify-row-before-claiming
  ;; 再起動(memory 空): 自分の Running の verify の行 1 本 + Bound 3 本 → 1 拍で CommandStart 0 回(拾い直しは claim の後に
  ;; 走るので、行から数えないと 2 本目を起こす)。走っている 1 本が終われば作成時刻の順に 1 本ずつ。
  (setv world (World))
  (.put-row world.acp (verify-row "vj-1" VERIFY-ID 9000 PHASE-BOUND))
  (.tick world 0)
  (assert (= (len world.local.commands) 1))
  (put-burst world (cut BURST 0 3))
  (setv world.state (initial-state))
  (.tick world 1000)
  (assert (= (len world.local.commands) 1) "再起動の直後に 2 本目を起こした(Running の行を数えていない)")
  (assert (= (lfor c world.state.commands c.job-id) ["vj-1"]) "Running の行を拾い直していない")
  (for [[run-key _ _] (cut BURST 0 3)]
    (assert (= (get (.status world run-key) "phase") PHASE-BOUND) run-key))
  (assert (= (len (held-metrics world)) 3))
  (.finish world "vj-1" 0)
  (.tick world 1000)
  (.tick world 1000)
  (assert (= (started-scripts world) [f"{VERIFY-ID}.sh" "land-verify-acp.sh"]) (started-scripts world)))


(deftest test-replaced-running-verify-is-adopted-not-started-again
  ;; 化身の交代(設計 6.1 盲検 A): 走っている verify の process が生きたまま、配置がその行を Bound(attempt 2)に置き直し、memory の
  ;; 無い新しい agentd が受ける → 起こし直さず Running + 手札を書いて観測を引き継ぐ(CommandStart 0 回・pid は古いもの)。
  ;; 引き取った 1 本は走っている数に入る(同じ拍の別の Bound は Bound のまま)。
  (setv world (World))
  (.put-row world.acp (verify-row "vj-1" VERIFY-ID 9000 PHASE-BOUND))
  (.tick world 0)
  (setv pid (. (get world.state.commands 0) pid))
  (assert (in pid world.local.alive-pids))
  (setv world.state (initial-state))
  (re-place-as-bound world "vj-1")
  (put-burst world (cut BURST 0 1))
  (.tick world 1000)
  (assert (= (len world.local.commands) 1) "生きている命令の job に 2 本目を起こした")
  (assert (= (get (.status world "vj-1") "phase") PHASE-RUNNING) (.status world "vj-1"))
  (assert (= (lfor c world.state.commands c.pid) [pid]) "引き取った命令の pid が file の値ではない")
  (setv handle (.verify-handle world "vj-1"))
  (assert (isinstance handle dict))
  (assert (= (get handle "jobId") VERIFY-ID))
  (assert (= (get handle "pidPath") f"{RUNS}/vj-1.pid"))
  (assert (= (get (.status world "vj-1") "binding") {"node" NODE "attempt" 2 "at" 900}) "binding を書き換えた(書き手は配置)")
  (assert (= (get (.status world "land-verify-acp-29835000") "phase") PHASE-BOUND) "引き取った 1 本を数えずに別の日次を起こした")
  (assert (any (gfor line world.local.logs (in "adopted, no second command" line))))
  (assert (= (len (lfor m world.local.metrics :if (= (.get m "metric") METRIC-VERIFY-COMMAND-ADOPTED) m)) 1))
  ;; 引き取った命令の結末は今日どおり(rc を result に写して Ended)、その次の拍で待っていた 1 本が起きる
  (.finish world "vj-1" 0)
  (.tick world 1000)
  (assert (= (get (.status world "vj-1") "phase") PHASE-ENDED))
  (assert (= (get (get (.status world "vj-1") "result") "rc") 0))
  (.tick world 1000)
  (assert (= (started-scripts world) [f"{VERIFY-ID}.sh" "land-verify-acp.sh"]))
  ;; agentd の居ない間に**終わっていた**(rc の file が在る)置き直しの行も起こし直さず、その rc で閉じる(古い rc で 2 本目を閉じない)
  (setv done (World))
  (.put-row done.acp (verify-row "vj-2" VERIFY-ID 9000 PHASE-BOUND))
  (.tick done 0)
  (.finish done "vj-2" 3)
  (setv done.state (initial-state))
  (re-place-as-bound done "vj-2")
  (.tick done 1000)
  (assert (= (len done.local.commands) 1) "終わっていた命令の job を起こし直した")
  (assert (= (get (.status done "vj-2") "phase") PHASE-ENDED) (.status done "vj-2"))
  (assert (= (get (get (.status done "vj-2") "result") "rc") 3) "終わっていた命令の rc を結末に写していない")
  (assert (= done.state.commands #()))
  ;; pid が死んで rc も無い(消えた)置き直しの行は命令が無い — 今日どおり起こす(pid の file が在るだけでは引き取らない)
  (setv gone (World))
  (.put-row gone.acp (verify-row "vj-3" VERIFY-ID 9000 PHASE-BOUND))
  (.tick gone 0)
  (.discard gone.local.alive-pids (. (get gone.state.commands 0) pid))
  (setv gone.state (initial-state))
  (re-place-as-bound gone "vj-3")
  (.tick gone 1000)
  (assert (= (len gone.local.commands) 2) "消えた命令の job を起こし直していない")
  (assert (= (get (.status gone "vj-3") "phase") PHASE-RUNNING)))


(defn #^ list source-lines [#^ str name]
  (setv acp-dir (os.path.dirname effects-module.__file__))
  (with [f (open (os.path.join acp-dir name) :encoding "utf-8")]
    (.splitlines (.read f))))


(defn #^ list defk-body [#^ list lines #^ str head]
  "(defk <head> … から次の最上位の (def… まで。"
  (setv start (next (gfor [i line] (enumerate lines) :if (.startswith line f"(defk {head} ") i)))
  (setv end (next (gfor [i line] (enumerate lines) :if (and (> i start) (.startswith line "(def")) i) (len lines)))
  (cut lines start end))


(deftest test-verify-claim-verdict-has-one-caller-and-the-arm-does-not-read-the-limit
  ;; 構造: 判定は judgment の 1 点・呼び手は受け口の側(claim-verify-candidates)の 1 か所・受け口(receive-bound-jobs)が候補を
  ;; そこへ渡す(verify の腕を直に呼ばない)・verify の腕(claim-verify-job)は上限を読まず判定も呼ばない・上限を読む行は全部
  ;; claim-verify-candidates の中・verify の腕の呼び手は claim-job の 1 か所のまま(ADR-DOE-AGENTS-012 R36)。
  (setv judgment-lines (source-lines "judgment.hy"))
  (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk verify-claim-verdict ") line)) 1) "判定は judgment の 1 点")
  (setv agentd-lines (source-lines "agentd.hy"))
  (setv code-lines (lfor line agentd-lines :if (not (.startswith (.strip line) ";")) line))
  (setv callers (lfor line code-lines :if (in "(verify-claim-verdict " line) line))
  (assert (= (len callers) 1) f"判定の呼び手は受け口の側の 1 か所: {callers}")
  (setv candidates-body (.join "\n" (defk-body agentd-lines "claim-verify-candidates")))
  (assert (in "(verify-claim-verdict " candidates-body) "判定の呼び手が claim-verify-candidates ではない")
  (setv receive-body (.join "\n" (defk-body agentd-lines "receive-bound-jobs")))
  (assert (in "(claim-verify-candidates " receive-body) "受け口が verify の候補を判定へ渡していない")
  (assert (not-in "(claim-verify-job " receive-body) "受け口が verify の腕を直に呼ぶ(判定を経ない経路)")
  (setv arm-body (.join "\n" (defk-body agentd-lines "claim-verify-job")))
  (assert (not-in "verify-concurrency" arm-body) "verify の腕が上限を読む(上限を知るのは受け口の側の 1 点)")
  (assert (not-in "verify-claim-verdict" arm-body) "verify の腕が判定を呼ぶ(行ごとの判定 = 鍵の順で起きる形)")
  (setv readers (lfor line code-lines :if (in "settings.verify-concurrency" line) line))
  (assert readers "上限を読む行が無い")
  (for [line readers]
    (assert (in line candidates-body) f"上限を読むのは claim-verify-candidates だけ: {line}"))
  (setv arm-callers (lfor line code-lines :if (in "(claim-verify-job " line) line))
  (assert (= (len arm-callers) 1) f"verify の腕の呼び手は claim-job の 1 か所: {arm-callers}"))


(deftest test-verify-concurrency-is-a-node-declaration-with-default-one
  ;; 上限は node の宣言: 既定 1(effects.AgentdSettings の 1 点)・宣言 file の [agentd].verify_concurrency → JoinSpec → env
  ;; DOEFF_AGENTD_VERIFY_CONCURRENCY → AgentdSettings。無し = 名乗らない(env に現れない)・1 未満と数でない値は参加 / 起動を断る。
  (assert (= (. (AgentdSettings :node-name NODE) verify-concurrency) 1))
  (assert (is (run (join.verify-concurrency-of None)) None))
  (assert (is (run (join.verify-concurrency-of "  ")) None))
  (assert (= (run (join.verify-concurrency-of "2")) 2))
  (for [bad ["0" "-1" "two" "1.5"]]
    (setv raised False)
    (try
      (run (join.verify-concurrency-of bad))
      (except [ValueError]
        (setv raised True)))
    (assert raised f"1 未満・数でない宣言を断っていない: {bad !r}"))
  (setv flags ["--server" "http://acp:8868" "--token-file" "/t/agentd.token" "--capacity" "1" "--places" "company"])
  (setv bare (run (join.join-spec-of (JoinArgv :items (tuple flags)) (JoinDeclaration :tables {}) "/state")))
  (assert (is bare.verify-concurrency None))
  (assert (not-in VERIFY-CONCURRENCY-ENV (dict (. (run (join.join-plan-of bare)) env))) "既定を join が env に写した")
  (setv declared (run (join.join-spec-of (JoinArgv :items (tuple flags))
                                          (JoinDeclaration :tables {"schema" "doeff.agentd-join.v1" "agentd" {"verify_concurrency" "2"}})
                                          "/state")))
  (assert (= declared.verify-concurrency 2))
  (assert (= (get (dict (. (run (join.join-plan-of declared)) env)) VERIFY-CONCURRENCY-ENV) "2"))
  (setv refused-join False)
  (try
    (run (join.join-spec-of (JoinArgv :items (tuple flags))
                            (JoinDeclaration :tables {"schema" "doeff.agentd-join.v1" "agentd" {"verify_concurrency" "0"}})
                            "/state"))
    (except [ValueError]
      (setv refused-join True)))
  (assert refused-join "上限 0 の宣言で参加した")
  (setv env {"DOEFF_AGENTD_NODE_NAME" NODE "RECORD_SERVICE_URL" "http://record:8874" "DOEFF_AGENTD_CAPACITY" "1" "DOEFF_AGENTD_PLACES" "company"})
  (assert (= (. (settings-from-env env #()) verify-concurrency) 1))
  (assert (= (. (settings-from-env (| env {VERIFY-CONCURRENCY-ENV "3"}) #()) verify-concurrency) 3))
  (setv refused False)
  (try
    (settings-from-env (| env {VERIFY-CONCURRENCY-ENV "0"}) #())
    (except [ValueError]
      (setv refused True)))
  (assert refused "上限 0 の宣言で起動した"))


;; ---------------------------------------------------------------------------
;; 測る作業場(card acp:kanban-issue:ki-0a50e47ac56d・依頼 lt-YVG7DDQ0N2M9J183B3KF8QPQX0・R36 (6))
;; ---------------------------------------------------------------------------
;;
;; charter が work_dir(家からの相対 `~/repos/<区画>`)を名乗る verify は、手番と同じ作業場の門(R32 — plan-with-node-home の展開 →
;; work-dir-step-of の段 → work-dir-ready)を通る。無ければ script を走らせず WorkDirMissing(配置は件名 × node × work_dir でこの
;; node を外す)、在れば展開した絶対 path を env AGENT_JOB_WORK_DIR ちょうど 1 つで script に渡す。名乗らない verify は今日の形。
;; 実弾 2026-09-24: 作業コピーの無い GCP の会社の機体に結ばれた proboscis-ema(rc 2)と mediagen(rc 1)が毎日即終了していた。

(setv WORK-DIR "~/repos/mediagen")
(setv EXPANDED-WORK-DIR f"{HOME}/repos/mediagen")


(defn #^ AcpRow verify-row-at [#^ str job-id #^ (| str None) work-dir]
  "work_dir を名乗る(None なら名乗らない)Bound の verify の行。"
  (setv base (verify-row job-id VERIFY-ID 9000 PHASE-BOUND))
  (setv spec (dict base.spec))
  (when (is-not work-dir None)
    (setv (get spec "charter") (| (get spec "charter") {"work_dir" work-dir})))
  (replace base :spec spec))


(deftest test-verify-job-whose-work-dir-is-missing-here-is-not-run
  ;; 反例 = 作業コピーの無い機体で script を走らせる(09-24 の即終了)。無い → CommandStart 0 回・session も札も無し・
  ;; Ended + 条件 WorkDirMissing(reason に node の名と展開した path)・無い作業場を作らない。
  (setv world (World))
  (.add world.local.missing-dirs EXPANDED-WORK-DIR)
  (.put-row world.acp (verify-row-at "vj-wd-missing" WORK-DIR))
  (.tick world 0)
  (assert (= world.local.commands []) "作業場の無い機体で script を走らせた(反例)")
  (assert (= world.sessions.launches []))
  (assert (= world.custody.borrowed []))
  (assert (in EXPANDED-WORK-DIR world.local.dir-checks) "この node の家で展開した path を読んでいない")
  (assert (= (get (.status world "vj-wd-missing") "phase") PHASE-ENDED))
  (setv conditions (.conditions world "vj-wd-missing"))
  (assert (= (lfor c conditions (get c "type")) [CONDITION-WORK-DIR-MISSING])
          f"条件が WorkDirMissing ちょうどではない: {conditions}")
  (setv reason (get (get conditions 0) "reason"))
  (assert (and (in NODE reason) (in EXPANDED-WORK-DIR reason)) f"reason が node と path を名指さない: {reason}")
  (assert (not-in EXPANDED-WORK-DIR world.local.made-dirs) "無い作業場を作った(測る作業コピーを空の dir で偽装しない)")
  (assert (= world.state.commands #()) "走らせていない命令を memory に置いた"))


(deftest test-verify-job-whose-work-dir-is-here-gets-the-expanded-path-in-one-env
  ;; 在る → 起こす・env は AGENT_JOB_WORK_DIR = 家で展開した絶対 path ちょうど 1 つ・argv と cwd は今日の形(path は argv に出ない)。
  (setv world (World))
  (.put-row world.acp (verify-row-at "vj-wd-here" WORK-DIR))
  (.tick world 0)
  (assert (= (len world.local.commands) 1) "作業場の在る機体で起こしていない")
  (assert (= (get world.local.command-envs 0) {AGENT-JOB-WORK-DIR-ENV EXPANDED-WORK-DIR})
          f"env が展開した作業場ちょうど 1 つではない: {(get world.local.command-envs 0)}")
  (setv argv (get world.local.commands 0))
  (assert (= (get argv 4) SCRIPT))
  (assert (not-in EXPANDED-WORK-DIR argv) "作業場を argv に埋めた(env 1 つで渡す)")
  (assert (= (get world.local.command-cwds 0) HOME))
  (assert (= (get (.status world "vj-wd-here") "phase") PHASE-RUNNING))
  (assert (= (.conditions world "vj-wd-here") [])))


(deftest test-verify-job-without-a-work-dir-keeps-todays-shape
  ;; 名乗らない verify(今日の charter)= 作業場を読まない・env を足さない・今日どおり起こす。
  (setv world (World))
  (.put-row world.acp (verify-row-at "vj-no-wd" None))
  (.tick world 0)
  (assert (= (len world.local.commands) 1))
  (assert (= (get world.local.command-envs 0) {}) "名乗らない verify に env を足した")
  (assert (= world.local.dir-checks []) f"名乗らない verify の作業場を読んだ: {world.local.dir-checks}")
  (assert (= (get (.status world "vj-no-wd") "phase") PHASE-RUNNING)))


(deftest test-verify-row-whose-work-dir-is-missing-does-not-take-the-slot
  ;; 資格は上限の前(設計 6.2 改訂 4): 作業場の無い古い行は枠を使わず WorkDirMissing で閉じ、同じ拍に次の行が起きる(待たせない)。
  (setv world (World))
  (.add world.local.missing-dirs EXPANDED-WORK-DIR)
  (.put-row world.acp (replace (verify-row-at "vj-old-missing" WORK-DIR) :created-at-ms 100))
  (.put-row world.acp (replace (verify-row-at "vj-new-plain" None) :created-at-ms 200))
  (.tick world 0)
  (assert (= (get (.status world "vj-old-missing") "phase") PHASE-ENDED))
  (assert (= (lfor c (.conditions world "vj-old-missing") (get c "type")) [CONDITION-WORK-DIR-MISSING]))
  (assert (= (get (.status world "vj-new-plain") "phase") PHASE-RUNNING) "作業場の無い行が枠を使って次の行を待たせた")
  (assert (= (len world.local.commands) 1))
  (assert (= (held-metrics world) []) "作業場の無い行のために次の行を待たせた"))


(deftest test-verify-plan-work-dir-is-expanded-at-the-one-point-and-carried-as-one-env
  ;; 純関数: verify-plan-of は宣言の綴りのまま写す → plan-with-node-home(手番と同じ 1 点)が `~` だけを家で展開 → verify-env-of が
  ;; env 1 つにする。絶対 path は触らない・名乗らない plan は env を持たない。
  (setv planned (run (verify-plan-of (verify-row-at "vj-pure" WORK-DIR) HOME RUNS)))
  (assert (= planned.work-dir WORK-DIR) "宣言の綴りのまま写していない(展開は plan-with-node-home の 1 点)")
  (setv located (run (plan-with-node-home planned HOME)))
  (assert (= located.work-dir EXPANDED-WORK-DIR))
  (assert (= (run (verify-env-of located)) #(#(AGENT-JOB-WORK-DIR-ENV EXPANDED-WORK-DIR))))
  (assert (= (. (run (plan-with-node-home (replace planned :work-dir "/srv/mediagen") HOME)) work-dir) "/srv/mediagen")
          "絶対 path を書き換えた")
  (setv bare (run (verify-plan-of (verify-row-at "vj-bare" None) HOME RUNS)))
  (assert (is bare.work-dir None))
  (assert (= (run (verify-env-of (run (plan-with-node-home bare HOME)))) #())))
