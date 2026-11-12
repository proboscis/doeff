;;; 急ぎを名乗る温める行の待ちの子(2026-11-12)の検: 急ぎの優先(KEEP-NOW-PRIORITY 以上)の task を送る送り手は、その task の root を
;;; 温める頼み(WarmRuntimeEnv)に急ぎを名乗れる。coordinator は行に名乗りの期限(WarmEntry の urgent-until-ms)を持ち、heartbeat の返事で
;;; 急ぎの行と配り、worker はその root の待ちの子を、上に載る系(会話のターン)の残す線を外した jobs の層の空き(WorldView.urgent-jobs-room —
;;; heartbeat の urgent-jobs-room と同じ数え方)で起こしてよいか判じる。置き先が急ぎの task を線を外した空きで置くのと同じ組。
;;;   (1) 本文は急ぎの時だけ urgent を載せ、受け口は行に名乗りの期限を書く。平常の頼み直しは名乗りを消さず、名乗りの期限を過ぎれば
;;;       行は平常の行として配る。保存の綴りを往復しても期限は残り、欄の無い前の行は名乗り無し
;;;   (2) heartbeat の返事の綴り → worker の受け(warm-env-of-row)で急ぎが写る
;;;   (3) 線で平常の空きが 0・線を外した空きが 30 GiB の worker は、急ぎの行の root の待ちの子を起こす
;;;   (4) 失敗ケース: 急ぎを名乗らない行・線を外した空きを名乗らない worker・線を外しても入らない空きでは、待ちの子を起こさない
;;;       (直す前の形 — 2026-11-12 の線を持つ機体は平常の jobs の層の空きが負で、急ぎの送り手の root の待ちの子を起こさなかった)
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff_cluster [ClusterState http-request responded JobSpec StartWarmChild]
        doeff_cluster._coordinator.core.cluster_policy [register-heartbeat heartbeat-reply]
        doeff_cluster._coordinator.protocol.replies [heartbeat-reply-json]
        doeff_cluster._coordinator.protocol.state_json [warm-entry-to-json warm-entry-from-json]
        doeff_cluster._shared.protocol.detached [warm-request-body]
        doeff_cluster._shared.core.warm_rules [warm-state-of-json]
        doeff_cluster._worker.core.heartbeat_rules [warm-env-of-row]
        doeff_cluster._worker.core.policy [warm-child-actions]
        doeff_cluster._worker.intent.worker_model [WarmEnv WarmLaunch])
(import tests.program_rows [heartbeat-of])
(import tests.room_rows [ROOM-JSONS])
(import tests.test_env_warm [TIMING declared-of key-on])
(import tests.test_warm_child_policy [task-on world KEY-A ROOT-A POLICY NOW MIB])

(val PLATFORM "linux-x86_64")
(val NEEDS (frozenset ["agent"]))
;; 2026-11-12 の線を持つ機体の形: 平常の jobs の層の空きは線で縮めて 0・線を外した空きは 30 GiB。
(val URGENT-ROOM (* 30 1024 MIB))


