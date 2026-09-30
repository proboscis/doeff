;;; worker が起動する子 process の入口(service と task)。その commit のコードを展開した木(か実行環境の root)の中で動く。
;;;
;;;   hy -m doeff_cluster.job_entry service --identity <指紋> --program PATH
;;;   hy -m doeff_cluster.job_entry task --program PATH --result PATH
;;;   hy -m doeff_cluster.job_entry probe --program PATH   (入口の検め — 版と復元だけを確かめて走らせない)
;;;
;;; job が受け取るのは Program の値 1 つだけ(ADR-DOE-CLUSTER-001 R1・R3)。この入口は既定の handler を 1 つも足さない(R2):
;;; 版を検め、詰めた Program を解き、(run program) するだけ。scheduler・時計・記録係・業務の handler は Program が自分の
;;; with-handlers で並べる。答えの無い effect はその場で上がり、process は 0 以外で終わる(worker が理由つきで起こし直す)。
;;; 宿(この入口と worker)が Program に提供するのは host_contract.HOST-CONTRACT の 3 つだけ(run-context・environ・Program の path)。
;;; 手元の sim-cluster(local.hy)の偽の宿は、この入口と同じく何も足さず、加えて柵(host_contract.SIM-PASSABLE の表の外の effect を
;;; 本番と同じ未処理の例外にする)で Program を包む — 本番の子で答えの無い effect が sim だけで通ることを防ぐ。
;;;
;;; Program の file = worker が coordinator の /programs/<sha> から取った JSON {"blob" 詰めた文字列 "versions" 詰めた送り手の版}。
;;; service と task は同じ file を同じ read-program で読む(運び方を分けない — R3b)。--identity は service の宣言の同一性の指紋
;;; (spec-hash の材料 — 入口では読まない)。
;;; task は結果(TaskSucceeded / TaskFailed)を必ず --result の file に書いてから 0 で終わる。0 以外で終わった = 結果を書けなかった。
;;; file に書いた後、終わる前に結果を coordinator の POST /tasks/<id>/result へ直に届ける(report_client.deliver-task-result — #1387:
;;; worker の次の heartbeat だけが運ぶ形では、exit 0 から heartbeat までに worker が死ぬと結果が届かず task が 2 度走った)。
;;; 届かなければ今までどおり worker が file を読んで heartbeat で運ぶ(coordinator は 2 度目の結果を冪等に受ける)。
;;; 版の違い・file の欠け・解けない Program は、task では TaskFailed(VersionMismatch / RemoteJobFailed)として結果の file に書き、
;;; service と probe では理由の 1 行を出して止まる。
;;;
;;; 実行環境(runtime env)の job: worker は env の root の venv で `uv run --no-sync --frozen --project <root の project> hy -m
;;; doeff_cluster.job_entry …` として起こし、宣言の JSON を DOEFF_RUNTIME_ENV、キーを DOEFF_RUNTIME_ENV_KEY で渡す。この入口は
;;; root の中の doeff-cluster(送り手の版)なので、worker と子の約束の版は runtime_env_model.CHILD-PROTOCOL。
(require doeff-hy.macros [deff])
(import argparse)
(import json)
(import os)
(import pathlib [Path])
(import sys)
(import doeff [run])
(import .remote_model [version-mismatch version-diffs decode-program encode-outcome
                       TaskSucceeded TaskFailed failed-from VersionMismatch RemoteJobFailed])
(import .process_versions [current-versions])
;; 子の文脈の型と読みは入口でない module に 1 つだけ置く(job_context の頭の註 — ここは import して、今の名を引けるように残す)。
(import .job_context [RunContext context-from-env runtime-env-of-context])
(import .report_client [deliver-task-result])


(deff program-row [#^ str path]  ; defk にできない: process の入口(Program の外)が file を読む
  {:pre [(: path str)] :post [(: % (| dict RemoteJobFailed))] :tags {:context "doeff-cluster" :role "entry"}}
  "Program の file の中身 {\"blob\" \"versions\"} を読むため(読めない・形の違う file は RemoteJobFailed — 理由の文を持つ)。"
  (let [row (try
              (json.loads (.read-text (Path path) :encoding "utf-8"))
              (except [error [OSError ValueError]]
                (RemoteJobFailed (.format "Program の file {} を読めない(worker が /programs から取れていない): {}" path error))))]
    (cond
      (isinstance row RemoteJobFailed) row
      (not (and (isinstance row dict) (isinstance (.get row "blob") str) (isinstance (.get row "versions" {}) dict)))
        (RemoteJobFailed (.format "Program の file {} の形が違う({{\"blob\" \"versions\"}} ではない)" path))
      True row)))


(deff decoded-program [#^ str blob]  ; defk にできない: process の入口(Program の外)が詰めた Program を解く
  {:pre [(: blob str)] :post [(: % tuple) (= (len %) 2)] :tags {:context "doeff-cluster" :role "entry"}}
  "詰めた Program を解くため: #(Program None) か、解けない時は #(None RemoteJobFailed)。"
  (try
    #((decode-program blob) None)
    (except [error Exception]
      #(None (RemoteJobFailed (.format "Program を解けない: {}: {}" (. (type error) __name__) error))))))


(deff read-program [#^ str path #^ str env-key]  ; defk にできない: process の入口(Program の外)が file を読む
  {:pre [(: path str) (: env-key str)] :post [(: % tuple) (= (len %) 2)] :tags {:context "doeff-cluster" :role "entry"}}
  "Program の file → #(Program None) か #(None 断り)。service・task・probe が同じ読みを使うため(入口の形を分けない — R3・R3b)。
   断り = VersionMismatch(file の版がこの process の版と違う — 食い違った欄と env のキーを持ち、解かない)か RemoteJobFailed
   (file が無い・形が違う・解けない)。env-key = 子の実行環境のキー(env の job でなければ空)。"
  (let [row (program-row path)]
    (if (isinstance row RemoteJobFailed)
        #(None row)
        (let [expected (.get row "versions" {})
              actual (current-versions)
              diffs (version-diffs expected actual)]
          (if diffs
              #(None (VersionMismatch (+ "版が違うので Program を解かない: " (version-mismatch expected actual)
                                         (if env-key (.format "(env {})" env-key) ""))
                                      diffs env-key))
              (decoded-program (get row "blob")))))))


(defn run-service [args]  ; defk にできない: process の入口(Program の外)
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
   :tags {:context "doeff-cluster" :role "entry"}}
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


(defn run-task [args]  ; defk にできない: process の入口(Program の外)
  "task の入口: 結果を必ず file に書き、終わる前に coordinator へ直に届けてから 0 で終わる(届かなければ worker の heartbeat が file の
   結果を運ぶ)。"
  (setv ctx (context-from-env))
  (setv outcome (task-outcome args.program ctx))
  (setv encoded (encode-outcome outcome))
  (setv tmp (+ args.result ".tmp"))
  (with [f (open tmp "w" :encoding "utf-8")]
    (.write f encoded))
  (os.replace tmp args.result)
  (deliver-task-result ctx encoded)
  (print (.format "task: {} → {}" ctx.job (. (type outcome) __name__)) :file sys.stderr :flush True))


(defn main []  ; defk にできない: process の入口
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
