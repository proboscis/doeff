;;; cluster の job ではない手元の道具(手元の機体の CLI)が、値で渡す名乗りで coordinator へ切り離した task を送り・待つ口
;;; (#2782 — 最初の使い手は、使い手の repo の日次の全体検証の「予定 1 つを今 1 回積む」手元の道具)。
;;;
;;;   with-detached-client [coordinator revision runtime-env body]
;;;       本体の切り離した task の effect(SubmitDetached・AwaitDetached・CancelDetached・ReleaseDetached・ReadRunners・
;;;       AwaitRunnersChange・AwaitProcessEnded)に、本番と同じ detached-cluster で答える。宛先 = coordinator(URL — `,` で並べれば
;;;       前ほど優先)・名乗り = revision(受け側はこの版のコードを準備してから task を復元する)と runtime-env(実行環境の宣言 — 在れば
;;;       worker は env の root の中の子 process で走らせる・None なら revision の木)。版の識別はこの process の版(process-versions)。
;;;       送り方(返事の上限・送り直しの期限・書きの送り手の名)は cluster の job と同じ coordinator-route-options。
;;;
;;; cluster の job は宿の run-context から送り手を作る(cluster_foundation.hy の cluster-handlers)。手元の道具は宿を持たないので、呼び手が
;;; 名乗りを値で渡す。出す HttpRequest に答える本物の I/O の答え手(http-production-handler)と時計(GetTime・Delay の答え手)は、
;;; 呼び手の process の組み立ての根が外側に積む。使い手の型検査のための公開面の宣言は同じ dir の client_foundation.pyi。
(require doeff-hy.macros [defk <- val])
(import os)
(import doeff [Program EffectBase with-handlers])
(import doeff_cluster.shared.entry.cluster_foundation [coordinator-route-options])
(import doeff_cluster.foundation.process_versions [process-versions])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.resend [IDEMPOTENT-DEADLINE-SECONDS])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteCell RouteOptions route-of])
(import doeff_cluster.shared.protocol.detached [detached-cluster DetachedSender])


(defk with-detached-client [coordinator revision runtime-env body]
  {:pre [(: coordinator str) (: revision str) (: runtime-env (| RuntimeEnv None)) (: body (| Program EffectBase))]
   :post [(: % "body の答え")] :tags {:context "doeff-cluster" :role "process"}}
  "手元の道具の本体の切り離した task の送りと待ちを、値で渡した名乗り(revision・runtime-env)で coordinator へ話す口の下で走らせるため
   (頭の註 — 宛先の状態は 1 つの入れ物で要求から要求へ持ち越す)。"
  (<- options RouteOptions (coordinator-route-options))
  (<- now int (now-epoch-ms))
  (<- route CoordinatorRoute (route-of coordinator now))
  ;; 版の識別: 宿の契約の versions-key を読む cluster の job と違い、手元の道具は宿を持たないので、この process の環境から作る
  ;; (process の入口と同じく os.environ を渡す — Program の中で run を入れ子にしない)。
  (<- versions (get dict #(str str)) (process-versions os.environ))
  (val sender (DetachedSender :revision revision :versions versions :runtime-env runtime-env
                              :deadline-seconds IDEMPOTENT-DEADLINE-SECONDS))
  (<- answer (with-handlers [(detached-cluster (RouteCell route) options sender)] body))
  answer)
