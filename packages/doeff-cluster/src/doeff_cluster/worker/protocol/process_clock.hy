;;; process の始まりの刻の問い ProcessStartedMs の言い換え(#3676)— /proc/<pid>/stat と /proc/uptime を file の効果 ReadText で読み、
;;; 今の刻(doeff-time の GetTime)と合わせて epoch ミリ秒にする。I/O を持たない(本物は外側の os-file-handler と時計・模擬は memory の
;;; 置き場と仮想の時計)。/proc を読めない機体(macOS 等)は None。1 秒の clock tick の数は呼び手(入口)が foundation から読んで渡す。
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_core_effects.file_effects [FileFailed ReadText])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.intent.worker_model [ProcessStartedMs])
(import doeff_cluster.worker.core.boot_timing [process-start-ms])


(defhandler process-clock [#^ int ticks]
  ;; 引数に残す理由: 1 秒の clock tick の数は機体ごとの値(入口が os から 1 度読む・検は決めた値を渡す)。
  (ProcessStartedMs [pid]
    (<- stat (| str FileFailed) (ReadText (.format "/proc/{}/stat" pid)))
    (<- uptime (| str FileFailed) (ReadText "/proc/uptime"))
    (<- now-ms int (now-epoch-ms))
    (match #(stat uptime)
      #((str) (str)) (do (<- started (| int None) (process-start-ms stat uptime now-ms ticks))
                         (resume started))
      _ (resume None))))
