;; 系の宣言(ADR-DOE-CLUSTER-001 — job は Program の値 1 つ・handler は Program の中で並べる・宣言は defsystem で書く)。
;;
;; - defsystem の関数に土台を渡すと System の値になり、job ごとに Program の値と、それを作った呼び出しの形(CallShape)を持つ。
;; - 宣言の行(system-declaration の rows)は詰めた Program の置き場のキー(sha)・identity(関数の参照と引数の正規 JSON)・版・
;;   describe・environ を運び、詰めた中身は programs に別に出る(改訂 1 の A・F)。
;; - 同一性(spec-hash)は identity・版・environ から作り、詰めた中身は比べない(cloudpickle の出力は揺れうる — 改訂 1 の A)。
;; - 旧い宣言の形は受け付けない(計画 2.8 の入口 1・3・4 — 構成子の TypeError・旧い関数の不在・declare の CLI の error)。
;; - declare は宣言の前に、系の関数の checkout が汚れておらず HEAD = --revision であることと、土台の :needs ⊆ job の :needs を
;;   検め、外れれば理由つきの終了 2(計画 2.2 の E・9 節の P)。
;; - 宣言した Program を実行先の入口(job_entry service)がそのまま走らせる(handler を足さない — Program が自分で並べる)。
(require doeff-hy.macros [defk deftest <- val])
(import collections.abc [Callable])
(import hashlib)
(import json)
(import os)
(import subprocess)
(import sys)
(import pathlib [Path])
(import pytest)
(import doeff_core_effects.handlers [reader])
(import doeff_cluster.service_model :as service-model)
(import doeff_cluster.service_model [Job System CallShape Declaration job system-of system-declaration identity-of
                                     describe-identity job-named])
(import doeff_cluster.cluster_policy [job-from-json identity-hash])
(import doeff_cluster.host_contract [host-reader])
(import doeff_cluster.remote_model [encode-program])
(import doeff_cluster.process_versions [current-versions])
(import doeff_cluster.runtime_env_model [EnvVar RuntimeEnvInvalid])
(import doeff_cluster.worker_model [spec-hash])
(import tests.fixtures.services [lab lab-pair tally-program greeter-program holding-program])
(import tests.fixtures.envs [plain-foundation greeting-foundation])

(val PACKAGE-ROOT (. (Path (os.path.abspath __file__)) parent parent))   ; 子 process の cwd(tests.fixtures を import する)
(val TALLY-CALL (CallShape :function tally-program :args [plain-foundation 2] :kwargs {}))


