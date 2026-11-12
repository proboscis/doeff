;; worker は準備で足す能力を、起動の時の一度きりの分かれ道でなく、heartbeat の前に読み直す準備の file から名乗る(worker/core/prepare_rules)。
;; 準備を起動の前の待ちにして --provides で名乗った worker は、準備が上限を越えた・落ちた時に process の一生のあいだ能力を欠いたまま動き、
;; その事を名乗らなかった(2026-11-12 の観測 — 2 台の機体が何時間も仕事を受けなかった)。
;;   (1) 準備の file の形(state・provides・sinceMs・stage・reason)を読み、形の誤りは malformed と訳。知らない欄は読まずに通し、省ける欄は
;;       既定の値。機体の印 host- と組の印 pool- は足す能力に書けない
;;   (2) coordinator への口は file を読み直して、file の能力を起動の引数の能力に足して heartbeat の provides で名乗り、準備の姿を heartbeat の
;;       prepare で名乗る。file が無い間は running(刻は起動の刻)、崩れた・読めない間は前に名乗った能力のまま状態だけを名乗る。能力の
;;       増減は次の読み直しの後の heartbeat から効く。file を読まない worker は prepare を名乗らない
;;   (3) coordinator は prepare を Worker の記録に写し、GET /state の worker の行に出す。Worker の status には done でない間だけ
;;       {state sinceMs stage} を載せる(訳の文と能力は載せない — 版を進めない)
;; 失敗ケース = 起動の時の能力を名乗り続ける(準備が済んでも能力が増えない)・崩れた file で足した能力を落とす・file が無い worker を
;; 何も名乗らない(落ちと区別できない)・準備の file から機体の印を名乗る・済んだ worker の status に欄を載せて版を揺らす。
(require doeff-hy.macros [deff deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import httpx)
(import pytest)
(import tests.link_rig [LinkRig])
(import tests.program_rows [heartbeat-of])
(import doeff_cluster [ClusterState ClusterTiming])
(import doeff_cluster._worker.core.prepare_rules [PrepareReading PrepareState prepare-of-text prepare-after prepare-missing
                                                  prepare-line])
(import doeff_cluster._coordinator.core.cluster_policy [register-heartbeat state-view])
(import doeff_cluster._coordinator.core.resource_policy [worker-row])
(import doeff_cluster._coordinator.protocol.replies [state-view-json])

(val T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))


