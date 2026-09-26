;;; 盲検 A の反例の再現: 同じ宣言から (a) 1 process の main と (b) 実行先(job_entry service)が同じ引数を作るか。
;;; 使い方: cd <この dir>; hy repro_a_record.hy
;;; 宣言は service の呼び出しの時に組む — 宣言の時点の拒否(修正後)も (a)(b) の失敗も、同じ形で 1 行ずつ印字する。
(import json subprocess sys)
(import doeff [run])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.service_model [service System system-main system-declaration])
(import cluster_a_fixture [ledger-program tally-program RECORD])

(defn outcome [thunk]
  (try
    (+ "OK " (repr (thunk)))
    (except [e Exception]
      (+ "失敗 " (. (type e) __name__) ": " (get (.splitlines (str e)) 0)))))

(defn in-process [system]
  (run (scheduled (system-main system))))

(defn job-entry [decl]
  ;; (b) 実行先: coordinator が worker に起こさせる命令と同じ形(factory・env・config の JSON)。
  (setv run-spec (get decl "run"))
  (setv done (subprocess.run [sys.executable "-m" "hy" "-m" "doeff_cluster.job_entry" "service"
                              "--factory" (get run-spec "factory") "--env" (get run-spec "env")
                              "--config" (json.dumps (get run-spec "config"))]
                             :capture-output True :text True :timeout 120))
  (setv tail (lfor line (.splitlines done.stderr) :if (.strip line) line))
  (.format "exit={} {}" done.returncode (if tail (get tail -1) "")))

(for [#(label program config) [#("ledger(宣言に record・本体は知らない)" ledger-program {"n" 1 "record" RECORD})
                               #("tally(本体の引数に record)" tally-program {"record" True "n" 1})]]
  (print "==" label)
  (setv made (outcome (fn [] (service (get (.split label "(") 0) program :env "cluster_a_fixture:plain_env" :config config))))
  (print "  宣言:" made)
  (when (.startswith made "OK")
    (setv svc (service (get (.split label "(") 0) program :env "cluster_a_fixture:plain_env" :config config))
    (setv system (System "s" #(svc)))
    (print "  (a) system-main:" (outcome (fn [] (in-process system))))
    (print "  (b) job_entry service:" (job-entry (get (system-declaration system "rev1") 0)))))
