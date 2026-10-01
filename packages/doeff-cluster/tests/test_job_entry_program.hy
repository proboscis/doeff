;; 子 process の入口 job_entry(ADR-DOE-CLUSTER-001 R1・R2・改訂 1 の F — 2026-09-27)。本物の入口を subprocess で起こす。
;;
;; - service は Program の file(worker が /programs/<sha> から取った JSON {"blob" "versions"})を解いて (run program) するだけ。既定の
;;   handler を 1 つも足さない: 自分で handler を並べた Program は走り、並べない Program の effect は答えが無く 0 以外で終わる。
;; - 版の食い違いは最初の段で確かめる: この検の process(= declare と同じ venv)で詰めた Program を子が解ける。versions を書き換えた file・
;;   file の無い時は解かずに理由つきで止まる(service は 3・probe は 1)。
;; - 旧い引数(--factory・--env・--config — 計画 2.8 の入口 13・task の --blob・--versions)は argparse の error。再生の道具(入口 14)も
;;   旧い --config を argparse の error で理由つきで断り、版の合わない Program の file は解かずに 3 で止まる(記録 → 再生の通しの検は
;;   test_boundary_recorder.hy)。task の入口の検(同じ Program の file を読む)は test_remote.hy。
(require doeff-hy.macros [deftest defk <- val])
(import json)
(import os)
(import subprocess)
(import sys)
(import pathlib [Path])
(import doeff [Program])
(import doeff_cluster.shared.protocol.program_codec [encode-program])
(import doeff_cluster.foundation.process_versions [current-versions])
(import tests.fixtures.envs [plain-foundation])
(import tests.fixtures.services [tally-program bare-program self-contained-program])

(val ROOT (str (. (Path __file__) (resolve) parent parent)))   ; この package の根(見本の module は tests.* の名)
(val HY (str (/ (. (Path sys.executable) parent) "hy")))


