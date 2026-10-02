;;; 本物の process と HTTP で、worker が起きて coordinator に名乗る(#1005)。
;;;
;;; 13 回目の本番の切り替え(2026-09-29)で、本番の状態の写しから起きた coordinator に新しい形の worker が 1 つも名乗れなかった。
;;; sim-cluster の検は本物の HTTP の口を通らないので見えなかった。ここでは coordinator(hy -m doeff_cluster.coordinator.entry.main)と worker
;;; (hy -m doeff_cluster.worker.entry.main — deploy/boot.sh の ROLE=worker と同じ引数)を子 process で起こし、worker が GET /state の workers に出て、
;;; heartbeat の返事を受けた(readiness の file が ready)ことを確かめる。置き場は 2 つ:
;;;   - 空の置き場
;;;   - 本番の写しと同じ形の置き場: 旧い形(labels だけ)の worker の行とその版の記録 meta/Worker/<名>、旧い形の Service の行とその版の記録。
;;;     読み直しは worker の行を捨て、版の記録は残る。同じ名の worker が名乗り、同じ名の新しい形の Service を書ける。
(require doeff-hy.macros [deftest defk val var <-])
(import os)
(import subprocess)
(import time)
(import pathlib [Path])
(import httpx)
(import doeff_cluster.foundation.wal_store [WalStore])
(import doeff_cluster.coordinator.protocol.store [durable-load durable-persist])
(import tests.served_fixtures [ROOT HY start-coordinator])
(import tests.program_rows [SAMPLE-RUN])

(val NAME "zeus-host-t")
(val SERVICE "old-service")
;; worker は起きてから最初の拍で名乗る。起動(Hy の module の読み込み)を含めてこの秒数の内に名乗らなければ赤。
(val REGISTER-SECONDS 30)
(val STALE-META {"resourceVersion" 866 "generation" 4 "createdBy" NAME "createdMs" 100 "updatedBy" NAME "updatedMs" 200})


(defk seed-old-store [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator の置き場(tmp-path/wal — served_fixtures.start-coordinator の state file の隣)へ、2026-09-27 より前の coordinator が
   書いた形の行を置く。"
  (val store (WalStore (str (/ tmp-path "wal"))))
  ;; 旧い coordinator は 1 分前まで生きていた。
  (val alive (- (int (* 1000 (time.time))) 60000))
  (<- (durable-load store))
  (<- (durable-persist store {"counter" {"nextTask" 1 "revision" 900 "auditSeq" 0 "aliveMs" alive}
                   (+ "worker/" NAME) {"name" NAME "capacity" 1 "labels" {"host" NAME "role" "agent"} "versions" {}
                                       "lastSeenMs" alive}
                   (+ "meta/Worker/" NAME) STALE-META
                   (+ "service/" SERVICE) {"name" SERVICE "revision" "r1" "needs" ["net"] "replicas" 1 "readiness" None
                                           "owner" None "run" {"kind" "service" "factory" "m:f" "env" "m:e" "config" {}}}
                   (+ "meta/Service/" SERVICE) (| STALE-META {"createdBy" "old-declarer" "updatedBy" "old-declarer"})}))
  (assert (is-not store.handle None) "開いた置き場は log の handle を持つ")
  (.close store.handle)
  None)


(defk start-worker [url tmp-path]
  {:pre [(: url str) (: tmp-path Path)] :post [(: % subprocess.Popen)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "本物の worker の process を deploy/boot.sh の ROLE=worker と同じ引数の形で起こす(業務の repo は空の bare repo・状態・世代と
   readiness の file・log は tmp-path の下)。能力は zeus の worker と同じ形(agent・機体・境界と、機体を専用にする)。止めるのは呼び手。"
  (val repo (/ tmp-path "mirror.git"))
  (subprocess.run ["git" "init" "-q" "--bare" (str repo)] :check True)
  (subprocess.Popen [HY "-m" "doeff_cluster.worker.entry.main" "--coordinator" url "--name" NAME
                     "--provides" "agent,host-t,boundary-personal" "--exclusive" "host-t" "--node" "" "--capacity" "1"
                     "--repo" (str repo) "--state-dir" (str (/ tmp-path "state")) "--stop-grace" "10"
                     "--import-roots" "." "--repo-keys" "" "--tools" "git=2.43.0" "--pass-env" ""]
                    :cwd (str ROOT) :stdout (open (/ tmp-path "worker.log") "w") :stderr subprocess.STDOUT
                    :env (| (dict os.environ) {"DOEFF_WORKER_BOOT_FILE" (str (/ tmp-path "boot"))
                                               "DOEFF_WORKER_READY_FILE" (str (/ tmp-path "ready"))
                                               "PYTHONUNBUFFERED" "1"})))


(defk stop-process [process]
  {:pre [(: process subprocess.Popen)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "SIGTERM で止め(worker は全 job を回収してから終わる)、30 秒で終わらなければ kill する。"
  (when (is (.poll process) None)
    (.terminate process)
    (try (.wait process :timeout 30)
         (except [subprocess.TimeoutExpired]
           (.kill process)
           (.wait process))))
  None)


(defk logs [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "赤の時に添える worker と coordinator の log。"
  (.join "\n" (gfor f ["worker/worker.log" "coordinator.log"] :if (.exists (/ tmp-path f))
                    (+ f ":\n" (.read-text (/ tmp-path f) :encoding "utf-8" :errors "replace")))))


(defk wait-registered [url worker tmp-path]
  {:pre [(: url str) (: worker subprocess.Popen) (: tmp-path Path)] :post [(: % float)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker が GET /state の workers に出て、readiness の file に ready を書くまで待つ。待った秒を返す。"
  (val started (time.monotonic))
  (val ready (/ tmp-path "worker" "ready"))
  (while (< (- (time.monotonic) started) REGISTER-SECONDS)
    (when (is-not (.poll worker) None)
      (<- text str (logs tmp-path))
      (raise (AssertionError (.format "worker が終わった({})\n{}" worker.returncode text))))
    (when (and (in NAME (get (.json (httpx.get (+ url "/state") :timeout 5.0)) "workers"))
               (.exists ready) (= (.strip (.read-text ready)) "ready"))
      (return (- (time.monotonic) started)))
    (time.sleep 0.2))
  (<- final-text str (logs tmp-path))
  (raise (AssertionError (.format "worker が {} 秒で名乗らなかった\n{}" REGISTER-SECONDS final-text))))


(defk registers-over-http [tmp-path old-store]
  {:pre [(: tmp-path Path) (: old-store bool)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator と worker を本物の process で起こし、worker が名乗ることを確かめる(old-store = 旧い形の行を持つ置き場から起こす)。"
  (when old-store (<- (seed-old-store tmp-path)))
  (val served (! (start-coordinator tmp-path)))
  (val url (get served 0))
  (var worker None)
  (try
    (.mkdir (/ tmp-path "worker"))
    (<- started subprocess.Popen (start-worker url (/ tmp-path "worker")))
    (:= worker started)
    (<- waited float (wait-registered url worker tmp-path))
    (val view (.json (httpx.get (+ url "/workers/" NAME) :timeout 5.0)))
    (assert (and (get view "alive") (get view "ready")) view)
    (assert (= (get view "provides") ["agent" "boundary-personal" "host-t"]) view)
    ;; 名乗れたことを worker 自身が log に 1 行出す(断られていた間の理由も同じく出る)。
    (<- text str (logs tmp-path))
    (assert (in "名乗りました" (.read-text (/ tmp-path "worker" "worker.log") :encoding "utf-8")) text)
    (when old-store
      ;; 読み直しで受け付けない行になった旧い Service も、同じ名の新しい形の宣言で書ける。
      (val made (httpx.post (+ url "/resources/Service") :timeout 10.0 :headers {"X-Actor" "declarer"}
                            :json {"name" SERVICE "spec" {"revision" "r1" "needs" ["net"] "run" SAMPLE-RUN}}))
      (assert (= made.status-code 201) made.text)
      (assert (not-in "refused" (get (.json made) "status")) made.text))
    (finally
      (when (is-not worker None) (<- (stop-process worker)))
      (<- (stop-process (get served 1)))))
  True)


(deftest test-a-real-worker-registers-with-a-real-coordinator-started-from-an-empty-store [tmp-path]
  (<- ok bool (registers-over-http tmp-path False))
  (assert ok))


(deftest test-a-real-worker-registers-with-a-real-coordinator-started-from-a-store-with-old-rows [tmp-path]
  ;; 13 回目の本番の切り替えの形: 同じ名の旧い形の worker の行と版の記録を持つ置き場から起きた coordinator。
  (<- ok bool (registers-over-http tmp-path True))
  (assert ok))
