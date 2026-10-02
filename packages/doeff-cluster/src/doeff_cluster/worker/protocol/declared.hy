;;; coordinator の heartbeat の返事の job と task の行を、worker が起こす JobSpec へ読む口(handlers.hy から移した・#2427)。
;;; worker の拍(coordinator への口)と、検・使い手の repo の模擬の世界が同じ読みを使う(handlers.hy は同じ名を読み直して残す)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す
(import json)
(import pathlib [Path])
(import doeff_cluster.shared.core.capabilities [environ-pairs])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env-of-json env-key current-platform])
(import doeff_cluster.shared.intent.job_model [JobSpec] doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.worker.core.worker_rules [ENV-KEY-PREFIX])


(defrecord EnvPlacement
  "job を起こす版と実行環境の置き場(env-placement の答え)。revision = 起こす版(版を持たない実行環境の task は \"env-<キー>\")・
   runtime-env = 宣言の runtimeEnv の JSON の正規化した文字列・env-key = root の置き場の鍵(この 2 つは実行環境の job だけ — 無ければ None)。"
  {:tags {:context "worker" :role "protocol"}}
  (#^ str revision)
  (#^ (| str None) runtime-env)
  (#^ (| str None) env-key))


(defk env-placement [declared revision]
  {:pre [(: declared (| (get dict #(str object)) None)) (: revision (| str None))] :post [(: % EnvPlacement)] :tags {:context "worker" :role "protocol" :reads "json"}}
  "job の宣言の runtimeEnv(在れば)と版から、起こす版と root の置き場を決めるため。実行環境の job(task も service も — 2026-09-26)は、
   env のキー(この worker の platform で計算)を root の置き場の鍵にする。版は宣言のまま運ぶ — coordinator が同じ宣言から計算する版と
   指紋に合わせるため(版を持たない task だけは \"env-<キー>\" を版の代わりにする)。無ければ版のまま。"
  (if (is declared None)
      ;; 版の無い行は空の版のまま渡す — JobSpec が「job には name・entry・revision が必要です」で断る(前と同じ断り)。
      (EnvPlacement :revision (or revision "") :runtime-env None :env-key None)
      (do (<- env RuntimeEnv (runtime-env-of-json declared))
          (<- key str (env-key env (current-platform)))
          (EnvPlacement :revision (or revision (+ ENV-KEY-PREFIX key))
                        :runtime-env (json.dumps declared :sort-keys True :ensure-ascii False) :env-key key))))


(defk declared-job-spec [job]
  {:pre [(: job (get dict #(str object)))] :post [(: % JobSpec)] :tags {:context "worker" :role "protocol" :reads "json"}}
  "heartbeat の返事の job 1 本 → worker が起動する形(runtimeEnv を持つ service は env の root で起こす)。worker が job を受けるのは
   coordinator からだけ(宣言の file を直に読む口は無い — ADR-DOE-CLUSTER-001 R1)。"
  (<- placed EnvPlacement (env-placement (.get job "runtimeEnv") (get job "revision")))
  (JobSpec (get job "name") (get job "entry") (tuple (.get job "args" [])) placed.revision
           :once (.get job "once" False) :placement (.get job "placement")
           :handoff (bool (.get job "handoff" False))
           :ready-instance (.get job "readyInstance") :runtime-env placed.runtime-env :env-key placed.env-key
           ;; 入れ替えの諦め(coordinator の期限 — 返事の handoff の job だけが持つ・無ければ偽)。
           :handoff-abandoned (bool (.get job "handoffAbandoned" False))
           ;; Program の job(改訂 1 の F・G): 詰めた Program の置き場のキーと、子の環境変数。
           :program (.get job "program")
           :environ (environ-pairs (.get job "environ" {}))
           ;; 途絶しても動かし続けてよい印(#2804 — 移せる先の無い job だけが持つ・無ければ偽 = 古い coordinator の返事も同じ)。
           :keep-when-cut-off (is (.get job "keepWhenCutOff" False) True)))


(defk declared-job-specs [jobs]
  {:pre [(: jobs (get list (get dict #(str object))))] :post [(: % (get tuple #(JobSpec ...)))] :tags {:context "worker" :role "protocol" :reads "json"}}
  "heartbeat の返事の job の行の列を、worker が起動する形の列に読むため(worker の拍と sim の宿が同じ読みを使う)。"
  (var specs #())
  (for [job jobs]
    (<- spec JobSpec (declared-job-spec job))
    (:= specs (+ specs #(spec))))
  specs)


(val JOB-ENTRY "doeff_cluster.worker.entry.job_entry")


(defk task-spec [task task-dir]
  {:pre [(: task (get dict #(str object))) (: task-dir Path)] :post [(: % JobSpec)] :tags {:context "worker" :role "protocol" :reads "json"}}
  "coordinator が割り当てた task 1 本 → 1 度だけ走らせる job。結果はこの worker の file(名前は task の id で決まる)。詰めた Program は
   service の job と同じく置き場のキー program(sha)で持ち、worker の coordinator への口が /programs/<sha> から cache へ取り、
   子 process の言い換え(worker/protocol/process_host)が `--program <cache の file>` を足す(入口は `task --result <file> --program <file>` — 版は file の中の versions)。
   実行環境の task(runtimeEnv を持つ)は、env のキー(この worker の platform で計算)を root の置き場の鍵にする(env-placement)。"
  (val id (get task "id"))
  (<- placed EnvPlacement (env-placement (.get task "runtimeEnv") (get task "revision")))
  (JobSpec (+ "task/" id) JOB-ENTRY
           #("task" "--result" (str (/ task-dir f"{id}.result")))
           placed.revision :once True :detached (bool (.get task "detached" False))
           :runtime-env placed.runtime-env :env-key placed.env-key
           :program (get task "program")
           ;; 子の環境変数(service の job と同じ欄・同じ路 — 子 process の言い換えが宣言の env-vars の上に重ねる)。
           :environ (environ-pairs (.get task "environ" {}))))


(defk task-specs [tasks task-dir]
  {:pre [(: tasks (get list (get dict #(str object)))) (: task-dir Path)] :post [(: % (get tuple #(JobSpec ...)))] :tags {:context "worker" :role "protocol" :reads "json"}}
  "heartbeat の返事の task の行の列を、1 度だけ走らせる job の列に読むため(worker の拍と sim の宿が同じ読みを使う)。"
  (var specs #())
  (for [task tasks]
    (<- spec JobSpec (task-spec task task-dir))
    (:= specs (+ specs #(spec))))
  specs)