(defk program-file [path program versions]
  {:pre [(: path Path) (: program Program) (: versions dict)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker が /programs/<sha> から取って置くのと同じ形の file を書き、その path を返す。"
  (.write-text path (json.dumps {"blob" (encode-program program) "versions" versions}) :encoding "utf-8")
  (str path))


(defk entry [module #* args]
  {:pre [(: module str) (: args tuple)] :post [(: % subprocess.CompletedProcess)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "hy -m <module> <args…> を子の文脈(DOEFF_WORKER_JOB)付きで起こす。"
  (val words (lfor a args :if (isinstance a str) a))
  (assert (= (len words) (len args)) #("子 process の引数は文字列だけ" args))
  (subprocess.run [HY "-m" module #* words]
                  :cwd ROOT :env (| (dict os.environ) {"PYTHONPATH" ROOT "DOEFF_WORKER_JOB" "entry-probe"})
                  :capture-output True :text True :timeout 120))


(deftest test-a-service-program-with-its-own-handlers-runs-and-reports-its-result [tmp-path]
  ;; この process で詰めた Program(土台の reader と scheduler を自分で並べる)を子が解いて走らせる。
  (<- path (program-file (/ tmp-path "p.json") (tally-program plain-foundation 2) (current-versions)))
  (<- done (entry "doeff_cluster.job_entry" "service" "--identity" (* "0" 16) "--program" path))
  (assert (= done.returncode 0) done.stderr)
  (assert (in "が終わった: 102" done.stderr) done.stderr)
  ;; 本体の中で handler を作る Program も同じ。
  (<- inner (program-file (/ tmp-path "q.json") (self-contained-program 5) (current-versions)))
  (<- again (entry "doeff_cluster.job_entry" "service" "--identity" (* "0" 16) "--program" inner))
  (assert (= again.returncode 0) again.stderr)
  (assert (in "が終わった: 15" again.stderr) again.stderr))


(deftest test-the-entry-adds-no-handler-so-an-unanswered-effect-ends-the-service [tmp-path]
  ;; 反例: handler を並べない Program の Ask "base" に答える物は子の中に無い(入口が既定の handler を足していない)。
  (<- path (program-file (/ tmp-path "p.json") (bare-program 1) (current-versions)))
  (<- done (entry "doeff_cluster.job_entry" "service" "--identity" (* "0" 16) "--program" path))
  (assert (!= done.returncode 0) done.stderr)
  (assert (in "Ask" done.stderr) done.stderr)
  (assert (not-in "が終わった" done.stderr) done.stderr))


(deftest test-a-program-file-from-another-version-is-refused-before-decoding [tmp-path]
  (<- path (program-file (/ tmp-path "p.json") (tally-program plain-foundation 2) (| (current-versions) {"cloudpickle" "0.0.1"})))
  (<- done (entry "doeff_cluster.job_entry" "service" "--identity" (* "0" 16) "--program" path))
  (assert (= done.returncode 3) done.stderr)
  (assert (in "版が違うので Program を解かない" done.stderr) done.stderr)
  (assert (in "cloudpickle: 送り手 0.0.1" done.stderr) done.stderr)
  ;; worker が取れていない(file が無い)時も解かずに止まる。
  (<- missing (entry "doeff_cluster.job_entry" "service" "--identity" (* "0" 16) "--program" (str (/ tmp-path "absent.json"))))
  (assert (= missing.returncode 3) missing.stderr)
  (assert (in "Program の file" missing.stderr) missing.stderr))


(deftest test-the-probe-decodes-the-program-without-running-it [tmp-path]
  ;; probe は版と復元だけを確かめる(走らせない — handler の無い Program でも通る)。
  (<- path (program-file (/ tmp-path "p.json") (bare-program 1) (current-versions)))
  (<- ok (entry "doeff_cluster.job_entry" "probe" "--program" path))
  (assert (= ok.returncode 0) ok.stderr)
  (assert (in "を解けた" ok.stderr) ok.stderr)
  (<- other (program-file (/ tmp-path "q.json") (bare-program 1) (| (current-versions) {"doeff" "0.0.0"})))
  (<- refused (entry "doeff_cluster.job_entry" "probe" "--program" other))
  (assert (= refused.returncode 1) refused.stderr)
  (assert (in "版が違う" refused.stderr) refused.stderr)
  (<- missing (entry "doeff_cluster.job_entry" "probe" "--program" (str (/ tmp-path "absent.json"))))
  (assert (= missing.returncode 1) missing.stderr))


(deftest test-old-entry-arguments-are-refused-by-argparse
  ;; 計画 2.8 の入口 13: 旧い引数(関数の参照 + handler の組の import path + 設定)は入口に無い。
  (<- old (entry "doeff_cluster.job_entry" "service" "--factory" "m:f" "--env" "m:e" "--config" "{}"))
  (assert (= old.returncode 2) old.stderr)
  (assert (in "--identity" old.stderr) old.stderr)
  (<- extra (entry "doeff_cluster.job_entry" "service" "--identity" (* "0" 16) "--program" "p.json" "--env" "m:e"))
  (assert (= extra.returncode 2) extra.stderr)
  (assert (in "unrecognized arguments: --env m:e" extra.stderr) extra.stderr)
  (<- task (entry "doeff_cluster.job_entry" "task" "--program" "p.json" "--result" "r" "--env" "m:e"))
  (assert (= task.returncode 2) task.stderr)
  (assert (in "unrecognized arguments: --env m:e" task.stderr) task.stderr)
  ;; task の旧い入口(詰めた Program を --blob の file で・版を --versions で渡す形)も無い — service と同じ --program の file 1 つ(R3b)。
  (<- blob (entry "doeff_cluster.job_entry" "task" "--program" "p.json" "--result" "r" "--blob" "b" "--versions" "{}"))
  (assert (= blob.returncode 2) blob.stderr)
  (assert (in "unrecognized arguments: --blob b --versions {}" blob.stderr) blob.stderr)
  (<- bare (entry "doeff_cluster.job_entry" "task" "--blob" "b" "--result" "r"))
  (assert (= bare.returncode 2) bare.stderr)
  (assert (in "--program" bare.stderr) bare.stderr))


(deftest test-the-replay-entry-refuses-the-old-config-and-a-program-from-another-version [tmp-path]
  ;; 計画 2.8 の入口 14: 再生の道具は記録した Program をそのまま走らせる。旧い形(--config で設定を外から渡して Program を作り直す)は
  ;; argparse の error で理由つきで断る(必須の引数を揃えても断る)。
  (val recording (/ tmp-path "recording.jsonl"))
  (.write-text recording (+ (json.dumps {"k" "run" "format" 2 "startedMs" 0}) "\n") :encoding "utf-8")
  (val out (/ tmp-path "report.json"))
  (<- path (program-file (/ tmp-path "p.json") (bare-program 1) (current-versions)))
  (<- old (entry "doeff_cluster.shared.entry.replay_main" "--recording" (str recording) "--program" path "--out" (str out) "--config" "{}"))
  (assert (= old.returncode 2) old.stderr)
  (assert (in "--config は受け付けない" old.stderr) old.stderr)
  ;; 版の合わない Program の file(旧い記録・別の版の記録)は解かずに 3 で止まり、報告を書かない(改訂 1 の O)。
  (<- other (program-file (/ tmp-path "q.json") (bare-program 1) (| (current-versions) {"cloudpickle" "0.0.1"})))
  (<- refused (entry "doeff_cluster.shared.entry.replay_main" "--recording" (str recording) "--program" other "--out" (str out)))
  (assert (= refused.returncode 3) refused.stderr)
  (assert (in "版が違うので Program を解かない" refused.stderr) refused.stderr)
  (assert (in "cloudpickle: 送り手 0.0.1" refused.stderr) refused.stderr)
  (assert (not (.exists out)) "版の合わない時は報告を書かない"))
