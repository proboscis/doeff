;;; 借りた錠を返す腕の焦点の検(card acp:kanban-issue:ki-f2747267e24d B2 / B3・ADR-DOE-AGENTS-012 R51)。
;;;
;;; 実弾 2026-09-19 08:44Z(Mac Proboscis-MBP の agentd の入れ替え): 手番の CLI が host と共に死に、
;;; 新しい agentd の拾い直しが「器に session が無い」を見て手番を閉じたが、貸与の id は死んだ process の
;;; memory(InFlightJob.lease-id)にしか無く、錠は返せなかった。預かり所の錠は hold の 900 秒そのまま残り、
;;; その間 pool の pod の借りは全部 409 — 17 時台 JST に 330 通の郵便が failed。
;;;
;;; ここで撃つのは
;;;   * 手元の journal(job の id → 貸与の id)の純関数(読み・足し・外し・綴り)と「いつ返すか」の 1 点
;;;     (lease-to-return-of — memory が先・無ければ journal)
;;;   * fake の handler で agentd を一周: 借りた拍に journal が載り、返した拍に消える
;;;   * ★ 反例(実弾の形): agentd を入れ替え(memory を捨て)器から session が消えた拍に、
;;;     拾い直しの腕が journal の貸与の id で錠を返す
;;;   * 排水(SIGTERM)で閉じた手番も錠を返し、journal から消える
;;;   * 返せなかった拍(預かり所が 200 で答えない)は黙って捨てず log 1 行(B3)
;;;   * journal の置き場は state_dir の下の 1 点(composition root が据える)
;;; HTTP も subprocess も無い。

(require doeff-hy.macros [deftest])

(import json)
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AGENT-JOB-KIND
  AGENT-JOB-NAMESPACE
  AGORA-KINDS-NAMESPACE
  AcpRow
  AgentdSettings
  MESSAGE-KIND
  NODE-KIND
  PHASE-BOUND
  PHASE-ENDED
  PHASE-RUNNING
  LeaseRefused
  TURN-RECORD-KIND])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [
  attempt-refused?
  credential-lease-held-condition-of
  lease-journal-of
  lease-journal-text
  lease-journal-with
  lease-journal-without
  lease-to-return-of])
(import doeff_agents.sessionhost.acp.runtime [
  initial-state
  lease-journal-path
  run-close-for-stop
  run-tick])

(setv NODE "mac-1")
(setv HOMES "/homes")
(setv TOKEN "sk-ant-oat01-secret-token")
(setv ACCOUNT "acct")
(setv JOURNAL "/state/acp-agentd/leases.json")
(setv CONVERSATION "c-01ARZ3NDEKTSV4RRFFQ69G5FAV")


(defn #^ AcpRow bound-job [#^ str job-id]
  "配置が結んだ手番の行(binding が口座を名乗る — 預かり所から借りる経路)。"
  (AcpRow :namespace AGENT-JOB-NAMESPACE
          :key f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{job-id}"
          :kind AGENT-JOB-KIND :resource-id job-id :version "v1" :generation 1 :created-at-ms 500
          :labels {} :payload {}
          :spec {"subject" CONVERSATION "inputs" []
                 "charter" {"session_id" f"charter-{job-id}" "session_name" f"charter-{job-id}"
                            "agent_type" "claude" "work_dir" "/work" "prompt" "start"
                            "model" "claude-opus-5"}}
          :status {"phase" PHASE-BOUND
                   "binding" {"node" NODE "profile" "personal" "account" ACCOUNT}
                   "conditions" []}))


