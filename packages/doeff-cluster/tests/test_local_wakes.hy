;; sim-cluster の終わりの待ちは読み直さず、書きで起きる(proboscis/doeff#631)。
;;
;; - AwaitProcessEnded(process_model.hy): 世界が process の終わりを書いた時に待ち手の Promise を満たす。待つ相手の判断
;;   (local.ended-process)を呼ぶのは、掛けた時と終わりの書きの時だけ — 1 秒ごとに読み直す形なら 120 秒の待ちで 120 回を越える。
;; - AwaitDetached: 読む前に呼び鈴を掛け、模擬の coordinator がその task の終わりの phase を書いた時(Persist)に鳴る。coordinator へ
;;   送る GET /detached/<key> は終わりの前後の 2 回ほど — 前の形(DETACHED-POLL-SECONDS = 1 秒ごと)なら 120 秒の task で 120 回を越える。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import pytest)
(import doeff_cluster.local :as local)
(import doeff_cluster.local [sim-cluster ProcessesOf SimProcess SimWorker AwaitProcessStarted StartWorker])
(import doeff_core_effects.scheduler [Spawn Task Wait])
(import doeff_time [Delay])
(import doeff_cluster.clock [now-epoch-ms])
(import doeff_cluster.process_model [AwaitProcessEnded ProcessEnded ProcessWaitExpired])
(import doeff_cluster.detached_model [SubmitDetached AwaitDetached DetachedSucceeded DetachedPending])
(import doeff_cluster.detached [process-watch-step ProcessWatch])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [long-quitters pulses slow-task sim-task-foundation NET])

;; 1 秒ごとに読み直す形なら、この上限を必ず越える(待ちは 120 秒)。書きで起きる形は掛けた時・終わりの書き・期限の数回だけ。
(val READS-BOUND 4)
(val SLOW-SECONDS 120.0)


