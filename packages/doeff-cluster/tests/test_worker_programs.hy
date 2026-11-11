;; worker が詰めた Program を受け取って子へ渡す所(ADR-DOE-CLUSTER-001 改訂 1 の F・G・H — 2026-09-27)。
;;
;; - coordinator への口の fetched-programs: 宣言の job の置き場のキー(spec.program)の Program を coordinator の GET /programs/<sha> から取り、
;;   state dir の programs/<sha>.json に書く。中身の sha256 がキーと合わない物・取れない物は書かない。在る物は取り直さない。
;; - coordinator への口の ObservePrograms と判断の program-waits: 手元に file の無い job は起こさず、取れるまで待つ(子の入口は file を読めないと
;;   終了 code 3 で落ちる)。筋書きは test_program_wait_on_sim.hy。
;; - 子 process の言い換えの起こし方(job-launch): 子の引数に `--program <その file>`、子の環境に HOST-CONTRACT の program-env と宣言の environ を足す。
;;   coordinator への口と子 process の言い換えは main の置き方(state dir の logs・tasks)で同じ programs の dir を指す。
(require doeff-hy.macros [deftest defk deff <- val])
(import dataclasses [replace])
(import hashlib)
(import json)
(import pathlib [Path])
(import httpx)
(import tests.link_rig [LinkRig])
(import doeff_cluster._worker.core.launch [spec-program-file])
(import doeff_cluster._shared.core.capabilities [environ-pairs])
(import tests.host_rig [host-settings launched])
(import doeff_cluster [HOST-CONTRACT])
(import doeff_cluster [JobSpec])
(import doeff_cluster [CodeState CodeView ObservePrograms ProcessView RetireJob SignalJob StartJob WorkerPolicy WorldView]
        doeff_cluster [JobPhase]
        doeff_cluster._worker.intent.worker_model [ProgramGap ProgramUnfetched StartHold])
(import doeff_cluster [plan statuses]
        doeff_cluster._worker.core.policy [start-holds])
(import doeff_cluster._coordinator.core.cluster_policy [JOB-ENTRY])

(val BLOB "cHJvZ3JhbQ==")
(val SHA (.hexdigest (hashlib.sha256 (.encode BLOB "ascii"))))
(val FORGED (* "b" 64))            ; 取った中身の sha256 がこのキーと合わない
(val ABSENT (* "c" 64))            ; coordinator に置かれていない
(val VERSIONS {"doeff" "0.4.1"})
(val BUSY (* "d" 64))              ; coordinator が 503 で答える(起きた直後の前の 5xx の形)
(val UNREACHED (* "e" 64))         ; coordinator に届かない
(val POLICY (WorkerPolicy))
(val NOW 1000000)
(val TREE (CodeView "rev1" CodeState.READY :path "/code/rev1"))
(val UNREACHABLE (ProgramUnfetched :detail "coordinator に届かない" :gone False))
(val REFUSED (ProgramUnfetched :detail "置かれていない" :gone True))


(defk served-programs [seen]
  {:pre [(: seen list)] :post [(: % httpx.MockTransport)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator の /programs/<sha> の偽の口(取った path を seen に積む)。"
  (deff answer [request]  ; cannot be a defk: httpx の MockTransport が呼ぶ素の callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "entry"}}
    (.append seen request.url.path)
    (cond
      (.endswith request.url.path SHA) (httpx.Response 200 :json {"blob" BLOB "versions" VERSIONS})
      (.endswith request.url.path FORGED) (httpx.Response 200 :json {"blob" BLOB "versions" VERSIONS})
      (.endswith request.url.path BUSY) (httpx.Response 503 :json {"error" "起きた直後"})
      (.endswith request.url.path UNREACHED) (raise (httpx.ConnectError "届かない" :request request))
      True (httpx.Response 404 :json {"error" "置かれていない"})))
  (httpx.MockTransport answer))


