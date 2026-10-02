;;; coordinator の heartbeat の返事の job と task の行を、worker が起こす JobSpec へ読む口(handlers.hy から移した・#2427)。
;;; worker の拍(coordinator への口)と、検・使い手の repo の模擬の世界が同じ読みを使う(handlers.hy は同じ名を読み直して残す)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import json)
(import pathlib [Path])
(import doeff [run])
(import doeff_cluster.shared.core.capabilities [environ-pairs])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env-of-json env-key current-platform])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.core.worker_rules [ENV-KEY-PREFIX])


(defn #^ tuple env-placement [#^ (| dict None) declared #^ str revision]  ; defk にできない: 宣言の読み(Program の外の I/O の道具)が呼ぶ
  "job の宣言の runtimeEnv(在れば)と版 → #(版 宣言の JSON の正規化した文字列 env のキー)。実行環境の job(task も service も —
   2026-09-26)は、env のキー(この worker の platform で計算)を root の置き場の鍵にする。版は宣言のまま運ぶ — coordinator が同じ
   宣言から計算する版と指紋に合わせるため(版を持たない task だけは \"env-<キー>\" を版の代わりにする)。無ければ版のまま。"
  (if (is declared None)
      #(revision None None)
      (do (setv key (run (env-key (run (runtime-env-of-json declared)) (current-platform))))
          #((or revision (+ ENV-KEY-PREFIX key)) (json.dumps declared :sort-keys True :ensure-ascii False) key))))


(defn #^ JobSpec declared-job-spec [#^ dict job]  ; defk にできない: 宣言の読み(Program の外の I/O の道具)が呼ぶ
  "heartbeat の返事の job 1 本 → worker が起動する形(runtimeEnv を持つ service は env の root で起こす)。worker が job を受けるのは
   coordinator からだけ(宣言の file を直に読む口は無い — ADR-DOE-CLUSTER-001 R1)。"
  (setv #(revision runtime key) (env-placement (.get job "runtimeEnv") (get job "revision")))
  (JobSpec (get job "name") (get job "entry") (tuple (.get job "args" [])) revision
           :once (.get job "once" False) :placement (.get job "placement")
           :handoff (bool (.get job "handoff" False))
           :ready-instance (.get job "readyInstance") :runtime-env runtime :env-key key
           ;; 入れ替えの諦め(coordinator の期限 — 返事の handoff の job だけが持つ・無ければ偽)。
           :handoff-abandoned (bool (.get job "handoffAbandoned" False))
           ;; Program の job(改訂 1 の F・G): 詰めた Program の置き場のキーと、子の環境変数。
           :program (.get job "program")
           :environ (environ-pairs (.get job "environ" {}))))


(val JOB-ENTRY "doeff_cluster.job_entry")


(defn #^ JobSpec task-spec [#^ dict task #^ Path task-dir]
  "coordinator が割り当てた task 1 本 → 1 度だけ走らせる job。結果はこの worker の file(名前は task の id で決まる)。詰めた Program は
   service の job と同じく置き場のキー program(sha)で持ち、worker の coordinator への口が /programs/<sha> から cache へ取り、
   子 process の言い換え(worker/protocol/process_host)が `--program <cache の file>` を足す(入口は `task --result <file> --program <file>` — 版は file の中の versions)。
   実行環境の task(runtimeEnv を持つ)は、env のキー(この worker の platform で計算)を root の置き場の鍵にする(env-placement)。"
  (setv id (get task "id"))
  (setv #(revision runtime key) (env-placement (.get task "runtimeEnv") (get task "revision")))
  (JobSpec (+ "task/" id) JOB-ENTRY
           #("task" "--result" (str (/ task-dir f"{id}.result")))
           revision :once True :detached (bool (.get task "detached" False)) :runtime-env runtime :env-key key
           :program (get task "program")
           ;; 子の環境変数(service の job と同じ欄・同じ路 — 子 process の言い換えが宣言の env-vars の上に重ねる)。
           :environ (environ-pairs (.get task "environ" {}))))
