;;; 待ちの子の宿(#3646 の A4b)— worker/protocol/warm_host の言い換えと、worker/protocol/process_host が warm-key の在る task を待ちの子から
;;; 分ける道を、I/O なしの台本の答え手(子 process・待ちの子・file を memory で)の上で確かめる。本物の待ちの子の入口と効果の答え手は
;;; A2・A3 の検(tests/test_warm_child.py・doeff-core-effects の test_warm_process_contract)が持つ。
(require doeff-hy.macros [deftest defk val var <-])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import pathlib [Path])
(import dataclasses [replace])
(import doeff [run with-handlers])
(import doeff_core_effects.handlers [state slog-handler])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.file_effects [MemoryFiles WriteText ReadText StatPath PathKind])
(import doeff_core_effects.process_effects [EnvEntry ProcessOutcome RunProcess timed-out-outcome])
(import doeff_core_effects.scripted_process [ScriptedCommand ProcessScript scripted-process-handler])
(import doeff_core_effects.scripted_warm_process [WarmScript WarmSocket WarmRun scripted-warm-process-handler])
(import doeff_time [sync-time-handler])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json])
(import doeff_cluster.worker.intent.worker_model [WarmChildMark WarmMarkUnreadable WarmChildView WarmLaunch StartWarmChild StopWarmChild ForgetWarmChild
  StopStage StartJob SignalJob ProcessView])
(import doeff_cluster.worker.protocol.observations [ObserveWarmChildren ObserveProcesses])
(import doeff_cluster.worker.protocol.warm_host [WarmSettings warm-host])
(import doeff_cluster.worker.protocol.process_host [process-host])
(import doeff_cluster.worker.core.warm_rules [WarmPlace warm-place])
(import tests.env_fixtures [LOCK env-of])
(import tests.host_rig [host-settings])

