;; 実行環境の job の作業 dir の消し(RemoveTree)が worker の拍を止めない形の検(card ki-2158e6d6e304)。
;;
;; 実例(zeus・2026-10-23 17:42〜10-24 06:55 JST の worker の log): 区間 ActionsToPublishStatus が 10 秒を越えた拍が 17 回在り、17 回とも
;; その拍の中で実行環境の task を回収していた。回収(ReapJob)は作業 dir(<state>/jobs/task_<id> — 14 万 file・3.4 GiB に育つ)を
;; RemoveTree で消し、拍はその終わりを待った(05:47:28 に 63 秒・05:46:24 に 250 秒)。拍が止まる間は、ほかの job の起動・止め・状態の報告も
;; 止まった。だから:
;;   - 回収と起動の拍は作業 dir を置き場へ退けるだけで、木の消しを待たない — 回収を含む拍の ActionsToPublishStatus は消しの長さに依らず
;;     5 秒未満・拍の遅れの行は出ない
;;   - 消しは拍の外の task が行い、同時に 1 つ・前の process が置き場に残した物も消す
;;   - 起こす job の cwd は空の dir(前の job の作業 dir の中身は、退けた後の新しい dir に残らない)
;; 反例 = 直す前の形(ReapJob と StartJob の節が作業 dir を RemoveTree で消し、拍がその終わりを待つ process-host)は、1 木 60 秒の台で
;; 回収の拍の ActionsToPublishStatus が 60 秒を越え、拍の遅れの行が出る — 下の断言が赤(2026-10-24 に直す前の process_host.hy で確かめた)。
;; 土台: 子 process = 台本(起こした子は作業 dir に file を 1 つ書いてすぐ終わる)・file system = memory(木の消しは仮想の時計で 60 秒待つ
;; slow-removes — 14 万 file の木の代役)・時計 = 仮想の時計・coordinator への口と観測 = 台本の宿(reap-world)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import pathlib [Path])
(import doeff [Program run with-handlers])
(import doeff_core_effects [SlogEffect])
(import doeff_core_effects [state])
(import doeff_core_effects [scheduled])
(import doeff_core_effects [StopRequested])
(import tests.stop_fixtures [stop-signal-never-comes])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [MemoryFile MemoryFiles RemoveTree StatPath ListDirectory PathKind FileFailed WriteText]
        doeff_core_effects._file_effects [file-done])
(import doeff_core_effects [ProcessOutcome RunProcess])
(import doeff_core_effects [ScriptedCommand ProcessScript scripted-process-handler])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster [now-epoch-ms])
(import doeff_cluster [JobSpec])
(import doeff_cluster [CodeView CodeState WorldView WorkerPolicy ReadDesired ObserveWorld EnvReport PublishStatus PrepareCode ReleaseLeases ReapJob]
        doeff_cluster._worker.intent.worker_model [DesiredJobs])
(import doeff_cluster [ObserveProcesses])
(import doeff_cluster [process-host]
        doeff_cluster._worker.protocol.process_host [HostSettings SET-ASIDE-DIR SET-ASIDE-CLEARED-LOG job-work-dir])
(import doeff_cluster [code-key])
(import doeff_cluster [tick-pauses])
(import tests.wake_fixtures [wakes-every])
(import doeff_cluster._worker.core.program [run-worker TICK-LAG-LOG TICK-LAG-MS])
(import tests.host_rig [host-settings])

