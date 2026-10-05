;; 待ちの子を uv の後ろに起こす形(worker/core/warm_rules の WARM-CHILD-FLAGS — 宿 worker/protocol/warm_host が使う)で、uv だけが外から KILL されても待ちの子が残らないこと
;; (#3646・2026-10-05 cc2-w50 の問い)。uv run は python の子を自分の process group に置くので、worker が uv の終わりを
;; PollProcess で観測して回収する時の reap-group が、group に残った子を止める。reap-group を外した頼みでは子が残る(失敗ケース — この
;; 守りが効いている事を示す)。本物の uv と process(subprocess-handler)で確かめる。
(require doeff-hy.macros [defk deftest <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass replace])  ; dataclass = defrecord の展開が名指す
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_core_effects.handlers [slog-handler state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.file_effects [ReadText])
(import doeff_core_effects.process_effects [EnvMode StartProcess PollProcess ProcessAlive RunProcess ReadEnvironment ProcessStarted
                                            ProcessNotStarted ProcessRunning ProcessExited ProcessNotChild])
(import doeff_time [sync-time-handler])
(import doeff_cluster.worker.core.launch [CHILD-ENV-ALLOWED CHILD-ENV-PREFIXES])
(import doeff_cluster.worker.core.warm_rules [WARM-CHILD-FLAGS])

;; repo の根(uv の --project — 待ちの子の代わりの sleeper は標準の module だけを使う)。
(val REPO (str (get (. (.resolve (Path __file__)) parents) 3)))
;; 待ちの子の代わり: 自分の pid を引数の file に書いて眠る(uv が出す行と混ざらないよう、出力の file とは別)。
(val SLEEPER "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid()) + '\\n'); time.sleep(60)")


(defrecord UvKilled
  "uv だけを KILL して回収した後の観測: ended = uv の終わりの答え・child = uv の後ろの python の pid・gone = その子が止まったか。"
  {:tags {:context "doeff-cluster-test" :role "type"}}
  (#^ (| ProcessExited ProcessRunning ProcessNotChild) ended)
  (#^ int child)
  (#^ bool gone))


(defk pid-in [path]
  {:pre [(: path str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "sleeper が書いた pid を読むため — 起こした直後はまだ書かれていないことがあるので、2 秒の内 0.05 秒ずつ読み直す(契約テストの first-line-of と同じ形)。"
  (var text "")
  (var tries 0)
  (while (and (not-in "\n" text) (< tries 40))
    (<- read (ReadText path))
    (:= text (if (isinstance read str) read ""))
    (when (not-in "\n" text)
      (<- (RunProcess :argv #("sleep" "0.05")))
      (:= tries (+ tries 1))))
  (int (get (.split text "\n") 0)))


(defk ended-soon [pid]
  {:pre [(: pid int)] :post [(: % (| ProcessExited ProcessRunning ProcessNotChild))] :tags {:context "doeff-cluster-test" :role "program"}}
  "KILL した uv の終わりを PollProcess で観測するため(worker の拍と同じ問い — 終わりを答えた時に回収する)。5 秒の内 0.05 秒ずつ問う。"
  (var seen (ProcessRunning :pid pid))
  (var tries 0)
  (while (and (isinstance seen ProcessRunning) (< tries 100))
    (<- polled (| ProcessExited ProcessRunning ProcessNotChild) (PollProcess pid))
    (:= seen polled)
    (when (isinstance seen ProcessRunning)
      (<- (RunProcess :argv #("sleep" "0.05")))
      (:= tries (+ tries 1))))
  seen)


(defk gone-soon [pid]
  {:pre [(: pid int)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "uv の後ろの子が 2 秒の内に止まったかを知るため(止めた子は init が拾うまで生きて見えるので 0.05 秒ずつ見直す)。"
  (var alive True)
  (var tries 0)
  (while (and alive (< tries 40))
    (<- seen bool (ProcessAlive pid))
    (:= alive seen)
    (when alive
      (<- (RunProcess :argv #("sleep" "0.05")))
      (:= tries (+ tries 1))))
  (not alive))


(defk uv-killed [root reap-group]
  {:pre [(: root str) (: reap-group bool)] :post [(: % UvKilled)] :tags {:context "doeff-cluster-test" :role "program"}}
  "待ちの子の起こし方(warm_rules の WARM-CHILD-FLAGS と宿 warm_host と同じ env と出力の形)で uv の後ろに sleeper を起こし、uv の pid
   だけを外から KILL して回収した後に、uv の後ろの python の子が残っているかを確かめるため。reap-group を外した頼みは失敗ケース(残った
   子はこの関数が片づける)。"
  (val pid-file (+ root "/sleeper.pid"))
  (val log (+ root "/warm.log"))
  (val flags (replace WARM-CHILD-FLAGS :reap-group reap-group))
  (<- env tuple (ReadEnvironment (tuple (sorted CHILD-ENV-ALLOWED)) :prefixes CHILD-ENV-PREFIXES))
  (<- started (| ProcessStarted ProcessNotStarted)
      (StartProcess :argv #("uv" "run" "--no-sync" "--frozen" "--project" REPO "python" "-c" SLEEPER pid-file) :cwd root :env env
                    :env-mode EnvMode.REPLACE :stdout-path log :stderr-path log
                    :process-group flags.process-group :hold-stdin flags.hold-stdin :reap-group flags.reap-group))
  (assert (isinstance started ProcessStarted) (.format "uv を起こせない: {}" started))
  (<- child int (pid-in pid-file))
  (<- (RunProcess :argv #("kill" "-KILL" (str started.pid))))  ; 外から uv だけを止める(worker の止めは group へ送るので、この形は外の KILL だけ)
  (<- ended (| ProcessExited ProcessRunning ProcessNotChild) (ended-soon started.pid))
  (<- gone bool (gone-soon child))
  (when (not gone)
    (<- (RunProcess :argv #("kill" "-KILL" (str child)))))  ; 失敗ケースで残った子を片づける
  (UvKilled :ended ended :child child :gone gone))


(defk uv-killed-for-real [root reap-group]
  {:pre [(: root str) (: reap-group bool)] :post [(: % UvKilled)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "uv-killed を本物の process と file の答え手の下で回すため。"
  (<- seen UvKilled (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler] (uv-killed root reap-group)))
  seen)


(deftest test-a-warm-child-behind-uv-does-not-outlive-a-killed-uv [tmp-path]
  (<- seen UvKilled (uv-killed-for-real (str tmp-path) True))
  (assert (isinstance seen.ended ProcessExited) seen)
  (assert seen.gone (.format "uv だけを KILL して回収した後も、uv の後ろの待ちの子(pid {})が生きている" seen.child)))


(deftest test-without-reap-group-the-warm-child-outlives-a-killed-uv
  ;; 失敗ケース: reap-group を外すと、uv を回収しても子は残る(上の検が reap-group の守りを測っている事を示す)。
  [tmp-path]
  (<- seen UvKilled (uv-killed-for-real (str tmp-path) False))
  (assert (isinstance seen.ended ProcessExited) seen)
  (assert (not seen.gone) (.format "reap-group 無しでも子(pid {})が止まった — uv の振る舞いが変わった(上の検が守りを測れていない)" seen.child)))
