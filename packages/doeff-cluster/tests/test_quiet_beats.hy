;; worker の先の拍を本番の判断で試す quiet-beats(worker/core/quiet_policy — #2781)の同値の検。
;;
;; 本物の run-worker を、時刻だけで観測が決まる偽の世界(コードの準備は prepare-ms 後に揃い、子 process は life-ms 後に落ちる)の上で
;; 1 拍ずつ回す。拍の間の眠りの答え手(AwaitNextTick)は、眠る前の記憶で quiet-beats を試して記録し、模擬の宿の sim-tick-pause(刻み 1.0 秒)で 1 拍だけ眠る。
;; - 各眠りの時点の quiet-beats の答え(眠ってよい拍の数)は、1 拍ずつの走りで次に action が出たか状態の報告が変わった拍と一致する
;;   (準備の揃い・落ちた process の回収・起こし直しの間の終わりを含む)。
;; - 反例: 先の拍の刻で観測を読み直さない判断(眠る前の観測のまま試す)は、準備の揃いと process の終わりを見落として長く答え、食い違う。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff_time [SimClock sim-time-handler])
(import doeff_core_effects.handlers [slog-discard-handler])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import tests.clock_fixtures [clock-ms])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeState CodeView ProcessView WorldView WorkerPolicy DesiredJobs JobStatus ReadDesired ObserveWorld
                                                  PublishStatus EnvReport PrepareCode StartJob SignalJob ReapJob
                                                  AwaitNextTick])
(import doeff_cluster.worker.core.program [run-worker])
(import doeff_cluster.sim.local [sim-tick-pause])
(import tests.wake_fixtures [wakes-every])
(import doeff_cluster.worker.core.quiet_policy [quiet-beats])

