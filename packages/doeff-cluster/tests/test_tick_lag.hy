;; worker の拍 1 つの時刻の読みの数と、拍の遅れの行の名指しの粒の検(#3715 の計りの費用の直し)。
;;
;; 実例(2026-10-06): #3715 で拍の中の effect を待ち終えるたびに時刻を刻む形にしたら、action の数だけ時刻の読みが増え、消費者の repo の
;; 模擬の roll の場面の歩数が 162,449 → 168,770 に増えた(上限 170,000 の手前)。だから:
;;   - 時刻の読みは拍の頭・重い 3 種(EnvReport・ReadDesired・最初の ObserveWorld)の待ちの後・拍の終わりの 5 回だけで、action の数に依らない
;;   - 拍の遅れの行の slowest は 3 種の名か、それ以外の区間(action・揃いの追いの観測・PublishStatus)の名 ACTIONS-TO-PUBLISH
;; 反例 = 直す前の形(action ごと・揃いの追いの ObserveWorld ごと・PublishStatus の後にも刻む)は、action の在る拍で読みが 7 回になり、
;; 遅い action と遅い PublishStatus をその effect の名で名指す — 下の断言が赤(2026-10-06 に直す前の program.hy に差し替えて確かめた)。
;; 土台: worker-tick を 1 回だけ、宿の代役(tick-world)と仮想の時計の下で回す。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import datetime [datetime])
(import doeff [Program run with-handlers])
(import doeff_core_effects.effects [SlogEffect])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [Delay GetTimeEffect SimClock sim-time-handler])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeView CodeState WorldView WorkerPolicy WorkerState DesiredJobs ReadDesired ObserveWorld
                                                 EnvReport PublishStatus PrepareCode])
(import doeff_cluster.worker.core.program [worker-tick TICK-LAG-LOG ACTIONS-TO-PUBLISH])