(deftest test-the-prepare-file-names-the-state-and-the-added-provides
  (<- done PrepareReading (prepare-of-text (json.dumps {"state" "done" "provides" ["guest-extra" "agent" "agent"] "sinceMs" 1000
                                                        "stage" "" "reason" ""})))
  (assert (= done (PrepareReading :state PrepareState.DONE :provides #("agent" "guest-extra") :since-ms 1000 :stage "" :reason "")) done)
  ;; 欄の増減: 省ける欄は既定の値・知らない欄(後の版の書き手が足した欄)は読まずに通す。
  (<- bare PrepareReading (prepare-of-text (json.dumps {"state" "running"})))
  (assert (= bare (PrepareReading :state PrepareState.RUNNING :provides #() :since-ms None :stage "" :reason "")) bare)
  (<- extra PrepareReading (prepare-of-text (json.dumps {"state" "failed" "stage" "merge-queue" "reason" "uv sync が 1 で終わった"
                                                         "attempt" 3 "retryAtMs" 99})))
  (assert (= extra (PrepareReading :state PrepareState.FAILED :provides #() :since-ms None :stage "merge-queue"
                                   :reason "uv sync が 1 で終わった"))
          extra))


(deftest test-the-prepare-file-in-a-wrong-shape-is-malformed-with-the-reason
  ;; 崩れた形はどれも malformed で能力を持たない(黙って外して残りを名乗らない・機体の印や組の印を準備の file から名乗らない)。
  (val broken [["{" "JSON でない"]
               ["[1]" "JSON の object でない"]
               [(json.dumps {"provides" ["agent"]}) "state は running・done・failed のどれかで書く: None"]
               [(json.dumps {"state" "ready"}) "state は running・done・failed のどれかで書く: 'ready'"]
               [(json.dumps {"state" "unreadable"}) "state は running・done・failed のどれかで書く: 'unreadable'"]
               [(json.dumps {"state" "done" "provides" "agent"}) "provides が列でない: 'agent'"]
               [(json.dumps {"state" "done" "provides" ["agent" 1]}) "provides の 1 が文字列でない"]
               [(json.dumps {"state" "done" "provides" ["host-node-h"]}) "provides の 'host-node-h' は機体の印 — 機体の境界は起動の引数で名乗る"]
               [(json.dumps {"state" "done" "provides" ["pool-a"]}) "provides の 'pool-a' は組の印 — 組の印は枠の file の pools で名乗る"]
               [(json.dumps {"state" "done" "provides" ["Agent"]}) "能力の名 'Agent' は小文字・数字・`.`・`-` だけで書く"]
               [(json.dumps {"state" "done" "sinceMs" -1}) "sinceMs が 0 以上の整数でない: -1"]
               [(json.dumps {"state" "done" "sinceMs" True}) "sinceMs が 0 以上の整数でない: True"]
               [(json.dumps {"state" "failed" "stage" 3}) "stage が文字列でない: 3"]
               [(json.dumps {"state" "failed" "reason" None}) "reason が文字列でない: None"]])
  (for [#(text reason) broken]
    (<- read PrepareReading (prepare-of-text text))
    (assert (= read (PrepareReading :state PrepareState.MALFORMED :provides #() :since-ms None :stage "" :reason reason)) #(text read))))


(deftest test-a-broken-or-missing-file-keeps-the-provides-named-before
  (val done (PrepareReading :state PrepareState.DONE :provides #("agent") :since-ms 1000 :stage "" :reason ""))
  (val broken (PrepareReading :state PrepareState.MALFORMED :provides #() :since-ms None :stage "" :reason "JSON でない"))
  ;; 崩れた file は前の能力のまま。刻は malformed に入った刻で、続く間は動かさない。
  (<- first PrepareReading (prepare-after done broken 5000))
  (<- still PrepareReading (prepare-after first broken 9000))
  (assert (= #(first.state first.provides first.since-ms) #(PrepareState.MALFORMED #("agent") 5000)) first)
  (assert (= still.since-ms 5000) still)
  ;; 初めての読みが崩れていれば足す能力は無い。
  (<- fresh PrepareReading (prepare-after None broken 5000))
  (assert (= fresh.provides #()) fresh)
  ;; 書き手の書いた file はそのまま名乗る(能力を減らした file も)。
  (val fewer (PrepareReading :state PrepareState.DONE :provides #() :since-ms 7000 :stage "" :reason ""))
  (<- told PrepareReading (prepare-after done fewer 9000))
  (assert (= told fewer) told)
  ;; file が無い間は running・刻は起動の刻・能力は前のまま。
  (<- missing PrepareReading (prepare-missing done 100))
  (assert (= missing (PrepareReading :state PrepareState.RUNNING :provides #("agent") :since-ms 100 :stage ""
                                     :reason "準備の file がまだ無い"))
          missing)
  ;; log の 1 行は姿が変わった時だけ。
  (<- moved (| str None) (prepare-line None done))
  (<- same (| str None) (prepare-line done done))
  (assert (= moved "worker: 準備の file から done・足す能力 agent を名乗る") moved)
  (assert (is same None) same))


(deftest test-the-heartbeat-adds-the-prepared-provides-and-names-the-prepare-state [tmp-path]
  (setv sent #())
  (deff handle [#^ httpx.Request request]  ; cannot be a defk: httpx.MockTransport が呼ぶ callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "foundation"}}
    "口が送った heartbeat の本文を控え、宣言の空の返事で答えるため。"
    (nonlocal sent)
    (setv sent (+ sent #((json.loads request.content))))
    (httpx.Response 200 :json {"jobs" [] "tasks" [] "warm" [] "draining" False}))
  (val prepared (/ tmp-path "prepared.json"))
  (val link (LinkRig "http://coord" "node-h" #("host-node-h" "host-systemd-readable") 60000 :task-dir (str (/ tmp-path "tasks"))
                     :transport (httpx.MockTransport handle)))
  (setv link.state.prepare-file (str prepared))
  (val named (fn [body] #((get body "provides") (get body "prepare" "state") (get body "prepare" "stage"))))
  (val reread (fn [] (setv link.state.prepare-read-ms None) (.poll link)))
  ;; file がまだ無い: 機体の能力だけで名乗り、準備は running(刻は起動の刻)。
  (.poll link)
  (assert (= (named (get sent -1)) #(["host-node-h" "host-systemd-readable"] "running" "")) sent)
  (assert (= (get sent -1 "prepare") {"state" "running" "provides" [] "sinceMs" link.state.boot-at "stage" ""
                                      "reason" "準備の file がまだ無い"})
          (get sent -1))
  ;; 準備が段を進める間は running と段。
  (.write-text prepared (json.dumps {"state" "running" "stage" "merge-queue" "sinceMs" 2000}))
  (reread)
  (assert (= (named (get sent -1)) #(["host-node-h" "host-systemd-readable"] "running" "merge-queue")) sent)
  ;; 落ちた準備は failed と訳を名乗る(能力は足さない)。
  (.write-text prepared (json.dumps {"state" "failed" "stage" "merge-queue" "sinceMs" 3000 "reason" "uv sync が 1 で終わった"}))
  (reread)
  (assert (= (named (get sent -1)) #(["host-node-h" "host-systemd-readable"] "failed" "merge-queue")) sent)
  (assert (= (get sent -1 "prepare" "reason") "uv sync が 1 で終わった") (get sent -1))
  ;; やり直しが済むと、次の読み直しの後の heartbeat から能力を足す(Pod を作り直さない)。
  (.write-text prepared (json.dumps {"state" "done" "provides" ["agent" "guest-extra"] "sinceMs" 4000}))
  (reread)
  (assert (= (named (get sent -1)) #(["host-node-h" "host-systemd-readable" "agent" "guest-extra"] "done" "")) sent)
  ;; 崩れた file の間は足した能力のまま、状態だけ malformed。
  (.write-text prepared "{")
  (reread)
  (assert (= (named (get sent -1)) #(["host-node-h" "host-systemd-readable" "agent" "guest-extra"] "malformed" "")) sent)
  ;; 能力を減らした file は減らした能力を名乗る。
  (.write-text prepared (json.dumps {"state" "done" "provides" ["agent"] "sinceMs" 5000}))
  (reread)
  (assert (= (named (get sent -1)) #(["host-node-h" "host-systemd-readable" "agent"] "done" "")) sent)
  ;; file を読まない worker は prepare を名乗らない。
  (val plain (LinkRig "http://coord" "node-b" #("host-node-b") 60000 :task-dir (str (/ tmp-path "plain"))
                      :transport (httpx.MockTransport handle)))
  (.poll plain)
  (assert (= (get sent -1 "provides") ["host-node-b"]) (get sent -1))
  (assert (not-in "prepare" (get sent -1)) (get sent -1)))


(deftest test-the-coordinator-shows-the-prepare-state-in-state-and-in-the-worker-status-until-done
  (val failing {"state" "failed" "provides" [] "sinceMs" 3000 "stage" "merge-queue" "reason" "uv sync が 1 で終わった"})
  (val heard (! (register-heartbeat (ClusterState) (! (heartbeat-of {"name" "node-h" "provides" ["host-node-h"] "prepare" failing})) 10)))
  (val both (! (register-heartbeat heard (! (heartbeat-of {"name" "node-b" "provides" []})) 10)))
  (val node-h (.row both.workers "node-h"))
  (assert (= #(node-h.prepare.state node-h.prepare.stage node-h.prepare.since-ms) #("failed" "merge-queue" 3000)) node-h)
  (assert (is (. (.row both.workers "node-b") prepare) None))
  ;; Worker の status: done でない間だけ {state sinceMs stage}(訳の文は載せない)。名乗らない worker には欄が無い。
  (assert (= (get (worker-row both node-h 10 T) "status" "prepare") {"state" "failed" "sinceMs" 3000 "stage" "merge-queue"}))
  (assert (not-in "prepare" (get (worker-row both (.row both.workers "node-b") 10 T) "status")))
  ;; GET /state の worker の行: 訳と足した能力も載せる。
  (val workers (get (! (state-view-json (! (state-view both 20 T)))) "workers"))
  (assert (= (get workers "node-h" "prepare") failing) (get workers "node-h"))
  (assert (not-in "prepare" (get workers "node-b")) (get workers "node-b"))
  ;; 準備が済んだ次の heartbeat: 足した能力が provides に入り、status から欄が消え、GET /state は done を出す。
  (val ready {"state" "done" "provides" ["agent"] "sinceMs" 4000 "stage" "" "reason" ""})
  (val again (! (register-heartbeat both (! (heartbeat-of {"name" "node-h" "provides" ["agent" "host-node-h"] "prepare" ready})) 30)))
  (val after (.row again.workers "node-h"))
  (assert (= after.provides #("agent" "host-node-h")) after)
  (assert (not-in "prepare" (get (worker-row again after 30 T) "status")))
  (assert (= (get (get (! (state-view-json (! (state-view again 40 T)))) "workers") "node-h" "prepare") ready))
  ;; 状態の語の外は本文の形の誤り(400)。
  (with [(pytest.raises ValueError)]
    (! (heartbeat-of {"name" "node-h" "provides" [] "prepare" {"state" "ready"}}))))
