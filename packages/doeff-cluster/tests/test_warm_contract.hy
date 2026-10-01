;;; 実行環境の先読み(WarmRuntimeEnv・ReadWarmState)の契約テスト — 同じ effect に答える本物(warm-cluster → coordinator の /warm)と
;;; fake(sim-cluster の送り手の口)が、同じ deftest を通る。解釈器の組み立ては coordinator_contract_handlers.hy。
;;;
;;;   * 頼みの答え: 行のキー(宣言と needs の組 — warm_model.warm-key)・期限(頼んだ時刻 + ttl)・合う担い手が居なければ準備済みも準備中も空
;;;   * 頼んだ行が coordinator の側に同じ形で載る(needs・holder・宣言の JSON・期限)・読みの答えは頼みの答えと同じ姿
;;;   * 同じ組の頼み直しは同じ行で、期限だけ延びる
;;;   * 表に無い行の読みは空の姿(期限 0)
;;;   * 断り: 期限の範囲の外の頼みは DetachedRefused(400・理由の文)で、行を書かない
;;;   * 届かない: coordinator に届かない間の頼みも読みも、例外でなく WarmUnreachable(理由は接続の失敗)
;;; 契約の外: 準備済み・準備中の担い手の数(本物の側の担い手は env を準備しない — sim-cluster の上の検 test_env_warm_runners.hy が持つ)・
;;; coordinator の 5xx の答え(本物だけの検 test_env_warm.hy)。
(require doeff-hy.macros [defk deftest <- val var])
(import pytest)
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.detached_model [DetachedRefused])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json])
(import doeff_cluster.shared.intent.warm_model [ReadWarmState WarmState WarmUnreachable])
(import doeff_cluster.shared.core.warm_rules [warm-key warm-runtime-env])
(import tests.coordinator_contract_handlers [WarmsSeen WarmSeen SetReachable contract-env])
(import tests.env_fixtures [LOCK env-of])

;; どの担い手も提供しない能力(両方の組で「合う担い手が居ない」行 — 準備済みの数は契約の外)。
(val NEEDS (frozenset ["contract-gpu"]))
(val HOLDER "contract-holder")
(val TTL-SECONDS 600.0)
(val LATER-SECONDS 10.0)
(val MISSING-KEY "000000000000000000000000")


(deftest test-a-warm-request-answers-its-row-and-sits-on-the-coordinator
  {:interpreters ["warm-cluster" "sim-cluster"]}
  (<- env RuntimeEnv (contract-env))
  (<- declared dict (runtime-env->json env))
  (<- key str (warm-key env (tuple (sorted NEEDS))))
  (<- asked int (now-epoch-ms))
  (<- written WarmState (warm-runtime-env env NEEDS TTL-SECONDS HOLDER))
  (<- answered int (now-epoch-ms))
  (<- read WarmState (ReadWarmState key))
  (<- rows tuple (WarmsSeen))
  ;; 期限 = coordinator が頼みを受けた時刻 + ttl(受けた時刻は頼んでから答えを受けるまでの間 — sim の coordinator は次の拍で受ける)。
  (val ttl-ms (int (* 1000 TTL-SECONDS)))
  (assert (<= (+ asked ttl-ms) written.until-ms (+ answered ttl-ms)) #(asked answered written))
  (assert (= written (WarmState :key key :ready #() :preparing #() :failed #() :until-ms written.until-ms)) written)
  (assert (= read written) #(read written))
  (assert (= rows #((WarmSeen :key key :needs (tuple (sorted NEEDS)) :holder HOLDER :runtime-env declared
                              :until-ms written.until-ms)))
          rows))


(deftest test-asking-again-extends-the-same-row
  {:interpreters ["warm-cluster" "sim-cluster"]}
  (<- env RuntimeEnv (contract-env))
  (<- first WarmState (warm-runtime-env env NEEDS TTL-SECONDS HOLDER))
  (<- (Delay LATER-SECONDS))
  (<- again WarmState (warm-runtime-env env NEEDS TTL-SECONDS HOLDER))
  (<- rows tuple (WarmsSeen))
  (assert (= again.key first.key) #(first again))
  (assert (= (- again.until-ms first.until-ms) (int (* 1000 LATER-SECONDS))) #(first again))
  (assert (= (lfor row rows #(row.key row.until-ms)) [#(again.key again.until-ms)]) rows))


(deftest test-reading-a-row-that-is-not-there-answers-an-empty-state
  {:interpreters ["warm-cluster" "sim-cluster"]}
  (<- read WarmState (ReadWarmState MISSING-KEY))
  (assert (= read (WarmState :key MISSING-KEY :ready #() :preparing #() :failed #() :until-ms 0)) read))


(deftest test-a-lifetime-out-of-range-is-refused-and-writes-no-row
  {:interpreters ["warm-cluster" "sim-cluster"]}
  (<- env RuntimeEnv (contract-env))
  ;; 断りの #(status 理由) — 断られなければ #(None "")。
  (var refusal #(None ""))
  (try
    (<- (warm-runtime-env env NEEDS 0.0 HOLDER))
    (except [error DetachedRefused]
      (:= refusal #(error.status error.message))))
  (<- rows tuple (WarmsSeen))
  (assert (= (get refusal 0) 400) refusal)
  (assert (in "ttlSeconds" (get refusal 1)) refusal)
  (assert (= rows #()) rows))


(deftest test-an-unreachable-coordinator-is-answered-as-unreachable
  {:interpreters ["warm-cluster" "sim-cluster"]}
  (<- env RuntimeEnv (contract-env))
  (<- key str (warm-key env (tuple (sorted NEEDS))))
  (<- (SetReachable False))
  (<- written (warm-runtime-env env NEEDS TTL-SECONDS HOLDER))
  (<- read (ReadWarmState key))
  (for [answer #(written read)]
    (assert (isinstance answer WarmUnreachable) answer)
    (assert (in "接続できない" answer.detail) answer)))


(deftest test-warm-runtime-env-refuses-empty-or-old-needs
  ;; 失敗ケース(#2564): WarmRuntimeEnv は構築関数 warm-runtime-env(core)を通してだけ作る。needs が能力の名の空でない frozenset で
  ;; なければ(空・旧い Requirement の組の tuple・label の形の名)、頼む前に TypeError で断る(ADR-DOE-CLUSTER-001 R4b)。断るのは
  ;; 構築関数で、型(intent)は core の判断を読まない。handler を並べないので、断らずに出せば UnhandledEffect になり TypeError に当たらない。
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (with [raised (pytest.raises TypeError)]
    (<- (warm-runtime-env env (frozenset) TTL-SECONDS HOLDER)))
  (assert (in "WarmRuntimeEnv.needs" (str raised.value)) raised.value)
  (assert (in "空" (str raised.value)) raised.value)
  ;; 旧い形: Requirement の (label value) の組の tuple。
  (with [raised (pytest.raises TypeError)]
    (<- (warm-runtime-env env #(#("kind" "k3s")) TTL-SECONDS HOLDER)))
  (assert (in "frozenset" (str raised.value)) raised.value)
  (with [raised (pytest.raises TypeError)]
    (<- (warm-runtime-env env (frozenset ["kind=k3s"]) TTL-SECONDS HOLDER)))
  (assert (in "kind=k3s" (str raised.value)) raised.value))
