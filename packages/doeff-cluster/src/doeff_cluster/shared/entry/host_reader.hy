;;; 宿の契約(foundation/host_contract の HOST-CONTRACT)の本番の答え手 host-reader — RunContext を組んで Ask に答える所を入口の側へ
;;; 移した(#2981・#2167 の子。foundation の層は foundation しか読めず、RunContext の型と読みは intent と core に在る)。
;;;
;;; worker が子へ渡した環境変数を読み、Ask HOST-CONTRACT.run-context-key に RunContext(shared/entry/run_context_env の
;;; context-from-env)・program-key に Program の path・versions-key にこの process の版(foundation/process_versions)で答える。
;;; 宣言の :environ(子の環境変数を名で読む Ask)に答えるのは foundation/host_contract の environ-reader のまま(本番の土台は両方を並べる)。
;;; host-reader は session val を使うので、その外側に状態の handler(doeff_core_effects.handlers の state)が要る — 土台の組の中で
;;; host-reader より外に置く。sim の偽の宿は同じ鍵に同じ型で答える(sim/local.hy)。
;;;
;;; foundation/host_contract には同じ名の答え手 host-reader を旧い入口として 1 版残す(使い手の付け替えの後に消す — #2981 の 2 段目)。
;;; あちらからここを名で指せない: foundation の層は入口を読めず、ここは宿の契約の鍵を読むためにあちらを import する(指すと輪になる)。
(require doeff-hy.macros [defhandler val])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
(import os)
(import doeff_core_effects.effects [Ask])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import doeff_cluster.foundation.process_versions [process-versions])
(import doeff_cluster.shared.entry.run_context_env [context-from-env])


(defhandler host-reader
  {:needs #{} :tags {:context "doeff-cluster" :role "main"}}
  ;; 本番の宿の答え(worker が子へ渡した環境変数を読む)。土台の handler なので os.environ を直に読む(ADR-DOE-CLUSTER-001 R5b —
  ;; 記録係の下に置く)。環境変数は process の間で変わらないので session で 1 回だけ読む。
  (session val context (context-from-env))
  (session val program-path (os.environ.get HOST-CONTRACT.program-env ""))
  ;; 版の識別は env のキー(DOEFF_RUNTIME_ENV_KEY)を毎回読む(process-versions の註 — 版そのものは 1 度だけ読んで持つ)。
  (Ask [key]
    :when (in key #(HOST-CONTRACT.run-context-key HOST-CONTRACT.program-key HOST-CONTRACT.versions-key))
    (resume (match key
              HOST-CONTRACT.run-context-key context
              HOST-CONTRACT.program-key program-path
              _ (! (process-versions os.environ))))))
