;;; 子 process の task の結果を、終わる前に coordinator へ直に届ける入口の組み立て(#2427 の直の届け — #2028 で job_entry から分けた)。
;;; job_entry は job の Program を (run program) で走らせるだけで、Program の外から handler を足さない(ADR-DOE-CLUSTER-001 R2)。
;;; 結果の届けは job の Program ではなく入口自身の I/O なので、その handler の組(待ち・記録の行・本物の HTTP・時計)はここで積む。
;;; 届けの本体は宛先の部品の上の HttpRequest(shared/protocol/task_result の delivered-task-result)。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "worker" :role "main"})
(import doeff [run with-handlers])
(import doeff_core_effects.handlers [await-handler slog-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [sync-time-handler])
(import doeff_cluster.shared.intent.run_context [RunContext])
(import doeff_cluster.shared.protocol.task_result [delivered-task-result])
(import doeff_cluster.shared.protocol.coordinator_route [RouteOptions])
(import doeff_cluster.foundation.coordinator_http [REPLY-SECONDS CONNECT-SECONDS PREFERRED-RECHECK-SECONDS RESEND-PAUSE-SECONDS default-actor])
(import doeff_cluster.shared.core.resend [IDEMPOTENT-DEADLINE-SECONDS])


(deff deliver-task-result [#^ RunContext ctx #^ str encoded]  ; defk にできない: process の入口(Program の外)が届けを 1 回走らせる
  {:pre [(: ctx RunContext) (: encoded str)] :post [(: % None)] :tags {:context "worker" :role "main"}}
  "task の詰めた結果 encoded を coordinator の POST /tasks/<id>/result へ届けるため(届かなければ worker の heartbeat が file の結果を運ぶ)。
   送り直しは接続の段の一巡し直し 1 回だけ(子の終わりを長く止めない)。"
  (run (scheduled (with-handlers [(await-handler) slog-handler (http-production-handler) (sync-time-handler)]
                                 (delivered-task-result ctx.coordinator-url ctx.job ctx.worker ctx.instance encoded
                                                        (RouteOptions :reply-seconds REPLY-SECONDS :connect-seconds CONNECT-SECONDS
                                                                      :resend-deadline-seconds IDEMPOTENT-DEADLINE-SECONDS
                                                                      :resend-pause-seconds RESEND-PAUSE-SECONDS
                                                                      :connect-retries 1 :recheck-ms (int (* PREFERRED-RECHECK-SECONDS 1000))
                                                                      :actor (default-actor))))))
  None)