(val STATE "/state")
(val ROOT-KEY "eeeeeeeeeeeeeeeeeeeeeeee")
(val ROOT (+ STATE "/roots/" ROOT-KEY))
;; 実行環境の service(終わるたびに起こし直す — 回収と起動が筋書きの中で何度も起きる)。
(val ENV-JSON {"project" {"repo" "r" "path" "p"} "repos" [{"name" "r" "url" "https://example.invalid/r"}]})
(val SPEC (JobSpec "svc" "jobs.svc" #() "rev-a" :runtime-env (json.dumps ENV-JSON :sort-keys True) :env-key ROOT-KEY))
(val POLICY (WorkerPolicy :stop-grace-ms 1000 :kill-grace-ms 500))
;; 木 1 つの消しにかかる仮想の秒(zeus の 14 万 file・3.4 GiB の作業 dir の消しは 40〜250 秒)。
(val SLOW-SECONDS 60.0)
;; 筋書きの長さ(仮想の ms): 起こし直しの待ちを挟んで回収が何度か起き、消しの task が何本か終わるまで。
(val RUN-MS 400000)
;; 前の process が残した物: 起こす前に在った前の job の作業 dir と、置き場に残った退けた dir。
(val LEFT-WORK-FILE "left-by-the-previous-job.txt")
(val LEFT-ASIDE "svc.1.0")
;; 起こした子が作業 dir に書く file の名。
(val CHILD-FILE "out.txt")


(defrecord ReapRun
  "筋書き 1 回の見え方: reap-spans = 回収を含んだ拍の ActionsToPublishStatus の ms(拍の順)・lags = 拍の遅れの行の elapsed-ms・
   removed = 消しを始めた path(始めた順)・most = 同時に走っていた消しのいちばん多い数・cleared = 置き場の消しの終わりの行の数・
   fresh-cwd = 起こした子の cwd が空だった回数・dirty-cwd = 起こした子の cwd に前の物が残っていた回数・left-aside = 筋書きの終わりの
   置き場の中の名。"
  (#^ (get tuple #(int ...)) reap-spans)
  (#^ (get tuple #(int ...)) lags)
  (#^ (get tuple #(str ...)) removed)
  (#^ int most)
  (#^ int cleared)
  (#^ int fresh-cwd)
  (#^ int dirty-cwd)
  (#^ (get tuple #(str ...)) left-aside))


(defrecord RemoveLoad
  "外側の係が数えた物: removed = 消しを始めた path(始めた順)・most = 同時に走っていた消しのいちばん多い数・fresh-cwd・dirty-cwd =
   起こした子の cwd が空だった回数・前の物が残っていた回数。"
  (#^ (get tuple #(str ...)) removed)
  (#^ int most)
  (#^ int fresh-cwd)
  (#^ int dirty-cwd))


(defrecord TickNotes
  "宿の代役が数えた物: reap-spans = 回収を含んだ拍の ActionsToPublishStatus の ms。"
  (#^ (get tuple #(int ...)) reap-spans))


(defrecord LineNotes
  "出た行: lags = 拍の遅れの行の elapsed-ms・cleared = 置き場の消しの終わりの行の数。"
  (#^ (get tuple #(int ...)) lags)
  (#^ int cleared))


(defeffect RemoveBegan
  "木の消しを始めた — remove-load が数える。"
  {:fields [(: path str)] :answer None :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect RemoveEnded
  "木の消しを終えた — remove-load が数える。"
  {:fields [] :answer None :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect ReapSeen
  "拍の中で回収が起きた — reap-world が拍の印にする。"
  {:fields [] :answer None :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect ChildCwdSeen
  "起こした子が cwd の様子を見た — remove-load が数える(台本の子の効果は子 process の答え手の外側へ出る)。empty = cwd が空だったか。"
  {:fields [(: empty bool)] :answer None :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect NotedRemoveLoad
  "ここまでの消しの重なりを読む — 検だけの問い。"
  {:fields [] :answer RemoveLoad :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect NotedTicks
  "ここまでに宿の代役が数えた物を読む — 検だけの問い。"
  {:fields [] :answer TickNotes :tags {:context "doeff-cluster-test" :role "intent"}})


(defeffect NotedLines
  "ここまでに出た行を読む — 検だけの問い。"
  {:fields [] :answer LineNotes :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler remove-load
  "木の消しの重なりと、起こした子の cwd の様子を数える外側の係(節は待たないので、記録は並んだ task の間で崩れない)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var fresh 0)
  (session var dirty 0)
  (session var running 0)
  (session var most 0)
  (session var removed #())
  (RemoveBegan [path]
    (:= running (+ running 1))
    (:= most (max most running))
    (:= removed (+ removed #(path)))
    (resume None))
  (RemoveEnded []
    (:= running (- running 1))
    (resume None))
  (ChildCwdSeen [empty]
    (if empty (:= fresh (+ fresh 1)) (:= dirty (+ dirty 1)))
    (resume None))
  (NotedRemoveLoad []
    (resume (RemoveLoad :removed removed :most most :fresh-cwd fresh :dirty-cwd dirty))))


(defhandler slow-removes [#^ float seconds]
  "遅い disk の代役: 木の消しを仮想の秒 seconds だけ待ってから外側の memory の file system へ出し直す(始めと終わりを remove-load へ)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに消しの遅さを変える(Ask で区別できない)。
  (RemoveTree [path]
    (<- (RemoveBegan path))
    (<- (Delay seconds))
    (<- answer effect)
    (<- (RemoveEnded))
    (resume answer)))


(defhandler reaps-noted
  "回収(ReapJob)を宿の代役へ知らせてから process-host へ渡す内側の係(宿の代役より内側に置く — 節の効果は外側へ出る)。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (ReapJob [name pid outcome exit-code]
    (<- (ReapSeen))
    (<- answer effect)
    (resume answer)))


(defhandler lines-noted
  "外の世界の log の代役: 拍の遅れの行の elapsed-ms を出た順に覚え、置き場の消しの終わりの行を数え、他の行は受け流す。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var lags #())
  (session var cleared 0)
  (SlogEffect []
    (val fields effect.kwargs)
    (match effect.msg
      m :if (= m TICK-LAG-LOG) (:= lags (+ lags #((get fields "elapsed_ms"))))
      m :if (= m SET-ASIDE-CLEARED-LOG) (:= cleared (+ cleared 1))
      _ None)
    (resume None))
  (NotedLines []
    (resume (LineNotes :lags lags :cleared cleared))))


(defhandler reap-world [#^ int stop-ms]
  "本番の宿のうち coordinator への口・観測・準備の代役: 宣言は毎拍 SPEC が届き、root はすぐ READY。拍の区間 ActionsToPublishStatus
   (拍の最初の ObserveWorld の答えから PublishStatus まで — program の区間と同じ境)を、回収を含んだ拍についてだけ覚える。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: 検ごとに止める刻を変える(Ask で区別できない)。
  (session var observed-ms None)
  (session var reaped False)
  (session var spans #())
  (ReadDesired [env-report stopping]
    ;; 拍の頭: 前の拍の印を捨てる。
    (:= observed-ms None)
    (:= reaped False)
    (resume (DesiredJobs #(SPEC))))
  (StopRequested []
    (<- now int (now-epoch-ms))
    (resume (if (>= now stop-ms) "signal 15" None)))
  (EnvReport [] (resume None))
  (ReapSeen []
    (:= reaped True)
    (resume None))
  (PublishStatus [statuses note]
    (<- now int (now-epoch-ms))
    (when (and reaped (is-not observed-ms None))
      (:= spans (+ spans #((- now observed-ms)))))
    (resume None))
  (ReleaseLeases [job instance] (resume None))
  (PrepareCode [revision] (resume None))
  (NotedTicks []
    (resume (TickNotes :reap-spans spans)))
  (ObserveWorld []
    (<- processes tuple (ObserveProcesses))
    (<- now int (now-epoch-ms))
    (when (is observed-ms None)
      (:= observed-ms now))
    (resume (WorldView #((CodeView (code-key SPEC) CodeState.READY ROOT)) processes))))


(defk writes-and-exits [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "台本の shim(job の子の代役): cwd が空かを宿の代役へ知らせ、cwd に file を 1 つ書いて、すぐ 0 で終わる。"
  (val cwd request.cwd)
  (assert (is-not cwd None) "実行環境の job の子は作業 dir を cwd に起こす")
  (<- listed (ListDirectory cwd))
  (<- (ChildCwdSeen (and (not (isinstance listed FileFailed)) (= (len listed) 0))))
  (<- (file-done (WriteText (+ cwd "/" CHILD-FILE) "x\n")))
  (ProcessOutcome :exit-code 0 :stdout "" :stderr "" :timed-out False :started True :start-error ""))


(defk machine-on-disk [host]
  {:pre [(: host HostSettings)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "memory の置き場に、root と、前の process が残した物(前の job の作業 dir の中の file 1 つ・置き場の退けた dir 1 つ)を置いた中身を返すため。"
  (<- work str (job-work-dir host SPEC.name))
  (val aside (+ host.jobs-dir "/" SET-ASIDE-DIR))
  (MemoryFiles :dirs #(STATE (+ STATE "/roots") ROOT host.jobs-dir work aside (+ aside "/" LEFT-ASIDE))
               :files #((MemoryFile :path (+ work "/" LEFT-WORK-FILE) :content b"old\n")
                        (MemoryFile :path (+ aside "/" LEFT-ASIDE "/" CHILD-FILE) :content b"old\n"))))


(defk aside-names [host]
  {:pre [(: host HostSettings)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "置き場の中の名(名の順)を返すため(置き場が無ければ空)。"
  (<- listed (ListDirectory (+ host.jobs-dir "/" SET-ASIDE-DIR)))
  (if (isinstance listed FileFailed) #() (tuple (sorted (gfor entry listed entry.name)))))


(defk worker-run [host]
  {:pre [(: host HostSettings)] :post [(: % ReapRun)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の調整ループを止まれの合図まで回し、筋書きの見え方を返すため。"
  (<- (run-worker POLICY))
  (<- ticks TickNotes (NotedTicks))
  (<- lines LineNotes (NotedLines))
  (<- load RemoveLoad (NotedRemoveLoad))
  (<- left tuple (aside-names host))
  (ReapRun :reap-spans ticks.reap-spans :lags lines.lags :removed load.removed :most load.most :cleared lines.cleared
           :fresh-cwd load.fresh-cwd :dirty-cwd load.dirty-cwd :left-aside left))


(defk on-slow-disk [seconds]
  {:pre [(: seconds float)] :post [(: % ReapRun)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "本物の調整ループを process-host と台本の子 process・遅い消しの memory の file system・仮想の時計の下で RUN-MS まで回すため
   (seconds = 木 1 つの消しの仮想の秒)。"
  (<- host HostSettings (host-settings (Path STATE) :policy POLICY))
  (val script (ProcessScript :commands #((ScriptedCommand :name (. (Path host.python) name) :run writes-and-exits))))
  (<- files MemoryFiles (machine-on-disk host))
  (run (scheduled (with-handlers [(state) (sim-time-handler :clock (SimClock)) remove-load lines-noted (memory-file-handler files)
                                  (slow-removes seconds) (scripted-process-handler script) (process-host host)
                                  (reap-world RUN-MS) stop-signal-never-comes tick-pauses (wakes-every 1000) reaps-noted]
                                 (worker-run host)))))


(deftest test-a-slow-remove-does-not-hold-the-reaping-tick
  ;; 木 1 つの消しに 60 秒。回収は何度も起きるが、回収を含む拍の ActionsToPublishStatus はどれも 5 秒未満で、拍の遅れの行は出ない。
  (<- got ReapRun (on-slow-disk SLOW-SECONDS))
  (assert (>= (len got.reap-spans) 3) got)
  (assert (all (gfor ms got.reap-spans (< ms TICK-LAG-MS))) got.reap-spans)
  (assert (= got.lags #()) got.lags))


(deftest test-set-aside-dirs-are-removed-off-the-tick-one-at-a-time
  ;; 消しは拍の外の task が同時に 1 つずつ行い、前の process が置き場に残した dir も消す(1 木 60 秒なので、筋書きの間に終わる task は 1 本)。
  (<- got ReapRun (on-slow-disk SLOW-SECONDS))
  (assert (= got.most 1) got)
  (assert (in (+ STATE "/jobs/" SET-ASIDE-DIR "/" LEFT-ASIDE) got.removed) got.removed)
  (assert (not-in LEFT-ASIDE got.left-aside) got.left-aside)
  (assert (>= got.cleared 1) got)
  ;; 最初の task が消している間に退けた物も、次の task が消し始める(刻 0 に置き場に在ったのは、残りの 1 つと刻 0 の起動と回収で退けた
  ;; 2 つまで — それより多く消し始めたなら、後から退けた物を次の task が受け継いだ)。
  (assert (> (len got.removed) 3) got.removed))


(deftest test-a-started-job-gets-an-empty-work-dir
  ;; 前の process が残した作業 dir(中に file が在る)も、前の回の子が書いた file も、起こす子の cwd には残らない。
  (<- got ReapRun (on-slow-disk SLOW-SECONDS))
  (assert (>= got.fresh-cwd 3) got)
  (assert (= got.dirty-cwd 0) got))


(deftest test-a-quick-remove-keeps-the-same-runs
  ;; 消しが速い disk(0 秒)でも同じ形で回る: 回収のたびに消しの task が起き(task が走っていた間に退けた物は次の task が消す)、置き場は
  ;; 筋書きの終わりに空になるか、最後の回収の分だけが残る。
  (<- got ReapRun (on-slow-disk 0.0))
  (assert (>= (len got.reap-spans) 3) got)
  (assert (>= got.cleared 3) got)
  (assert (= got.dirty-cwd 0) got)
  (assert (<= (len got.left-aside) 1) got.left-aside))
