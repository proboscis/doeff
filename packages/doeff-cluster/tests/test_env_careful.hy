;; 実行環境(runtime env)の丁寧な模擬 — 本物の root の言い換え env-host(準備の process = env_handlers の翻訳 env-translation と本物の答え手)・手元の bare repo と file:// の URL・
;; PATH の先頭の fake の uv(tests/fixtures/fake_uv.hy)・本物の ProcessHost(子 process と shim)・実時間。
;;
;; ここは本物でしか確かめられない縁だけを見る(本物の git・root の venv の interpreter で作る bytecode・本物の子 process が見る環境)。
;; 本物の準備の process は 1 回 約 6 秒・本物の子は 1 組 約 4.6 秒かかる(#2795 の測り)ので、判断の主張は速い検に置く:
;;   - 準備の判断(筋書き 3 lock を変える・4 同じ lock の別の commit・5 native の source・7 repo を 3 つ・失敗の種類・名前の影)
;;     = test_env_prepare.hy(同じ翻訳 env-translation を台本の git と uv・memory の置き場の上で回す)
;;   - env-host の判断(筋書き 6 同じキーの準備は 1 本・8 先読み・9 掃除)= test_env_host_judgments.hy(準備の道具の起こし方だけを
;;     同じ process の中の答え手に替える)
;; この file の筋書き(設計 worker-runtime-env.md 節 5):
;;   1 宣言 → 準備 → 実行: 結果が返る・子の環境変数に PYTHONPATH が無い・子の cwd が作業 dir・bytecode は root の venv の interpreter で作った
;;   2 送り手の repo の commit だけ変えて再送(返す値が commit で違う関数): 新しい値が返る・worker の pid が同じ・download は 0・build は 0
;;   push していない commit: 本物の git の fetch の答えを翻訳が commit-missing と読む
;; 反例: 子に PYTHONPATH を残す / worker の再起動で走らせる実装 / 送り手の版の doeff をずらす。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import os)
(import sys)
(import pathlib [Path])
(import doeff_cluster.shared.intent.runtime_env_model [NativeWheel RuntimeEnv EnvFailureKind])
(import doeff_cluster.shared.core.runtime_env_rules [env-key current-platform])
(import doeff_cluster.shared.intent.checkout_model [LocalCheckout ProjectOfCheckout])
(import doeff_cluster.shared.core.runtime_env [runtime-env-of-checkouts])
(import doeff_cluster.shared.protocol.checkout_reads [checkout-reads])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.worker.protocol.env_store [env-root])
(import doeff_cluster.worker.protocol.process_host [HostSettings job-work-dir])
(import doeff_core_effects.process_effects [EnvEntry StartProcess])
(import doeff_cluster.worker.intent.worker_model [CodeState CodeView])
(import doeff_cluster.shared.intent.remote_model [TaskSucceeded TaskFailed])
(import doeff_cluster.foundation.process_versions [process-versions])


(import tests.careful_rig [LOCK git push-commit app-files Rig make-rig declare count-log downloads prepare run-task])

;; --- 筋書き ---------------------------------------------------------------------------------

