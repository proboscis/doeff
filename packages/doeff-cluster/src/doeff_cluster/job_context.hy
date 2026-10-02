;;; 旧い入口 — 使い手の付け替えが入った次の pin の後に消す(#2981 の 2 段目)。中身は持たず、新しい置き場の 1 点を同じ名で指すだけ。
;;;
;;; 実行先の子 process の文脈(RunContext)と、その読みの置き場(#2981 で層へ分けた):
;;;   型 RunContext                                         → doeff_cluster.shared.intent.run_context
;;;   綴りと読み(worker-context-environ・process-context-environ・context-of-environ・runtime-env-of-context)
;;;                                                         → doeff_cluster.shared.core.run_context_rules
;;;   この process の環境変数の読み context-from-env        → doeff_cluster.shared.entry.run_context_env
;;;   宿の契約の Ask に RunContext で答える host-reader      → doeff_cluster.shared.entry.host_reader
;;; 新しい使い手はここを import しない(上の置き場を直に読む)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import doeff_cluster.shared.intent.run_context [RunContext])
(import doeff_cluster.shared.core.run_context_rules [worker-context-environ process-context-environ context-of-environ
                                                     runtime-env-of-context])
(import doeff_cluster.shared.entry.run_context_env [context-from-env])
