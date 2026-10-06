;; 壁の時計の手元の runner wall-sim-cluster(doeff_cluster.sim.local — #1086・#908)。
;;
;; sim-cluster と同じ本物の coordinator と本物の run-worker(偽の宿)を、仮想の時計ではなく壁の時計(doeff-time の async-time-handler と
;; Await の答え手)で回す。検は本物の process の中の実時間で走り、1 本あたり数秒で終わる。
;;   - 系の中の service が切り離した task を出し(SubmitDetached)、worker が走らせた答えを待つ(AwaitDetached)往復が実時間で終わる。
;;     系の中の時計は検の外の時計と同じ(job が読んだ時刻が、検が外で測った走りの間に在る)。
;;   - 外の thread の客が本物の socket(ws)で系の中の job と話す: job の返事は実時間で届き(すぐの 1 通は 1 秒の内・道具の後の 1 通は道具の
;;     秒の後 1 秒の内)、job の時計は客の時計と 1 秒の内で合う(仮想の時計では job の時刻は起点の 2026-01-01 のまま進まない)。
(require doeff-hy.macros [deftest defk <- val])
(import doeff_events [MemoryBroker])
(import doeff [with-handlers])
(import doeff_time [sync-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.sim.local [wall-sim-cluster])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.wall_programs [wall-io-foundation submitters listeners rows-when-present talk-to-the-listener])

(val LIVE-SECONDS 1.0)   ; 返事の遅れの上限(秒)
(val TOOL-SECONDS 1.0)   ; listeners の道具の秒(系の宣言と同じ値)
(val TASK-MS 500)        ; submitters の task の待ち(ms — 系の宣言と同じ値)


(defk outside-now-ms []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "検が系の外で読む壁の時計の epoch ms(doeff-time の sync-time-handler — 系の中の時計と比べる物差し)。"
  (<- ms int (with-handlers [(sync-time-handler)] (now-epoch-ms)))
  ms)


(deftest test-a-service-round-trips-a-detached-task-through-a-worker-on-the-wall-clock
  (<- before int (outside-now-ms))
  (<- rows dict (wall-sim-cluster :notice-broker (MemoryBroker) (submitters sim-foundation) (rows-when-present "wall/" "wall/task" 20.0)))
  (<- after int (outside-now-ms))
  (assert (in "wall/task" rows) rows)
  (val row (get rows "wall/task"))
  (assert (= [(get row "created") (get row "value") (get row "outcome")] [True 103 "DetachedSucceeded"]) row)
  ;; 系の中の時計は壁の時計: job が読んだ送り・受けの時刻が、検が外で測った走りの間に在る。
  (assert (<= before (get row "sentMs") (get row "answeredMs") after) #(before row after))
  ;; task の待ち(0.5 秒)は実時間で過ぎた。
  (assert (>= (- (get row "answeredMs") (get row "sentMs")) TASK-MS) row)
  ;; 数秒で終わる(coordinator と worker の起動・止めの手順を含む)。
  (assert (< (- after before) 20000) #(before after)))


(deftest test-an-outside-thread-talks-with-a-job-over-a-real-socket-on-the-wall-clock
  ;; 待ち受けを持つ job の土台は Await の答え手と aiohttp の待ち受けを並べる(柵は Await を通さない)。
  (<- heard tuple (wall-sim-cluster :notice-broker (MemoryBroker) (listeners wall-io-foundation) (talk-to-the-listener "wall/address" 20.0)))
  (assert (= (lfor h heard (get h.body "kind")) ["started" "done"]) heard)
  (val started (get heard 0))
  (val done (get heard 1))
  ;; すぐの 1 通は 1 秒の内に届き、道具の後の 1 通は道具の秒の後 1 秒の内に届く。
  (assert (< started.after-seconds LIVE-SECONDS) started)
  (assert (<= TOOL-SECONDS done.after-seconds (+ TOOL-SECONDS LIVE-SECONDS)) done)
  ;; job の時計は客の時計と 1 秒の内で合う。
  (for [h heard]
    (assert (< (abs (- (get h.body "atMs") h.client-ms)) (* 1000 LIVE-SECONDS)) h))
  ;; 話の途中で job が coordinator と往復した: 道具の始まりの時刻を盤に書き、読み直して返した。
  (assert (= (get done.body "board") (get started.body "atMs")) heard))
