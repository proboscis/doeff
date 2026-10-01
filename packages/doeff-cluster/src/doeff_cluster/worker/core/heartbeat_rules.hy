;;; heartbeat の返事と途絶の判断 — 温める表の行の読み・終わった task の見分け・coordinator に届かない拍の宣言(本番の coordinator への口 と
;;; 手元の sim-cluster の宿 sim/local が同じ判断を使う)。handlers.hy から分けた(#2026)。本文の形は worker/protocol/heartbeat。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import json)
(import doeff [run])
(import doeff_cluster.shared.intent.job_model [JobPhase])
(import doeff_cluster.shared.intent.runtime_env_model [runtime-env-of-json env-key])
(import doeff_cluster.worker.intent.worker_model [WarmEnv JobStatus DesiredJobs DesiredUnreadable])
(import doeff_cluster.worker.core.worker_rules [ENV-KEY-PREFIX])
(import doeff_cluster.worker.core.policy [kept-when-cut-off])


(deff warm-env-of-row [#^ dict row #^ str platform]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ判断で返事を読む
  {:pre [(: row dict) (: platform str)] :post [(: % WarmEnv)] :tags {:context "doeff-cluster" :role "judgment"}}
  "heartbeat の返事の温める表の行 1 つを、この worker の root のキー(platform で計算した env のキーに env- を付けた物)の WarmEnv に
   するため。"
  (WarmEnv :key (+ ENV-KEY-PREFIX (run (env-key (run (runtime-env-of-json (get row "runtimeEnv"))) platform)))
           :runtime-env (json.dumps (get row "runtimeEnv") :sort-keys True :ensure-ascii False)))


(deff finished-task-id [s]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が状態の行を読む純粋な判断
  {:pre [(: s JobStatus)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "終わった task の状態の行なら task の id(結果を添える相手)、それ以外は None — 結果の file を読む・世界の結果を引く所を 1 つにするため。"
  (if (and (.startswith s.name "task/") (= s.phase JobPhase.FINISHED)) (cut s.name 5 None) None))


(deff desired-when-unreachable [#^ int silent-ms #^ int fence-ms #^ tuple last #^ tuple warm #^ str reason]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ判断を使う
  {:pre [(: silent-ms int) (: fence-ms int) (: last tuple) (: warm tuple) (: reason str)] :post [(: % (| DesiredJobs DesiredUnreadable))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator に届かなかった拍の宣言を決めるため。連絡が fence を超えて途絶えたら、lease を持たない job と task を止める(coordinator は
   後で他へ移す)。書き手(入れ替えを宣言した job)と切り離した task は動かし続ける — 書きは lease の柵だけが守り、切り離した task の
   lease はこの worker の heartbeat が延ばす(worker_policy.kept-when-cut-off・2026-09-25)。fence の内なら「読めない」(直前の宣言を
   使い続ける)。"
  (if (> silent-ms fence-ms)
      (DesiredJobs (kept-when-cut-off last) :warm warm)
      (DesiredUnreadable f"coordinator に届かない({silent-ms} ms): {reason}")))