(val SPEC (JobSpec "svc" "jobs.svc" #() "rev-a"))
(val POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500))
;; 遅い待ちの秒(拍の遅れの線 5 秒を越える)。
(val SLOW-SECONDS 6.0)
;; 拍 1 つの時刻の読みの数(拍の頭・EnvReport の後・ReadDesired の後 = 判断の now・最初の ObserveWorld の後・拍の終わり)。
(val READS-PER-TICK 5)


(defrecord TickWorld
  "宿の代役の台本: declared = 宣言する job(在れば版の準備がまだ無いので PrepareCode の action が 1 つ出る)・slow = 6 秒待たせる effect の名
   (空 = どれも待たせない)。"
  (#^ (get tuple #(JobSpec ...)) declared)
  (setv #^ str slow ""))


(defrecord LagLine
  "拍の遅れの行 1 つの欄。"
  (#^ int elapsed-ms)
  (#^ str slowest)
  (#^ int slowest-ms))


(defrecord TickSeen
  "拍 1 つの見え方: reads = 時刻の読み(GetTime)の数・lags = 拍の遅れの行・actions = 撃たれた action の数。"
  (#^ int reads)
  (#^ (get tuple #(LagLine ...)) lags)
  (#^ int actions))


(defeffect NotedTick
  "ここまでの時刻の読み・拍の遅れの行・action の数を読む — 検だけの問い。"
  {:fields [] :answer TickSeen :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler tick-noted
  "外の世界の時計と log の手前に立つ代役: 時刻の読み(GetTime)を数えて外の時計へ出し直し、拍の遅れの行を覚え、他の行は受け流す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var reads 0)
  (session var lags #())
  (session var actions 0)
  (GetTimeEffect []
    (:= reads (+ reads 1))
    (<- now datetime effect)
    (resume now))
  (SlogEffect []
    (val fields effect.kwargs)
    (match effect.msg
      m :if (= m TICK-LAG-LOG) (:= lags (+ lags #((LagLine :elapsed-ms (get fields "elapsed_ms") :slowest (get fields "slowest")
                                                           :slowest-ms (get fields "slowest_ms")))))
      _ None)
    (resume None))
  (PrepareCode [revision]
    (:= actions (+ actions 1))
    (<- answer effect)
    (resume answer))
  (NotedTick []
    (resume (TickSeen :reads reads :lags lags :actions actions))))


(defk slowed [world name]
  {:pre [(: world TickWorld) (: name str)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "台本が name を待たせる時だけ、仮想の時計で SLOW-SECONDS 待つため。"
  (match world.slow
    s :if (= s name) (<- (Delay SLOW-SECONDS))
    _ None)
  None)


(defhandler tick-world [#^ TickWorld world]
  "本番の宿のうち coordinator への口・観測・版の準備の代役: 宣言は毎拍 world.declared・観測は版も子 process も無い・版の準備は何もしない。
   world.slow の名の effect だけ 6 秒待つ。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに宣言と待たせる effect の台本を変える(Ask で区別できない)。
  (EnvReport []
    (<- (slowed world "EnvReport"))
    (resume None))
  (ReadDesired [env-report stopping]
    (<- (slowed world "ReadDesired"))
    (resume (DesiredJobs world.declared)))
  (ObserveWorld []
    (<- (slowed world "ObserveWorld"))
    (resume (WorldView #() #())))
  (PrepareCode [revision]
    (<- (slowed world "PrepareCode"))
    (resume None))
  (PublishStatus [statuses note]
    (<- (slowed world "PublishStatus"))
    (resume None)))


(defk one-tick []
  {:pre [] :post [(: % TickSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の拍を 1 回回し、時刻の読み・拍の遅れの行・action の数を返すため。"
  (<- (worker-tick (WorkerState) POLICY False))
  (<- seen TickSeen (NotedTick))
  seen)


(defk tick-on [world]
  {:pre [(: world TickWorld)] :post [(: % TickSeen)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "拍 1 回を宿の代役と仮想の時計の下で回すため。"
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) (tick-world world) tick-noted] (one-tick)))))


(deftest test-a-quiet-tick-reads-the-time-five-times
  ;; 宣言も観測も空の静かな拍: action は無く、時刻の読みは 5 回。
  (<- seen TickSeen (tick-on (TickWorld :declared #())))
  (assert (= seen.actions 0) seen)
  (assert (= seen.reads READS-PER-TICK) seen))


(deftest test-a-tick-with-an-action-reads-the-time-as-often-as-a-quiet-tick
  ;; 版の準備の action が 1 つ在り、揃いの追いの観測も入る拍: 時刻の読みは静かな拍と同じ 5 回(直す前は 7 回)。
  (<- seen TickSeen (tick-on (TickWorld :declared #(SPEC))))
  (assert (= seen.actions 1) seen)
  (assert (= seen.reads READS-PER-TICK) seen))


(defk lag-of [world]
  {:pre [(: world TickWorld)] :post [(: % LagLine)] :tags {:context "doeff-cluster-test" :role "program"}}
  "拍 1 回を回し、拍の遅れの行がちょうど 1 つ出た事を断言してその行を返すため。"
  (<- seen TickSeen (tick-on world))
  (assert (= (len seen.lags) 1) seen)
  (val lag (get seen.lags 0))
  (assert (>= lag.slowest-ms (* SLOW-SECONDS 1000)) lag)
  (assert (>= lag.elapsed-ms lag.slowest-ms) lag)
  lag)


(deftest test-a-slow-heavy-effect-is-named-by-its-own-name
  ;; 重い 3 種のどれかが遅い拍は、その effect の名で名指す。
  (for [name #("EnvReport" "ReadDesired" "ObserveWorld")]
    (<- lag LagLine (lag-of (TickWorld :declared #(SPEC) :slow name)))
    (assert (= lag.slowest name) #(name lag))))


(deftest test-a-slow-action-or-publish-is-named-as-the-rest-of-the-tick
  ;; action か PublishStatus が遅い拍は、最初の ObserveWorld の後から拍の終わりまでの区間の名 ACTIONS-TO-PUBLISH で名指す。
  (for [name #("PrepareCode" "PublishStatus")]
    (<- lag LagLine (lag-of (TickWorld :declared #(SPEC) :slow name)))
    (assert (= lag.slowest ACTIONS-TO-PUBLISH) #(name lag))))
