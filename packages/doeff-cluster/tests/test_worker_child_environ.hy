;; worker が job の子へ渡す環境変数の名(childEnviron)を heartbeat で名乗り、coordinator が GET /state の workers に出す。
;; 上に載る系の宣言の道具は、job が worker の環境から読む名がその worker の子の環境に在るかを、宣言し直す前に GET /state で照らす。
;; 名乗りが無いと照らしは空振りし、2026-11-02 に機体の worker node-h-native の上の job が子の環境に無い XDG_CACHE_HOME を読んで
;; 起動の後に落ち、系の宣言し直しが戻った。
;;   (1) 子の環境の名 = worker の環境のうち許可表(CHILD-ENV-ALLOWED と LC_*)に在る名 + worker が足す名(--pass-env・worker の文脈)。
;;       WITHHELD-ENV-NAMES は、どの層から来ても名乗らない(子へ届かない名を名乗らない)
;;   (2) coordinator への口は名を heartbeat の childEnviron で名乗る。名を持たない口(None)は欄を書かない
;;   (3) coordinator は名乗りを Worker の記録に写し、GET /state の workers の値に childEnviron(名の順の列)で出す。名乗らない旧い worker
;;       の行には欄が無い(空の列を出さない — 空の列は「子へ何も渡さない」と読まれる)
;; 失敗ケース = 許可表の外の worker の環境の名(資格を含む)を名乗る・--pass-env の名を落とす・名乗らない worker に空の列を出す・
;; 次の heartbeat の名乗りで書き直さない。
(require doeff-hy.macros [deff deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import httpx)
(import tests.link_rig [LinkRig])
(import tests.program_rows [heartbeat-of])
(import doeff_cluster [ClusterState ClusterTiming])
(import doeff_cluster._shared.intent.runtime_env_model [WITHHELD-ENV-NAMES])
(import doeff_cluster._worker.core.launch [child-environ-names])
(import doeff_cluster._coordinator.core.cluster_policy [register-heartbeat state-view])
(import doeff_cluster._coordinator.protocol.replies [state-view-json])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))


(deftest test-the-child-environ-names-are-the-allowed-and-the-added-names
  ;; worker の環境のうち許可表の名と LC_* だけ + worker が足す名。許可表の外の名(資格・PYTHONPATH)と、許可表に無い XDG_CACHE_HOME は
  ;; worker の環境に在っても名乗らない(node-h-native の実際の形)。
  (val machine {"PATH" "/bin" "HOME" "/home/z" "LC_ALL" "C.UTF-8" "XDG_CACHE_HOME" "/home/z/.cache" "SECRET_TOKEN" "t"
                "PYTHONPATH" "/x"})
  (val added {"WORK_DIR" "/work" "AGENT_CLAUDE_HOME" "/home/z/.claude" "DOEFF_WORKER_NAME" "node-h-native"})
  (<- names tuple (child-environ-names machine added))
  (assert (= names #("AGENT_CLAUDE_HOME" "DOEFF_WORKER_NAME" "HOME" "LC_ALL" "PATH" "WORK_DIR")) names)
  ;; 子へ届かない名は、worker の環境からも足す名からも名乗らない。
  (val marker (next (iter (sorted WITHHELD-ENV-NAMES))))
  (<- held tuple (child-environ-names {marker "/w" "PATH" "/bin"} {marker "/w"}))
  (assert (= held #("PATH")) held))


(deftest test-the-heartbeat-names-the-child-environ-only-when-the-link-has-it [tmp-path]
  (setv sent #())
  (deff handle [#^ httpx.Request request]  ; defk にできない: httpx.MockTransport が呼ぶ callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
    "口が送った heartbeat の本文を控え、宣言の空の返事で答えるため。"
    (nonlocal sent)
    (setv sent (+ sent #((json.loads request.content))))
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" [] "draining" False}))
  (val plain (LinkRig "http://coord" "node-b" #() 60000 :task-dir (str (/ tmp-path "plain")) :transport (httpx.MockTransport handle)))
  (.poll plain)
  (assert (not-in "childEnviron" (get sent -1)) (get sent -1))
  (val named (LinkRig "http://coord" "node-h-native" #() 60000 :task-dir (str (/ tmp-path "named")) :transport (httpx.MockTransport handle)))
  (setv named.state.child-environ #("HOME" "PATH" "WORK_DIR"))
  (.poll named)
  (assert (= (get sent -1 "childEnviron") ["HOME" "PATH" "WORK_DIR"]) (get sent -1)))


(deftest test-the-coordinator-shows-the-child-environ-in-state-only-for-workers-that-name-it
  (val heard (! (register-heartbeat (ClusterState) (! (heartbeat-of {"name" "node-h-native" "provides" ["host-node-h"]
                                                                     "childEnviron" ["WORK_DIR" "HOME" "PATH"]})) 10)))
  (val both (! (register-heartbeat heard (! (heartbeat-of {"name" "node-b" "provides" []})) 10)))
  (assert (= (. (.row both.workers "node-h-native") child-environ) #("HOME" "PATH" "WORK_DIR")))
  (assert (is (. (.row both.workers "node-b") child-environ) None))
  (val workers (get (! (state-view-json (! (state-view both 20 T)))) "workers"))
  (assert (= (get workers "node-h-native" "childEnviron") ["HOME" "PATH" "WORK_DIR"]) (get workers "node-h-native"))
  (assert (not-in "childEnviron" (get workers "node-b")) (get workers "node-b"))
  ;; 次の heartbeat の名乗りで書き直す(版を上げた worker が名を足した時)。
  (val again (! (register-heartbeat both (! (heartbeat-of {"name" "node-h-native" "provides" ["host-node-h"]
                                                           "childEnviron" ["HOME" "PATH" "WORK_DIR" "XDG_CACHE_HOME"]})) 30)))
  (assert (= (. (.row again.workers "node-h-native") child-environ) #("HOME" "PATH" "WORK_DIR" "XDG_CACHE_HOME"))))