(defk count-calls [monkeypatch name]
  {:pre [(: monkeypatch pytest.MonkeyPatch) (: name str)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "local の module の関数 name を包み、呼ばれた時の引数を積む箱(list)を返すため(読みの回数を数える)。"
  (val calls [])
  (val original (getattr local name))
  (.setattr monkeypatch local name (fn [#* args] (.append calls args) (original #* args)))
  calls)


(defrecord ProcessWait
  "process の終わりの待ちの検の読み: answer = AwaitProcessEnded の答え・woke-ms = 起きた刻・process = job の最初の process の記録。"
  (#^ object answer)
  (#^ int woke-ms)
  (#^ SimProcess process))


(defk wait-for-quitter [timeout-seconds]
  {:pre [(: timeout-seconds (| float None))] :post [(: % ProcessWait)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: long-quitter の process の終わりを timeout-seconds まで待ち、起きた刻と process の記録を読む。"
  (<- answer (AwaitProcessEnded "long-quitter" :timeout-seconds timeout-seconds))
  (<- woke int (now-epoch-ms))
  (<- processes tuple (ProcessesOf "long-quitter"))
  (ProcessWait :answer answer :woke-ms woke :process (get processes 0)))


(deftest test-a-process-end-wakes-its-waiter-at-the-write-without-rereading [monkeypatch]
  ;; 120 拍で抜ける service の終わりを待つ: 起きるのは終わりを書いた刻ちょうど(1 秒の刻みに丸めない)で、待つ相手の判断は定数回。
  (<- judged list (count-calls monkeypatch "ended_process"))
  (<- seen ProcessWait (sim-cluster (long-quitters sim-foundation) (wait-for-quitter None)))
  (assert (= seen.answer (ProcessEnded :job "long-quitter" :instance seen.process.instance :worker seen.process.worker)) seen)
  (assert (= seen.woke-ms seen.process.ended-ms) seen)
  (assert (<= 1 (len judged) READS-BOUND) (len judged)))


(defrecord ExpiredWaits
  "時間切れの検の読み: first = 30 秒の待ちの答え・started-ms / woke-ms = 待ち始めと起きた刻・now = 待たない読み(timeout 0)の答え。"
  (#^ object first)
  (#^ int started-ms)
  (#^ int woke-ms)
  (#^ object now))


(defk expire-on-quitter []
  {:pre [] :post [(: % ExpiredWaits)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: long-quitter の終わりを 30 秒まで待ち(終わらない)、続けて待たずに読む。"
  (<- started int (now-epoch-ms))
  (<- first (AwaitProcessEnded "long-quitter" :timeout-seconds 30.0))
  (<- woke int (now-epoch-ms))
  (<- now (AwaitProcessEnded "long-quitter" :timeout-seconds 0.0))
  (ExpiredWaits :first first :started-ms started :woke-ms woke :now now))


(deftest test-a-process-wait-expires-at-its-timeout-and-a-zero-timeout-only-reads [monkeypatch]
  ;; 終わらない間の待ちは期限の刻に ProcessWaitExpired で返り、timeout 0 は待たずに読む。その間に読み直さない。
  (<- judged list (count-calls monkeypatch "ended_process"))
  (<- seen ExpiredWaits (sim-cluster (long-quitters sim-foundation) (expire-on-quitter)))
  (assert (= seen.first (ProcessWaitExpired :job "long-quitter" :waited-seconds 30.0)) seen)
  (assert (= (- seen.woke-ms seen.started-ms) 30000) seen)
  (assert (= seen.now (ProcessWaitExpired :job "long-quitter" :waited-seconds 0.0)) seen)
  (assert (<= 1 (len judged) READS-BOUND) (len judged)))


(defrecord DetachedWait
  "切り離した task の待ちの検の読み: answer = AwaitDetached の答え・woke-ms = 起きた刻・started-ms = 待ち始めの刻。"
  (#^ object answer)
  (#^ int woke-ms)
  (#^ int started-ms))


(defk wait-for-slow-task [key timeout-seconds]
  {:pre [(: key str) (: timeout-seconds (| float None))] :post [(: % DetachedWait)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: SLOW-SECONDS 秒眠る task を key で送り、timeout-seconds まで待って、答えと起きた刻を読む。"
  (<- (SubmitDetached (slow-task sim-task-foundation SLOW-SECONDS) :key key :needs NET :name "slow"))
  (<- started int (now-epoch-ms))
  (<- answer (AwaitDetached key :timeout-seconds timeout-seconds))
  (<- woke int (now-epoch-ms))
  (DetachedWait :answer answer :woke-ms woke :started-ms started))


(defk detached-reads [calls key]
  {:pre [(: calls list) (: key str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "数えた send-resent の呼びのうち、key の task の GET /detached/<key> の数を数えるため。"
  (len (lfor args calls :if (and (= (get args 1) "GET") (= (get args 2) (+ "/detached/" key))) args)))


(deftest test-a-detached-task-end-wakes-its-waiter-without-polling-the-coordinator [monkeypatch]
  ;; 120 秒眠る task を期限なしで待つ: 答えは task の答えで、coordinator への GET /detached/<key> は定数回(前の形は 1 秒ごと)。
  (<- sent list (count-calls monkeypatch "send_resent"))
  (<- seen DetachedWait (sim-cluster (pulses sim-foundation) (wait-for-slow-task "slow" None)))
  (assert (= seen.answer (DetachedSucceeded SLOW-SECONDS)) seen)
  (assert (>= (- seen.woke-ms seen.started-ms) (int (* 1000 SLOW-SECONDS))) seen)
  (<- reads int (detached-reads sent "slow"))
  (assert (<= 1 reads READS-BOUND) reads))


(deftest test-a-detached-wait-with-a-timeout-returns-pending-at-the-timeout-without-polling [monkeypatch]
  ;; 30 秒の期限の待ちは、終わらない task を期限の刻に DetachedPending で返し、その間に読み直さない。
  (<- sent list (count-calls monkeypatch "send_resent"))
  (<- seen DetachedWait (sim-cluster (pulses sim-foundation) (wait-for-slow-task "slow-30" 30.0)))
  (assert (isinstance seen.answer DetachedPending) seen)
  (assert (= (- seen.woke-ms seen.started-ms) 30000) seen)
  (<- reads int (detached-reads sent "slow-30"))
  (assert (<= 1 reads READS-BOUND) reads))


;; --- 本番の答え(detached-cluster が coordinator の GET /state を読む)の 1 回の読みの判断 -----------------------------


(deftest test-the-production-answer-watches-a-running-process-until-it-leaves-the-live-rows
  ;; 動いている行を見張り始め、同じ世代が動いている間は待ち、行から消えれば(backoff に移った・別の世代になった)終わり。沈黙した
  ;; worker の報告は数えない。まだ起きる前(starting)は待ち、終わった姿の行(finished)だけならすぐ終わり。
  (val running {"w1" {"jobs" [{"name" "tally" "phase" "running" "instance" "i1"}] "stale" False}
                "w2" {"jobs" [{"name" "tally" "phase" "running" "instance" "old"}] "stale" True}})
  (val restarted {"w1" {"jobs" [{"name" "tally" "phase" "backoff" "instance" "i1"}] "stale" False}})
  (val ended (ProcessEnded :job "tally" :instance "i1" :worker "w1"))
  (<- first ProcessWatch (process-watch-step running "tally" None))
  (assert (= first (ProcessWatch :watched ended :ended None)) first)
  (<- still ProcessWatch (process-watch-step running "tally" first.watched))
  (assert (is still.ended None) still)
  (<- gone ProcessWatch (process-watch-step restarted "tally" first.watched))
  (assert (= gone.ended ended) gone)
  (<- starting ProcessWatch (process-watch-step {"w1" {"jobs" [{"name" "tally" "phase" "starting" "instance" ""}] "stale" False}} "tally" None))
  (assert (= starting (ProcessWatch :watched None :ended None)) starting)
  (<- finished ProcessWatch (process-watch-step {"w1" {"jobs" [{"name" "tally" "phase" "finished" "instance" "i9"}] "stale" False}} "tally" None))
  (assert (= finished.ended (ProcessEnded :job "tally" :instance "i9" :worker "w1")) finished))


;; --- 最初の process の起き上がりの待ち(AwaitProcessStarted — 検の effect)---------------------------------------------

(defrecord StartWait
  "起き上がりの待ちの検の読み: process = AwaitProcessStarted の答え・woke-ms = 起きた刻・started-ms = 筋書きの始まりの刻。"
  (#^ SimProcess process)
  (#^ int woke-ms)
  (#^ int started-ms))


(defk start-worker-later [name seconds]
  {:pre [(: name str) (: seconds float)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの部品: seconds 秒後に止まったまま始まった worker name を起こす(job が起きる刻を筋書きが決めるため)。"
  (<- (Delay seconds))
  (<- started bool (StartWorker name))
  started)


(defk wait-for-late-start []
  {:pre [] :post [(: % StartWait)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 止まったまま始まった worker を 60 秒後に起こし、long-quitter の最初の process が起きるまで待つ。"
  (<- started int (now-epoch-ms))
  (<- starter Task (Spawn (start-worker-later "late" 60.0)))
  (<- process SimProcess (AwaitProcessStarted "long-quitter"))
  (<- woke int (now-epoch-ms))
  (<- (Wait starter))
  (StartWait :process process :woke-ms woke :started-ms started))


(deftest test-a-process-start-wakes-its-waiter-at-the-write-without-rereading [monkeypatch]
  ;; 60 秒後に起きる worker の上の job の起き上がりを待つ: 起きるのは process を記録した刻ちょうどで、待つ相手の判断は定数回。
  (<- judged list (count-calls monkeypatch "first_process"))
  (<- seen StartWait (sim-cluster (long-quitters sim-foundation) (wait-for-late-start)
                                  :workers #((SimWorker :name "late" :provides NET :starts-down True))))
  (assert (= seen.woke-ms seen.process.started-ms) seen)
  (assert (>= (- seen.woke-ms seen.started-ms) 60000) seen)
  (assert (<= 1 (len judged) READS-BOUND) (len judged)))
