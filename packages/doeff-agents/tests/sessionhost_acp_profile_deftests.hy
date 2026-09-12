;;; agentd が profile の残量の観測(status.observed)を書く腕の焦点の検(段 7 lane 7d-3・
;;; agora-redesign 7d の登記済み間隙 2・ADR-DOE-AGENTS-012 R18)。
;;;
;;; 既知の形 = kubelet の node status: 観測は runner(agentd)が書き、判断(枯渇)は controller
;;; (agora-budget)。ここで撃つのは
;;;   * 観測 → post-image の純関数(窓の選び方・percent の残量・resetAt・committed の欄の写し)
;;;   * 断られた profile(会社境界の断り — 判定は agentcli の葉)と単位の違う profile は書かない
;;;   * 世代の競合(Conflict)は 1 拍見送り、次の周期に書く
;;;   * 変わった時だけ書く・周期(profile_observe_seconds)の刻印・この機体に無い profile は黙る
;;; fake の handler で同じ program(agentd.hy)を一周させる。HTTP も subprocess も無い。

(require doeff-hy.macros [deftest])

(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AGORA-KINDS-NAMESPACE
  AcpRow
  AgentdSettings
  NODE-KIND
  PROFILE-KIND
  PROFILE-USAGE-KIND
  ProfileNotHeld
  ProfileObservation
  ProfileUnobserved
  ProfileUsage
  ProfileUsageUnavailable
  UsageWindow])
(import doeff_agents.sessionhost.acp.fake [FakeAcp FakeCustody FakeLocal FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [
  observed-window-of
  profile-observed-changed
  profile-observed-of
  profile-rows-active
  profile-status-with-observed])
(import doeff_agents.sessionhost.acp.runtime [initial-state run-tick])


(setv NODE "mac-1")
(setv CAPTURED-MS 1700000000000)
(setv RESETS-MS 1700001000000)
(setv CONDITIONS [{"type" "ProfileExhausted" "status" "Unknown" "reason" "unobserved"}])


(defn #^ AcpRow profile-row [#^ str name #^ str unit #^ int every #^ str state
                             #^ (| dict None) observed]
  "契約 profile の 1 行(status = state + controller の conditions + 在れば observed)。"
  (setv status {"state" state "conditions" (list CONDITIONS)})
  (when (is-not observed None)
    (setv (get status "observed") observed))
  (AcpRow :namespace AGORA-KINDS-NAMESPACE
          :key f"{AGORA-KINDS-NAMESPACE}:{PROFILE-KIND}:{name}"
          :kind PROFILE-KIND :resource-id name :version "v1" :generation 2 :created-at-ms 0
          :labels {} :payload {}
          :spec {"name" name "boundary" "personal"
                 "budget" {"amount" 100 "unit" unit}
                 "reset" {"everySeconds" every}
                 "seats" 2}
          :status status))


(defn #^ ProfileUsage usage-of [#^ str name #^ float used-5h #^ (| int None) resets-5h]
  (ProfileUsage :profile name :captured-at-ms CAPTURED-MS
                :windows #((UsageWindow :name "5h" :used-percent used-5h :resets-at-ms resets-5h)
                           (UsageWindow :name "7d" :used-percent 10.0 :resets-at-ms None))))


