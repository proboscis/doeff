;;; worker の入口の検め(probe)の言い換え(handlers.hy の ProbeStore を置き換えた・#2465)— ProbeEntry・ForgetProbes と観測 ObserveProbes を、
;;; 汎用の子 process の効果(StartProcess・PollProcess・StopProcess・ReadEnvironment)と file system の効果(MakeDirectory・ReadText・
;;; RemoveTree)へ言い換える。I/O を持たない — 本物は外側の subprocess-handler と os-file-handler。
;;;
;;; 振る舞いは前の ProbeStore と同じ(判断は worker/core/probe_rules):
;;;   * 同じ木の検めは 1 本ずつ(probe-launches)— 同じ拍に来た同じ木の spec は 1 本の process にまとめ、対象ごとの結果の行から読み込めない
;;;     理由をその対象を持つ spec にだけ付ける。ProbeEntry は束に積むだけで、process は ObserveProbes の頭で起こす。
;;;   * 検めは shim の下で新しい process group に起こし(worker が消えれば shim が標準入力の EOF で group を止める — hold-stdin)、束が
;;;     終わったら(通った・失敗した・時間切れ・shim が先に死んだのどれでも)group ごと止めて回収する(reap-group・StopProcess)。
;;;   * timeout-seconds を越えた束は止め、結果の出た対象は結果どおり・進んでいた対象を持つ spec だけ時間切れの FAILED・残りの spec は
;;;     待ちへ戻す(probe-settle)。時間切れの spec の撃ち直しは単独の束で起こす。結果の鍵は spec-hash。回数と前の回の失敗の理由は
;;;     撃ち直しの間も持つ。
;;;   * 束の標準出力と標準エラーは probe-dir の下の file に受け(pipe は溜まると子が止まる)、束が終わったら読んで消す。
;;; 記録(待ち・走っている束・答え・回数・前の回の失敗・時間切れの印)は handler の session の値で持つ。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass])
(import doeff_core_effects.file_effects [MakeDirectory ReadText RemoveTree file-done])
(import doeff_core_effects.process_effects [ReadEnvironment StartProcess PollProcess StopProcess ProcessNotStarted ProcessExited])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.job_rules [spec-hash])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeLayout ProbeEntry ForgetProbes ProbeState ProbeView])
(import doeff_cluster.worker.protocol.observations [ObserveProbes])
(import doeff_cluster.worker.core.worker_rules [probe-refusal])
(import doeff_cluster.worker.core.launch [JobLaunch CHILD-ENV-ALLOWED CHILD-ENV-PREFIXES])
(import doeff_cluster.worker.core.probe_rules [PROBE-SECONDS PROBE-STOP-GRACE probe-targets probe-launches probe-reason probe-results
                                               probe-settle probe-command ProbeSettle])


