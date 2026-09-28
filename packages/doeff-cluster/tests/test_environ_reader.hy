;; 宣言の :environ の読み(宿の契約の 2 — host_contract.environ-reader)の契約: 値は字面どおりの文字列で、本番の子と sim の子で同じ。
;;
;; 責務:
;;   読みの定義   … environ-reader の 1 つ(名 → 値の置き場を引数に取る)。宣言の名の Ask に値を字面どおり答え、置き場に無い名と文字列で
;;                 ない鍵は外側へ通す。本番の土台は引数なしの (environ-reader)(子の os.environ の上)・sim の宿は子の spec.environ の上に同じ定義を並べる。
;;   本番の子     … job_entry の task 入口の子 process(ProcessHost が組んだ環境)で、JSON の object の値が字面どおり返る。
;;   sim の子     … sim-cluster の task で、同じ Program・同じ :environ が同じ字面を返す。
;;   反例         … 同じ値を env_var_ask(接頭辞なし)で読むと {module.path} の import として解かれ、本番の子でだけ ModuleNotFoundError で
;;                 落ちる(sim の子では環境に無い名を外へ通すので sim の宿が字面どおり返し、食い違いが sim の検では見えない — この
;;                 読み手を足した理由。job API の計画の決定 4 の戻し方「専用の handler を足して置き換える」)。
(require doeff-hy.macros [deftest defk <- val])
(import os)
(import sys)
(import subprocess)
(import pathlib [Path])
(import httpx)
(import pytest)
(import doeff [with-handlers DoExpr])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader env-var-ask])
(import doeff_time [SimClock])
(import doeff_cluster.host_contract [HOST-CONTRACT environ-reader])
(import doeff_cluster.handlers [CoordinatorLink ProcessHost program-file])
(import doeff_cluster.detached [DetachedClient])
(import doeff_cluster.remote_model [RemoteJob TaskSucceeded TaskFailed encode-program decode-outcome current-versions])
(import doeff_cluster.service_model [system-of])
(import doeff_cluster.worker_model [DesiredJobs])
(import doeff_cluster.local [sim-cluster SimWorker])
(import tests.detached_rig [MemoryCoordinator RIG-PROVIDES])
(import tests.fixtures.entry_programs [environ-read environ-resolved-read])

;; 見本の設定の名(検の process の環境変数に無い名)と、JSON の object の値({ で始まり } で終わる — env_var_ask が import として解く形)。
(val NAME "ENVIRON_READER_POLICY")
(val POLICY "{\"a\": 1}")
(val LOCAL (frozenset RIG-PROVIDES))
(val ROOT (. (Path (os.path.abspath __file__)) parent parent))
(val HY (str (/ (. (Path sys.executable) parent) "hy")))


;; --- 読みの定義 ---------------------------------------------------------------------------------------------------

(deftest test-the-environ-reader-answers-the-literal-value-and-passes-other-names
  ;; 置き場の名は字面どおり(JSON を parse しない・{…} を解かない)。置き場に無い名・文字列でない鍵は外側(ここでは reader)が答える。
  (<- literal str (with-handlers [(reader {"OTHER" "outer"}) (environ-reader {NAME POLICY})] (Ask NAME)))
  (assert (= literal POLICY) literal)
  (<- other str (with-handlers [(reader {"OTHER" "outer"}) (environ-reader {NAME POLICY})] (Ask "OTHER")))
  (assert (= other "outer") other)
  (<- typed str (with-handlers [(reader {int "by-type"}) (environ-reader {NAME POLICY})] (Ask int)))
  (assert (= typed "by-type") typed))


(deftest test-the-production-reader-returns-the-json-object-literally-and-env-var-ask-resolves-it [monkeypatch]
  ;; 本番の土台の (environ-reader)(引数なし)は子の os.environ を字面どおり読む。反例: 同じ値を env_var_ask(接頭辞なし)は {module.path} の import と
  ;; して解き、ModuleNotFoundError で落ちる。
  (.setenv monkeypatch NAME POLICY)
  (<- literal str (with-handlers [(environ-reader)] (Ask NAME)))
  (assert (= literal POLICY) literal)
  ;; 文字列でない鍵(型を鍵にする Ask)は os.environ に問わずに外側へ通す(os.environ は文字列でない鍵の問いで TypeError を投げる)。
  (<- typed str (with-handlers [(reader {int "by-type"}) (environ-reader)] (Ask int)))
  (assert (= typed "by-type") typed)
  (with [raised (pytest.raises ModuleNotFoundError)]
    (<- (with-handlers [(env-var-ask :prefix "")] (Ask NAME))))
  (assert (in "\"a\"" (str raised.value)) (str raised.value)))


;; --- 本番の子: job_entry の task 入口の子 process -----------------------------------------------------------------

