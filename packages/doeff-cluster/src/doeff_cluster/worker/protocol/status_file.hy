;;; worker の状態の file の答え手(handlers.hy から移した・#2466)— PublishStatus を、外から覗ける 1 つの JSON の file の書きへ言い換える。
;;; 焼きの経過の秒はコードの木の言い換え(worker/protocol/code_store)へ CodeTimings で問う。file の I/O は file system の effect
;;; (本番 = os-file-handler・検 = memory-file-handler)。
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import json)
(import os)
(import doeff_core_effects.file_effects [MakeDirectory WriteText file-done])
(import doeff_cluster.worker.intent.worker_model [PublishStatus CodeTimings])
(import doeff_cluster.worker.protocol.heartbeat [status-row])


(defn #^ dict status-json [#^ tuple statuses #^ str note #^ dict timings]
  {"note" note
   "codePrepareSeconds" timings
   "jobs" (lfor s statuses (status-row s))})


;; 状態の file の mode(前の形の Path.write-text が umask 022 の下で作った物と同じ — 置き換えの書きの一時 file は 0600 なので明示する)。
(val STATUS-FILE-MODE 0o644)


(defk write-status-file [path content]
  {:pre [(: path str) (: content dict)] :post [(: % None)] :tags {:context "doeff-cluster" :role "protocol"}}
  "worker の状態を、外から覗ける 1 つの JSON の file として置くため(親の dir を作り、置き換えで書いて書きかけを読ませない)。
   file の I/O は file system の effect(本番 = os-file-handler・検 = memory-file-handler)で、断りは OSError で上げる。"
  (<- (file-done (MakeDirectory (os.path.dirname path))))
  (<- (file-done (WriteText path (json.dumps content :ensure-ascii False :indent 1) :mode STATUS-FILE-MODE :replace True)))
  None)


(defhandler status-file [#^ str path]
  ;; 引数に残す理由: 置き場の path は worker ごとの値(main が state dir から作る)。焼きの経過の秒は CodeTimings で問う(#2466)。
  (PublishStatus [statuses note]
    (<- timings dict (CodeTimings))
    (<- (write-status-file path (status-json statuses note timings)))
    (resume None)))