(val POLICY (WorkerPolicy :restart-backoff-ms 2000 :stop-grace-ms 1000 :kill-grace-ms 500))
(val TICK-MS 1000)
(val JOB (JobSpec "a" "jobs.a" #() "rev1"))
(val LIMIT 8)           ; 1 回の眠りで試す拍の上限
(val STOP-MS 40000)     ; worker を止める刻(止めの合図は観測が前もって知らない出来事 — これを越える窓は比べない)


(defclass TimedWorld []
  "時刻だけで観測が決まる偽の世界: codes = 版 → 揃う刻・procs = job の名 → #(ProcessView 落ちる刻 exit-code)・ticks = 拍ごとの
   #(刻 状態の報告)・acts = action を撃った刻・pauses = 眠りごとの #(刻 quiet-beats の答え)。stale = quiet-beats に眠る前の観測を
   渡す反例の世界か。"
  (defn #^ None __init__ [self #^ int prepare-ms #^ int life-ms #^ bool stale]
    (setv self.clock (SimClock) self.prepare-ms prepare-ms self.life-ms life-ms self.stale stale self.next-pid 100)
    (setv #^ (get dict #(str int)) self.codes {})
    (setv #^ (get dict #(str (get tuple #(ProcessView int int)))) self.procs {})
    (setv #^ (get tuple #((get tuple #(int (get tuple #(JobStatus ...)))) ...)) self.ticks #())
    (setv #^ (get frozenset int) self.acts (frozenset))
    (setv #^ (get tuple #((get tuple #(int int)) ...)) self.pauses #())
    None))


(defk timed-view [world at]
  {:pre [(: world TimedWorld) (: at int)] :post [(: % WorldView)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "偽の世界の at の刻の観測を知るため(コードは揃う刻から READY・子 process は落ちる刻から exit-code を持つ)。"
  (WorldView (tuple (gfor #(revision ready) (.items world.codes)
                          (if (>= at ready)
                              (CodeView revision CodeState.READY (+ "/c/" revision))
                              (CodeView revision CodeState.PREPARING))))
             (tuple (gfor row (.values world.procs)
                          (if (>= at (get row 1)) (replace (get row 0) :exit-code (get row 2)) (get row 0))))))


(defhandler timed-host [#^ TimedWorld world]
  ;; 引数に残す理由: 検ごとに別の世界(時計・準備と process の長さ・反例の印)で並べる(Ask で区別できない)。
  ;; 宣言は job a の 1 つ。STOP-MS で止まれと答える。拍の間の眠りは、quiet-beats の答えを記録してから本番と同じく 1 拍だけ眠る。
  (ReadDesired [env-report] (resume (DesiredJobs #(JOB))))
  (StopRequested [] (resume (if (>= (! (clock-ms world.clock)) STOP-MS) "signal 15" None)))
  (EnvReport [] (resume None))
  (ObserveWorld []
    (<- seen WorldView (timed-view world (! (clock-ms world.clock))))
    (resume seen))
  (PublishStatus [statuses note]
    (setv world.ticks (+ world.ticks #(#((! (clock-ms world.clock)) statuses))))
    (resume None))
  (PrepareCode [revision]
    (val now (! (clock-ms world.clock)))
    (setv world.codes (| world.codes {revision (+ now world.prepare-ms)}) world.acts (| world.acts #{now}))
    (resume None))
  (StartJob [spec attempt code-path]
    (val now (! (clock-ms world.clock)))
    (setv world.next-pid (+ world.next-pid 1))
    (setv world.procs (| world.procs {spec.name #((ProcessView spec.name spec attempt world.next-pid now) (+ now world.life-ms) 1)})
          world.acts (| world.acts #{now}))
    (resume None))
  (SignalJob [name pid stage]
    ;; 止めの合図で、その刻に exit-code 0 で落ちる。
    (val now (! (clock-ms world.clock)))
    (val row (get world.procs name))
    (setv world.procs (| world.procs {name #((get row 0) (min now (get row 1)) 0)}) world.acts (| world.acts #{now}))
    (resume None))
  (ReapJob [name pid outcome exit-code]
    (val now (! (clock-ms world.clock)))
    (setv world.procs (dfor #(k v) (.items world.procs) :if (!= k name) k v) world.acts (| world.acts #{now}))
    (resume None))
  (AwaitNextTick [policy changed wakes state stopping]
    (val now (! (clock-ms world.clock)))
    (<- beats int (quiet-beats state policy (fn [at] (timed-view world (if world.stale now at))) now LIMIT TICK-MS))
    (setv world.pauses (+ world.pauses #(#(now beats))))
    (<- (sim-tick-pause policy changed 1.0))
    (resume None)))


(defk breaches-of [world]
  {:pre [(: world TimedWorld)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "各眠りの quiet-beats の答えと、1 拍ずつの走りで次に action が出たか状態の報告が変わった拍(LIMIT 拍で頭打ち)との食い違いを
   並べるため(空 = 一致)。止めの刻を越える窓は比べない。"
  (val reports (dict world.ticks))
  (lfor #(at beats) world.pauses
        :if (< (+ at (* LIMIT TICK-MS)) STOP-MS)
        :setv later (lfor #(t report) world.ticks :if (and (> t at) (or (in t world.acts) (!= report (get reports at)))) t)
        :setv expected (if later (min LIMIT (// (- (get later 0) at) TICK-MS)) LIMIT)
        :if (!= beats expected)
        (.format "{} ms の眠り: quiet-beats は {} 拍・1 拍ずつの走りでは {} 拍先に変化" at beats expected)))


(defk run-timed [stale]
  {:pre [(: stale bool)] :post [(: % TimedWorld)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の run-worker を偽の世界(準備 3.3 秒・process は 4.5 秒で落ちる)の上で STOP-MS まで 1 拍ずつ回し、記録の残った世界を返すため。"
  (val world (TimedWorld 3300 4500 stale))
  (<- ((sim-time-handler :clock world.clock) (slog-discard-handler ((timed-host world) ((wakes-every TICK-MS) (run-worker POLICY))))))
  world)


(deftest test-quiet-beats-match-the-next-change-of-the-tick-by-tick-loop
  ;; どの眠りでも、quiet-beats の答えは 1 拍ずつの走りで次に action が出たか報告が変わった拍と同じ。眠りには静かな拍を含む物が在る
  ;; (答えが 1 より大きい — 準備の間・process の動いている間・起こし直しの間)。
  (<- world TimedWorld (run-timed False))
  (assert (> (len world.pauses) 20) world.pauses)
  (<- breaches list (breaches-of world))
  (assert (= breaches []) breaches)
  (assert (any (gfor #(_ beats) world.pauses (> beats 1))) world.pauses)
  ;; 反例: 先の拍の刻で観測を読み直さない判断は、準備の揃いと process の終わりを見落として食い違う。
  (<- stale TimedWorld (run-timed True))
  (<- missed list (breaches-of stale))
  (assert (!= missed []) stale.pauses))
