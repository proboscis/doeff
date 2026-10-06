;;; worker が起動する子 process の入口(service と task)。その commit のコードを展開した木(か実行環境の root)の中で動く。
;;; #2028 で doeff_cluster/job_entry.hy からここへ移した。worker が送る名 JOB-ENTRY はこの入口の名(#2112)。旧い path の
;;; doeff_cluster.job_entry(この入口へ渡すだけの file)は 2026-10-03 に消した(利用者の決め・#2167)— 旧い名を送る worker と、旧い
;;; doeff で宣言した Service の子は、この入口を持つ doeff で宣言し直すまで起動できない。
;;;
;;;   hy -m doeff_cluster.worker.entry.job_entry service --identity <指紋> --program PATH
;;;   hy -m doeff_cluster.worker.entry.job_entry task --program PATH --result PATH
;;;   hy -m doeff_cluster.worker.entry.job_entry probe --program PATH   (入口の検め — 版と復元だけを確かめて走らせない)
;;;
;;; job が受け取るのは Program の値 1 つだけ(ADR-DOE-CLUSTER-001 R1・R3)。この入口は既定の handler を 1 つも足さない(R2):
;;; 版を検め、詰めた Program を解き、(run program) するだけ。scheduler・時計・記録係・業務の handler は Program が自分の
;;; with-handlers で並べる。答えの無い effect はその場で上がり、process は 0 以外で終わる(worker が理由つきで起こし直す)。
;;; 宿(この入口と worker)が Program に提供するのは host_contract.HOST-CONTRACT の 3 つだけ(run-context・environ・Program の path)。
;;; 手元の sim-cluster(local.hy)の偽の宿は、この入口と同じく何も足さず、加えて柵(host_contract.SIM-PASSABLE の表の外の effect を
;;; 本番と同じ未処理の例外にする)で Program を包む — 本番の子で答えの無い effect が sim だけで通ることを防ぐ。
;;;
;;; Program の file = worker が coordinator の /programs/<sha> から取った詰めた文字列と、その job の送り手の版を並べた JSON
;;; {"blob" 詰めた文字列 "versions" 送り手の版}。版は置き場でなく task の行・宣言の行の版(#3762 — 置き場の Program は版を持たない)。
;;; service と task は同じ file を同じ read-program で読む(運び方を分けない — R3b)。--identity は service の宣言の同一性の指紋
;;; (spec-hash の材料 — 入口では読まない)。
;;; task は結果(TaskSucceeded / TaskFailed)を必ず --result の file に書いてから 0 で終わる。0 以外で終わった = 結果を書けなかった。
;;; file に書いた後、終わる前に結果を coordinator の POST /tasks/<id>/result へ直に届ける(shared/protocol/task_result の delivered-task-result — #1387:
;;; worker の次の heartbeat だけが運ぶ形では、exit 0 から heartbeat までに worker が死ぬと結果が届かず task が 2 度走った)。
;;; 届かなければ今までどおり worker が file を読んで heartbeat で運ぶ(coordinator は 2 度目の結果を冪等に受ける)。
;;; 版の違い・file の欠け・解けない Program は、task では TaskFailed(VersionMismatch / RemoteJobFailed)として結果の file に書き、
;;; service と probe では理由の 1 行を出して止まる。
;;;
;;; 実行環境(runtime env)の job: worker は env の root の venv で `uv run --no-sync --frozen --project <root の project> hy -m
;;; doeff_cluster.worker.entry.job_entry …` として起こし、宣言の JSON を DOEFF_RUNTIME_ENV、キーを DOEFF_RUNTIME_ENV_KEY で渡す。この入口は
;;; root の中の doeff-cluster(送り手の版)なので、worker と子の約束の版は runtime_env_model.CHILD-PROTOCOL。
(require doeff-hy.macros [defk deff <- val])
(val MODULE-TAGS {:context "worker" :role "main"})
(import argparse)
(import json)
(import os)
(import sys)
(import doeff [run with_handlers])
(import doeff_core_effects.file_effects [FileFailed ReadText WriteText file-done])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_cluster.shared.intent.remote_model [TaskSucceeded TaskFailed VersionMismatch RemoteJobFailed])
(import doeff_cluster.shared.core.remote_rules [version-diffs diffs-text failed-from])
(import doeff_cluster.shared.protocol.program_codec [decode-program encode-outcome])
(import doeff_cluster.foundation.process_versions [this-process-versions])
;; 子の文脈の型と読みは入口でない module に 1 つだけ置く(shared/intent/run_context の頭の註 — ここは import して、今の名を引けるように残す)。
(import doeff_cluster.shared.intent.run_context [RunContext])
(import doeff_cluster.shared.core.run_context_rules [runtime-env-of-context])
(import doeff_cluster.shared.entry.run_context_env [context-from-env])
(import doeff_cluster.worker.entry.result_delivery [deliver-task-result])


(defk parsed-program-row [path text]
  {:pre [(: path str) (: text (| str FileFailed))] :post [(: % (| dict list str int float bool None RemoteJobFailed))]
   :tags {:context "worker" :role "main"}}
  "読んだ Program の file の text を JSON に解くため(読めない・解けない file は RemoteJobFailed — 理由の文を持つ)。"
  (if (isinstance text FileFailed)
      (RemoteJobFailed (.format "Program の file {} を読めない(worker が /programs から取れていない): {}" path text.detail))
      (try
        (json.loads text)
        (except [error ValueError]
          (RemoteJobFailed (.format "Program の file {} を読めない(worker が /programs から取れていない): {}" path error))))))


(defk program-row [path]
  {:pre [(: path str)] :post [(: % (| dict RemoteJobFailed))] :tags {:context "worker" :role "main"}}
  "Program の file の中身 {\"blob\" \"versions\"} を読むため(読めない・形の違う file は RemoteJobFailed — 理由の文を持つ)。
   file の読みは effect ReadText(答え手 = 入口が積む os-file-handler — #3014)。"
  (<- text (ReadText path))
  (<- row (parsed-program-row path text))
  (cond
    (isinstance row RemoteJobFailed) row
    (not (and (isinstance row dict) (isinstance (.get row "blob") str) (isinstance (.get row "versions" {}) dict)))
      (RemoteJobFailed (.format "Program の file {} の形が違う({{\"blob\" \"versions\"}} ではない)" path))
    True row))


(deff decoded-program [#^ str blob]  ; defk にできない: process の入口(Program の外)が詰めた Program を解く
  {:pre [(: blob str)] :post [(: % tuple) (= (len %) 2)] :tags {:context "worker" :role "main"}}
  "詰めた Program を解くため: #(Program None) か、解けない時は #(None RemoteJobFailed)。"
  (try
    #((decode-program blob) None)
    (except [error Exception]
      #(None (RemoteJobFailed (.format "Program を解けない: {}: {}" (. (type error) __name__) error))))))


(deff read-program [#^ str path #^ str env-key]  ; defk にできない: process の入口(Program の外)が file を読む
  {:pre [(: path str) (: env-key str)] :post [(: % tuple) (= (len %) 2)] :tags {:context "worker" :role "main" :reads "json"}}
  "Program の file → #(Program None) か #(None 断り)。service・task・probe が同じ読みを使うため(入口の形を分けない — R3・R3b)。
   断り = VersionMismatch(file の版がこの process の版と違う — 食い違った欄と env のキーを持ち、解かない)か RemoteJobFailed
   (file が無い・形が違う・解けない)。env-key = 子の実行環境のキー(env の job でなければ空)。"
  (let [row (run (with_handlers [os-file-handler] (program-row path)))]
    (if (isinstance row RemoteJobFailed)
        #(None row)
        (let [expected (.get row "versions" {})
              actual (run (this-process-versions))
              diffs (version-diffs expected actual)]
          (if diffs
              #(None (VersionMismatch (+ "版が違うので Program を解かない: " (diffs-text diffs)
                                         (if env-key (.format "(env {})" env-key) ""))
                                      diffs env-key))
              (decoded-program (get row "blob")))))))


(defn #^ None run-service [#^ argparse.Namespace args]  ; defk にできない: process の入口(Program の外)
  "service の入口: Program を解いて、そのまま走らせる(handler を足さない — R2)。"
  (setv ctx (context-from-env))
  (setv #(program refusal) (read-program args.program (or ctx.env-key "")))
  (when (is-not refusal None)
    (print (.format "service: {}: {}" ctx.job refusal) :file sys.stderr :flush True)
    (sys.exit 3))
  (print (.format "service: {} を起動(commit {})" ctx.job ctx.revision) :file sys.stderr :flush True)
  (setv result (run program))
  (print (.format "service: {} が終わった: {!r}" ctx.job result) :file sys.stderr :flush True))


(deff task-outcome [#^ str program-path #^ RunContext ctx]  ; defk にできない: process の入口(Program の外)
  {:pre [(: program-path str) (: ctx RunContext)] :post [(: % (| TaskSucceeded TaskFailed))]
   :tags {:context "worker" :role "main"}}
  "task の入口の本体: service と同じ読み(版 → 復元)の後に走らせ、どこで断ったか分かる失敗を返す(handler は足さない — R2)。"
  (let [#(program refusal) (read-program program-path (or ctx.env-key ""))]
    (if (is-not refusal None)
        (failed-from refusal)
        (try
          (TaskSucceeded (run program))
          (except [error Exception]
            (failed-from error))))))


(defn #^ None run-probe [#^ argparse.Namespace args]  ; defk にできない: process の入口(Program の外)
  "入口の検め: 版と復元だけを確かめて走らせない(worker が起こす前に、起こせない理由を先に出すため)。"
  (setv #(program refusal) (read-program args.program ""))
  (when (is-not refusal None)
    (print refusal :file sys.stderr :flush True)
    (sys.exit 1))
  (print (.format "probe: {} を解けた" args.program) :file sys.stderr :flush True))


(defn #^ None run-task [#^ argparse.Namespace args]  ; defk にできない: process の入口(Program の外)
  "task の入口: 結果を必ず file に書き、終わる前に coordinator へ直に届けてから 0 で終わる(届かなければ worker の heartbeat が file の
   結果を運ぶ)。"
  (setv ctx (context-from-env))
  (setv outcome (task-outcome args.program ctx))
  (setv encoded (encode-outcome outcome))
  ;; 別名に書いてから置き換える(書きかけを worker に読ませない)— effect WriteText :replace True(答え手 = os-file-handler・#3014)。
  (run (with_handlers [os-file-handler] (file-done (WriteText args.result encoded :replace True))))
  ;; 届けは入口自身の I/O(worker/entry/result_delivery — job の Program を包まない)。
  (deliver-task-result ctx encoded)
  (print (.format "task: {} → {}" ctx.job (. (type outcome) __name__)) :file sys.stderr :flush True))


(defn #^ None main []  ; defk にできない: process の入口
  "子 process の入口の引数を読む。旧い引数(--factory・--env・--config・task の --blob・--versions)は argparse が知らない引数として断る。"
  (setv parser (argparse.ArgumentParser :description "doeff worker の子 process の入口(job = Program の値 1 つ)"))
  (setv sub (.add-subparsers parser :dest "kind" :required True))
  (setv service (.add-parser sub "service"))
  (.add-argument service "--identity" :required True :help "宣言の同一性の指紋(spec-hash の材料・入口では読まない)")
  (.add-argument service "--program" :required True :help "詰めた Program の file(worker が /programs/<sha> から取った JSON)")
  (setv task (.add-parser sub "task"))
  (.add-argument task "--program" :required True :help "詰めた Program の file(service と同じ形 — 版は file の中の versions)")
  (.add-argument task "--result" :required True :help "結果(TaskSucceeded / TaskFailed)を書く file")
  (setv probe (.add-parser sub "probe"))
  (.add-argument probe "--program" :required True)
  (setv args (.parse-args parser))
  (cond
    (= args.kind "service") (run-service args)
    (= args.kind "probe") (run-probe args)
    True (run-task args)))


(when (= __name__ "__main__")
  (main))
