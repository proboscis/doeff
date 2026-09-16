;;; agentd が profile の残量の観測(status.observed)を書く腕の焦点の検(段 7 lane 7d-3・
;;; agora-redesign 7d の登記済み間隙 2・ADR-DOE-AGENTS-012 R18)。
;;;
;;; 既知の形 = kubelet の node status: 観測は runner(agentd)が書き、判断(枯渇)は controller
;;; (agora-budget)。ここで撃つのは
;;;   * 観測 → post-image の純関数(窓の選び方・percent の残量・resetAt・committed の欄の写し)
;;;   * 断られた profile(会社境界の断り — 判定は agentcli の葉)と単位の違う profile は書かない
;;;   * 世代の競合(Conflict)は 1 拍見送り、次の周期に書く
;;;   * 変わった時だけ書く・周期(profile_observe_seconds)の刻印・この機体に無い profile は黙る
;;;   * 家の在る profile が 1 つも無い機体(pool の pod・段 8e lane 4j)は usage を撃たず 1 度だけ名乗る
;;; fake の handler で同じ program(agentd.hy)を一周させる。HTTP も subprocess も無い。

(require doeff-hy.macros [deftest])

(import dataclasses [replace])
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AGORA-KINDS-NAMESPACE
  AcpRow
  AgentdSettings
  NODE-KIND
  Ownership
  PROFILE-KIND
  PROFILE-USAGE-KIND
  ProfileHome
  ProfileNotHeld
  ProfileObservation
  ProfileUnobserved
  ProfileUsage
  ProfileUsageUnavailable
  UsageWindow])
