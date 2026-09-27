;;; worker が起動する子 process の入口(service と task)。その commit のコードを展開した木(か実行環境の root)の中で動く。
;;;
;;;   hy -m doeff_cluster.job_entry service --identity <指紋> --program PATH
;;;   hy -m doeff_cluster.job_entry task --blob PATH --result PATH --versions '{…}'
;;;   hy -m doeff_cluster.job_entry probe --program PATH   (入口の検め — 版と復元だけを確かめて走らせない)
;;;
;;; job が受け取るのは Program の値 1 つだけ(ADR-DOE-CLUSTER-001 R1・R3)。この入口は既定の handler を 1 つも足さない(R2):
;;; 版を検め、詰めた Program を解き、(run program) するだけ。scheduler・時計・記録係・業務の handler は Program が自分の
;;; with-handlers で並べる。答えの無い effect はその場で上がり、process は 0 以外で終わる(worker が理由つきで起こし直す)。
;;; 宿(この入口と worker)が Program に提供するのは host_contract.HOST-CONTRACT の 3 つだけ(run-context・environ・Program の path)。
;;;
;;; service の Program の file = worker が coordinator の /programs/<sha> から取った JSON {"blob" 詰めた文字列 "versions" 詰めた送り手の版}。
;;; --identity は宣言の同一性の指紋(spec-hash の材料 — 入口では読まない)。
;;; task は結果(TaskSucceeded / TaskFailed)を必ず --result の file に書いてから 0 で終わる。0 以外で終わった = 結果を書けなかった。
;;;
;;; 実行環境(runtime env)の job: worker は env の root の venv で `uv run --no-sync --frozen --project <root の project> hy -m
;;; doeff_cluster.job_entry …` として起こし、宣言の JSON を DOEFF_RUNTIME_ENV、キーを DOEFF_RUNTIME_ENV_KEY で渡す。この入口は
;;; root の中の doeff-cluster(送り手の版)なので、worker と子の約束の版は runtime_env_model.CHILD-PROTOCOL。
(import argparse)
(import json)
(import os)
(import pathlib [Path])
(import sys)
(import doeff [run])
(import .remote_model [current-versions version-mismatch version-diffs decode-program encode-outcome
                       TaskSucceeded TaskFailed failed-from VersionMismatch RemoteJobFailed])
;; 子の文脈の型と読みは入口でない module に 1 つだけ置く(job_context の頭の註 — ここは import して、今の名を引けるように残す)。
(import .job_context [RunContext context-from-env runtime-env-of-context])


(defn #^ tuple read-program [#^ str path]  ; defk にできない: process の入口(Program の外)が file を読む
  "service の Program の file → #(Program 理由)。版が合わない・file が無い・解けない時は Program が None で理由の 1 行。"
  (try
    (setv row (json.loads (.read-text (Path path) :encoding "utf-8")))
    (except [error OSError]
      (return #(None (.format "Program の file {} を読めない(worker が /programs から取れていない): {}" path error)))))
  (setv mismatch (version-mismatch (.get row "versions" {}) (current-versions)))
  (when (is-not mismatch None)
    (return #(None (+ "版が違うので Program を解かない: " mismatch))))
  (try
    #((decode-program (get row "blob")) None)
    (except [error Exception]
      #(None (.format "Program を解けない: {}: {}" (. (type error) __name__) error)))))


(defn run-service [args]  ; defk にできない: process の入口(Program の外)
  "service の入口: Program を解いて、そのまま走らせる(handler を足さない — R2)。"
  (setv ctx (context-from-env))
  (setv #(program problem) (read-program args.program))
  (when (is-not problem None)
    (print (.format "service: {}: {}" ctx.job problem) :file sys.stderr :flush True)
    (sys.exit 3))
  (print (.format "service: {} を起動(commit {})" ctx.job ctx.revision) :file sys.stderr :flush True)
  (setv result (run program))
  (print (.format "service: {} が終わった: {!r}" ctx.job result) :file sys.stderr :flush True))


(defn #^ (| TaskSucceeded TaskFailed) task-outcome [args #^ RunContext ctx]  ; defk にできない: process の入口(Program の外)
  "task の入口の本体: 版 → 復元 → 実行の順に、どこで断ったか分かる失敗を返す(handler は足さない — R2)。"
  (setv expected (json.loads args.versions) actual (current-versions))
  (setv mismatch (version-mismatch expected actual))
  (when (is-not mismatch None)
    (return (failed-from (VersionMismatch (+ "版が違うので復元しない: " mismatch
                                             (if ctx.env-key (.format "(env {})" ctx.env-key) ""))
                                          (version-diffs expected actual) ctx.env-key))))
  (try
    (setv program (decode-program (.read-text (Path args.blob) :encoding "ascii")))
    (except [error Exception]
      (return (failed-from (RemoteJobFailed (.format "Program を復元できない: {}: {}" (. (type error) __name__) error))))))
  (try
    (TaskSucceeded (run program))
    (except [error Exception]
      (failed-from error))))


(defn #^ None run-probe [#^ argparse.Namespace args]  ; defk にできない: process の入口(Program の外)
  "入口の検め: 版と復元だけを確かめて走らせない(worker が起こす前に、起こせない理由を先に出すため)。"
  (setv #(program problem) (read-program args.program))
  (when (is-not problem None)
    (print problem :file sys.stderr :flush True)
    (sys.exit 1))
  (print (.format "probe: {} を解けた" args.program) :file sys.stderr :flush True))


(defn run-task [args]  ; defk にできない: process の入口(Program の外)
  "task の入口: 結果を必ず file に書いてから 0 で終わる。"
  (setv ctx (context-from-env))
  (setv outcome (task-outcome args ctx))
  (setv tmp (+ args.result ".tmp"))
  (with [f (open tmp "w" :encoding "utf-8")]
    (.write f (encode-outcome outcome)))
  (os.replace tmp args.result)
  (print (.format "task: {} → {}" ctx.job (. (type outcome) __name__)) :file sys.stderr :flush True))


(defn main []  ; defk にできない: process の入口
  "子 process の入口の引数を読む。旧い引数(--factory・--env・--config)は argparse が知らない引数として断る。"
  (setv parser (argparse.ArgumentParser :description "doeff worker の子 process の入口(job = Program の値 1 つ)"))
  (setv sub (.add-subparsers parser :dest "kind" :required True))
  (setv service (.add-parser sub "service"))
  (.add-argument service "--identity" :required True :help "宣言の同一性の指紋(spec-hash の材料・入口では読まない)")
  (.add-argument service "--program" :required True :help "詰めた Program の file(worker が /programs/<sha> から取った JSON)")
  (setv task (.add-parser sub "task"))
  (.add-argument task "--blob" :required True)
  (.add-argument task "--result" :required True)
  (.add-argument task "--versions" :default "{}")
  (setv probe (.add-parser sub "probe"))
  (.add-argument probe "--program" :required True)
  (setv args (.parse-args parser))
  (cond
    (= args.kind "service") (run-service args)
    (= args.kind "probe") (run-probe args)
    True (run-task args)))


(when (= __name__ "__main__")
  (main))
