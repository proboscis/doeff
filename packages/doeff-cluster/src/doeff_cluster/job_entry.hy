;;; worker が起動する子 process の入口(service と task)。その commit のコードを展開した木の中で動く。
;;;
;;;   hy -m doeff_cluster.job_entry service --factory M:f --env M:e --config '{…}'
;;;   hy -m doeff_cluster.job_entry task --blob PATH --result PATH --env M:e --versions '{…}'
;;;   hy -m doeff_cluster.job_entry probe --factory M:f --env M:e   (入口の検め — worker が起こす前に撃つ)
;;;
;;; env = handler の組を組み立てる関数の import path。(config ctx) を受けて handler の list(外側が先)を返す。
;;; task は結果(TaskSucceeded / TaskFailed)を必ず --result の file に書いてから 0 で終わる。
;;;
;;; 実行環境(runtime env)の task: worker は env の root の venv で `uv run --no-sync --frozen --project <root の project> hy -m
;;; doeff_cluster.job_entry task …` として起こし、宣言の JSON を DOEFF_RUNTIME_ENV、キーを DOEFF_RUNTIME_ENV_KEY で渡す。この入口は
;;; root の中の doeff-cluster(送り手の版)なので、worker と子の約束の版は runtime_env_model.CHILD-PROTOCOL(worker が準備の確かめで
;;; 読む)。
;;; 0 以外で終わった = 結果を書けなかった(worker はそれを「結果なし」として報告する)。
(import argparse)
(import dataclasses [dataclass])
(import json)
(import os)
(import re)
(import pathlib [Path])
(import sys)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import .service_model [resolve program-arguments settings-left-to-env RECORD-KEY])
(import .remote_model [current-versions version-mismatch version-diffs decode-program encode-outcome
                       TaskSucceeded TaskFailed failed-from VersionMismatch RemoteJobFailed])


