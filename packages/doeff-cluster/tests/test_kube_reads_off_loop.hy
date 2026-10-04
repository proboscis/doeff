;;; k8s の API の読みが答えない間も、coordinator の調停ループが要求に答え続けるかを、本物の coordinator と worker の上で検める
;;; (#2807 — 2026-10-02 13:53:37〜57 に coordinator が 20.2 秒止まった形・#2803)。
;;;
;;; 筋書き: worker の job が動き出した後に Rollout を宣言する(ここから Rollout の相手の Deployment の読みが毎拍始まる)。模擬の k8s は
;;; 仮想の時刻 STALLED-UNTIL-SECONDS まで読みに答えない(KubeMemory.stalled-until-ms)。その間 2 秒ごとに coordinator の口に問い、答えまでの
;;; 仮想の時間を測る。
;;;   直した形: 読みは調停ループの外で走り、ループは待たない — 答えは ANSWER-WITHIN-MS の内・job の process は止まらない・読みが
;;;            名指す秒を超えたら名指しの 1 行が出る
;;;   失敗ケース: 読みを始めた所で k8s が答えるまで調停ループの中で待つ壊した答え手(以前の同期の読みの形)を差すと、答えが遅れ、
;;;            worker が途絶で job を止める(本番の 13:53 の自己停止の形)
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import pytest)
(import doeff_time [Delay])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.sim.local :as sim-local)
(import doeff_cluster.sim.local [sim-cluster SimWorker DeclareRollout ReadCoordinator ProcessesOf SIM-START-MS])
(import doeff_cluster.coordinator.intent.kube_model [StartKubeReads])
(import doeff_cluster.shared.intent.remote_model [RemoteJobFailed])
(import doeff_cluster.coordinator.protocol.kube [KubeMemory])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [beacons])

(val DEP "prod/old-beacon")
(val DEPLOYMENTS {DEP {"specReplicas" 1 "replicas" 1 "readyReplicas" 1}})
(val FORWARD {"from" {"kind" "Deployment" "namespace" "prod" "name" "old-beacon"}
              "to" {"kind" "Service" "name" "beacon"}
              "readyTimeoutSeconds" 90 "stopTimeoutSeconds" 30 "observeSeconds" 1})
