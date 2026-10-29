;;; 追記の log の置き場(foundation/wal_store の WalStore)を読み書きする検の足場。置き場の file の読み書きは file の effect
;;; (coordinator/protocol/wal_files.hy — #2943)なので、置き場の口(durable-load・durable-persist・durable-checkpoint・load-state)を撃つ検は、
;;; file system の答え手・1 行の報告 slog の答え手・時計(まとめ直しの秒を測る)を本体の外側に被せる。入口(coordinator.main)の load-state と同じ組。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [EffectBase Program with-handlers])
(import doeff_time [sync-time-handler])
(import doeff_core_effects [slog-handler])
(import doeff_core_effects.os_file [os-file-handler])


(defk on-wal-files [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :tags {:context "doeff-cluster-test" :role "entry"}}
  "置き場の口を撃つ body を、本物の file system(os-file-handler)・1 行の報告(slog-handler)・壁の時計(sync-time-handler)の下で走らせるため。"
  (<- answer (with-handlers [slog-handler os-file-handler (sync-time-handler)] body))
  answer)