(defclass [(dataclass :frozen True)] RunContext []
  "実行先の文脈(worker が環境変数で渡す)。env の組み立てだけが読む。"
  (#^ str coordinator-url)
  (#^ str worker)
  (#^ str revision)
  (#^ str job)
  ;; この process の世代(worker が起こした時に振った名・試行の番号・起こした spec の指紋・割り当ての世代)。
  ;; readiness と計器の報告に載せ、coordinator は今の宣言で今動いている process の報告だけを数える。
  (setv #^ str instance "")
  (setv #^ str attempt "")
  (setv #^ str spec-hash "")
  (setv #^ str placement "")
  ;; 実行環境の宣言(JSON の文字列)とキー。env の task でなければ空。子がさらに task を送る時の既定の env になる。
  (setv #^ str runtime-env "")
  (setv #^ str env-key "")

  (defn #^ dict identity [self]
    "報告に載せる process の世代(coordinator の resource_policy.report-matches が比べる欄)。"
    {"instance" self.instance "attempt" self.attempt "specHash" self.spec-hash
     "placement" (if self.placement (int self.placement) None)}))


(defn #^ RunContext context-from-env []
  (RunContext (os.environ.get "DOEFF_WORKER_COORDINATOR" "")
              (os.environ.get "DOEFF_WORKER_NAME" "")
              (os.environ.get "DOEFF_WORKER_REVISION" "")
              (os.environ.get "DOEFF_WORKER_JOB" "")
              :instance (os.environ.get "DOEFF_WORKER_INSTANCE" "")
              :attempt (os.environ.get "DOEFF_WORKER_ATTEMPT" "")
              :spec-hash (os.environ.get "DOEFF_WORKER_SPEC_HASH" "")
              :placement (os.environ.get "DOEFF_WORKER_PLACEMENT" "")
              :runtime-env (os.environ.get "DOEFF_RUNTIME_ENV" "")
              :env-key (os.environ.get "DOEFF_RUNTIME_ENV_KEY" "")))


(defn #^ list env-handlers [#^ str env #^ dict config #^ RunContext ctx]
  (setv build (resolve env))
  (setv handlers (build config ctx))
  (when (not (isinstance handlers list))
    (raise (TypeError (.format "env {} は handler の list を返す必要がある: {}" env (type handlers)))))
  handlers)


(defn #^ list recording-layer [record #^ dict config #^ RunContext ctx args]
  "設定の record 欄(effect の記録 — record_handlers.hy)が在れば、env の一番内側に足す記録係を 1 つ返す。無ければ空。"
  (when (not (isinstance record dict)) (return []))
  (import doeff_cluster.record_handlers [recording-handler])
  ;; code のキー: env の task は env のキー(DOEFF_RUNTIME_ENV_KEY — cwd は空の作業 dir で、名は何も言わない)。
  ;; 版の組: worker は木を「<base>~<重ねる commit>」の名の dir に作って cwd にする(DOEFF_WORKER_REVISION は宣言の revision だけ)。
  (setv here (. (Path.cwd) name))
  (setv code-key (cond
                   ctx.env-key (+ "env-" ctx.env-key)
                   (re.fullmatch r"[0-9a-f]{40}(~[0-9a-f]{40})?" here) here
                   True ctx.revision))
  (setv #(base _ overlay) (.partition code-key "~"))
  [(recording-handler record ctx.job
                      {"worker" ctx.worker "pid" (os.getpid) "instance" ctx.instance "attempt" ctx.attempt
                       "specHash" ctx.spec-hash "placement" ctx.placement "codeKey" code-key
                       "base" base "revision" (or overlay base)
                       "factory" args.factory "env" args.env "config" config})])

(defn run-service [args]
  (setv config (json.loads args.config))
  ;; record 欄は業務の Program の引数ではなく、組み立て側(記録係を足すか)の設定。本体の引数は program-arguments が本体の引数の名の
  ;; 設定だけで作る。env には record を除いた全体を渡す(env だけが読む設定を含む)。
  (setv record (.pop config RECORD-KEY None))
  (setv ctx (context-from-env))
  (setv factory (resolve args.factory))
  (setv program (factory #** (program-arguments factory config)))
  (print (.format "service: {} を起動({}・commit {}・本体へ渡さない設定 {})" ctx.job args.factory ctx.revision
                  (settings-left-to-env factory config))
         :file sys.stderr :flush True)
  (setv handlers (+ (env-handlers args.env config ctx) (recording-layer record config ctx args)))
  (setv result (run (scheduled (with-handlers handlers program))))
  (print (.format "service: {} が終わった: {!r}" ctx.job result) :file sys.stderr :flush True))


(defn #^ (| TaskSucceeded TaskFailed) task-outcome [args #^ RunContext ctx]
  ;; 版 → 復元 → 実行の順に、どこで断ったか分かる失敗を返す。
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
    (setv handlers (env-handlers args.env {} ctx))
    (TaskSucceeded (run (scheduled (with-handlers handlers program))))
    (except [error Exception]
      (failed-from error))))


(defn #^ (| str None) probe-problem [#^ str factory #^ str env]
  "入口の検め(2026-09-25): factory と env を service_model.resolve で解けるか(import と属性の在否だけ・呼ばない)。
   解ければ None、解けなければ理由の 1 行。この module 自身の import(doeff 等の実行環境)は、この関数に届く前に試されている。"
  (for [#(label path) [#("factory" factory) #("env" env)]]
    (try
      (resolve path)
      (except [error Exception]
        (return (.format "{} {} を読み込めない: {}: {}" label path (. (type error) __name__)
                         (.join " " (.split (str error))))))))
  None)


(defn #^ None run-probe [#^ argparse.Namespace args]
  (setv problem (probe-problem args.factory args.env))
  (when (is-not problem None)
    (print problem :file sys.stderr :flush True)
    (sys.exit 1))
  (print (.format "probe: {} と {} を読み込めた" args.factory args.env) :file sys.stderr :flush True))


(defn run-task [args]
  (setv ctx (context-from-env))
  (setv outcome (task-outcome args ctx))
  (setv tmp (+ args.result ".tmp"))
  (with [f (open tmp "w" :encoding "utf-8")]
    (.write f (encode-outcome outcome)))
  (os.replace tmp args.result)
  (print (.format "task: {} → {}" ctx.job (. (type outcome) __name__)) :file sys.stderr :flush True))


(defn main []
  (setv parser (argparse.ArgumentParser :description "doeff worker の子 process の入口"))
  (setv sub (.add-subparsers parser :dest "kind" :required True))
  (setv service (.add-parser sub "service"))
  (.add-argument service "--factory" :required True)
  (.add-argument service "--env" :required True)
  (.add-argument service "--config" :default "{}")
  (setv task (.add-parser sub "task"))
  (.add-argument task "--blob" :required True)
  (.add-argument task "--result" :required True)
  (.add-argument task "--env" :required True)
  (.add-argument task "--versions" :default "{}")
  (setv probe (.add-parser sub "probe"))
  (.add-argument probe "--factory" :required True)
  (.add-argument probe "--env" :required True)
  (setv args (.parse-args parser))
  (cond
    (= args.kind "service") (run-service args)
    (= args.kind "probe") (run-probe args)
    True (run-task args)))


(when (= __name__ "__main__")
  (main))