(defk service-spec [name program]
  {:pre [(: name str) (: program (| str None))] :post [(: % JobSpec)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "Program の job の service の spec(置き場のキー program・子の環境変数 environ)。"
  (JobSpec name JOB-ENTRY #("service" "--identity" (* "0" 16)) "rev1" :program program :versions (if program (environ-pairs VERSIONS) None) :environ #(#("POLL" "5.0"))))


(deftest test-the-link-fetches-only-programs-whose-content-matches-the-key [tmp-path]
  (val seen [])
  (<- transport (served-programs seen))
  (val link (LinkRig "http://coord" "node-h" #("net") 60000 :task-dir (str (/ tmp-path "tasks")) :transport transport))
  (<- good (service-spec "good" SHA))
  (<- forged (service-spec "forged" FORGED))
  (<- absent (service-spec "absent" ABSENT))
  (<- plain (service-spec "plain" None))
  (.accept-programs link #(good forged absent plain))
  (val dir (/ tmp-path "programs"))
  (assert (= (json.loads (.read-text (! (spec-program-file dir good)) :encoding "utf-8")) {"blob" BLOB "versions" VERSIONS}))
  ;; 中身の合わない物・置かれていない物は書かない(file が無い間は判断が job を起こさない)。置き場のキーの無い job は取らない。
  (assert (not (.exists (! (spec-program-file dir forged)))))
  (assert (not (.exists (! (spec-program-file dir absent)))))
  (assert (= (sorted seen) (sorted (lfor s [SHA FORGED ABSENT] (+ "/programs/" s)))) seen)
  ;; 在る物は取り直さない(取れなかった物は次の拍で取り直す)。
  (.clear seen)
  (.accept-programs link #(good absent))
  (assert (= seen [(+ "/programs/" ABSENT)]) seen))


(deftest test-the-host-hands-the-program-file-and-the-environ-to-the-child [tmp-path]
  (val state-dir (/ tmp-path "state"))
  ;; main と同じ置き方(state dir の logs・tasks)で、取る側と渡す側が同じ programs の dir を指す。
  (<- host (host-settings state-dir))
  (val link (LinkRig "http://coord" "node-h" #("net") 60000 :task-dir (str (/ state-dir "tasks"))))
  (assert (= (Path host.program-dir) (.program-dir link) (/ state-dir "programs")))
  (<- spec JobSpec (service-spec "svc" SHA))
  (<- planned tuple (launched host spec (str tmp-path) "1-1" 1))
  (val argv (get planned 0))
  (val cwd (get planned 1))
  (val env (get planned 2))
  (val file (str (! (spec-program-file (Path host.program-dir) spec))))
  (assert (= (list (cut argv -5 None)) ["service" "--identity" (* "0" 16) "--program" file]) argv)
  (assert (= (get env HOST-CONTRACT.program-env) file) env)
  (assert (= (get env "POLL") "5.0") env)
  (assert (= cwd (str tmp-path)))
  ;; 置き場のキーを持たない worker の内部の JobSpec には足さない(宣言の job も task も置き場のキーを持つ — task は test_task_programs.hy)。
  (<- bare JobSpec (service-spec "bare" None))
  (<- bare-launched tuple (launched host bare (str tmp-path) "1-1" 1))
  (val bare-argv (get bare-launched 0))
  (val bare-env (get bare-launched 2))
  (assert (not-in "--program" bare-argv) bare-argv)
  (assert (not-in HOST-CONTRACT.program-env bare-env)))


(defk bare-spec [name program once]
  {:pre [(: name str) (: program (| str None)) (: once bool)] :post [(: % JobSpec)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "入口の検めの対象でない Program の job(検めの門を除いて、Program の待ちだけを見る)。once = 1 度だけ走らせる task。"
  (JobSpec name JOB-ENTRY #() "rev1" :program program :versions (environ-pairs VERSIONS) :once once))


(defk planned-for [spec gaps [processes #()]]
  {:pre [(: spec JobSpec) (: gaps tuple) (: processes tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "木の揃った世界で、手元に無い Program が gaps の時の判断の action を求めるため。"
  (<- actions tuple (plan NOW #(spec) (WorldView #(TREE) processes :program-gaps gaps) {} POLICY))
  actions)


(defk has-action [actions kind]
  {:pre [(: actions tuple) (: kind type)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "actions に型 kind の action が在るかを見るため。"
  (any (gfor action actions (isinstance action kind))))


(deftest test-the-link-reports-the-jobs-whose-program-is-not-at-hand [tmp-path]
  ;; 手元に取れた job だけが欠けから外れ、coordinator の答えの違いが gone に分かれる: 置かれていない 404・中身の違いは待っても届かない(真)、
  ;; 5xx・届かないは次の拍で取り直す(偽)。置き場のキーの無い job は欠けに数えない。
  (<- transport (served-programs []))
  (val link (LinkRig "http://coord" "node-h" #("net") 60000 :task-dir (str (/ tmp-path "tasks")) :transport transport))
  (<- good (service-spec "good" SHA))
  (<- forged (service-spec "forged" FORGED))
  (<- absent (service-spec "absent" ABSENT))
  (<- busy (service-spec "busy" BUSY))
  (<- unreached (service-spec "unreached" UNREACHED))
  (<- plain (service-spec "plain" None))
  (val specs #(good forged absent busy unreached plain))
  (setv link.state.last-jobs specs)
  ;; 取り寄せの前(起動の直後)は全部が欠け — 在ると確かめていない物は無いと読む。
  (val before (.on-link link (ObservePrograms)))
  (assert (= (lfor gap before gap.job) ["good" "forged" "absent" "busy" "unreached"]) before)
  (assert (all (gfor gap before (is gap.failure None))) before)
  (.accept-programs link specs)
  (val after (.on-link link (ObservePrograms)))
  (assert (= (dfor gap after gap.job gap.failure.gone)
             {"forged" True "absent" True "busy" False "unreached" False})
          after))


(deftest test-a-job-is-started-only-after-its-program-is-at-hand
  ;; 実例の根: 手元に file が無いまま起こすと子の入口が終了 code 3 で落ち、coordinator に届かない間は落ち続けた。待って、取れた拍で起こす。
  (<- spec JobSpec (bare-spec "svc" SHA False))
  (val gap (ProgramGap :job "svc" :failure UNREACHABLE))
  (<- waiting tuple (planned-for spec #(gap)))
  (assert (= waiting #()) waiting)
  (<- arrived tuple (planned-for spec #()))
  (<- started bool (has-action arrived StartJob))
  (assert started arrived)
  ;; 別の job の欠けでは待たない。
  (<- others tuple (planned-for spec #((ProgramGap :job "other"))))
  (<- started-others bool (has-action others StartJob))
  (assert started-others others))


(deftest test-a-waiting-job-tells-the-reason-without-counting-a-failure
  ;; 待ちは失敗に数えない: 見送りの訳 program-wait・段階は準備のまま(coordinator の語彙を足さない)・文に取れない訳・続けて落ちた回数は 0。
  (<- spec JobSpec (bare-spec "svc" SHA False))
  (val world (WorldView #(TREE) #() :program-gaps #((ProgramGap :job "svc" :failure UNREACHABLE))))
  (<- holds tuple (start-holds NOW #(spec) world {} POLICY))
  (assert (= (. (get holds 0) hold) StartHold.PROGRAM-WAIT) holds)
  (<- report tuple (statuses NOW #(spec) world {} POLICY))
  (val row (get report 0))
  (assert (= row.phase JobPhase.PREPARING) row)
  (assert (in UNREACHABLE.detail row.detail) row)
  (assert (= row.failures 0) row))


(deftest test-a-refused-program-ends-a-task-but-does-not-crash-a-service
  ;; coordinator が渡さないと答えた Program: task は起こして、子の入口が TaskFailed を結果に書いて終わる(待つと送り手が結果を待ち続ける)。
  ;; service は起こさない(終わる結果が無く、落ちても起こし直すだけ)。届かないだけの task は待つ。
  (<- task JobSpec (bare-spec "task/a" SHA True))
  (<- ended tuple (planned-for task #((ProgramGap :job "task/a" :failure REFUSED))))
  (<- task-started bool (has-action ended StartJob))
  (assert task-started ended)
  (<- held tuple (planned-for task #((ProgramGap :job "task/a" :failure UNREACHABLE))))
  (assert (= held #()) held)
  (<- service JobSpec (bare-spec "svc" SHA False))
  (<- refused tuple (planned-for service #((ProgramGap :job "svc" :failure REFUSED))))
  (assert (= refused #()) refused))


(deftest test-the-old-process-keeps-running-until-the-new-program-is-at-hand
  ;; 入れ替えの空白を作らない: 新しい版の file が手元に無い間は、recreate の旧を止めず、handoff の旧を名から外さない。
  ;; 旧の spec は宣言と引数が違う(spec の比べは Program のキーを見ない — 違いは service の同一性の指紋が運ぶ)。
  (val old (replace (! (bare-spec "svc" SHA False)) :args #("old")))
  (val running (ProcessView "svc" old 1 42 0 :instance "1-1"))
  (val gap (ProgramGap :job "svc" :failure UNREACHABLE))
  (<- want JobSpec (bare-spec "svc" SHA False))
  (<- kept tuple (planned-for want #(gap) #(running)))
  (<- kept-stopped bool (has-action kept SignalJob))
  (assert (not kept-stopped) kept)
  (<- moved tuple (planned-for want #() #(running)))
  (<- moved-stopped bool (has-action moved SignalJob))
  (assert moved-stopped moved)
  (val handoff (replace want :handoff True))
  (<- handed tuple (planned-for handoff #(gap) #(running)))
  (<- handed-retired bool (has-action handed RetireJob))
  (assert (not handed-retired) handed)
  (<- retired tuple (planned-for handoff #() #(running)))
  (<- retired-now bool (has-action retired RetireJob))
  (assert retired-now retired))
