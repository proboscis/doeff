;;; heartbeat の返事と途絶の判断 — 温める表の行の読み・終わった task の見分け・coordinator に届かない拍の宣言(本番の coordinator への口 と
;;; 手元の sim-cluster の宿 sim/local が同じ判断を使う)。handlers.hy から分けた(#2026)。本文の形は worker/protocol/heartbeat。
(require doeff-hy.macros [defk deff val])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import json)
(import doeff [run])
(import doeff_cluster.shared.intent.job_model [JobPhase])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env-of-json env-key])
(import doeff_cluster.worker.intent.worker_model [WarmEnv JobStatus DesiredJobs DesiredUnreadable CutOff])
(import doeff_cluster.worker.core.worker_rules [ENV-KEY-PREFIX])
(import doeff_cluster.worker.core.policy [kept-when-cut-off])


(deff warm-env-of-row [#^ dict row #^ str platform]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ判断で返事を読む
  {:pre [(: row dict) (: platform str)] :post [(: % WarmEnv)] :tags {:context "worker" :role "judgment"}}
  "heartbeat の返事の温める表の行 1 つを、この worker の root のキー(platform で計算した env のキーに env- を付けた物)の WarmEnv に
   するため。"
  (WarmEnv :key (+ ENV-KEY-PREFIX (run (env-key (run (runtime-env-of-json (get row "runtimeEnv"))) platform)))
           :runtime-env (json.dumps (get row "runtimeEnv") :sort-keys True :ensure-ascii False)))


(deff finished-task-id [s]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が状態の行を読む純粋な判断
  {:pre [(: s JobStatus)] :post [(: % (| str None))] :tags {:context "worker" :role "judgment"}}
  "終わった task の状態の行なら task の id(結果を添える相手)、それ以外は None — 結果の file を読む・世界の結果を引く所を 1 つにするため。"
  (if (and (.startswith s.name "task/") (= s.phase JobPhase.FINISHED)) (cut s.name 5 None) None))


(defk keep-marks-held [jobs]
  {:pre [(: jobs tuple)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "最後に受け取った宣言のうち、途絶しても動かし続けてよい印(#2804)の在る service の job の名(名の順)— heartbeat の keptWhenCutOff で
   coordinator に知らせ、coordinator が「この worker はもう印を持たない」と確かめてから印の約束を外すため(印の無い返事が届いていない
   担い手から job を移さない)。本番の coordinator への口と sim の宿が同じ判断で本文に載せる。"
  (tuple (sorted (gfor job jobs :if (and job.keep-when-cut-off (not job.once)) job.name))))


(deff desired-when-unreachable [#^ int silent-ms #^ int fence-ms #^ int keep-fence-ms #^ tuple last #^ tuple warm #^ str reason]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ判断を使う
  {:pre [(: silent-ms int) (: fence-ms int) (: keep-fence-ms int) (: last tuple) (: warm tuple) (: reason str)]
   :post [(: % (| DesiredJobs DesiredUnreadable))] :tags {:context "worker" :role "judgment"}}
  "coordinator に届かなかった拍の宣言を決めるため。連絡が fence を超えて途絶えたら、lease を持たない job と task を止める(coordinator は
   後で他へ移す)。書き手(入れ替えを宣言した job)と切り離した task は動かし続ける — 書きは lease の柵だけが守り、切り離した task の
   lease はこの worker の heartbeat が延ばす(worker_policy.kept-when-cut-off・2026-09-25)。途絶しても動かし続けてよい印の在る job は、
   途絶が長い方の柵 keep-fence-ms を越えるまで動かし続ける(#2804)。fence の内なら「読めない」(直前の宣言を使い続ける)。"
  (if (> silent-ms fence-ms)
      (DesiredJobs (kept-when-cut-off last silent-ms keep-fence-ms) :warm warm :cut-off (CutOff :silent-ms silent-ms))
      (DesiredUnreadable f"coordinator に届かない({silent-ms} ms): {reason}")))


(deff desired-after-silence [#^ int silent-ms #^ int fence-ms #^ int keep-fence-ms #^ bool holding #^ tuple last #^ tuple warm]  ; defk にできない: worker の coordinator への口(worker/protocol/coordinator_link)と sim の宿が同じ判断を使う
  {:pre [(: silent-ms int) (: fence-ms int) (: keep-fence-ms int) (: holding bool) (: last tuple) (: warm tuple)]
   :post [(: % (| DesiredJobs None))] :tags {:context "worker" :role "judgment"}}
  "処理の周期の頭で、heartbeat の成否を待たずに時間だけで自己停止を決めるため(#2806)。処理が止まって heartbeat を送れなかった worker は、
   戻った最初の周期で最後の成功から fence を越えていれば、lease を持たない job と task を止めた宣言を返す(戻って最初の heartbeat の返事を
   待つ間に動かし続けない — その間に coordinator が移し替えの期限を越えて他へ置いても 2 か所で走らない)。止める物は途絶の時と同じ
   kept-when-cut-off(印の在る job は長い方の柵まで動かし続ける)。holding = 最後に成功した返事の宣言をまだ持っているか(止めた宣言を
   返した後・届かなかった後は持たない — 次の周期は heartbeat を送って返事で戻すか、届かなければ desired-when-unreachable が判じる)。
   答え None = 止めない(fence の内・もう止めてある)— heartbeat の判断へ進む。"
  (if (and holding (> silent-ms fence-ms))
      (DesiredJobs (kept-when-cut-off last silent-ms keep-fence-ms) :warm warm :cut-off (CutOff :silent-ms silent-ms))
      None))