(defk production-child-outcome [tmp-path program key]
  {:pre [(: tmp-path Path) (: program DoExpr) (: key str)] :post [(: % (| TaskSucceeded TaskFailed))]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "本番の形の通しで program を切り離した task として 1 回走らせ、その結末を返すため: DetachedClient が :environ {NAME POLICY} つきで
   送り、本物の coordinator の判断(MemoryCoordinator)が返事に載せ、本物の CoordinatorLink が Program を cache へ取り、ProcessHost が
   組んだ子の環境で job_entry の task 入口の子 process が走る。"
  (val base (/ tmp-path key))
  (val coordinator (MemoryCoordinator (SimClock)))
  (val transport (httpx.MockTransport coordinator.handle))
  (val link (CoordinatorLink "http://coordinator" "w1" RIG-PROVIDES 10 60000 :task-dir (str (/ base "state" "tasks"))
                             :versions (current-versions) :transport transport))
  (.poll link)
  (val client (DetachedClient "http://coordinator" "r" :transport transport))
  (val submitted (.submit client key (encode-program program) LOCAL "env" 60.0 600.0 {NAME POLICY}))
  (assert (get submitted "created") submitted)
  (val desired (.poll link))
  (assert (isinstance desired DesiredJobs) desired)
  (val spec (next (gfor j desired.jobs :if (.startswith j.name "task/") j)))
  (assert (= spec.environ #(#(NAME POLICY))) spec)
  (.mkdir base :parents True :exist-ok True)
  (val launched (.launch (ProcessHost (str (/ base "state" "logs")) "hy") spec (str base) "1-1" 1))
  (val env (| (get launched 2) {"PYTHONPATH" (str ROOT)}))
  (assert (not-in NAME os.environ))
  (val done (subprocess.run [HY "-m" spec.entry #* spec.args "--program" (str (program-file link.program-dir spec.program))]
                            :cwd (str ROOT) :env env :capture-output True :text True :timeout 120))
  (assert (= done.returncode 0) done.stderr)
  (decode-outcome (.read-text (Path (get spec.args 2)) :encoding "ascii")))


(deftest test-a-production-child-reads-a-json-object-environ-literally-and-env-var-ask-fails-there [tmp-path]
  ;; 本番の子で、JSON の object を置いた :environ を (environ-reader)(本番の土台の読み)で読むと字面どおり返る。反例: 同じ子で env_var_ask
  ;; で読むと {module.path} の import として解かれ、task は ModuleNotFoundError で失敗する(本番の宿で起動のたびに落ちた形)。
  (<- literal (production-child-outcome tmp-path (environ-read NAME) "job-literal"))
  (assert (= literal (TaskSucceeded POLICY)) literal)
  (<- resolved (production-child-outcome tmp-path (environ-resolved-read NAME) "job-resolved"))
  (assert (isinstance resolved TaskFailed) resolved)
  (assert (= resolved.kind "ModuleNotFoundError") resolved))


;; --- sim の子: 同じ Program・同じ :environ を sim-cluster の task で ------------------------------------------------

(val NO-JOBS (system-of "environ-reader-scenarios" #()))


(defk sim-reads []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 本番の子と同じ 2 つの Program を、同じ :environ で sim の task として走らせ、答えを返すため。"
  (<- literal str (RemoteJob (environ-read NAME) :needs LOCAL :environ {NAME POLICY}))
  (<- resolved str (RemoteJob (environ-resolved-read NAME) :needs LOCAL :environ {NAME POLICY}))
  #(literal resolved))


(deftest test-a-sim-child-reads-the-same-json-object-literally-through-the-same-reader
  ;; sim の宿は本番の土台と同じ読みの定義(environ-reader)を子の spec.environ の上に並べるので、(environ-reader) で読む Program は本番の子と
  ;; 同じ字面を返す。反例の見本(env_var_ask)も sim では字面どおり返る — 環境に無い名を外へ通し sim の宿が答えるため。本番の子でだけ
  ;; 落ちる食い違いは sim の検では見えない(だから本番の土台は env_var_ask ではなく (environ-reader) を並べる)。
  (<- answer tuple (sim-cluster NO-JOBS (sim-reads) :workers #((SimWorker :name "w1" :provides LOCAL))))
  (assert (= answer #(POLICY POLICY)) answer))


(deftest test-the-host-contract-keys-are-not-environ-names
  ;; 宿の契約の Ask の鍵(run-context と Program の path)は environ の名の形([A-Z][A-Z0-9_]*)に当たらない — environ-reader と
  ;; 宿の答え(host-reader・sim の host-answers)が同じ Ask を取り合わない。
  (for [key #(HOST-CONTRACT.run-context-key HOST-CONTRACT.program-key)]
    (assert (not (.isupper key)) key)
    (assert (in "." key) key)))