(defclass World []
  "fake の 4 handler + 値の宣言 + Node の行(観測の腕を撃つ最小の世界)。"
  (defn #^ None __init__ [self]
    (setv self.settings (AgentdSettings :node-name NODE :homes-root "/homes"))
    (setv self.acp (FakeAcp :births {}))
    (.put-row self.acp (AcpRow :namespace AGORA-KINDS-NAMESPACE
                               :key f"{AGORA-KINDS-NAMESPACE}:{NODE-KIND}:{NODE}"
                               :kind NODE-KIND :resource-id NODE :version "v1"
                               :generation 1 :created-at-ms 0 :labels {} :payload {}
                               :spec {"name" NODE "labels" {} "capacity" 1 "streamCapability" "frames"}
                               :status {"state" "joined"}))
    (setv self.custody (FakeCustody))
    (setv self.sessions (FakeSessions))
    (setv self.local (FakeLocal :now-ms 1000))
    (setv self.state (initial-state)))

  (defn #^ None tick [self #^ int advance-ms]
    (setv self.local.now-ms (+ self.local.now-ms advance-ms))
    (setv self.state
          (run-tick self.settings self.state
                    [self.acp.dispatch self.custody.dispatch
                     self.sessions.dispatch self.local.dispatch]))
    None)

  (defn #^ dict status-of [self #^ str name]
    (setv row (get self.acp.rows f"{AGORA-KINDS-NAMESPACE}:{PROFILE-KIND}:{name}"))
    (if (isinstance row.status dict) row.status {}))

  (defn #^ list profile-writes [self]
    (lfor [key status] self.acp.writes :if (in f":{PROFILE-KIND}:" key) #(key status)))

  (defn #^ list profile-logs [self]
    (lfor line self.local.logs :if (in "agentd: profile " line) line)))


;; ---------------------------------------------------------------------------
;; 観測 → post-image の純関数
;; ---------------------------------------------------------------------------

(deftest test-profile-observed-of-builds-the-post-image
  ;; percent の budget・5h の reset → 5h の窓: remaining = 100 - used・resetAt = 窓の戻る時刻・
  ;; observedAt = 断面の時刻・node = 自分。
  (setv row (profile-row "personal" "percent" 18000 "active" None))
  (setv verdict (run (profile-observed-of row (usage-of "personal" 40.0 RESETS-MS) NODE)))
  (assert (isinstance verdict ProfileObservation))
  (assert (= verdict.observed {"window" "5h" "remaining" 60.0 "resetAt" RESETS-MS
                               "observedAt" CAPTURED-MS "node" NODE}))
  ;; post-image は committed の status(state・conditions = 他の書き手の欄)を写して observed を据える。
  (setv status (run (profile-status-with-observed row verdict.observed)))
  (assert (= (get status "state") "active"))
  (assert (= (get status "conditions") CONDITIONS))
  (assert (= (get status "observed") verdict.observed))
  ;; 使い切り(used > 100)は 0 に留め、窓が空(resets_at 無し)なら resetAt = 観測の時刻(待つ窓が無い)。
  (setv spent (run (profile-observed-of row (usage-of "personal" 120.0 None) NODE)))
  (assert (isinstance spent ProfileObservation))
  (assert (= (get spent.observed "remaining") 0.0))
  (assert (= (get spent.observed "resetAt") CAPTURED-MS))
  ;; 変わった時だけ: committed と同じ observed は changed = False。
  (setv same (profile-row "personal" "percent" 18000 "active" verdict.observed))
  (assert (is (run (profile-observed-changed same verdict.observed)) False))
  (assert (is (run (profile-observed-changed row verdict.observed)) True)))


(deftest test-profile-window-follows-the-reset-period
  ;; 窓の選び方は observed-window-of の 1 点: reset と周期が一致する窓、無ければ既定 5h。
  (assert (= (run (observed-window-of (profile-row "p" "percent" 18000 "active" None))) "5h"))
  (assert (= (run (observed-window-of (profile-row "p" "percent" 604800 "active" None))) "7d"))
  (assert (= (run (observed-window-of (profile-row "p" "percent" 3600 "active" None))) "5h"))
  (setv weekly (run (profile-observed-of (profile-row "p" "percent" 604800 "active" None)
                                         (usage-of "p" 40.0 RESETS-MS) NODE)))
  (assert (isinstance weekly ProfileObservation))
  (assert (= (get weekly.observed "window") "7d"))
  (assert (= (get weekly.observed "remaining") 90.0))
  ;; 選んだ窓が答えに無ければ書かない(発明しない)。
  (setv bare (ProfileUsage :profile "p" :captured-at-ms CAPTURED-MS :windows #()))
  (setv missing (run (profile-observed-of (profile-row "p" "percent" 18000 "active" None) bare NODE)))
  (assert (isinstance missing ProfileUnobserved))
  (assert (in "no 5h window" missing.reason)))


(deftest test-profile-refused-and-foreign-unit-are-not-written
  ;; 断られた profile(会社境界 — 判定は agentcli の葉)は書かない(理由は log に 1 行)。
  (setv refused (run (profile-observed-of (profile-row "ca" "percent" 18000 "active" None)
                                          (ProfileUsageUnavailable :profile "ca" :reason "company-boundary: host unverified")
                                          NODE)))
  (assert (isinstance refused ProfileUnobserved))
  (assert (= refused.reason "company-boundary: host unverified"))
  ;; 単位が percent でない budget は remaining の単位が無いので書かない。
  (setv tokens (run (profile-observed-of (profile-row "t" "tokens" 18000 "active" None)
                                         (usage-of "t" 40.0 RESETS-MS) NODE)))
  (assert (isinstance tokens ProfileUnobserved))
  (assert (in "tokens" tokens.reason))
  ;; この機体に無い profile は持たない(書かず log もしない)。
  (assert (isinstance (run (profile-observed-of (profile-row "x" "percent" 18000 "active" None) None NODE))
                      ProfileNotHeld))
  ;; retired の行は観測しない。
  (setv rows #((profile-row "a" "percent" 18000 "active" None)
               (profile-row "r" "percent" 18000 "retired" None)))
  (assert (= (lfor row (run (profile-rows-active rows)) row.resource-id) ["a"]))
  ;; 一周: refused / tokens は行に observed が立たず、log に 1 行ずつ。
  (setv world (World))
  (.put-row world.acp (profile-row "ca" "percent" 18000 "active" None))
  (.put-row world.acp (profile-row "t" "tokens" 18000 "active" None))
  (.put-row world.acp (profile-row "x" "percent" 18000 "active" None))
  (setv (get world.local.usage PROFILE-USAGE-KIND)
        #((ProfileUsageUnavailable :profile "ca" :reason "company-boundary: host unverified")
          (usage-of "t" 40.0 RESETS-MS)))
  (.tick world 0)
  (assert (= (.profile-writes world) []))
  (assert (not-in "observed" (.status-of world "ca")))
  (assert (not-in "observed" (.status-of world "t")))
  (assert (not-in "observed" (.status-of world "x")))
  (setv logs (.profile-logs world))
  (assert (= (len logs) 2) logs)
  (assert (any (gfor line logs (and (in "profile ca" line) (in "company-boundary" line)))))
  (assert (any (gfor line logs (and (in "profile t" line) (in "tokens" line)))))
  (assert (not (any (gfor line logs (in "profile x" line)))) "持たない profile は log しない"))


;; ---------------------------------------------------------------------------
;; 一周: 書く・変わった時だけ・世代の競合は 1 拍見送る・周期
;; ---------------------------------------------------------------------------

(deftest test-profile-tick-writes-observed-only-when-changed
  (setv world (World))
  (.put-row world.acp (profile-row "personal" "percent" 18000 "active" None))
  (setv (get world.local.usage PROFILE-USAGE-KIND) #((usage-of "personal" 40.0 RESETS-MS)))
  (.tick world 0)
  ;; usage は 1 度読まれ(cache の寿命 = 観測の周期)、行に observed が立つ。conditions は残る。
  (assert (= world.local.usage-reads [#(PROFILE-USAGE-KIND world.settings.profile-observe-seconds)]))
  (setv status (.status-of world "personal"))
  (assert (= (get status "observed") {"window" "5h" "remaining" 60.0 "resetAt" RESETS-MS
                                      "observedAt" CAPTURED-MS "node" NODE}))
  (assert (= (get status "conditions") CONDITIONS))
  (assert (= (get status "state") "active"))
  (assert (= (len (.profile-writes world)) 1))
  (assert (= world.state.last-profile-observed-ms 1000))
  ;; 周期の内は読み直さない・書かない。
  (.tick world 10000)
  (assert (= (len world.local.usage-reads) 1))
  (assert (= (len (.profile-writes world)) 1))
  ;; 周期が来て断面が同じ → 読むが書かない(変わった時だけ)。
  (.tick world (* 1000 world.settings.profile-observe-seconds))
  (assert (= (len world.local.usage-reads) 2))
  (assert (= (len (.profile-writes world)) 1))
  (setv metrics (lfor m world.local.metrics :if (= (get m "metric") "profile-observed") m))
  (assert (= (get (get metrics -1) "unchanged") 1))
  (assert (= (get (get metrics -1) "written") 0))
  ;; 断面が変わる → 書く。
  (setv (get world.local.usage PROFILE-USAGE-KIND) #((usage-of "personal" 70.0 RESETS-MS)))
  (.tick world (* 1000 world.settings.profile-observe-seconds))
  (assert (= (len (.profile-writes world)) 2))
  (assert (= (get (get (.status-of world "personal") "observed") "remaining") 30.0)))


(deftest test-profile-generation-conflict-waits-one-period
  (setv world (World))
  (.put-row world.acp (profile-row "personal" "percent" 18000 "active" None))
  (setv (get world.local.usage PROFILE-USAGE-KIND) #((usage-of "personal" 40.0 RESETS-MS)))
  ;; 読んだ後に他の書き手(controller の conditions)が行を進めた race: この拍の書きは Conflict。
  (setv (get world.acp.conflict-once f"{AGORA-KINDS-NAMESPACE}:{PROFILE-KIND}:personal") 3)
  (.tick world 0)
  (assert (= (.profile-writes world) []))
  (assert (not-in "observed" (.status-of world "personal")))
  (assert (any (gfor line (.profile-logs world) (in "generation moved" line))))
  (setv metrics (lfor m world.local.metrics :if (= (get m "metric") "profile-observed") m))
  (assert (= (get (get metrics -1) "conflicts") 1))
  ;; 見送りは失敗ではない: 刻印は進み、周期の内は撃ち直さない。
  (assert (= world.state.last-profile-observed-ms 1000))
  (.tick world 10000)
  (assert (= (.profile-writes world) []))
  ;; 次の周期に読み直して書く。
  (.tick world (* 1000 world.settings.profile-observe-seconds))
  (assert (= (len (.profile-writes world)) 1))
  (assert (= (get (get (.status-of world "personal") "observed") "remaining") 60.0)))


(deftest test-profile-observation-failure-does-not-stop-the-tick
  ;; usage の読み口が落ちても(RuntimeError = IO の失敗)tick は続き、次の周期へ持ち越す(R9)。
  (setv world (World))
  (.put-row world.acp (profile-row "personal" "percent" 18000 "active" None))
  (setv (get world.acp.list-failures PROFILE-KIND) (RuntimeError "agentd: ACP list of profile failed"))
  (.tick world 0)
  (assert (any (gfor line world.local.logs (in "profile observation failed" line))))
  (assert (= world.state.last-profile-observed-ms 1000))
  (assert (is-not world.state.last-heartbeat-ms None) "heartbeat の腕は観測の失敗で止まらない")
  ;; profile の行が無ければ usage は読まない(計器も出ない)。
  (setv bare (World))
  (.tick bare 0)
  (assert (= bare.local.usage-reads []))
  (assert (= (lfor m bare.local.metrics :if (= (get m "metric") "profile-observed") m) [])))