(val WORKERS #((SimWorker :name "w1" :provides (frozenset #{"cluster-net"}) :task-reserve 0)))
;; Rollout を宣言する仮想の刻と、k8s の読みが答えない区間の終わり(どちらも起点からの秒)— 区間は宣言から 35 秒(本番の止まり 20 秒・
;; worker の fence 20 秒を超える)。
(val DECLARE-AT-SECONDS 10.0)
(val STALLED-UNTIL-SECONDS 45.0)
;; 問いの間隔と回数(区間の中を 2 秒ごとに 15 回)と、答えの上限(仮想の ms — 受入「1 秒以内」)。
(val ASK-EVERY-SECONDS 2.0)
(val ASKS 15)
(val ANSWER-WITHIN-MS 1000)


(defrecord StalledReads
  "筋書きの結果: slowest-ms = 区間の中の問いのうち答えまでがいちばん長かった仮想の ms・before / after = 宣言の前と区間の後の beacon の
   process の列。"
  (#^ int slowest-ms)
  (#^ tuple before)
  (#^ tuple after))


(defhandler loop-blocking-kube [#^ KubeMemory kube]
  "壊した k8s の答え手(失敗ケース): 読みを始めた所で、k8s が答えるまで(stalled-until-ms)調停ループの中で待ってから外側の模擬の k8s へ
   出し直す — 以前の同期の読み(ReadDeployment をループの中で撃つ形)と同じく、読みの詰まりがループごと止める。"
  ;; 引数に残す理由: 答えない区間の終わりは、外側の模擬の k8s と同じ 1 つの KubeMemory が持つ(検が組を作る時に置く)。
  (StartKubeReads [deployments nodes started-ms]
    (when (and (is-not kube.stalled-until-ms None) (< started-ms kube.stalled-until-ms))
      (<- (Delay (/ (- kube.stalled-until-ms started-ms) 1000.0))))
    (<- answer effect)
    (resume answer)))


(defk stalled-reads-scenario []
  {:pre [] :post [(: % StalledReads)] :tags {:context "doeff-cluster-test" :role "program"}}
  "job が動き出した後に Rollout を宣言し、k8s の読みが答えない区間の中で coordinator の口に 2 秒ごとに問い、答えまでの仮想の時間と
   区間の前後の beacon の process を返すため。"
  (<- (Delay DECLARE-AT-SECONDS))
  (<- before tuple (ProcessesOf "beacon"))
  (<- _declared dict (DeclareRollout "forward" FORWARD))
  (var slowest 0)
  (for [_ (range ASKS)]
    (<- (Delay ASK-EVERY-SECONDS))
    (<- asked int (now-epoch-ms))
    ;; 返事の上限(15 秒)で打ち切られた問いも、打ち切られるまでを答えまでの時間に数える(答えなかった問い)。
    (try
      (<- _state dict (ReadCoordinator "/state"))
      (except [RemoteJobFailed]
        None))
    (<- answered int (now-epoch-ms))
    (:= slowest (max slowest (- answered asked))))
  (<- after tuple (ProcessesOf "beacon"))
  (StalledReads :slowest-ms slowest :before before :after after))


(defk stalled-k8s [monkeypatch blocking]
  {:pre [(: monkeypatch pytest.MonkeyPatch) (: blocking bool)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "模擬の coordinator の組を作る関数を包み、模擬の k8s に読みが答えない区間を置くため(blocking = 一番内側に壊した答え手を足す)。"
  (val original sim-local.emulated-handlers)
  (.setattr monkeypatch sim-local "emulated_handlers"
            (fn [queue store stop kube #* rest]
              (setv kube.stalled-until-ms (+ SIM-START-MS (int (* STALLED-UNTIL-SECONDS 1000))))
              (+ (original queue store stop kube #* rest) (if blocking [(loop-blocking-kube kube)] []))))
  None)


(defk kept-running [before after]
  {:pre [(: before tuple) (: after tuple)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "区間の前に動いていた beacon の process 1 つが、区間の後も止まらず(起き直さず)同じ 1 つのまま動いているかを判じるため。"
  (val alive-before (frozenset (gfor p before :if (is p.exit-code None) p.started-ms)))
  (val started-after (frozenset (gfor p after p.started-ms)))
  (val alive-after (frozenset (gfor p after :if (is p.exit-code None) p.started-ms)))
  (and (= (len alive-before) 1) (= alive-after alive-before) (= started-after alive-before)))


(deftest test-the-coordinator-keeps-answering-while-the-k8s-reads-do-not-answer [monkeypatch capfd]
  (<- (stalled-k8s monkeypatch False))
  (<- got StalledReads (sim-cluster (beacons sim-foundation) (stalled-reads-scenario) :workers WORKERS :deployments DEPLOYMENTS))
  (assert (<= got.slowest-ms ANSWER-WITHIN-MS) got)
  ;; 区間の間に job が止まって起き直していない(同じ 1 つの process が動き続ける)。
  (assert (! (kept-running got.before got.after)) got)
  ;; 読みが名指す秒を超えた時の名指しの 1 行。
  (assert (in "k8s の読みが" (. (.readouterr capfd) err))))


(deftest test-a-k8s-read-inside-the-loop-stops-the-answers-and-the-worker-stops-its-job [monkeypatch]
  ;; 失敗ケース: 読みを調停ループの中で待つと、答えが区間の分だけ遅れ、worker が fence を越えて job を止める。
  (<- (stalled-k8s monkeypatch True))
  (<- got StalledReads (sim-cluster (beacons sim-foundation) (stalled-reads-scenario) :workers WORKERS :deployments DEPLOYMENTS))
  (assert (> got.slowest-ms ANSWER-WITHIN-MS) got)
  (assert (not (! (kept-running got.before got.after))) got))