(defrecord ProbeSettings
  "入口の検めの起こし方の設定(worker の組み立ての入口 main が作る): python = shim を起こす interpreter・hy-command = 木の job の hy・
   uv = 実行環境の job の uv・layout = 業務の repo の木の形・probe-dir = 実行環境の job の検めの cwd と束の出力の file の dir・
   timeout-seconds = 束 1 本の時間の上限。"
  (#^ str python)
  (#^ str hy-command)
  (#^ str uv)
  (#^ CodeLayout layout)
  (#^ str probe-dir)
  (setv #^ (| int float) timeout-seconds PROBE-SECONDS))


(defrecord ProbeRun
  "走っている検めの束 1 本(木ごとに 1 本): pid = shim の process(group の先頭)・specs = 束の spec・targets = 束の対象(検める順)・
   runtime-env = 実行環境の宣言・started-ms = 起こした時刻(epoch ms)・out / err = 標準出力と標準エラーを受ける file。"
  (#^ int pid)
  (#^ tuple specs)
  (#^ tuple targets)
  (#^ (| str None) runtime-env)
  (#^ int started-ms)
  (#^ str out)
  (#^ str err))


(defrecord ProbeOutcome
  "束が終わった後の spec 1 つの行き先: kind = 結果どおりに決まった・時間切れ・待ちへ戻す・reason = 決まった時の読み込めない理由
   (None = 読み込めた)・timed-out-detail = 時間切れの理由の文・ended-ms = 片づけた時刻。"
  (#^ JobSpec spec)
  (#^ ProbeSettle kind)
  (#^ (| str None) reason)
  (#^ str timed-out-detail)
  (#^ int ended-ms))


(defk launch-probe [settings batch specs n]
  {:pre [(: settings ProbeSettings) (: batch tuple) (: specs tuple) (: n int)] :post [(: % ProbeRun)]
   :tags {:context "worker" :role "protocol"}}
  "束 1 本を起こすため: 束の spec の対象を重ねずに並べ、shim を group の先頭にして 1 つの process で検める。n = 出力の file の名の番号。"
  (val code-path (get batch 0))
  (val runtime-env (get batch 1))
  (val targets (tuple (dict.fromkeys (gfor spec specs target (probe-targets spec) target))))
  (var allowed #())
  (when runtime-env
    (<- read tuple (ReadEnvironment (tuple (sorted CHILD-ENV-ALLOWED)) :prefixes CHILD-ENV-PREFIXES))
    (:= allowed read))
  (<- plan JobLaunch (probe-command code-path runtime-env targets :hy-command settings.hy-command :uv settings.uv :layout settings.layout
                                    :allowed-env (dfor e allowed e.name e.value) :probe-dir settings.probe-dir))
  (val runs-dir (+ settings.probe-dir "/runs"))
  (<- (file-done (MakeDirectory runs-dir)))
  (val out (.format "{}/{}.out" runs-dir n))
  (val err (.format "{}/{}.err" runs-dir n))
  (<- started-ms int (now-epoch-ms))
  (<- answer (StartProcess :argv (+ #(settings.python "-B" "-m" "doeff_cluster.shim" PROBE-STOP-GRACE "--") plan.argv)
                           :cwd plan.cwd :env plan.env :env-mode plan.env-mode :stdout-path out :stderr-path err
                           :process-group True :hold-stdin True :reap-group True))
  (when (isinstance answer ProcessNotStarted)
    (raise (OSError answer.detail)))
  (ProbeRun :pid answer.pid :specs specs :targets targets :runtime-env runtime-env :started-ms started-ms :out out :err err))


(defk finish-probe [settings run code]
  {:pre [(: settings ProbeSettings) (: run ProbeRun) (: code (| int None))] :post [(: % tuple)]
   :tags {:context "worker" :role "protocol"}}
  "束を片づけて spec ごとの行き先(ProbeOutcome の tuple)を返すため。code = 終了の番号(None = 時間切れ — 止めて回収する)。終わった束は
   回収の時に group の残りを止めている(reap-group)。出力の file を読んで消す。"
  (when (is code None)
    (<- (StopProcess :pid run.pid :stop-grace 0.0)))
  (<- stdout (ReadText run.out))
  (<- stderr (ReadText run.err))
  (<- (RemoveTree run.out))
  (<- (RemoveTree run.err))
  (val results (probe-results (if (isinstance stdout str) stdout "")))
  (val stuck (next (gfor t run.targets :if (not-in t results) t) None))
  (val crash (if (or (is code None) (= code 0)) None (probe-reason code (if (isinstance stderr str) stderr ""))))
  (<- now-ms int (now-epoch-ms))
  (val detail (.format "入口の検めが {} 秒で終わらない(process group ごと止めた): {} の読み込みの途中" settings.timeout-seconds stuck))
  (tuple (gfor spec run.specs
               :setv settled (probe-settle (probe-targets spec) results (is code None) stuck crash)
               (ProbeOutcome :spec spec :kind settled.kind :reason settled.reason :timed-out-detail detail :ended-ms now-ms))))


(defhandler probe-host [#^ ProbeSettings settings]
  ;; 引数に残す理由: 検めの起こし方と時間の上限は worker の process ごとの設定(main が引数から作る)。
  ;; 記録: 束の鍵 #(木 実行環境の宣言 単独の spec-hash か None)→ 待っている spec の tuple(来た順)・木 → 走っている束・spec-hash → 答え・
  ;; spec-hash → 検めた回数・前の回の失敗の理由・直前の検めが時間切れだった spec-hash(次は単独で起こす)・起こした束の数。
  (session var waiting {})
  (session var runs {})
  (session var done {})
  (session var attempts {})
  (session var last-failure {})
  (session var timed-out (frozenset))
  (session var launched 0)
  (ProbeEntry [spec code-path]
    ;; 前の ProbeStore.start と同じ: 待っているか走っている spec はそのまま。前の答えが失敗なら理由を持ち越し、回数を増やす。旧い形の
    ;; service の spec は process を起こさずに失敗とする(計画 2.8 の入口 15)。直前が時間切れの spec は単独の束。
    (val key (spec-hash spec))
    (val in-flight (or (any (gfor specs (.values waiting) s specs (= (spec-hash s) key)))
                       (any (gfor run (.values runs) s run.specs (= (spec-hash s) key)))))
    (when (not in-flight)
      (val prior (.get done key))
      (when (and (is-not prior None) (= prior.state ProbeState.FAILED))
        (:= last-failure (| last-failure {key prior.detail})))
      (:= done (dfor #(k v) (.items done) :if (!= k key) k v))
      (:= attempts (| attempts {key (+ (.get attempts key 0) 1)}))
      (val refusal (probe-refusal spec))
      (if (is-not refusal None)
          (do (<- now-ms int (now-epoch-ms))
              (:= done (| done {key (ProbeView key ProbeState.FAILED :detail refusal :failed-ms now-ms :started-ms now-ms
                                               :attempts (.get attempts key 1) :last-failure (.get last-failure key ""))})))
          (do (val batch #(code-path spec.runtime-env (if (in key timed-out) key None)))
              (:= timed-out (- timed-out #{key}))
              (:= waiting (| waiting {batch (+ (.get waiting batch #()) #(spec))})))))
    (resume None))
  (ForgetProbes [keep]
    ;; 宣言から消えた spec の記録を落とす(前の ProbeStore.forget と同じ)。走っている束の process は止めない(終わった後の答えは次の
    ;; 拍の観測に出て、その拍の ForgetProbes で落ちる)。
    (val running (frozenset (gfor run (.values runs) s run.specs (spec-hash s))))
    (val stays (fn [k] (or (in k keep) (in k running))))
    (:= waiting (dfor #(batch specs) (.items waiting)
                      :setv kept (tuple (gfor s specs :if (in (spec-hash s) keep) s))
                      :if kept
                      batch kept))
    (:= done (dfor #(k v) (.items done) :if (stays k) k v))
    (:= attempts (dfor #(k v) (.items attempts) :if (stays k) k v))
    (:= last-failure (dfor #(k v) (.items last-failure) :if (stays k) k v))
    (:= timed-out (frozenset (gfor k timed-out :if (stays k) k)))
    (resume None))
  (ObserveProbes []
    ;; 待っている束を起こす(木ごとに 1 本 — 走っている木の束は、その終わりを待つ)。
    (for [batch (probe-launches (list waiting) (frozenset runs))]
      (val specs (get waiting batch))
      (:= waiting (dfor #(k v) (.items waiting) :if (!= k batch) k v))
      (:= launched (+ launched 1))
      (<- run ProbeRun (launch-probe settings batch specs launched))
      (:= runs (| runs {(get batch 0) run})))
    ;; 終わった束と時間切れの束を片づけ、spec ごとの行き先を記録に置く。
    (<- now-ms int (now-epoch-ms))
    (for [#(tree run) (list (.items runs))]
      (<- polled (PollProcess run.pid))
      (val code (if (isinstance polled ProcessExited) polled.exit-code None))
      (when (or (isinstance polled ProcessExited) (> (- now-ms run.started-ms) (* settings.timeout-seconds 1000)))
        (<- outcomes tuple (finish-probe settings run code))
        (:= runs (dfor #(k v) (.items runs) :if (!= k tree) k v))
        (for [o outcomes]
          (val key (spec-hash o.spec))
          (val view (fn [state #** fields] (ProbeView key state :started-ms run.started-ms :attempts (.get attempts key 1)
                                                      :last-failure (.get last-failure key "") #** fields)))
          (match o.kind
            ProbeSettle.REQUEUE
              ;; 結果の出る前に束が止まった spec は、回数を増やさずに待ちへ戻す(次の束で検める)。
              (do (val again #(tree run.runtime-env None))
                  (:= waiting (| waiting {again (+ (.get waiting again #()) #(o.spec))})))
            ProbeSettle.TIMED-OUT
              (do (:= timed-out (| timed-out #{key}))
                  (:= done (| done {key (view ProbeState.FAILED :detail o.timed-out-detail :failed-ms o.ended-ms)})))
            _ (:= done (| done {key (if (is o.reason None)
                                        (view ProbeState.PASSED)
                                        (view ProbeState.FAILED :detail o.reason :failed-ms o.ended-ms))}))))))
    (resume (tuple (+ (lfor specs (.values waiting) s specs
                            (ProbeView (spec-hash s) ProbeState.QUEUED :attempts (.get attempts (spec-hash s) 1)
                                       :last-failure (.get last-failure (spec-hash s) "")))
                      (lfor run (.values runs) s run.specs
                            (ProbeView (spec-hash s) ProbeState.RUNNING :started-ms run.started-ms :attempts (.get attempts (spec-hash s) 1)
                                       :last-failure (.get last-failure (spec-hash s) "")))
                      (list (.values done)))))))