(val STATE "/state")
(val SETTINGS (WarmSettings :warm-dir "/state/warm" :log-dir "/state/logs" :uv "uv"))
(val LAUNCH (WarmLaunch :root "/roots/env-a" :project "/roots/env-a/app" :preload #("app.jobs" "app.models")))
(val ARGV-RECORD "/record/argv.json")
;; 検の memory の世界(root の dir だけが在る — 待ちの子の cwd と task の作業 dir の親)。
(val ROOTS (MemoryFiles :dirs #("/roots/env-a" "/record")))
(val SCRIPT-ENV #((EnvEntry :name "PATH" :value "/usr/bin") (EnvEntry :name "HOME" :value "/home/w")
                  (EnvEntry :name "SECRET_TOKEN" :value "never-passed")))


(defk warm-child-starts [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "台本の uv(待ちの子の起動の代役): 起こされた argv と env の名を記録し、--ready の path へ分かれる前の形の準備完了の印を書き、止めるまで
   走り続ける(本物の入口 warm_child の読み込みの後の形)。"
  (val argv (list request.argv))
  (val ready (get argv (+ (.index argv "--ready") 1)))
  (<- (WriteText ARGV-RECORD (json.dumps {"argv" argv "env" (sorted (gfor e (or request.env #()) e.name))}) :replace True))
  (<- (WriteText ready (json.dumps {"pid" 1 "threads" 1 "vmLive" [0 0 0]}) :replace True))
  (<- running ProcessOutcome (timed-out-outcome "" ""))
  running)


(defk warm-lifecycle []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "待ちの子を起こし・観測し・止め・忘れる筋を回して、各段の観測と記録を返すため。"
  (<- (StartWarmChild "env-a" LAUNCH))
  (<- ready tuple (ObserveWarmChildren))
  (<- (StopWarmChild "env-a" StopStage.TERM "root の待ちの子が要らなくなった"))
  (<- stopped tuple (ObserveWarmChildren))
  (<- (ForgetWarmChild "env-a"))
  (<- forgotten tuple (ObserveWarmChildren))
  (<- place WarmPlace (warm-place SETTINGS.warm-dir "env-a"))
  (<- gone (StatPath place.dir))
  (<- record str (ReadText ARGV-RECORD))
  #(ready stopped forgotten gone (json.loads record)))


(deftest test-the-warm-host-starts-observes-stops-and-forgets-a-warm-child
  (val script (ProcessScript :commands #((ScriptedCommand :name "uv" :run warm-child-starts)) :env SCRIPT-ENV))
  (val seen (run (with-handlers [(state) (sync-time-handler) slog-handler (memory-file-handler ROOTS) (scripted-process-handler script)
                                 (warm-host SETTINGS)]
                   (warm-lifecycle))))
  (val ready (get seen 0))
  (val stopped (get seen 1))
  (val forgotten (get seen 2))
  (val gone (get seen 3))
  (val record (get seen 4))
  ;; 起こし方: root の venv の uv run・待ちの子の入口・置き場は root の外の <warm-dir>/<キー>・読む module を名の順に。env は許可表の名だけ
  ;; (値の在る資格の名 SECRET_TOKEN は渡さない)。
  (assert (= (get record "argv")
             ["uv" "run" "--no-sync" "--frozen" "--project" "/roots/env-a/app" "python" "-m" "doeff_cluster.worker.entry.warm_child"
              "--root" "/roots/env-a" "--socket" "/state/warm/env-a/sock" "--ready" "/state/warm/env-a/ready.json"
              "--preload" "app.jobs" "--preload" "app.models"])
          record)
  (assert (= (get record "env") ["HOME" "PATH"]) record)
  ;; 準備完了の印を読んだ観測(判断が準備済みに数える形)。
  (assert (= (tuple (gfor v ready #(v.key v.mark v.exit-code))) #(#("env-a" (WarmChildMark :threads 1 :vm-live #(0 0 0)) None))) ready)
  ;; 止めた待ちの子は TERM で終わり、止めた訳が残る。
  (assert (= (tuple (gfor v stopped #(v.exit-code v.detail (is-not v.stop None)))) #(#(-15 "root の待ちの子が要らなくなった" True))) stopped)
  ;; 忘れた待ちの子は観測から消え、置き場も消える。
  (assert (= forgotten #()) forgotten)
  (assert (= gone.kind PathKind.MISSING) gone))


(defk bad-mark [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "台本の uv: 印の file に印の形でない JSON を書いて走り続ける(読めない印の失敗ケースのため)。"
  (val argv (list request.argv))
  (<- (WriteText (get argv (+ (.index argv "--ready") 1)) "{\"threads\": \"one\"}" :replace True))
  (<- running ProcessOutcome (timed-out-outcome "" ""))
  running)


(defk start-and-observe []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "待ちの子を起こして 1 度観測するため。"
  (<- (StartWarmChild "env-a" LAUNCH))
  (<- seen tuple (ObserveWarmChildren))
  seen)


(deftest test-an-unreadable-ready-mark-is-a-named-mark-not-a-start
  ;; 印の file が印の形でなければ、黙って起こし中のまま待たず、読めない印として観測に出す(判断はそれを止める)。
  (val script (ProcessScript :commands #((ScriptedCommand :name "uv" :run bad-mark)) :env SCRIPT-ENV))
  (val seen (run (with-handlers [(state) (sync-time-handler) slog-handler (memory-file-handler ROOTS) (scripted-process-handler script)
                                 (warm-host SETTINGS)]
                   (start-and-observe))))
  (assert (= (tuple (gfor v seen (isinstance v.mark WarmMarkUnreadable))) #(True)) seen))


;; --- task を待ちの子から分ける(process_host)----------------------------------------------------------------

(defk task-spec-on [key]
  {:pre [(: key str)] :post [(: % JobSpec)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "root のキー key の実行環境で走る task の宣言を組むため。"
  (<- env RuntimeEnv (env-of "app" "lib" LOCK))
  (<- value dict (runtime-env->json env))
  (JobSpec "task/t1" "doeff_cluster.worker.entry.job_entry" #("task") "rev1" :once True
           :runtime-env (json.dumps value :sort-keys True) :env-key (cut key 4 None)))


(defk fork-then-observe [spec]
  {:pre [(: spec JobSpec)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "warm-key の在る task を起こし、2 度観測するため(1 度目 = 走っている・2 度目 = 終わった)。"
  (<- (StartJob spec 1 "/roots/env-a" :warm-key "env-a"))
  (<- first tuple (ObserveProcesses))
  (<- second tuple (ObserveProcesses))
  #(first second))


(deftest test-a-warm-key-task-forks-from-its-own-roots-warm-child [tmp-path]
  (<- spec JobSpec (task-spec-on "env-a"))
  (<- settings (host-settings tmp-path))
  (<- place WarmPlace (warm-place settings.warm-dir "env-a"))
  ;; 台本の世界の待ちの子は、自分の root のキーの置き場の socket だけ(別の root の socket へ頼めば「socket が無い」で断られる)。
  (val warm (WarmScript :sockets #((WarmSocket :path place.socket)) :runs #((WarmRun :entry spec.entry :polls 1 :exit-code 0))))
  (val script (ProcessScript :commands #() :env SCRIPT-ENV))
  (val seen (run (with-handlers [(state) (sync-time-handler) slog-handler (memory-file-handler ROOTS) (scripted-process-handler script)
                                 (scripted-warm-process-handler warm) (process-host settings)]
                   (fork-then-observe spec))))
  (val first (get seen 0))
  (val second (get seen 1))
  (assert (= (tuple (gfor v first #(v.name v.exit-code))) #(#("task/t1" None))) first)
  (assert (= (tuple (gfor v second #(v.name v.exit-code))) #(#("task/t1" 0))) second))


(defk start-only [spec]
  {:pre [(: spec JobSpec)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "warm-key の在る task を起こすため。"
  (<- (StartJob spec 1 "/roots/env-a" :warm-key "env-a"))
  None)


(deftest test-a-warm-key-task-whose-warm-child-is-missing-is-refused-by-name [tmp-path]
  ;; 待ちの子の socket が無い root の task は、shim の道へ倒れずに名指しで断る(判断の層が待ちの子の準備済みを見てから起こすので、
  ;; 本番の拍でここに来るのは待ちの子が間で落ちた時だけ)。
  (<- spec JobSpec (task-spec-on "env-a"))
  (<- settings (host-settings tmp-path))
  (val script (ProcessScript :commands #() :env SCRIPT-ENV))
  (var refused None)
  (try
    (run (with-handlers [(state) (sync-time-handler) slog-handler (memory-file-handler ROOTS) (scripted-process-handler script)
                         (scripted-warm-process-handler (WarmScript)) (process-host settings)]
           (start-only spec)))
    (except [error OSError]
      (:= refused (str error))))
  (assert (and (is-not refused None) (.startswith refused "待ちの子が分けるのを断った: ")) refused))
