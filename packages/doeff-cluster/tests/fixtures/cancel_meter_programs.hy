;;; 壁の時計の sim-cluster の検(test_cancel_meter_report.hy)の service の Program — 子 process の要求を 1 回取り消し、止めた子の数え
;;; (doeff-core-effects の metered-offloaded-subprocess-handler が計器に積む counter)を、計器の橋 with-meter-report で coordinator へ
;;; 送る(#2938 — #2847 の数えを #2740 の橋で送る)。
;;;
;;; 止めた子を数えるのは要求の run の外(取り消しの後の別の thread の新しい VM)なので、計器は process に 1 つの置き場の答え手
;;; process-meter-handler(os_process.hy の頭の註)。同じ答え手の値 1 つを、子 process の答え手と橋の読みの両方に渡す(本体が数える計器と
;;; 橋が読む計器を同じにする — meter_report.hy の頭の註)。置き場は process に 1 つなので、名は検ごとの pid の file の path から作る。
;;; 反例は子 process の答え手に計器を渡さない(None — offloaded-subprocess-handler と同じ形で、止めるが数えない)。
;;;
;;; 子が走り出してから取り消す(始まる前の取り消しは子を起こさないので数えない — doeff-core-effects の test_offloaded_process_cancel.hy の
;;; (a)): 子は自分の pid を file へ書いてから眠り、本体は file が現れるまで待ってから取り消す。子 process の答え手は thread で子を待つので
;;; 外側に scheduler が要る — sim-cluster の外側の scheduler が答え、待ちの effect(CreateExternalPromise・Wait)は柵を通る。
(require doeff-hy.macros [defk defsystem <- val])
(import os)
(import collections.abc [Callable])
(import doeff [with-handlers])
(import doeff_time [Delay])
(import doeff_core_effects.meter_effects [MeterSettings])
(import doeff_core_effects.process_meter [process-meter-handler])
(import doeff_core_effects.os_process [metered-offloaded-subprocess-handler])
(import doeff_core_effects.process_effects [RunProcess])
(import doeff_core_effects.scheduler [Spawn Cancel Task])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_cluster.shared.intent.shared_model [WriteShared])
(import doeff_cluster.shared.protocol.meter_report [with-meter-report])

;; 子: 自分の pid を $1 へ書いてから眠る(exec なので眠りも同じ pid)。眠りは検より長い — 止める係が居なければ残る。
(val CHILD-SCRIPT "echo $$ > \"$1\"; exec sleep 30")
;; 本体が子の pid の file を問う間隔(秒)。
(val POLL-SECONDS 0.05)
;; 橋が計器の断面を送る間隔(秒 — 壁の時計。本番の既定 30 秒では検が長くなる)。
(val REPORT-SECONDS 0.2)
;; 本体が子の要求を取り消した後に書く盤の行(筋書きはこの行を待ってから coordinator の計測値を読む)。
(val CANCELLED-KEY "cancel/stopper")


(defk child-started [pid-file]
  {:pre [(: pid-file str)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "子が pid-file を書く(走り出す)まで POLL-SECONDS おきに待つため。"
  (while (not (os.path.exists pid-file))
    (<- (Delay POLL-SECONDS)))
  None)


(defk cancel-once-then-idle [pid-file]
  {:pre [(: pid-file str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "子 process の要求を 1 本 Spawn で出し、子が走り出した後に取り消し、取り消した事を盤に書いてから、止められるまで待つため(待つ間も橋が
   数えを送り続ける)。"
  (<- task Task (Spawn (RunProcess :argv #("/bin/sh" "-c" CHILD-SCRIPT "sh" pid-file))))
  (<- (child-started pid-file))
  (<- (Cancel task))
  (<- (WriteShared CANCELLED-KEY (OpaqueJson.of {"pidFile" pid-file})))
  (while True
    (<- (Delay 60.0)))
  1)


(defk stopper-program [foundation meter counted pid-file]
  {:pre [(: foundation Callable) (: meter Callable) (: counted (| Callable None)) (: pid-file str)] :post [(: % int)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: 子の要求を 1 回取り消す本体を、止めた子を counted(計器の答え手・None = 数えない)へ数える子 process の答え手と、meter を読む
   計器の橋 with-meter-report の下で、土台で包んで走らせる。"
  (<- n int (foundation (with-handlers [meter (metered-offloaded-subprocess-handler counted)]
                          (with-meter-report (cancel-once-then-idle pid-file) REPORT-SECONDS))))
  n)


(defk counting-stopper-program [foundation pid-file]
  {:pre [(: foundation Callable) (: pid-file str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: stopper-program を、橋が読む計器と同じ答え手の値で止めた子を数える形で走らせる(系の宣言の引数は土台と literal と系の引数
   だけなので、計器の渡し方ごとに入口を置く)。"
  (val meter (process-meter-handler (+ "cancel-meter:" pid-file) (MeterSettings)))
  (<- n int (stopper-program foundation meter meter pid-file))
  n)


(defk uncounted-stopper-program [foundation pid-file]
  {:pre [(: foundation Callable) (: pid-file str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service の反例: stopper-program を、子 process の答え手に計器を渡さない形(None)で走らせる — 子は止めるが数えない。"
  (val meter (process-meter-handler (+ "cancel-meter:" pid-file) (MeterSettings)))
  (<- n int (stopper-program foundation meter None pid-file))
  n)


(defsystem cancel-reporters [#^ Callable foundation #^ str pid-file]
  "見本の系: 子 process の要求を 1 回取り消し、止めた子の数えを橋で coordinator へ送る service 1 つ"
  (stopper (counting-stopper-program foundation pid-file) :replicas 1 :needs #{"cluster-net"}))


(defsystem uncounted-cancel-reporters [#^ Callable foundation #^ str pid-file]
  "cancel-reporters の反例: 子 process の答え手に計器を渡さない"
  (stopper (uncounted-stopper-program foundation pid-file) :replicas 1 :needs #{"cluster-net"}))
