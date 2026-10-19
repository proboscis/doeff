;; Pod の止まりの印(AL-D2p — card ki-bfa05ac34c77): worker の Pod の preStop(drain の入口 drain_main の drain)が止まりの始まりを Pod の
;; 中の file(世代の file の隣)に書き(書き手と印の形 = worker/protocol/leaving_mark)、worker の観測のまとめ(local-host)が拍ごとに
;; WorldView.leaving として読む(worker の入口が並べる答え手 = worker/protocol/leaving_observer — 書き手と形を import しない)。worker の判断
;; (worker/core/policy の start-step)はこの観測で、止まる Pod の handoff の job の新しい試行を起こさない — coordinator の返事の印
;; supersededBy が欠けても(10-11 の zeus の画面の process の群 — card ki-3a57786eba88)。判断の側の検は test_policy.hy の AL-D2p の節。
(require doeff-hy.macros [defhandler deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import sys)
(import pathlib [Path])
(import pytest)
(import doeff [with-handlers])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_cluster.worker.intent.worker_model [EnvDisk ObserveWorld])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses ObserveCode ObserveEnvs ObserveEnvDisk ObserveProbes ObserveWarmChildren ObserveMemoryTight ObserveMemoryKills])
(import doeff_cluster.worker.protocol.world [local-host])
(import doeff_cluster.worker.protocol.leaving_mark [leaving-mark-written])
(import doeff_cluster.worker.protocol.leaving_observer [leaving-observer])
(import doeff_cluster.worker.entry.main [boot-file-written])
(import doeff_cluster.worker.entry.drain_main [main :as drain-main])

;; 届かない coordinator(preStop の頼みは届かずに期限で終わる — 印は頼みの前に書く)。
(val UNREACHABLE "http://127.0.0.1:9")


(defhandler nothing-else-observed
  ;; 止まりの印のほかの観測(コードの木・root・子 process・検め・disk・待ちの子)は空。
  (ObserveCode [] (resume #()))
  (ObserveEnvs [] (resume #()))
  (ObserveProcesses [] (resume #()))
  (ObserveProbes [] (resume #()))
  (ObserveEnvDisk [] (resume (EnvDisk :free 10 :sweep-wanted False :pinned (frozenset))))
  (ObserveWarmChildren [] (resume #()))
  (ObserveMemoryTight [] (resume False))
  (ObserveMemoryKills [] (resume (frozenset))))


(deftest test-the-pre-stop-mark-tells-the-worker-its-pod-is-leaving [#^ Path tmp-path]
  ;; 失敗ケース: worker の本体は自分の Pod の preStop が始まった事を知らず(preStop は別の process が coordinator へ頼むだけ)、coordinator の
  ;; 返事の印だけで退いていた。preStop の入口が世代の file の隣に印を書き、worker の観測(本番と同じ local-host・本物の file の答え手)が読む。
  (val boot-file (str (/ tmp-path "doeff-worker-boot")))
  (! (with-handlers [os-file-handler] (boot-file-written boot-file "b-new")))
  (val observe (with-handlers [os-file-handler (leaving-observer boot-file "b-new") nothing-else-observed local-host] (ObserveWorld)))
  (assert (not (. (! observe) leaving)) "preStop の前は止まっていない")
  (! (with-handlers [os-file-handler] (leaving-mark-written boot-file "b-new")))
  (assert (. (! observe) leaving) "preStop の印の後の拍は、止まりの始まった Pod と読む"))


(deftest test-a-mark-of-another-generation-is-not-this-workers-leaving [#^ Path tmp-path]
  ;; 印は世代つき: 前の process の印(/tmp が worker の process より長く残る機体 — 例えば mac の worker)を、新しい process の止まりと
  ;; 読まない。読めば、新しい process は落ちた handoff の job を起こし直さなくなる。世代の file を持たない worker(Pod の外)は印を読まない。
  (val boot-file (str (/ tmp-path "doeff-worker-boot")))
  (! (with-handlers [os-file-handler] (leaving-mark-written boot-file "b-old")))
  (assert (not (. (! (with-handlers [os-file-handler (leaving-observer boot-file "b-new") nothing-else-observed local-host] (ObserveWorld)))
                  leaving))
          "別の世代の印は、この process の止まりではない")
  (assert (not (. (! (with-handlers [os-file-handler (leaving-observer None "b-old") nothing-else-observed local-host] (ObserveWorld)))
                  leaving))
          "世代の file の無い worker は止まりを読まない"))


(deftest test-the-pre-stop-entry-leaves-the-mark-before-asking-the-coordinator [#^ Path tmp-path #^ pytest.MonkeyPatch monkeypatch]
  ;; 本番の preStop の入口(boot.sh の ROLE=drain と同じ引数の drain_main drain)が印を書く。coordinator に届かなくても書く(頼みの前に
  ;; 書く — preStop の頼みが届かない間も Pod の止まりは始まっている)。readinessProbe(ready)は書かない。
  (val boot-file (str (/ tmp-path "doeff-worker-boot")))
  (! (with-handlers [os-file-handler] (boot-file-written boot-file "b-1234")))
  (val observe (with-handlers [os-file-handler (leaving-observer boot-file "b-1234") nothing-else-observed local-host] (ObserveWorld)))
  (.setattr monkeypatch sys "argv" ["drain_main" "ready" "--coordinator" UNREACHABLE "--name" "zeus" "--boot-file" boot-file])
  (with [exited (pytest.raises SystemExit)] (drain-main))
  (assert (= exited.value.code 1) "届かない coordinator の readinessProbe は NotReady")
  (assert (not (. (! observe) leaving)) "readinessProbe は止まりの印を書かない")
  (.setattr monkeypatch sys "argv" ["drain_main" "drain" "--coordinator" UNREACHABLE "--name" "zeus"
                                    "--deadline" "0" "--interval" "0" "--boot-file" boot-file])
  (with [drained (pytest.raises SystemExit)] (drain-main))
  (assert (= drained.value.code 0) "preStop はいつも 0 で終わる")
  (assert (. (! observe) leaving) "preStop の入口が、この Pod の worker の世代の印を書いた"))