(import doeff_agents.sessionhost.acp.fake [FakeAcp FakeCustody FakeLocal FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [
  observed-window-of
  profile-latest-should-replace
  profile-observed-changed
  profile-observed-of
  profile-rows-active
  profile-rows-held
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
  (setv status (run (profile-status-with-observed row verdict.observed NODE 300000)))
  (assert (= (get status "state") "active"))
  (assert (= (get status "conditions") CONDITIONS))
  (assert (= (get status "observed") verdict.observed))
  ;; 段 12 lane 12j(#351): 自分の枡 observedBy[node] にも同じ観測が立つ。
  (assert (= (get (get status "observedBy") NODE) verdict.observed))
  ;; 使い切り(used > 100)は 0 に留め、窓が空(resets_at 無し)なら resetAt = 観測の時刻(待つ窓が無い)。
  (setv spent (run (profile-observed-of row (usage-of "personal" 120.0 None) NODE)))
  (assert (isinstance spent ProfileObservation))
  (assert (= (get spent.observed "remaining") 0.0))
  (assert (= (get spent.observed "resetAt") CAPTURED-MS))
  ;; 変わった時だけ: committed の**自分の枡**(observedBy[node])と同じ観測は changed = False(段 12 lane 12j・#351:
  ;; 最新の 1 枡 observed だけが同じで自分の枡が無い行は、自分の枡を書くので changed = True)。
  (setv same (profile-row "personal" "percent" 18000 "active" verdict.observed))
  (assert (is (run (profile-observed-changed same verdict.observed NODE)) True))
  (setv (get same.status "observedBy") {NODE verdict.observed})
  (assert (is (run (profile-observed-changed same verdict.observed NODE)) False))
  (assert (is (run (profile-observed-changed row verdict.observed NODE)) True)))


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

(deftest test-the-worker-publishes-its-face-and-headroom-on-the-observation-tick
  ;; agora-redesign #445: 会社 profile の残量の公開(dotfiles の艦隊の断面)は退役した headless-worker の拍にしか無く、
  ;; 09-13 から凍っていた。worker 役の常駐 = agentd の観測の拍(家の在る profile を持つ機体)が公開を撃つ。
  (setv world (World))
  (.put-row world.acp (profile-row "personal" "percent" 18000 "active" None))
  (setv (get world.local.usage PROFILE-USAGE-KIND) #((usage-of "personal" 40.0 RESETS-MS)))
  (.tick world 0)
  (assert (= (len world.local.publishes) 1) world.local.publishes)
  (setv published (get world.local.publishes 0))
  (assert (= published.cadence-seconds world.settings.profile-observe-seconds) "公開の拍の申告 = 観測の周期")
  (assert (= published.running-turns #()) "走らせている手番は測った値(0 本)")
  (assert (= published.poll-tick-at-ms 1000) "poll の刻 = この拍の壁時計")
  (setv metrics (lfor m world.local.metrics :if (= (get m "metric") "worker-published") m))
  (assert (= (len metrics) 1))
  (assert (is (get (get metrics 0) "ok") True))
  (assert (= (get (get metrics 0) "worker") "fake-mac"))
  ;; 周期の内は撃ち直さない(観測と同じ拍)
  (.tick world 10000)
  (assert (= (len world.local.publishes) 1))
  ;; 公開が断られても観測の腕は落ちず、log 1 行 + 計器 ok False
  (setv world.local.publish-ok False)
  (.tick world (* 1000 world.settings.profile-observe-seconds))
  (assert (= (len world.local.publishes) 2))
  (assert (any (gfor line world.local.logs (in "worker publish failed" line))) world.local.logs)
  (setv metrics (lfor m world.local.metrics :if (= (get m "metric") "worker-published") m))
  (assert (is (get (get metrics -1) "ok") False))
  (assert (= (len world.local.usage-reads) 2) "観測は続く"))


(deftest test-a-node-without-profile-homes-does-not-publish
  ;; pool の pod(家の在る profile が無い)は usage も公開も撃たない(pod 自身の公開は pod の口)。
  (setv world (World))
  (.put-row world.acp (profile-row "personal" "percent" 18000 "active" None))
  (setv (get world.local.homes PROFILE-USAGE-KIND) #((ProfileHome :name "personal" :home "/homes/personal" :present False)))
  (.tick world 0)
  (assert (= world.local.usage-reads []))
  (assert (= world.local.publishes [])))


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


;; ---------------------------------------------------------------------------
;; 家の在否(段 8e lane 4j): pool の pod は profile を持たない — usage を撃たず 1 度だけ名乗る
;; ---------------------------------------------------------------------------

(deftest test-profile-rows-held-follows-the-homes-on-this-node
  ;; 判断はここ 1 点: 生きている行のうち、家(config dir)の在る profile の行だけ(行の順のまま)。
  (setv rows #((profile-row "personal" "percent" 18000 "active" None)
               (profile-row "ca" "percent" 18000 "active" None)
               (profile-row "vega" "percent" 18000 "active" None)))
  (setv homes #((ProfileHome :name "personal" :home "/homes/personal" :present True)
                (ProfileHome :name "ca" :home "/homes/ca" :present False)
                (ProfileHome :name "kento" :home "/homes/kento" :present True)))
  (assert (= (lfor row (run (profile-rows-held rows homes (AgentdSettings :node-name NODE :homes-root "/homes"))) row.resource-id) ["personal"]))
  ;; 家が 1 つも無い(登録簿はあるが dir が無い = pool の pod)→ 空。登録簿が空でも空。
  (setv absent (tuple (gfor home homes (ProfileHome :name home.name :home home.home :present False))))
  (assert (= (run (profile-rows-held rows absent (AgentdSettings :node-name NODE :homes-root "/homes"))) #()))
  (assert (= (run (profile-rows-held rows #() (AgentdSettings :node-name NODE :homes-root "/homes"))) #())))


(defn #^ AcpRow company-row [#^ str name]
  "契約 profile の 1 行で、口座の置き場が company のもの(会社の口座)。"
  (setv row (profile-row name "percent" 18000 "active" None))
  (AcpRow :namespace row.namespace :key row.key :kind row.kind :resource-id row.resource-id :version row.version
          :generation row.generation :created-at-ms row.created-at-ms :labels row.labels :payload row.payload
          :spec (| row.spec {"boundary" "company"}) :status row.status))


(deftest test-profile-rows-held-keeps-company-accounts-off-a-machine-not-company-owned
  ;; 反例(段 10 lane 10y・agora-redesign #110・operator 指示 2026-09-09「会社 profile の API 呼び出しは会社所有の機体だけ」):
  ;; operator の個人の MacBook(proboscis-mbp・所有 personal)には ca / p10xxx の家が在る。家の在否だけで絞ると、会社の
  ;; 口座の行が観測の列に入り、usage を読む列と log(`agentd: profile ca not observed: …`)に会社 profile が現れる。
  ;; 所有が company でない機体(personal・未宣言)は、家が在っても boundary = company の行を持たない。軸は所有で置き場ではない。
  (setv rows #((profile-row "kento" "percent" 18000 "active" None)
               (company-row "ca")
               (company-row "p10169")))
  (setv homes #((ProfileHome :name "kento" :home "/homes/kento" :present True)
                (ProfileHome :name "ca" :home "/homes/ca" :present True)
                (ProfileHome :name "p10169" :home "/homes/p10169" :present True)))
  (defn #^ list held [#^ (| Ownership None) ownership]
    (lfor row (run (profile-rows-held rows homes (AgentdSettings :node-name NODE :homes-root "/homes" :ownership ownership)))
          row.resource-id))
  (assert (= (held (Ownership :grade "personal" :proof "declared")) ["kento"]))
  (assert (= (held None) ["kento"]) "所有を名乗らない機体は会社所有と読まない(判らないものを許しにしない)")
  ;; 会社所有の機体(会社 Mac — place は personal でも所有は company)は今日どおり会社の口座も観測する。
  (assert (= (held (Ownership :grade "company" :proof "declared")) ["kento" "ca" "p10169"])))


(deftest test-profile-observation-on-a-personal-machine-never-names-a-company-account
  ;; 反例の一周(fake の handler): 所有 personal の機体に会社の口座の家が在っても、usage の答えに会社の口座の断りが
  ;; 在っても、観測の log と書きに会社 profile の名は出ない。
  (setv world (World))
  (setv world.settings (replace world.settings :ownership (Ownership :grade "personal" :proof "declared")))
  (.put-row world.acp (profile-row "kento" "percent" 18000 "active" None))
  (.put-row world.acp (company-row "ca"))
  (setv (get world.local.homes PROFILE-USAGE-KIND)
        #((ProfileHome :name "kento" :home "/homes/kento" :present True)
          (ProfileHome :name "ca" :home "/homes/ca" :present True)))
  (setv (get world.local.usage PROFILE-USAGE-KIND)
        #((usage-of "kento" 40.0 RESETS-MS)
          (ProfileUsageUnavailable :profile "ca" :reason "company-boundary:company-credential-on-noncompany-host")))
  (.tick world 0)
  (assert (= (get (get (.status-of world "kento") "observed") "remaining") 60.0))
  (assert (not-in "observed" (.status-of world "ca")))
  (assert (= (lfor line world.local.logs :if (in "profile ca" line) line) []) world.local.logs)
  (setv metrics (lfor m world.local.metrics :if (= (get m "metric") "profile-observed") m))
  (assert (= (get (get metrics -1) "homes") 1)))


(deftest test-profile-usage-is-not-read-when-no-profile-has-a-home
  ;; 実弾 2026-09-13(pool の agentd・zeus): profile.gen の無い器で `ai usage` が毎周 exit 1
  ;; (FileNotFoundError)を吐いていた。家の在る profile が無い機体は usage を撃たず、
  ;; 「観測する profile なし」を 1 度だけ名乗る。計器は出る(homes 0)。
  (setv world (World))
  (.put-row world.acp (profile-row "personal" "percent" 18000 "active" None))
  (.put-row world.acp (profile-row "ca" "percent" 18000 "active" None))
  (setv (get world.local.usage PROFILE-USAGE-KIND) #((usage-of "personal" 40.0 RESETS-MS)))
  (setv (get world.local.homes PROFILE-USAGE-KIND)
        #((ProfileHome :name "personal" :home "/homes/personal" :present False)
          (ProfileHome :name "ca" :home "/homes/ca" :present False)))
  (.tick world 0)
  (assert (= world.local.home-reads [PROFILE-USAGE-KIND]))
  (assert (= world.local.usage-reads []) "家の無い機体は usage を撃たない")
  (assert (= (.profile-writes world) []))
  (assert (not-in "observed" (.status-of world "personal")))
  (assert (is world.state.no-profile-homes-logged True))
  (setv quiet (lfor line world.local.logs :if (in "no profile has a home" line) line))
  (assert (= (len quiet) 1) quiet)
  (assert (in "registry 2 profiles" (get quiet 0)))
  (assert (in "2 live rows" (get quiet 0)))
  (setv metrics (lfor m world.local.metrics :if (= (get m "metric") "profile-observed") m))
  (assert (= (len metrics) 1))
  (assert (= (get (get metrics -1) "homes") 0))
  (assert (= (get (get metrics -1) "rows") 2))
  (assert (= (get (get metrics -1) "held") 0))
  ;; 次の周期: 家は読み直すが usage は撃たず、名乗りは繰り返さない。
  (.tick world (* 1000 world.settings.profile-observe-seconds))
  (assert (= (len world.local.home-reads) 2))
  (assert (= world.local.usage-reads []))
  (assert (= (len (lfor line world.local.logs :if (in "no profile has a home" line) line)) 1))
  ;; 家が現れたら(借用の後便)usage を読んで書き、印は戻る。
  (setv (get world.local.homes PROFILE-USAGE-KIND)
        #((ProfileHome :name "personal" :home "/homes/personal" :present True)
          (ProfileHome :name "ca" :home "/homes/ca" :present False)))
  (.tick world (* 1000 world.settings.profile-observe-seconds))
  (assert (= (len world.local.usage-reads) 1))
  (assert (= (get (get (.status-of world "personal") "observed") "remaining") 60.0))
  (assert (not-in "observed" (.status-of world "ca")) "家の無い profile は usage の答えに依らず観測しない")
  (assert (is world.state.no-profile-homes-logged False))
  (setv last (get (lfor m world.local.metrics :if (= (get m "metric") "profile-observed") m) -1))
  (assert (= (get last "homes") 1))
  (assert (= (get last "held") 1)))


;; ---------------------------------------------------------------------------
;; 段 12 lane 12j(agora-redesign #351・依頼者の裁定 2026-09-16 (B)): node ごとの枡と最新の 1 枡
;; ---------------------------------------------------------------------------

(deftest test-profile-observation-has-a-slot-per-node-and-replaces-the-latest-only-when-changed-or-stale
  ;; 実弾 2026-09-16: 会社 Mac と mbp が同じ profile の observed(1 枡)を毎周期書き合い、node の名は数秒で消え
  ;; generation だけが進んだ(personal 1436 / btc 1568)。以後 = 自分の枡 observedBy[node] を毎周期・最新の 1 枡は
  ;; 値が変わった時か古い時だけ。
  (setv period 300000)
  (setv other-at (- CAPTURED-MS 60000))
  (setv theirs {"window" "5h" "remaining" 60.0 "resetAt" RESETS-MS "observedAt" other-at "node" "mac-2"})
  (setv mine {"window" "5h" "remaining" 60.0 "resetAt" RESETS-MS "observedAt" CAPTURED-MS "node" NODE})
  (setv row (profile-row "personal" "percent" 18000 "active" theirs))
  (setv (get row.status "observedBy") {"mac-2" theirs})
  ;; 自分の枡が無い = 変化(書く)。最新の 1 枡が他の機体の新しい同じ値でも、比べるのは自分の枡。
  (assert (is (run (profile-observed-changed row mine NODE)) True))
  (setv status (run (profile-status-with-observed row mine NODE period)))
  (assert (= (get status "observedBy") {"mac-2" theirs NODE mine}) "自分の枡だけを据え、他の node の枡は行のまま写す")
  (assert (= (get status "observed") theirs) "同じ値の新しい拍では最新の 1 枡を置き換えない(書き合いの根)")
  (assert (= (get status "state") "active"))
  (assert (= (get status "conditions") CONDITIONS))
  ;; 自分の枡が同じ = 変化なし(最新の 1 枡が誰のものでも)。
  (setv same (profile-row "personal" "percent" 18000 "active" theirs))
  (setv (get same.status "observedBy") {"mac-2" theirs NODE mine})
  (assert (is (run (profile-observed-changed same mine NODE)) False))
  ;; 値が変わった = 最新の 1 枡を置き換える。
  (setv spent (dict mine))
  (setv (get spent "remaining") 30.0)
  (assert (= (get (run (profile-status-with-observed row spent NODE period)) "observed") spent))
  ;; 載っている観測が自分の周期より古い = 置き換える(同じ値でも鮮度で勝つ)。
  (setv stale (dict theirs))
  (setv (get stale "observedAt") (- CAPTURED-MS period))
  (setv old-row (profile-row "personal" "percent" 18000 "active" stale))
  (assert (= (get (run (profile-status-with-observed old-row mine NODE period)) "observed") mine))
  ;; 枡が無い = 置き換える。observedAt を読めない枡は古いと読む。
  (assert (is (run (profile-latest-should-replace None mine period)) True))
  (assert (is (run (profile-latest-should-replace {"window" "5h" "remaining" 60.0 "resetAt" RESETS-MS} mine period)) True))
  (assert (is (run (profile-latest-should-replace theirs mine period)) False))
  ;; tick の一周: 他の機体の最新の 1 枡が載った行に、自分の枡だけが足されて書かれる(1 回)。
  (setv world (World))
  (setv seeded (profile-row "personal" "percent" 18000 "active" theirs))
  (setv (get seeded.status "observedBy") {"mac-2" theirs})
  (.put-row world.acp seeded)
  (setv (get world.local.usage PROFILE-USAGE-KIND) #((usage-of "personal" 40.0 RESETS-MS)))
  (.tick world 0)
  (setv written (.status-of world "personal"))
  (assert (= (get (get written "observedBy") NODE) mine))
  (assert (= (get (get written "observedBy") "mac-2") theirs))
  (assert (= (get written "observed") theirs))
  (assert (= (len (.profile-writes world)) 1)))

