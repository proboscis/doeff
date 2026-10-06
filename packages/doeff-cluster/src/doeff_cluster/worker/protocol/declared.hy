;;; coordinator の heartbeat の返事の job と task の行を、worker が起こす JobSpec へ読む口(handlers.hy から移した・#2427)。
;;; worker の拍(coordinator への口)と、検・使い手の repo の模擬の世界が同じ読みを使う(handlers.hy は同じ名を読み直して残す)。
;;; 返事のうち宣言の部分(job の行と draining)は、JSON の境界 declared-reply-of-json で 1 度だけ型 DeclaredReply へ解く(#3684)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord defwire])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass replace])  ; defrecord と defwire の展開が名指す
(import json)
(import pathlib [Path])
(import doeff_hy.wire [Malformed parse])
(import doeff_cluster.shared.core.capabilities [environ-pairs])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env-of-json env-key])
(import doeff_cluster.shared.core.native_wheel [current-platform])
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


(defk declared-job-spec [job draining]
  {:pre [(: job (get dict #(str object))) (: draining bool)] :post [(: % JobSpec)] :tags {:context "worker" :role "protocol" :reads "json"}}
  "heartbeat の返事の job 1 本 → worker が起動する形(runtimeEnv を持つ service は env の root で起こす)。worker が job を受けるのは
   coordinator からだけ(宣言の file を直に読む口は無い — ADR-DOE-CLUSTER-001 R1)。draining = 同じ返事の draining(この worker が drain
   中か)— 版を据え置く印 hold-version に写す(#3684)。既定の値は持たない: 既定があると、新しい読み手が渡し忘れた時に drain 中の
   worker が新しい版を黙って起こす(既定が無ければ、渡し忘れは呼んだ所で引数の不足として止まる)。"
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
           :keep-when-cut-off (is (.get job "keepWhenCutOff" False) True)
           ;; 版を据え置く印(#3684 — drain 中の worker は、drain の間に宣言し直された新しい版を準備も起動もしない・worker_policy.plan-job)。
           :hold-version draining))


(defwire DeclaredReply
  "heartbeat の返事のうち、worker が起こす宣言の部分(#3684): jobs = job の行の列(1 行ずつ declared-job-spec が JobSpec へ読む)・
   draining = この worker が drain 中か(ready の file と、版を据え置く印 hold-version が同じ値を読む)。どちらも省けない欄 — coordinator は
   2026-09-25 から返事に draining を必ず載せ、旧い形の返事を作る相手はもう無いので、欄の無い返事は誤り(DeclaredReplyMalformed)。
   ほかの欄(tasks・warm・timing・revision など)はここでは読まない。JSON の綴りは coordinator/protocol/replies の heartbeat-reply-json。"
  {:tags {:context "worker" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ (get tuple #((get dict #(str object)) ...)) jobs)
  (#^ bool draining))


(defclass DeclaredReplyMalformed [ValueError]  ; class にする理由: 返事の読めない形を名指す例外の型(拍の except と検が型で名指す — 欄も状態も足さない)
  "heartbeat の返事が DeclaredReply の形でない(jobs か draining が無い・jobs が job の行の列でない・draining が真偽でない)。読みを黙って
   既定の値で埋めない — 埋めると drain 中の worker が新しい版を起こす(#3684)。")


(defk declared-reply-of-json [reply]
  {:pre [(: reply (get dict #(str object)))] :post [(: % DeclaredReply)] :tags {:context "worker" :role "protocol" :reads "json"}}
  "heartbeat の返事(本文の JSON の object)を、JSON の境界で 1 度だけ DeclaredReply へ解くため(本番の coordinator への口・sim の宿・
   使い手の repo の模擬の世界が同じ読みを使う)。欄の形が違えば DeclaredReplyMalformed(どの欄が・なぜ)で落ちる。"
  (<- read (| DeclaredReply Malformed) (parse DeclaredReply reply))
  (match read
    (Malformed :fields fields)
      (raise (DeclaredReplyMalformed (.format "heartbeat の返事が DeclaredReply の形でない: {}"
                                              (.join "・" (gfor f fields (+ f.field " " f.reason))))))
    _ read))


(defk declared-job-specs [reply]
  {:pre [(: reply DeclaredReply)] :post [(: % (get tuple #(JobSpec ...)))] :tags {:context "worker" :role "protocol" :reads "json"}}
  "heartbeat の返事の宣言の部分(declared-reply-of-json で解いた物)の job の行の列を、worker が起動する形の列に読むため(worker の拍と
   sim の宿が同じ読みを使う)。同じ返事の draining(この worker が drain 中か)を全部の job の版を据え置く印 hold-version に写す(#3684)。"
  (var specs #())
  (for [job reply.jobs]
    (<- spec JobSpec (declared-job-spec job reply.draining))
    (:= specs (+ specs #(spec))))
  specs)


(val JOB-ENTRY "doeff_cluster.worker.entry.job_entry")


(defk task-spec [task task-dir]
  {:pre [(: task (get dict #(str object))) (: task-dir Path)] :post [(: % JobSpec)] :tags {:context "worker" :role "protocol" :reads "json"}}
  "coordinator が割り当てた task 1 本 → 1 度だけ走らせる job。結果はこの worker の file(名前は task の id で決まる)。詰めた Program は
   service の job と同じく置き場のキー program(sha)で持ち、worker の coordinator への口が /programs/<sha> から cache へ取り、
   子 process の言い換え(worker/protocol/process_host)が `--program <cache の file>` を足す(入口は `task --result <file> --program <file>` — 版は file の中の versions)。
   file の中の versions は task の行の versions で、cache の file は版ごとに分かれる(launch.spec-program-file・#3762)。
   実行環境の task(runtimeEnv を持つ)は、env のキー(この worker の platform で計算)を root の置き場の鍵にする(env-placement)。"
  (val id (get task "id"))
  (<- placed EnvPlacement (env-placement (.get task "runtimeEnv") (get task "revision")))
  (JobSpec (+ "task/" id) JOB-ENTRY
           #("task" "--result" (str (/ task-dir f"{id}.result")))
           placed.revision :once True :detached (bool (.get task "detached" False))
           :runtime-env placed.runtime-env :env-key placed.env-key
           :program (get task "program")
           ;; 子の入口が比べる送り手の版 = task の行の versions(task を作った時の版 — coordinator の Program の行の版は後の送り手に
           ;; 上書きされうるので使わない・#3762)。
           :versions (environ-pairs (get task "versions"))
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