(defk offered [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker w1 への heartbeat の返事を本番の綴り(heartbeat-reply-json)にし、worker の受け(warm-env-of-row)で読んだ温める行を得るため。"
  (<- reply (heartbeat-reply state "w1" TIMING :now now))
  (<- spelled dict (heartbeat-reply-json reply))
  (tuple (gfor row (get spelled "warm") (warm-env-of-row row PLATFORM))))


(deftest test-the-request-body-names-urgent-only-when-urgent
  (<- declared dict (declared-of "app-1"))
  (val urgent (warm-request-body declared NEEDS 300.0 "keeper" True))
  (val plain (warm-request-body declared NEEDS 300.0 "keeper"))
  (assert (is (get urgent "urgent") True) urgent)
  (assert (not-in "urgent" plain) plain))


(deftest test-the-urgent-name-lasts-until-its-own-deadline-and-reaches-the-worker
  (<- declared dict (declared-of "app-1"))
  ;; worker の root のキー(warm-env-of-row と同じ env- の頭を付けた形)。
  (<- bare str (key-on declared PLATFORM))
  (val key (+ "env-" bare))
  ;; 急ぎの頼み(期限 300 秒)を 1000 に書く。
  (val written (responded (ClusterState) (! (http-request "POST" "/warm" {} (warm-request-body declared NEEDS 300.0 "keeper" True)
                                                          :actor "keeper"))
                          1000 TIMING))
  (assert (= (get written 1) 200) written)
  (val warmed (warm-state-of-json (get written 2)))
  (val entry (.row (. (get written 0) warms) warmed.key))
  (assert (= entry.urgent-until-ms 301000) entry)
  (val hb {"name" "w1" "provides" ["agent"] "measured" (get ROOM-JSONS 2) "versions" {} "boot" "b1" "platform" PLATFORM
           "envs" {"ready" [] "preparing" [] "failed" []} "envCapacity" "ok"})
  (<- s1 (register-heartbeat (get written 0) (! (heartbeat-of hb)) 2000))
  (<- first tuple (offered s1 2000))
  (assert (= first #((WarmEnv key (. (get first 0) runtime-env) :urgent True))) first)
  ;; 平常の頼み直し(期限 600 秒)は行の期限を延ばし、急ぎの名乗りの期限は残す。
  (val rewritten (responded s1 (! (http-request "POST" "/warm" {} (warm-request-body declared NEEDS 600.0 "other") :actor "other"))
                            200000 TIMING))
  (val s2 (get rewritten 0))
  (assert (= (. (.row s2.warms warmed.key) urgent-until-ms) 301000) (.row s2.warms warmed.key))
  (<- still tuple (offered s2 250000))
  (assert (= (tuple (gfor w still w.urgent)) #(True)) still)
  ;; 名乗りの期限を過ぎた行は、行の期限の内なら平常の行として配る。
  (<- after tuple (offered s2 400000))
  (assert (= (tuple (gfor w after #(w.key w.urgent))) #(#(key False))) after)
  ;; 保存の綴りを往復しても名乗りの期限は残る。欄の無い前の版の行は名乗り無し。
  (val row (.row s2.warms warmed.key))
  (<- back (warm-entry-from-json (warm-entry-to-json row)))
  (assert (= back row) back)
  (<- old (warm-entry-from-json (dfor #(k v) (.items (warm-entry-to-json row)) :if (!= k "urgent_until_ms") k v)))
  (assert (= old.urgent-until-ms 0) old))


(deftest test-an-urgent-warm-row-starts-its-warm-child-in-the-room-under-the-keep-line
  (<- ta JobSpec (task-on "ta" KEY-A :entries #("app.warm")))
  (val launch (WarmLaunch :root ROOT-A :project (+ ROOT-A "/app") :preload #("app.warm")))
  (val kept (replace (! (world)) :jobs-room 0 :urgent-jobs-room URGENT-ROOM))
  (<- urgent tuple (warm-child-actions NOW #() kept #((WarmEnv KEY-A ta.runtime-env :urgent True)) POLICY))
  (assert (= urgent #((StartWarmChild KEY-A launch))) urgent)
  ;; 失敗ケース: 急ぎを名乗らない行は、同じ worker でも平常の空き 0 で判じて起こさない(直す前の形)。
  (<- plain tuple (warm-child-actions NOW #() kept #((WarmEnv KEY-A ta.runtime-env)) POLICY))
  (assert (= plain #()) plain)
  ;; 失敗ケース: 線を外した空きを名乗らない worker(線が層を縮めていない・前の版)は、平常の空きで判じる。
  (<- unnamed tuple (warm-child-actions NOW #() (replace kept :urgent-jobs-room None) #((WarmEnv KEY-A ta.runtime-env :urgent True)) POLICY))
  (assert (= unnamed #()) unnamed)
  ;; 失敗ケース: 線を外しても待ちの子 1 本(見積もり 560 MiB)が入らない空きでは起こさない(急ぎでも層の上限は越えない)。
  (<- small tuple (warm-child-actions NOW #() (replace kept :urgent-jobs-room (* 300 MIB)) #((WarmEnv KEY-A ta.runtime-env :urgent True))
                                      POLICY))
  (assert (= small #()) small))