(defk child-answer [outcome]
  {:pre [(: outcome TaskSucceeded)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "成功した task の子の答え #(値 PYTHONPATH cwd env のキー worker の pid venv の prefix)(appjobs の report)— 値が組であることを確かめて返す。"
  (assert (isinstance outcome.value tuple) outcome)
  outcome.value)

(deftest test-careful-scenarios-1-and-2 [tmp-path subprocess-bytecode job-child-code-store]
  (<- rig Rig (make-rig tmp-path))
  (<- files-a1 dict (app-files 1 LOCK))
  (<- a1 str (push-commit rig.app files-a1 "app 1"))
  (<- l1 str (push-commit rig.lib {"native/core/lib.rs" "fn a() {}\n" "native/core/Cargo.toml" "[package]\n"} "lib 1"))
  (.insert sys.path 0 (str rig.app))
  ;; 1 宣言(送り手の組み立て — 本物の git の checkout から)→ 準備 → 実行
  (<- env-1 RuntimeEnv (subprocess-handler (os-file-handler (checkout-reads
                         (runtime-env-of-checkouts #((LocalCheckout :name "app" :path (str rig.app))
                                                     (LocalCheckout :name "lib" :path (str rig.lib)))
                                                   (ProjectOfCheckout :repo "app" :path "." :python "3.14"
                                                                      :native #((NativeWheel :package "lib-native" :repo "lib" :paths #("native/core"))))
                                                   #("app/."))))))
  (<- view-1 CodeView (prepare rig env-1))
  (assert (= view-1.state CodeState.READY) view-1)
  (assert (is-not view-1.path None) view-1)
  (<- outcome-1 TaskSucceeded (run-task rig env-1 "t1"))
  ;; 子の答え = #(値 PYTHONPATH cwd env のキー worker の pid venv の prefix)
  (<- answer-1 tuple (child-answer outcome-1))
  (val value-1 (get answer-1 0))
  (val pythonpath (get answer-1 1))
  (val cwd (get answer-1 2))
  (val key-1 (get answer-1 3))
  (val pid-1 (get answer-1 4))
  (val prefix-1 (get answer-1 5))
  (assert (= value-1 1))
  (assert (is pythonpath None) "子の環境変数に PYTHONPATH が無い")
  (<- work-1 str (job-work-dir rig.host "task/t1"))
  (assert (= cwd work-1) "子の cwd は空の作業 dir")
  (assert (not (.exists (Path work-1))) "作業 dir は回収の後に消す")
  (<- key-1-want str (env-key env-1 (current-platform)))
  (assert (= key-1 key-1-want))
  (assert (= prefix-1 (str (/ (Path view-1.path) "app" ".venv"))) "子は root の venv で走った")
  (val marker (json.loads (.read-text (/ (Path view-1.path) ENV-MARKER))))
  (assert (= (get marker "interpreter") (os.path.realpath (/ (Path view-1.path) "app" ".venv" "bin" "python")))
          "bytecode を作った interpreter は root の venv の物")
  (assert (list (.glob (/ (Path view-1.path) "app") "__pycache__/appjobs.*.pyc")) "root の中に bytecode を作った")
  ;; 2 project の commit だけ変えて再送(同じ lock・同じ native)
  (<- downloads-1 int (downloads rig))
  (<- builds-1 int (count-log rig "build "))
  (<- files-a2 dict (app-files 2 LOCK))
  (<- a2 str (push-commit rig.app files-a2 "app 2"))
  (<- env-2 RuntimeEnv (declare rig a2 l1 LOCK))
  (<- view-2 CodeView (prepare rig env-2))
  (assert (= view-2.state CodeState.READY) view-2)
  (assert (!= view-2.path view-1.path) "新しい commit は新しい root")
  (<- outcome-2 TaskSucceeded (run-task rig env-2 "t2"))
  (<- answer-2 tuple (child-answer outcome-2))
  (val value-2 (get answer-2 0))
  (val pid-2 (get answer-2 4))
  (assert (= value-2 2) "新しい root の source の値が返る")
  (assert (= pid-2 pid-1 (str (os.getpid))) "worker の process は同じ(再起動していない)")
  (assert (is (get answer-2 1) None) "2 本目の子の環境変数にも PYTHONPATH が無い")
  (<- work-2 str (job-work-dir rig.host "task/t2"))
  (assert (= (get answer-2 2) work-2) "2 本目の子の cwd も空の作業 dir")
  (<- downloads-2 int (downloads rig))
  (<- builds-2 int (count-log rig "build "))
  (assert (= downloads-2 downloads-1) "同じ lock なので download は 0")
  ;; 4 同じ lock・別の project の commit → 新しい root・download 0・native の build 0
  (assert (= builds-2 builds-1 1) "native の source が同じなので build は 0(最初の 1 回だけ)"))


(defk failure-kind [rig env]
  {:pre [(: rig Rig) (: env RuntimeEnv)] :post [(: % EnvFailureKind)]}
  "準備が失敗し、完成マーカーを置かなかったことを確かめて kind を返す。"
  (<- view CodeView (prepare rig env))
  (assert (= view.state CodeState.FAILED) view)
  (<- key str (env-key env (current-platform)))
  (<- root str (env-root rig.envs (+ "env-" key)))
  (assert (not (.exists (/ (Path root) ENV-MARKER))) "失敗した root に完成マーカーは無い")
  (assert (is-not view.failure None) "実行環境の準備の失敗は理由の種類を運ぶ")
  view.failure.kind)


(deftest test-careful-an-unpushed-commit-comes-back-as-commit-missing [tmp-path subprocess-bytecode]
  ;; 本物の git の縁: remote に push していない commit を宣言すると、本物の git の fetch の答えを翻訳が commit-missing と読む
  ;; (完成マーカーは置かない)。ほかの失敗の種類(届かない・lock の hash・uv の失敗・native の build・空き・名前の影・
  ;; 子の約束の版)は、同じ翻訳を台本の git と uv の上で回す test_env_prepare.hy の test-each-failure-comes-back-as-its-kind と
  ;; test-a-third-party-package-shadowing-a-root-is-refused が見る。
  (<- rig Rig (make-rig tmp-path))
  (<- files-a1 dict (app-files 1 LOCK))
  (<- (push-commit rig.app files-a1 "app 1"))
  (<- l1 str (push-commit rig.lib {"native/core/lib.rs" "fn a() {}\n"} "lib 1"))
  (.write-text (/ rig.app "local.txt") "x\n")
  (<- (git rig.app "add" "-A"))
  (<- (git rig.app "commit" "-q" "-m" "local only"))
  (<- unpushed str (git rig.app "rev-parse" "HEAD"))
  (<- env-1 RuntimeEnv (declare rig unpushed l1 LOCK))
  (<- kind-1 EnvFailureKind (failure-kind rig env-1))
  (assert (= kind-1 EnvFailureKind.COMMIT-MISSING) kind-1))


;; --- 反例: 子の環境と worker の process ----------------------------------------------------

;; 反例は、子 process の言い換え(process-host)と本物の答え手の間に置く壊した handler — StartProcess の子の環境変数を書き換えてから
;; 本物へ渡す(#2464 — 以前は ProcessHost.launch を書き換えた子 class)。
(defhandler leaky-start
  ;; 反例: 実行環境の job の子に PYTHONPATH を残す実装。
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group]
    (<- answer (StartProcess :argv argv :cwd cwd :env (+ env #((EnvEntry :name "PYTHONPATH" :value cwd))) :env-mode env-mode
                             :env-drop env-drop :stdout-path stdout-path :stderr-path stderr-path :process-group process-group
                             :hold-stdin hold-stdin :reap-group reap-group))
    (resume answer)))


(defhandler restarting-start
  ;; 反例: task ごとに worker の process を作り直して走らせる実装(子が名乗る worker の pid が task ごとに変わる)。
  ;; 子が名乗る worker の pid を task ごとに違う値にする(task ごとに違う出力の file の path を混ぜる — task は別々の run で回るので数えない)。
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group]
    (<- answer (StartProcess :argv argv :cwd cwd
                             :env (tuple (gfor e env (if (= e.name "DOEFF_WORKER_PID") (EnvEntry :name e.name :value f"restarted-{stdout-path}") e)))
                             :env-mode env-mode :env-drop env-drop :stdout-path stdout-path :stderr-path stderr-path
                             :process-group process-group :hold-stdin hold-stdin :reap-group reap-group))
    (resume answer)))


(defk child-problems [outcomes settings]
  {:pre [(: outcomes tuple) (: settings HostSettings)] :post [(: % list)]}
  "筋書き 1・2 の子の確かめ: PYTHONPATH が無い・cwd が作業 dir・どの task も同じ worker の process から起きた。破れた所の列。"
  (var problems [])
  (for [#(name outcome) outcomes]
    (when (is-not (get outcome.value 1) None) (.append problems (.format "{}: 子に PYTHONPATH が在る" name)))
    (<- work str (job-work-dir settings name))
    (when (!= (get outcome.value 2) work) (.append problems (.format "{}: cwd が作業 dir でない" name))))
  (when (!= (len (set (gfor #(_ o) outcomes (get o.value 4)))) 1)
    (.append problems "task ごとに worker の pid が違う(worker の process を作り直した)"))
  problems)


(deftest test-careful-counterexamples-are-caught [tmp-path subprocess-bytecode job-child-code-store]
  (<- rig Rig (make-rig tmp-path))
  (<- files-a1 dict (app-files 1 LOCK))
  (<- a1 str (push-commit rig.app files-a1 "app 1"))
  (<- l1 str (push-commit rig.lib {"native/core/lib.rs" "fn a() {}\n"} "lib 1"))
  (.insert sys.path 0 (str rig.app))
  (<- env RuntimeEnv (declare rig a1 l1 LOCK))
  (<- view CodeView (prepare rig env))
  (assert (= view.state CodeState.READY) view)
  ;; 本物の実装で子の確かめが破れないこと(PYTHONPATH が無い・cwd が作業 dir・どの task も同じ worker の process)は、筋書き 1・2 の検
  ;; (test-careful-scenarios-1-and-2 — 本物の子 2 本)が見る。ここは壊した実装が赤になることだけを見る: PYTHONPATH を残す実装は
  ;; 子 1 本で破れ、worker を作り直す実装は子の pid を 2 本で比べて破れる。
  (var results {})
  (for [#(label around runs) [#("leaky" #(leaky-start) 1) #("restarting" #(restarting-start) 2)]]
    (var outcomes #())
    (for [n (range 1 (+ runs 1))]
      (val task-id (.format "{}-{}" label n))
      (<- outcome (run-task rig env task-id :around around))
      (assert (isinstance outcome TaskSucceeded) outcome)
      (:= outcomes (+ outcomes #(#(f"task/{task-id}" outcome)))))
    (<- problems list (child-problems outcomes rig.host))
    (:= results (| results {label problems})))
  (assert (any (gfor p (get results "leaky") (in "PYTHONPATH" p))) "子に PYTHONPATH を残すと筋書き 1 が赤")
  (assert (any (gfor p (get results "restarting") (in "worker の pid" p))) "worker の再起動で走らせると筋書き 2 が赤")
  ;; 送り手の版の doeff を 1 つずらす → 子が復元を断り、欄の名と env のキーが載る
  (import doeff_cluster.shared.intent.detached_model [DetachedVersionMismatch])
  (import doeff_cluster.shared.protocol.detached [outcome-from-task-outcome])
  (<- key str (env-key env (current-platform)))
  (val shifted (| (! (process-versions os.environ)) {"doeff" "0.0.0-shifted"}))
  (<- refused (run-task rig env "shifted" :versions shifted))
  (assert (isinstance refused TaskFailed) refused)
  (val answer (outcome-from-task-outcome refused))
  (assert (isinstance answer DetachedVersionMismatch) answer)
  (assert (= (lfor d answer.diffs d.field) ["doeff"]) answer.diffs)
  (assert (= answer.env-key key) answer))
