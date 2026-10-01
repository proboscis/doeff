;;; RemoteJob の契約テスト — 同じ effect に答える本物(remote-cluster → coordinator の /programs・/tasks、担い手 RigWorker)と
;;; fake(sim-cluster の送り手の口 — 本物の coordinator の調停ループと本物の run-worker・偽の宿)が、同じ deftest を通る。
;;; 解釈器の組み立ては coordinator_contract_handlers.hy。
;;;
;;;   * 答え: task の Program の戻り値が呼び手へ戻る
;;;   * 断り: task の Program が投げた例外は、その例外そのもの(型と文)が呼び手へ届く
;;;   * 呼び手の handler を継がない: 呼び手が並べた reader は task の Program に届かず、答えの無い Ask で task が落ち、呼び手へ例外が届く
;;;   * 送った job が coordinator の側に同じ形で載る(name・needs・environ・切り離していない)・終われば coordinator から落とす
;;;   * :environ の値は task の Program の名の Ask に字面どおり届く(JSON の object の文字列も解かない)
;;;   * 送れない Program(handler の値を捕まえた Program)は送る前に断り、coordinator に何も載らない
;;;   * 呼び手が取り消せば coordinator から task を落とす
;;;   * 実行環境を宣言する送り手(-env の組)の task は、coordinator の行に同じ宣言を載せる
;;; 契約の外: coordinator に届かない・断られた時の例外の型(sim は RemoteJobFailed・本物は httpx の例外 — local.hy の頭の註)・task の
;;; 置かれる時刻(担い手の拍と準備の拍が違う)・子が受け取る文脈(本物の側の担い手 RigWorker は子 process の文脈を持たない — 本物の
;;; ProcessHost と sim の宿の文脈は test_job_context.hy の検が同じ関数で比べる)。
(require doeff-hy.macros [defk deftest <- val])
(import doeff [with-handlers])
(import doeff_core_effects.handlers [reader])
(import doeff_core_effects.scheduler [Spawn Wait Cancel Task TaskCancelledError])
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.remote_model [RemoteJob UnsendableProgram])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv runtime-env->json])
(import tests.coordinator_contract_handlers [TasksSeen TaskSeen contract-env])
(import tests.detached_rig [slow-add RIG-PROVIDES])
(import tests.fixtures.entry_programs [answer-base based-add boom-program environ-read])
(import tests.fixtures.services [bare-program holding-program])

(val LOCAL (frozenset RIG-PROVIDES))
(val NAME "contract-task")
;; 走っている間に coordinator の行を読む時刻と、走らせる長さ(仮想の秒 — 両方の組で task が置かれてから終わるまでの間に読む)。
(val OBSERVE-AT 5.0)
(val RUN-SECONDS 30.0)


(deftest test-the-answer-of-the-task-comes-back
  {:interpreters ["remote-cluster" "sim-cluster"]}
  (<- value int (RemoteJob (based-add 5) :needs LOCAL :name NAME))
  (assert (= value 105) value))


(deftest test-the-exception-of-the-task-comes-back-as-itself
  {:interpreters ["remote-cluster" "sim-cluster"]}
  (var caught None)
  (try
    (<- (RemoteJob (boom-program) :needs LOCAL :name NAME))
    (except [error ValueError]
      (:= caught error)))
  (assert (isinstance caught ValueError) caught)
  (assert (= (str caught) "業務の失敗 base=100") caught))


(deftest test-the-task-does-not-inherit-the-handlers-of-the-caller
  {:interpreters ["remote-cluster" "sim-cluster"]}
  ;; 呼び手は base = 1 の reader を並べて送る。handler を並べない task の Ask "base" に答える物は実行先に無く、task は落ちる
  ;; (呼び手の handler を継げば黙って 1 + 1 = 2 と答える)。
  (var failure None)
  (try
    (<- answered (with-handlers [(reader {"base" 1})] (RemoteJob (bare-program 1) :needs LOCAL :name NAME)))
    (:= failure (.format "呼び手の reader が task に届いた: {}" answered))
    (except [error Exception]
      (:= failure error)))
  (assert (isinstance failure Exception) failure)
  (assert (in "Ask" (str failure)) (str failure)))


