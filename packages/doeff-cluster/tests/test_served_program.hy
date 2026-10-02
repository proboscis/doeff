;;; 通しの検: 宣言 → 置き場 → worker → 子 process(ADR-DOE-CLUSTER-001 R1・R2・改訂 1 の F)。
;;;
;;; 本物の coordinator の process(conftest の served_coordinator)に、見本の系(tests.fixtures.services の lab)の宣言を
;;; declare.apply-declaration で置き(先に Program を PUT /programs/<sha>・次に Service を POST)、本物の coordinator への口 の heartbeat で
;;; 返事の job に置き場のキーが載り、accept-programs が cache の file を書き、その file を job_entry の service 入口の子 process で
;;; 走らせる。Program は自分で土台の reader と scheduler を並べるので、入口は何も足さずに tally の値 102(base 100 + step 2)を返す。
(require doeff-hy.macros [deftest defk <- val var])
(import json)
(import os)
(import subprocess)
(import sys)
(import time)
(import pathlib [Path])
(import httpx)
(import doeff [with-handlers])
(import doeff_core_effects.handlers [await-handler slog-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_cluster.shared.entry.service_build [system-declaration])
(import doeff_cluster.shared.intent.service_model [Declaration])
(import doeff_cluster.shared.entry.declare [apply-declaration])
(import tests.link_rig [LinkRig])
(import doeff_cluster.worker.core.launch [program-file])
(import doeff_cluster.foundation.process_versions [process-versions])
(import doeff_cluster.worker.intent.worker_model [DesiredJobs] doeff_cluster.shared.intent.job_model [JobSpec])
(import tests.fixtures.services [lab])
(import tests.fixtures.envs [plain-foundation])

(val PACKAGE-ROOT (. (Path __file__) (resolve) parent parent))
;; 共有の coordinator の上で他の検の worker に置かれないよう、この検だけの能力を要る(名も他の検と重ならない)。
(val NEED "served-program-e2e")
(val JOB "served-program-tally")
(val WORKER "served-program-worker")


(defk declaration-for-this-test []
  {:pre [] :post [(: % Declaration)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "見本の系 lab の宣言(Program を詰めた本物の Declaration)を、名と needs だけこの検の物に替えた宣言。"
  (val declared (system-declaration (lab plain-foundation) "r-served" :versions (! (process-versions os.environ))))
  (Declaration :rows (lfor row declared.rows (| row {"name" JOB "needs" [NEED]})) :programs declared.programs))


(defk desired-job [link]
  {:pre [(: link LinkRig)] :post [(: % JobSpec)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "heartbeat を送り、返事にこの検の job が載るまで待つ(載った JobSpec・30 秒で断念)。"
  (val deadline (+ (time.monotonic) 30))
  (var found None)
  (while (and (is found None) (< (time.monotonic) deadline))
    (val desired (.poll link))
    (assert (isinstance desired DesiredJobs) desired)
    (:= found (next (gfor j desired.jobs :if (= j.name JOB) j) None))
    (when (is found None) (time.sleep 0.2)))
  (assert (is-not found None) "30 秒のうちに返事に job が載らなかった")
  found)


(deftest test-a-declared-program-reaches-the-worker-and-runs-in-job-entry [served-coordinator tmp-path]
  ;; fixture の答えは coordinator の base URL の文字列(型の無い fixture の値を、ここで str に絞ってから URL を組む)。
  (assert (isinstance served-coordinator str) served-coordinator)
  (<- declaration Declaration (declaration-for-this-test))
  (val sha (get (get declaration.rows 0) "run" "program"))
  ;; declare: 置き場へ Program を置いてから Service を書く(書きの HTTP は declare の入口と同じ汎用の答え手が本物の coordinator へ送る)。
  (<- applied bool (with-handlers [(await-handler) (http-production-handler) slog-handler]
                     (apply-declaration served-coordinator declaration "c-served-test")))
  (assert applied "宣言の書きのどれかが 300 以上を返した")
  (val stored (httpx.get (+ served-coordinator "/programs/" sha) :timeout 10))
  (assert (= stored.status-code 200) stored.text)
  (assert (= (get (.json stored) "blob") (get declaration.programs sha)))
  ;; worker: 本物の coordinator への口 の heartbeat → 返事の job に置き場のキー → cache の file。
  (val link (LinkRig served-coordinator WORKER #(NEED) 10 60000
                             :task-dir (str (/ tmp-path "state" "tasks")) :versions (! (process-versions os.environ))))
  (try
    (do
      (<- spec JobSpec (desired-job link))
      (assert (= spec.program sha) spec)
      (assert (= spec.environ #(#("TALLY_BASE" "1"))) spec.environ)
      (assert (= (get spec.args 0) "service") spec.args)
      (val cached (program-file (.program-dir link) sha))
      (assert (.exists cached) (list (.iterdir (.program-dir link))))
      (assert (= (get (json.loads (.read-text cached :encoding "utf-8")) "blob") (get declaration.programs sha)))
      ;; 子 process: 宣言の引数(identity の指紋)と cache の file で job_entry の service 入口を起こす。
      (val done (subprocess.run [sys.executable "-m" "hy" "-m" (. spec entry) #* spec.args "--program" (str cached)]
                                :cwd (str PACKAGE-ROOT) :capture-output True :text True :timeout 120
                                :env (| (dict os.environ) {"PYTHONPATH" (str PACKAGE-ROOT)} (dict spec.environ))))
      (assert (= done.returncode 0) done.stderr)
      (assert (in "が終わった: 102" done.stderr) done.stderr))
    (finally
      ;; 共有の coordinator から、この検の Service を消す(他の検に残さない)。
      (httpx.delete (+ served-coordinator "/resources/Service/" JOB) :params {"force" "true"}
                    :headers {"X-Actor" "c-served-test"} :timeout 10))))