(defk tally-job [environ]
  {:pre [(: environ dict)] :post [(: % Job)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "defsystem の展開と同じ呼び方で作った tally の job(environ だけを変える)。"
  (job "tally" (tally-program plain-foundation 2) :call TALLY-CALL :needs #{"cluster-net"} :environ environ))


(defk only-row [system]
  {:pre [(: system System)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "job 1 つの系の宣言の行。"
  (val rows (. (system-declaration system "rev1" :versions (current-versions)) rows))
  (assert (= (len rows) 1) rows)
  (get rows 0))


(defk declare-cli [#* argv]
  {:pre [(: argv tuple)] :post [(: % subprocess.CompletedProcess)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "declare の CLI を子 process で撃つ(cwd = package の根 — tests.fixtures を import する)。"
  (val words (lfor a argv :if (isinstance a str) a))
  (assert (= (len words) (len argv)) #("子 process の引数は文字列だけ" argv))
  (subprocess.run [sys.executable "-m" "hy" "-m" "doeff_cluster.declare" #* words]
                  :cwd (str PACKAGE-ROOT) :capture-output True :text True :timeout 120))


;; --- 系の値 -------------------------------------------------------------------------------------

(deftest test-a-system-holds-each-program-with-its-call-shape
  ;; defsystem の関数に土台を渡すと System の値。job は Program の値と、それを作った呼び出しの形(関数と引数の値)を持つ。
  (val system (lab plain-foundation))
  (assert (= system.name "lab"))
  (val tally (job-named system "tally"))
  (assert (= tally.call TALLY-CALL) tally.call)
  (assert (is tally.call.function tally-program))
  (assert (= tally.needs (frozenset ["cluster-net"])))
  (assert (= tally.environ #((EnvVar :name "TALLY_BASE" :value "1"))))
  (assert (= #(tally.update tally.readiness) #("recreate" None)))
  (assert (is (job-named system "nothing") None))
  ;; Program は Program の値のまま(土台の handler を自分で並べるので、そのまま走らせて答えが出る)。
  (<- total int tally.program)
  (assert (= total 102)))


(deftest test-job-names-in-a-system-do-not-overlap
  (<- tally Job (tally-job {}))
  (with [raised (pytest.raises ValueError)]
    (system-of "twice" #(tally tally)))
  (assert (in "tally" (str raised.value)))
  (assert (in "2 つ" (str raised.value))))


(deftest test-a-job-without-needs-is-refused
  ;; 要る能力を書かない job(どこにでも置ける仕事)は断る(R4b)。空の集合も、:needs の欠けも同じ。旧い label の形も能力の名にしない。
  (with [raised (pytest.raises ValueError)]
    (job "nowhere" (tally-program plain-foundation 1) :call TALLY-CALL :needs #{}))
  (assert (in "nowhere" (str raised.value)))
  (assert (in ":needs が空" (str raised.value)))
  (with [raised (pytest.raises TypeError)]
    (job "missing" (tally-program plain-foundation 1) :call TALLY-CALL))
  (assert (in "needs" (str raised.value)))
  (with [raised (pytest.raises ValueError)]
    (job "label" (tally-program plain-foundation 1) :call TALLY-CALL :needs #{"kind=k3s"}))
  (assert (in "kind=k3s" (str raised.value))))


(deftest test-a-job-must-carry-a-program-value
  ;; job は Program の値 1 つを受ける(R1)。関数そのもの・呼び出しの結果でない値は断る。
  (with [raised (pytest.raises TypeError)]
    (job "not-a-program" 3 :call TALLY-CALL :needs #{"net"}))
  (assert (in "not-a-program" (str raised.value))))


(deftest test-an-unknown-update-form-and-a-bad-readiness-are-refused
  (with [raised (pytest.raises ValueError)]
    (job "rolling" (tally-program plain-foundation 1) :call TALLY-CALL :needs #{"net"} :update "rolling"))
  (assert (in "rolling" (str raised.value)))
  (with [raised (pytest.raises ValueError)]
    (job "no-window" (tally-program plain-foundation 1) :call TALLY-CALL :needs #{"net"} :update "handoff"
         :readiness {"windowSeconds" "soon"}))
  (assert (in "no-window" (str raised.value))))


(deftest test-environ-names-follow-the-env-var-rules
  ;; :environ は子の環境変数。名の形と worker の予約(DOEFF_)は実行環境の env-vars と同じ検め(EnvVar)で断る。
  (with [(pytest.raises RuntimeEnvInvalid)]
    (<- (tally-job {"lower-case" "1"})))
  (with [(pytest.raises RuntimeEnvInvalid)]
    (<- (tally-job {"DOEFF_WORKER_NAME" "x"})))
  (with [(pytest.raises TypeError)]
    (<- (tally-job {"POLL" 5}))))


(deftest test-a-handler-value-or-a-nested-function-is-not-a-program-argument
  ;; 土台は module の最上位の関数で渡す(参照で詰める)。handler の値・入れ子の関数・lambda を Program の引数にすると、宣言が handler を
  ;; 値として運ぶか、実行先で引けない参照になるので、宣言の時点で断る(R3b・計画 6 節)。
  (val made (reader {"base" 1}))
  (with [raised (pytest.raises TypeError)]
    (job "made-handler" (holding-program made 1) :call (CallShape :function holding-program :args [made 1] :kwargs {})
         :needs #{"net"}))
  (assert (in "made-handler" (str raised.value)))
  (assert (in "handler の値" (str raised.value)))
  ;; defhandler の値(module の最上位に在っても handler の値)。
  (with [raised (pytest.raises TypeError)]
    (job "defhandler" (holding-program host-reader 1) :call (CallShape :function holding-program :args [host-reader 1] :kwargs {})
         :needs #{"net"}))
  (assert (in "handler の値" (str raised.value)))
  (defk inner-foundation []
    {:pre [] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "foundation"}}
    [])
  (with [raised (pytest.raises TypeError)]
    (job "inner" (tally-program inner-foundation 1) :call (CallShape :function tally-program :args [inner-foundation 1] :kwargs {})
         :needs #{"net"}))
  (assert (in "最上位" (str raised.value)))
  (val anonymous (fn [] []))
  (with [(pytest.raises TypeError)]
    (job "anonymous" (tally-program anonymous 1) :call (CallShape :function tally-program :args [anonymous 1] :kwargs {})
         :needs #{"net"}))
  ;; 呼んだ関数そのものが入れ子でも同じ。
  (with [(pytest.raises TypeError)]
    (job "inner-call" (inner-foundation) :call (CallShape :function inner-foundation :args [] :kwargs {}) :needs #{"net"})))


;; --- 宣言の行 -----------------------------------------------------------------------------------

(deftest test-the-row-carries-the-identity-the-describe-and-the-program-key
  (val declaration (system-declaration (lab plain-foundation) "rev1" :versions (current-versions)))
  (assert (isinstance declaration Declaration))
  (val row (get declaration.rows 0))
  (val run (get row "run"))
  (val identity {"function" "tests.fixtures.services:tally_program"
                 "args" [{"ref" "tests.fixtures.envs:plain_foundation"} 2]
                 "kwargs" {}})
  (assert (= row {"name" "tally"
                  "revision" "rev1"
                  "needs" ["cluster-net"]
                  "run" {"kind" "service"
                         "program" (get run "program")
                         "identity" identity
                         "versions" (current-versions)
                         "describe" "tests.fixtures.services:tally_program(tests.fixtures.envs:plain_foundation, 2)"}
                  "environ" {"TALLY_BASE" "1"}})
          row)
  (assert (= (identity-of TALLY-CALL "tally") identity))
  (assert (= (describe-identity identity) (get run "describe")))
  ;; 行は置き場のキー(sha)だけを持ち、詰めた中身は programs に別に出る(中身の sha256 がキー)。
  (val blob (get declaration.programs (get run "program")))
  (assert (= (list declaration.programs) [(get run "program")]))
  (assert (= (.hexdigest (hashlib.sha256 (.encode blob "ascii"))) (get run "program")))
  (assert (not-in blob (json.dumps row)) "詰めた中身は行に載らない"))


(deftest test-every-option-reaches-the-declaration-row
  ;; readiness と handoff は行に残る。recreate(既定)は update を書かない。名の引数(kwargs)も identity と describe に残る。
  (val declaration (system-declaration (lab-pair plain-foundation) "rev1" :versions (current-versions)))
  (val rows (dfor r declaration.rows (get r "name") r))
  (assert (= (sorted rows) ["greeter" "tally"]))
  (val greeter (get rows "greeter"))
  (assert (= (get greeter "readiness") {"windowSeconds" 30}))
  (assert (= (get greeter "update") "handoff"))
  (assert (not-in "update" (get rows "tally")))
  (assert (not-in "readiness" (get rows "tally")))
  (assert (= (len declaration.programs) 2) "job ごとに詰めた Program")
  (val named (job "named" (tally-program :foundation plain-foundation :step 4)
                  :call (CallShape :function tally-program :args [] :kwargs {"foundation" plain-foundation "step" 4})
                  :needs #{"net"}))
  (<- row dict (only-row (system-of "named" #(named))))
  (assert (= (get row "run" "identity" "kwargs") {"foundation" {"ref" "tests.fixtures.envs:plain_foundation"} "step" 4}))
  (assert (= (get row "run" "describe")
             "tests.fixtures.services:tally_program(foundation=tests.fixtures.envs:plain_foundation, step=4)")))


(deftest test-the-same-program-packed-twice-keeps-the-same-identity
  ;; 同じ宣言を 2 回詰める: 詰めた文字列(blob)は揺れうるが、同一性の指紋(identity-hash)と coordinator の spec-hash は同じ —
  ;; 宣言し直すたびに入れ替えが起きない(改訂 1 の A)。
  (<- first dict (only-row (lab plain-foundation)))
  (<- second dict (only-row (lab plain-foundation)))
  (assert (= (identity-hash (get first "run")) (identity-hash (get second "run"))))
  (assert (= (spec-hash (. (job-from-json first) spec)) (spec-hash (. (job-from-json second) spec))))
  ;; 詰めた中身が違っても(置き場のキーだけを変えた行でも)同一性は同じ — program は比べない欄。
  (val moved (| second {"run" (| (get second "run") {"program" (* "f" 64)})}))
  (assert (= (spec-hash (. (job-from-json first) spec)) (spec-hash (. (job-from-json moved) spec))))
  (assert (= (. (job-from-json first) spec) (. (job-from-json moved) spec)))
  ;; 引数の値が変われば同一性も変わる。
  (val other (system-of "lab" #((job "tally" (tally-program plain-foundation 3)
                                     :call (CallShape :function tally-program :args [plain-foundation 3] :kwargs {})
                                     :needs #{"cluster-net"} :environ {"TALLY_BASE" "1"}))))
  (<- changed dict (only-row other))
  (assert (!= (identity-hash (get first "run")) (identity-hash (get changed "run"))))
  (assert (!= (spec-hash (. (job-from-json first) spec)) (spec-hash (. (job-from-json changed) spec)))))


(deftest test-changing-the-environ-changes-the-spec-hash
  ;; environ は子の環境変数 — 変われば process を起こし直す(spec-hash に入る・改訂 1 の G)。
  (<- one Job (tally-job {"TALLY_BASE" "1"}))
  (<- two Job (tally-job {"TALLY_BASE" "2"}))
  (<- none Job (tally-job {}))
  (<- row-one dict (only-row (system-of "lab" #(one))))
  (<- row-two dict (only-row (system-of "lab" #(two))))
  (<- row-none dict (only-row (system-of "lab" #(none))))
  (val hashes (lfor r [row-one row-two row-none] (spec-hash (. (job-from-json r) spec))))
  (assert (= (len (set hashes)) 3) hashes)
  (assert (= (. (job-from-json row-one) spec environ) #(#("TALLY_BASE" "1")))))


(deftest test-the-environ-overlay-changes-only-declared-names-and-enters-the-spec-hash
  ;; 配る先ごとの値(口の URL など)は宣言の :environ に重ねる上書きで渡す — declare の --environ と sim-cluster の :environ が同じ
  ;; system-declaration の規則を使う。宣言に無い名・系に無い job・文字列でない値は断り、上書きは spec-hash に入る。
  (<- one Job (tally-job {"TALLY_BASE" "1"}))
  (val system (system-of "lab" #(one)))
  (val plain (get (. (system-declaration system "rev1" :versions (current-versions)) rows) 0))
  (val overlaid (get (. (system-declaration system "rev1" :versions (current-versions) :environ {"tally" {"TALLY_BASE" "9"}}) rows) 0))
  (assert (= (get overlaid "environ") {"TALLY_BASE" "9"}) overlaid)
  (assert (!= (spec-hash (. (job-from-json plain) spec)) (spec-hash (. (job-from-json overlaid) spec))))
  (for [#(overlay word) [#({"tally" {"UNDECLARED" "x"}} "UNDECLARED") #({"elsewhere" {"TALLY_BASE" "1"}} "elsewhere")
                         #({"tally" {"TALLY_BASE" 9}} "TALLY_BASE")]]
    (with [raised (pytest.raises ValueError)]
      (system-declaration system "rev1" :versions (current-versions) :environ overlay))
    (assert (in word (str raised.value)) (str raised.value))))


;; --- 旧い形を断る入口(計画 2.8)---------------------------------------------------------------

(deftest test-entry-1-the-job-constructor-refuses-the-old-arguments
  ;; 入口 1: 旧い引数(:env・:config・:env-config・:requires)は構成子に無いので TypeError(名を出す)。
  (for [#(key value) [#("env" "m:e") #("config" {"step" 1}) #("env_config" {"greeting" "hi"}) #("requires" {"kind" "k3s"})]]
    (with [raised (pytest.raises TypeError)]
      (job "old" (tally-program plain-foundation 1) :call TALLY-CALL :needs #{"net"} #** {key value}))
    (assert (in key (str raised.value)) (str raised.value))))


(deftest test-entry-3-the-old-service-function-is-gone
  ;; 入口 3: 旧い関数 service(と、関数の参照 + 設定の宣言を支えた道具)は消えた — import で落ちる。
  (with [(pytest.raises ImportError)]
    (import doeff_cluster.service_model [service]))
  (for [name ["service" "ServiceDef" "system_main" "service_program" "config_of" "program_arguments" "settings_left_to_env"
              "RECORD_KEY"]]
    (assert (not (hasattr service-model name)) name)))


(deftest test-entry-4-the-declare-cli-refuses-the-old-arguments
  ;; 入口 4: declare の CLI は --config・--pin・System の値を指す形を argparse の error(終了 2)で断り、理由を出す。
  (val base ["tests.fixtures.services:lab" "--foundation" "tests.fixtures.envs:plain_foundation" "--revision" "r"])
  (<- config subprocess.CompletedProcess (declare-cli #* base "--config" "{}"))
  (assert (= config.returncode 2) config.stderr)
  (assert (in "--config は受け付けない" config.stderr) config.stderr)
  (<- pin subprocess.CompletedProcess (declare-cli #* base "--pin" "worker-1"))
  (assert (= pin.returncode 2) pin.stderr)
  (assert (in "--pin は受け付けない" pin.stderr) pin.stderr)
  (<- value subprocess.CompletedProcess (declare-cli "tests.fixtures.system_values:LAB_VALUE" #* (cut base 1 None)))
  (assert (= value.returncode 2) value.stderr)
  (assert (in "defsystem の関数" value.stderr) value.stderr)
  (<- bare subprocess.CompletedProcess (declare-cli "tests.fixtures.services:lab" "--revision" "r"))
  (assert (= bare.returncode 2) bare.stderr)
  (assert (in "--foundation" bare.stderr) bare.stderr))


;; --- declare の宣言の前の検め(計画 2.2 の E・9 節の P)----------------------------------------------
;;
;; 系の関数の module(tests/fixtures/declared_system.hy を写した declared_system.hy)を 1 commit 持ち、bare の remote へ push 済みの
;; 一時の clone(本物の git)の中で declare を撃つ。汚れた checkout・HEAD と違う --revision・job の :needs に無い能力を名乗る土台は、
;; どれも理由つきの終了 2 で断られ、宣言の行を印字しない。汚れておらず HEAD = --revision なら印字する。

(val DECLARED-SYSTEM-SOURCE (/ PACKAGE-ROOT "tests" "fixtures" "declared_system.hy"))


(defk git-in [cwd #* args]
  {:pre [(: cwd Path) (: args tuple)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "検の一時の repo を作る・読むために git を 1 回呼ぶ(標準出力を返す)。"
  (val words (lfor a args :if (isinstance a str) a))
  (assert (= (len words) (len args)) #("子 process の引数は文字列だけ" args))
  (val done (subprocess.run ["git" "-C" (str cwd) "-c" "user.name=t" "-c" "user.email=t@example.invalid" #* words]
                            :capture-output True :text True :check True))
  (.strip done.stdout))


(defk declared-checkout [base]
  {:pre [(: base Path)] :post [(: % Path)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "bare の remote と、見本の系(declared_system.hy)を 1 commit 持つ push 済みの clone を base の下に作り、clone の path を返すため。"
  (val remote (/ base "declared.git"))
  (val work (/ base "declared"))
  (<- (git-in base "init" "-q" "--bare" (str remote)))
  (<- (git-in base "clone" "-q" (str remote) (str work)))
  (.write-text (/ work "declared_system.hy") (.read-text DECLARED-SYSTEM-SOURCE :encoding "utf-8") :encoding "utf-8")
  (<- (git-in work "add" "-A"))
  (<- (git-in work "commit" "-q" "-m" "first"))
  (<- (git-in work "push" "-q" "origin" "HEAD:main"))
  (<- (git-in work "fetch" "-q" "origin"))
  work)


(defk declare-in [work #* argv]
  {:pre [(: work Path) (: argv tuple)] :post [(: % subprocess.CompletedProcess)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "work の checkout の中で declare の CLI を子 process で撃つため(import の路 = work — 系の関数の module はそこに在る)。"
  (val words (lfor a argv :if (isinstance a str) a))
  (assert (= (len words) (len argv)) #("子 process の引数は文字列だけ" argv))
  (subprocess.run [sys.executable "-m" "hy" "-m" "doeff_cluster.declare" #* words]
                  :cwd (str work) :capture-output True :text True :timeout 120
                  :env (| (dict os.environ) {"PYTHONPATH" (str work)})))


(deftest test-the-declare-cli-prints-the-rows-from-a-clean-checkout-at-the-revision [tmp-path]
  ;; 汚れておらず push 済みの checkout で HEAD = --revision: 宣言の行を JSON で印字し、job ごとの describe(呼んだ関数と引数)を
  ;; stderr に出す。--only で絞る。
  (<- work Path (declared-checkout tmp-path))
  (<- head str (git-in work "rev-parse" "HEAD"))
  (<- done subprocess.CompletedProcess
      (declare-in work "declared_system:pair" "--foundation" "declared_system:net_foundation" "--revision" head "--only" "greeter"))
  (assert (= done.returncode 0) done.stderr)
  (val rows (get (json.loads done.stdout) "jobs"))
  (assert (= (lfor r rows (get r "name")) ["greeter"]) rows)
  (assert (= (get rows 0 "revision") head) rows)
  (assert (= (get rows 0 "run" "identity" "function") "declared_system:greeter_program"))
  (assert (in "greeter: declared_system:greeter_program(declared_system:net_foundation, 3)" done.stderr) done.stderr))


(deftest test-the-declare-cli-refuses-a-checkout-with-uncommitted-changes [tmp-path]
  ;; 系の関数の module に commit していない変更がある: 詰める Program の code が --revision の木と違いうるので宣言しない。
  (<- work Path (declared-checkout tmp-path))
  (<- head str (git-in work "rev-parse" "HEAD"))
  (.write-text (/ work "declared_system.hy") (+ (.read-text (/ work "declared_system.hy") :encoding "utf-8") ";; 変更\n")
               :encoding "utf-8")
  (<- done subprocess.CompletedProcess
      (declare-in work "declared_system:pair" "--foundation" "declared_system:net_foundation" "--revision" head))
  (assert (= done.returncode 2) done.stderr)
  (assert (in "dirty-tree" done.stderr) done.stderr)
  (assert (= done.stdout "") done.stdout))


(deftest test-the-declare-cli-refuses-a-revision-other-than-the-head [tmp-path]
  ;; --revision が checkout の HEAD と違う(1 つ前の commit): 実行先がその版で展開する code と、いま詰める code がずれるので宣言しない。
  (<- work Path (declared-checkout tmp-path))
  (<- first str (git-in work "rev-parse" "HEAD"))
  (.write-text (/ work "extra.py") "X = 1\n" :encoding "utf-8")
  (<- (git-in work "add" "-A"))
  (<- (git-in work "commit" "-q" "-m" "second"))
  (<- (git-in work "push" "-q" "origin" "HEAD:main"))
  (<- (git-in work "fetch" "-q" "origin"))
  (<- done subprocess.CompletedProcess
      (declare-in work "declared_system:pair" "--foundation" "declared_system:net_foundation" "--revision" first))
  (assert (= done.returncode 2) done.stderr)
  (assert (in "revision-differs" done.stderr) done.stderr)
  (assert (in first done.stderr) done.stderr)
  (assert (= done.stdout "") done.stdout))


(deftest test-the-declare-cli-refuses-a-foundation-whose-needs-exceed-a-job [tmp-path]
  ;; 土台の頭の :needs(cluster-net・gpu)が job の :needs(cluster-net)の一部でない: job が要る能力を書き漏らしているので宣言しない
  ;; (置かれた worker が土台の要る能力を持たないまま起きる)。checkout は汚れておらず HEAD = --revision(断る理由は needs だけ)。
  (<- work Path (declared-checkout tmp-path))
  (<- head str (git-in work "rev-parse" "HEAD"))
  (<- done subprocess.CompletedProcess
      (declare-in work "declared_system:pair" "--foundation" "declared_system:wide_foundation" "--revision" head))
  (assert (= done.returncode 2) done.stderr)
  (assert (in "の土台 declared_system:wide_foundation(足りない" done.stderr) done.stderr)
  (assert (in "tally の土台 declared_system:wide_foundation(足りない ['gpu'])" done.stderr) done.stderr)
  (assert (= done.stdout "") done.stdout))


;; --- 宣言した Program を実行先の入口で走らせる ----------------------------------------------------

(deftest test-the-worker-entry-runs-the-declared-program-as-is [tmp-path]
  ;; 宣言が詰めた Program を、worker が /programs から取るのと同じ形の file で job_entry service に渡す。入口は handler を足さず、
  ;; Program が自分で並べた土台の reader が答える(同じ venv で詰めて、子 process で解ける — 版の食い違いが無い)。
  (val declaration (system-declaration (lab-pair greeting-foundation) "rev1" :versions (current-versions)))
  (val greeter (next (gfor r declaration.rows :if (= (get r "name") "greeter") r)))
  (val run (get greeter "run"))
  (val program-file (/ tmp-path "program.json"))
  (.write-text program-file (json.dumps {"blob" (get declaration.programs (get run "program")) "versions" (get run "versions")})
               :encoding "utf-8")
  (val done (subprocess.run [sys.executable "-m" "hy" "-m" "doeff_cluster.job_entry" "service"
                             "--identity" (identity-hash run) "--program" (str program-file)]
                            :cwd (str PACKAGE-ROOT) :capture-output True :text True :timeout 120
                            :env (| (dict os.environ) {"DOEFF_WORKER_JOB" "greeter"})))
  (assert (= done.returncode 0) done.stderr)
  (assert (in "が終わった: 'hi3'" done.stderr) done.stderr))


(defk two-foundations-job [foundation other]
  {:pre [(: foundation Callable) (: other Callable)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "系が土台を 2 つ受ける見本の job(2 つ目の土台は引数の値として呼び出しの形に載る)。撃たない。"
  (<- total int (foundation (tally-program other 1)))
  total)


(deftest test-every-foundation-passed-to-a-job-is-checked-against-its-needs
  ;; 系が土台を 2 つ以上受ける時も、job の呼び出しの形の引数に渡した土台の :needs を全部検める(宣言の道具が土台ごとに手で写さない —
  ;; 業務の系には家族ごとの土台を 6 つ受ける物がある)。2 つ目の土台の :needs が job の :needs に無ければ断る。
  (import tests.fixtures.declared_system [wide-foundation])
  (val narrow (system-of "two" #((job "tally" (two-foundations-job plain-foundation plain-foundation)
                                      :call (CallShape :function two-foundations-job :args [plain-foundation plain-foundation] :kwargs {})
                                      :needs #{"cluster-net"}))))
  (val wide (system-of "two" #((job "tally" (two-foundations-job plain-foundation wide-foundation)
                                    :call (CallShape :function two-foundations-job :args [plain-foundation wide-foundation] :kwargs {})
                                    :needs #{"cluster-net"}))))
  (<- ok (service-model.foundation-needs-refusal narrow plain-foundation))
  (assert (is ok None) ok)
  (<- refused str (service-model.foundation-needs-refusal wide plain-foundation))
  (assert (and refused (in "wide_foundation" refused)) refused))