(defclass World []
  "fake の 4 handler + 値の宣言(錠の腕を撃つ最小の世界)。journal は FakeLocal.files = 機体の disk。"
  (defn #^ None __init__ [self]
    (setv self.settings (AgentdSettings :node-name NODE :homes-root HOMES :node-capacity 1
                                        :lease-journal-path JOURNAL))
    (setv self.acp (FakeAcp :births {TURN-RECORD-KIND (Birth "state" "running")
                                     NODE-KIND (Birth "state" "joined")}))
    (.put-row self.acp (AcpRow :namespace AGORA-KINDS-NAMESPACE
                               :key f"{AGORA-KINDS-NAMESPACE}:{NODE-KIND}:{NODE}"
                               :kind NODE-KIND :resource-id NODE :version "v1"
                               :generation 1 :created-at-ms 0 :labels {} :payload {}
                               :spec {"name" NODE "labels" {} "capacity" 1 "streamCapability" "frames"}
                               :status {"state" "joined"}))
    (setv self.custody (FakeCustody :tokens {ACCOUNT TOKEN}))
    (setv self.sessions (FakeSessions))
    (setv self.local (FakeLocal :now-ms 1000))
    (setv self.state (initial-state)))

  (defn #^ None tick [self #^ int advance-ms]
    (setv self.local.now-ms (+ self.local.now-ms advance-ms))
    (setv self.state
          (run-tick self.settings self.state
                    [self.acp.dispatch self.custody.dispatch self.sessions.dispatch self.local.dispatch]))
    None)

  (defn #^ None restart [self]
    "agentd の入れ替え(process の memory を捨てて生まれの状態から始める — disk は残る)。"
    (setv self.state (initial-state))
    None)

  (defn #^ None close-for-stop [self #^ str signal-name]
    (setv self.state
          (run-close-for-stop self.settings self.state
                              [self.acp.dispatch self.custody.dispatch self.sessions.dispatch self.local.dispatch]
                              signal-name))
    None)

  (defn #^ AcpRow job [self #^ str job-id]
    (get self.acp.rows f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{job-id}"))

  (defn #^ str sid [self #^ str job-id]
    (get (get (. (.job self job-id) status) "sessionHandle") "sessionId"))

  (defn #^ dict journal [self]
    "機体の disk の journal(無い = 空)。"
    (setv text (.get self.local.files JOURNAL))
    (if (is text None) {} (json.loads text))))


;; ---------------------------------------------------------------- 純関数(journal の読みと「いつ返すか」)

(deftest test-lease-journal-reads-and-writes-only-the-pairs-it-knows
  ;; 無い file・綴りでない text・object でない JSON・str でない値は空(発明しない)。
  (assert (= (run (lease-journal-of None)) {}))
  (assert (= (run (lease-journal-of "")) {}))
  (assert (= (run (lease-journal-of "{")) {}) "途中で切れた file は空(読めない = 何も握っていない)")
  (assert (= (run (lease-journal-of "[1, 2]")) {}))
  (assert (= (run (lease-journal-of (json.dumps {"aj-1" "lease-1" "aj-2" 7 "aj-3" None})))
             {"aj-1" "lease-1"})
          "str → str の組だけ運ぶ")
  ;; 足し・外しは元の dict を触らない(純関数)。
  (setv before {"aj-1" "lease-1"})
  (setv after (run (lease-journal-with before "aj-2" "lease-2")))
  (assert (= after {"aj-1" "lease-1" "aj-2" "lease-2"}))
  (assert (= before {"aj-1" "lease-1"}) "元の journal は触らない")
  (setv dropped (run (lease-journal-without after "aj-1")))
  (assert (= dropped {"aj-2" "lease-2"}))
  (assert (= (run (lease-journal-without dropped "aj-9")) {"aj-2" "lease-2"}) "無い job を外しても壊れない")
  ;; 綴りは読み直せる(round trip)。
  (assert (= (run (lease-journal-of (run (lease-journal-text after)))) after)))


(deftest test-lease-to-return-prefers-memory-then-the-journal
  ;; 「いつ返すか」は 1 点: memory の貸与の id が在ればそれ、無ければ journal、どちらも無ければ返さない。
  (setv journal {"aj-1" "lease-j"})
  (assert (= (run (lease-to-return-of "lease-m" journal "aj-1")) "lease-m") "memory が先")
  (assert (= (run (lease-to-return-of None journal "aj-1")) "lease-j") "memory が無ければ journal")
  (assert (is (run (lease-to-return-of None journal "aj-2")) None) "journal にも無い job は返さない")
  (assert (is (run (lease-to-return-of None {} "aj-1")) None)))


;; ---------------------------------------------------------------- 一周(借りた拍に載り、返した拍に消える)

(deftest test-the-borrowed-lease-is-journalled-and-forgotten-when-it-is-returned
  (setv world (World))
  (.put-row world.acp (bound-job "s-1"))
  (.tick world 0)
  (assert (= world.custody.borrowed [#("claude" ACCOUNT "agent-job s-1")]))
  (assert (= (.journal world) {"s-1" "lease-1"})
          "借りた拍に job の id → 貸与の id が機体の disk に載る(B2)")
  (.finish world.sessions (.sid world "s-1") "done" {"ok" True})
  (.tick world 500)
  (assert (= (get (. (.job world "s-1") status) "phase") PHASE-ENDED))
  (assert (= world.custody.revoked ["lease-1"]))
  (assert (= (.journal world) {}) "返した拍に journal から消える(次の agentd が二度返さない)"))


;; ---------------------------------------------------------------- ★ 実弾の反例(入れ替え → 器から session が消える)

(deftest test-a-replaced-agentd-returns-the-lease-of-a-vanished-session
  ;; 実弾 2026-09-19 08:44Z: agentd の入れ替えで手番の CLI が死に(session exited: vanished)、拾い直しの腕は
  ;; 器に session が無いのを見て手番を閉じたが、貸与の id は死んだ process の memory にしか無く錠は返せなかった。
  (setv world (World))
  (.put-row world.acp (bound-job "s-1"))
  (.tick world 0)
  (setv sid (.sid world "s-1"))
  (assert (= (.journal world) {"s-1" "lease-1"}))
  ;; agentd を入れ替える: memory は消える・disk の journal は残る・器の session は host と共に消える。
  (.restart world)
  (del (get world.sessions.views sid))
  (.tick world 1000)
  (assert (= (get (. (.job world "s-1") status) "phase") PHASE-ENDED) "行は SessionFailed で閉じる(今日どおり)")
  (assert (= world.custody.revoked ["lease-1"])
          "手番を閉じた process が錠を返す — journal の貸与の id で(B2)")
  (assert (= (.journal world) {}) "返した拍に journal から消える"))


;; ---------------------------------------------------------------- 排水(SIGTERM)で閉じた手番

(deftest test-the-drain-returns-the-lease-of-the-turn-it-closes
  (setv world (World))
  (.put-row world.acp (bound-job "s-1"))
  (.tick world 0)
  (assert (= (.journal world) {"s-1" "lease-1"}))
  (.close-for-stop world "SIGTERM")
  (assert (= world.custody.revoked ["lease-1"]) "排水で閉じた手番も錠を返す(B2)")
  (assert (= (.journal world) {}) "排水の後の disk に握りは残らない"))


;; ---------------------------------------------------------------- B3: 返せなかった拍は log 1 行

(deftest test-a-refused-revoke-is-not-swallowed
  (setv world (World))
  (.put-row world.acp (bound-job "s-1"))
  (.tick world 0)
  (setv world.custody.revoke-ok False)
  (.finish world.sessions (.sid world "s-1") "done" {"ok" True})
  (.tick world 500)
  (assert (= world.custody.revoked ["lease-1"]) "撃ってはいる")
  (setv lines (lfor line world.local.logs :if (and (in "lease-1" line) (in "s-1" line)) line))
  (assert (= (len lines) 1) f"返せなかった錠の log が 1 行でない: {world.local.logs}")
  (assert (in "custody" (get lines 0)) "どこへ返せなかったかを名乗る(B3)"))


;; ---------------------------------------------------------------- 置き場の定義点

(deftest test-the-journal-lives-under-the-state-dir
  ;; 置き場の定義点を増やさない(state_dir は spool と同じ 1 点から導く — verify / summarize の置き場と同じ形)。
  (setv env {"DOEFF_AGENTD_RECORD_SPOOL_DIR" "/state/acp-agentd/record-spool"})
  (assert (= (lease-journal-path env) "/state/acp-agentd/leases.json")))


;; ---------------------------------------------------------------- B1: 409 は失った試みの記録で、手番の終わりではない

(deftest test-a-custody-409-with-a-hold-is-recorded-and-does-not-end-the-turn
  ;; 実弾 2026-09-19 17 時台 JST: 錠が旧 process のまま残り、pool の pod の借りが全部 409 で断られた。
  ;; 断りを Ended(CredentialUnavailable)と書くと配達係の再試行(上限 2・backoff なし)が数秒で尽き、
  ;; 330 通の郵便が failed になった。409 は「いまこの口座を借りられなかった」だけ — 錠は hold の期限で解ける。
  (setv world (World))
  (setv world.custody.refuse-with (LeaseRefused 409 "account acct is held by borrower sa:acp-control/default" 1900000))
  (.put-row world.acp (bound-job "s-1"))
  (.tick world 0)
  (setv status (. (.job world "s-1") status))
  ;; ⚠ 断りの拍の行は **Running**(Bound ではない): claim-job は借りより先に Running + sessionHandle を CAS で
  ;; 書くので、借りが断られた時には既に受けている。だから読み手(ACP の配置)はこの記録を #519 の
  ;; ProviderLimit と同じ **Running の行の supervision** として読む。phase は触らない。
  (assert (= (get status "phase") PHASE-RUNNING) f"409 の断りで phase を動かした: {status}")
  (assert (not-in "result" status) "結末は書かない(手番は結果を報告していない)")
  (assert (= (get (get status "sessionHandle") "sessionId") (.sid world "s-1")) "sessionHandle も触らない")
  (setv held (lfor c (get status "conditions") :if (= (get c "type") "CredentialLeaseHeld") c))
  (assert (= (len held) 1) f"CredentialLeaseHeld の記録が 1 行でない: {(get status "conditions")}")
  (setv record (get held 0))
  (assert (= (get record "status") "True"))
  (assert (= (get record "reason") "account acct is held by borrower sa:acp-control/default") "断りの逐語")
  (assert (= (get record "attempt") 1) "行の binding.attempt(欄の無い結びは 1)")
  (assert (= (get record "at") world.local.now-ms) "記録を書いた拍の時計")
  (assert (= (get record "until") 1900000) "錠が解ける時刻 = 預かり所の holdExpiresAt")
  (assert (= (get record "account") "acct") "借りられなかった口座")
  (assert (= world.state.jobs #()) "memory には載せない(置き直しを待つ行)")
  (assert (= world.sessions.launches []) "器は起こさない"))


(deftest test-the-row-of-a-held-lease-is-not-started-again-on-the-next-beat
  ;; 記録を置いたまま次の拍が同じ行を起動すると、同じ錠をまた借りにいって同じ 409 を数え続ける
  ;; (拾い直しの腕が走れば器に session が無いので SessionFailed で Ended — #519 と同じ穴)。
  (setv world (World))
  (setv world.custody.refuse-with (LeaseRefused 409 "held by another borrower" 1900000))
  (.put-row world.acp (bound-job "s-1"))
  (.tick world 0)
  (setv borrows (len world.custody.borrowed))
  (setv before (. (.job world "s-1") status))
  (.tick world 1000)
  (setv after (. (.job world "s-1") status))
  (assert (!= (get after "phase") PHASE-ENDED) f"次の拍が手番を閉じた: {after}")
  (assert (= (len world.custody.borrowed) borrows) "同じ試みでもう一度借りにいかない")
  (assert (= (len (lfor c (get after "conditions") :if (= (get c "type") "CredentialLeaseHeld") c)) 1)
          "記録は 1 行のまま(拍ごとに積まない)")
  (assert (= world.sessions.launches []) "器は起こさない")
  ;; 置き直し(配置が attempt を進めて結び直す)た行は今日どおり受ける。
  (setv placed (dict after))
  (setv (get placed "binding") {"node" NODE "profile" "personal" "account" ACCOUNT "attempt" 2})
  (setv (get placed "phase") PHASE-BOUND)
  (assert (is (run (attempt-refused? placed)) False) "attempt が進んだ行は起動する")
  (assert (is (run (attempt-refused? before)) True) "同じ試みの行は起動しない"))


(deftest test-refusals-that-are-not-a-held-lease-still-end-the-turn
  ;; 待って直る保証の無い断り(口座が預かり所に無い・宣言が無い・hold を名乗らない 409)は今日のまま Ended。
  (for [refusal [(LeaseRefused 404 "account not in custody" None)
                 (LeaseRefused 503 "custody URL is not declared" None)
                 (LeaseRefused 409 "held (no hold time)" None)]]
    (setv world (World))
    (setv world.custody.refuse-with refusal)
    (.put-row world.acp (bound-job "s-1"))
    (.tick world 0)
    (setv status (. (.job world "s-1") status))
    (assert (= (get status "phase") PHASE-ENDED) f"{refusal} で閉じなかった: {status}")
    (setv types (lfor c (get status "conditions") (get c "type")))
    (assert (in "CredentialUnavailable" types) f"{refusal}: {types}")
    (assert (not-in "CredentialLeaseHeld" types) f"{refusal}: {types}")))


(deftest test-the-held-lease-record-is-one-judgment
  ;; 「記録にするか Ended にするか」は 1 関数(呼び手に第 2 の判定を置かない)。
  (setv status {"phase" PHASE-RUNNING
                "binding" {"node" NODE "profile" "personal" "account" ACCOUNT "attempt" 3 "nodeRow" "nr-1"}})
  (setv record (run (credential-lease-held-condition-of (LeaseRefused 409 "held by sa:acp-control/default" 90000) status 7000)))
  (assert (= record {"type" "CredentialLeaseHeld" "status" "True" "reason" "held by sa:acp-control/default"
                     "attempt" 3 "at" 7000 "until" 90000 "account" ACCOUNT "nodeRow" "nr-1"}))
  ;; 409 でない・hold を名乗らない断りは記録にしない(None = 呼び手は今日どおり Ended)。
  (for [refusal [(LeaseRefused 404 "account not in custody" None)
                 (LeaseRefused 503 "custody URL is not declared" None)
                 (LeaseRefused 500 "custody is down" 90000)
                 (LeaseRefused 409 "held" None)]]
    (assert (is (run (credential-lease-held-condition-of refusal status 7000)) None) f"{refusal} が記録に解けた"))
  ;; 欄の無い結びは attempt 1・口座と行の id を名乗らない結びはその欄を落とす(発明しない)。
  (setv bare (run (credential-lease-held-condition-of (LeaseRefused 409 "held" 90000) {"phase" PHASE-RUNNING} 7000)))
  (assert (= bare {"type" "CredentialLeaseHeld" "status" "True" "reason" "held" "attempt" 1 "at" 7000 "until" 90000}))
  ;; ProviderLimit と同じ 1 点で「この試みは断られた」と読める(拾いの判定は 1 つ)。
  (setv refused-status {"phase" PHASE-RUNNING "binding" {"attempt" 3} "conditions" [record]})
  (assert (is (run (attempt-refused? refused-status)) True))
  (setv next-attempt {"phase" PHASE-RUNNING "binding" {"attempt" 4} "conditions" [record]})
  (assert (is (run (attempt-refused? next-attempt)) False)))