(defk observe-running-task [program]
  {:pre [(: program Task)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "走っている task の coordinator の行を OBSERVE-AT 秒目に読み、task の答えを待ち、終わった後の行も読むため。答え = #(走っている間の行
   答え 終わった後の行)。"
  (<- (Delay OBSERVE-AT))
  (<- running tuple (TasksSeen))
  (<- value (Wait program))
  (<- after tuple (TasksSeen))
  #(running value after))


(deftest test-the-sent-task-sits-on-the-coordinator-in-the-same-shape-and-leaves-when-done
  {:interpreters ["remote-cluster" "sim-cluster"]}
  (<- task Task (Spawn (RemoteJob (slow-add RUN-SECONDS 1) :needs LOCAL :name NAME :environ {"GREETING" "hi" "A" "1"})))
  (<- seen tuple (observe-running-task task))
  (val running (get seen 0))
  (val value (get seen 1))
  (val after (get seen 2))
  (assert (= running #((TaskSeen :name NAME :needs #("local") :environ #(#("A" "1") #("GREETING" "hi")) :runtime-env None
                                 :detached False)))
          running)
  (assert (= value 101) value)
  (assert (= after #()) after))


(deftest test-the-environ-reaches-the-task-as-the-literal-value
  {:interpreters ["remote-cluster" "sim-cluster"]}
  (val literal "{\"a\": 1}")
  (<- value str (RemoteJob (environ-read "GREETING") :needs LOCAL :name NAME :environ {"GREETING" literal}))
  (assert (= value literal) value))


(deftest test-an-unsendable-program-is-refused-before-sending
  {:interpreters ["remote-cluster" "sim-cluster"]}
  (var refused False)
  (try
    (<- (RemoteJob (holding-program answer-base 1) :needs LOCAL :name NAME))
    (except [UnsendableProgram]
      (:= refused True)))
  (<- seen tuple (TasksSeen))
  (assert refused "handler の値を捕まえた Program が送られた")
  (assert (= seen #()) seen))


(deftest test-cancelling-the-caller-drops-the-task
  {:interpreters ["remote-cluster" "sim-cluster"]}
  (<- task Task (Spawn (RemoteJob (slow-add (* 4 RUN-SECONDS) 1) :needs LOCAL :name NAME)))
  (<- (Delay OBSERVE-AT))
  (<- running tuple (TasksSeen))
  (<- (Cancel task))
  (try
    (<- (Wait task))
    (except [TaskCancelledError]
      None))
  (<- (Delay OBSERVE-AT))
  (<- after tuple (TasksSeen))
  (assert (= (len running) 1) running)
  (assert (= after #()) after))


(deftest test-a-sender-with-a-runtime-env-puts-the-declaration-on-the-task
  {:interpreters ["remote-cluster-env" "sim-cluster-env"]}
  ;; 送り手の実行環境の宣言(本番の TaskSender の runtime-env)は task の行に載り、coordinator はその env の root を準備した担い手へ置く
  ;; (置く・走らせるは組ごとに違う — 本物の側の担い手 RigWorker は env を準備しない)。行を読んだら取り消す。
  (<- env RuntimeEnv (contract-env))
  (<- declared dict (runtime-env->json env))
  (<- task Task (Spawn (RemoteJob (slow-add (* 4 RUN-SECONDS) 1) :needs LOCAL :name NAME)))
  (<- (Delay OBSERVE-AT))
  (<- running tuple (TasksSeen))
  (<- (Cancel task))
  (try
    (<- (Wait task))
    (except [TaskCancelledError]
      None))
  (assert (= running #((TaskSeen :name NAME :needs #("local") :environ #() :runtime-env declared :detached False))) running))
