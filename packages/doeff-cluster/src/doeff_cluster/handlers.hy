;; worker の coordinator との連絡(CoordinatorLink — heartbeat・名指しの待ち・task と Program の受け取り・lease の返し)と、宣言の job と task を
;; JobSpec へ読む口。コードの木・実行環境の root・子 process・入口の検め・状態の file・世界の観測のまとめは worker/protocol の言い換えへ移した
;; (#2464〜#2469)。CoordinatorLink の移しは #2427。
(require doeff-hy.macros [defhandler defk deff <- val])
(require doeff-hy.record [defrecord])
(import json os re sys threading time uuid)
(import httpx)
(import pathlib [Path])
(import urllib.parse [quote :as url-quote])
(import doeff_cluster.foundation.coordinator_http [CoordinatorEndpoint REPLY-SECONDS])
(import doeff_cluster.worker.core.beat_policy [WatchKind WatchReading beat-interval-ms heartbeat-due watch-params watch-reading reply-revision
                      WATCH-RETRY-SECONDS WAKE-HOLD-SECONDS])
(import doeff [run])
(import doeff_cluster.shared.intent.protocol [PROTOCOL-FORMAT])
(import doeff_cluster.shared.core.capabilities [environ-pairs])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import .job_context [process-context-environ])
(import doeff_cluster.shared.intent.remote_model [program-sha])
(import doeff_cluster.shared.intent.runtime_env_model [runtime-env-of-json env-key current-platform])
(import doeff_cluster.shared.intent.semaphore_model [SEMAPHORE-PREFIX])
(import doeff_cluster.shared.core.lease_rules [drop-holders lease-holder holder-tokens-prefix])
(import doeff_cluster.worker.protocol.heartbeat [env-heartbeat-part heartbeat-body status-report status-row])
(import doeff_cluster.worker.core.heartbeat_rules [warm-env-of-row finished-task-id desired-when-unreachable])
(import doeff_cluster.foundation.ready_file [write-ready-file])
(import doeff_cluster.worker.core.launch [program-file])
(import doeff_cluster.worker.intent.worker_model [EnvReport DesiredJobs DesiredUnreadable ReadDesired PublishStatus WarmEnv ReleaseLeases]
        doeff_cluster.shared.intent.job_model [JobSpec] doeff_cluster.worker.core.worker_rules [ENV-KEY-PREFIX])

(defn #^ tuple env-placement [#^ (| dict None) declared #^ str revision]  ; defk にできない: 宣言の読み(Program の外の I/O の道具)が呼ぶ
  "job の宣言の runtimeEnv(在れば)と版 → #(版 宣言の JSON の正規化した文字列 env のキー)。実行環境の job(task も service も —
   2026-09-26)は、env のキー(この worker の platform で計算)を root の置き場の鍵にする。版は宣言のまま運ぶ — coordinator が同じ
   宣言から計算する版と指紋に合わせるため(版を持たない task だけは \"env-<キー>\" を版の代わりにする)。無ければ版のまま。"
  (if (is declared None)
      #(revision None None)
      (do (setv key (run (env-key (run (runtime-env-of-json declared)) (current-platform))))
          #((or revision (+ ENV-KEY-PREFIX key)) (json.dumps declared :sort-keys True :ensure-ascii False) key))))


(defn #^ JobSpec declared-job-spec [#^ dict job]  ; defk にできない: 宣言の読み(Program の外の I/O の道具)が呼ぶ
  "heartbeat の返事の job 1 本 → worker が起動する形(runtimeEnv を持つ service は env の root で起こす)。worker が job を受けるのは
   coordinator からだけ(宣言の file を直に読む口は無い — ADR-DOE-CLUSTER-001 R1)。"
  (setv #(revision runtime key) (env-placement (.get job "runtimeEnv") (get job "revision")))
  (JobSpec (get job "name") (get job "entry") (tuple (.get job "args" [])) revision
           :once (.get job "once" False) :placement (.get job "placement")
           :handoff (bool (.get job "handoff" False))
           :ready-instance (.get job "readyInstance") :runtime-env runtime :env-key key
           ;; 入れ替えの諦め(coordinator の期限 — 返事の handoff の job だけが持つ・無ければ偽)。
           :handoff-abandoned (bool (.get job "handoffAbandoned" False))
           ;; Program の job(改訂 1 の F・G): 詰めた Program の置き場のキーと、子の環境変数。
           :program (.get job "program")
           :environ (environ-pairs (.get job "environ" {}))))


(deff write-program-file [#^ Path program-dir #^ str sha #^ str blob #^ dict versions]  ; defk にできない: worker の I/O の道具(CoordinatorLink)が呼ぶ
  {:pre [(: program-dir Path) (: sha str) (: blob str) (: versions dict)] :post [(: % Path)] :tags {:context "doeff-cluster" :role "foundation"}}
  "coordinator の /programs/<sha> から取った詰めた Program を、子の入口(job_entry の read-program)が読む形の cache の file に書くため。
   形の定義点はここ 1 つ({\"blob\" \"versions\"} — service と task で同じ)。書きかけを子に見せないよう rename で置く。"
  (let [path (program-file program-dir sha)
        tmp (Path (+ (str path) ".tmp"))]
    (.mkdir program-dir :parents True :exist-ok True)
    (.write-text tmp (json.dumps {"blob" blob "versions" versions}) :encoding "utf-8")
    (os.replace tmp path)
    path))

(setv JOB-ENTRY "doeff_cluster.job_entry")


(defn #^ JobSpec task-spec [#^ dict task #^ Path task-dir]
  "coordinator が割り当てた task 1 本 → 1 度だけ走らせる job。結果はこの worker の file(名前は task の id で決まる)。詰めた Program は
   service の job と同じく置き場のキー program(sha)で持ち、CoordinatorLink.accept-programs が /programs/<sha> から cache へ取り、
   ProcessHost が `--program <cache の file>` を足す(入口は `task --result <file> --program <file>` — 版は file の中の versions)。
   実行環境の task(runtimeEnv を持つ)は、env のキー(この worker の platform で計算)を root の置き場の鍵にする(env-placement)。"
  (setv id (get task "id"))
  (setv #(revision runtime key) (env-placement (.get task "runtimeEnv") (get task "revision")))
  (JobSpec (+ "task/" id) JOB-ENTRY
           #("task" "--result" (str (/ task-dir f"{id}.result")))
           revision :once True :detached (bool (.get task "detached" False)) :runtime-env runtime :env-key key
           :program (get task "program")
           ;; 子の環境変数(service の job と同じ欄・同じ路 — ProcessHost.launch が宣言の env-vars の上に重ねる)。
           :environ (environ-pairs (.get task "environ" {}))))


(defclass WatchState []
  "背景の待ちの thread と拍(CoordinatorLink.poll)が分ける状態(#1933 — beat_policy)。after = 次の待ちの版(前の heartbeat の返事の
   版・lock で守る)・confirmed = 待ちが 1 度答えた(口を確かめた)・unsupported = 待つ口が無い(404)・woken = 待ちが「変わった」と
   答えた印・beat-done = heartbeat が届いた合図・closing = 止めの合図・failure = thread が止まった理由・told = 最後に出した 1 行の鍵。"
  (defn #^ None __init__ [self]
    (setv self.lock (threading.Lock) self.after None self.confirmed False self.unsupported False
          self.woken (threading.Event) self.beat-done (threading.Event) self.closing (threading.Event)
          self.failure "" self.told None))

  (defn #^ (| int None) after-now [self]
    "次の待ちの版を読むため(拍の thread が書き換える)。"
    (with [self.lock]
      (setv after self.after))
    after)

  (defn #^ None beaten [self #^ (| int None) revision]
    "heartbeat が届いた時に、次の待ちの版を返事の版にし、起こした後の待ちを解くため。"
    (with [self.lock]
      (setv self.after revision))
    (.set self.beat-done))

  (defn #^ None advance [self #^ int after #^ int revision]
    "「変わっていない」と答えた待ちの版へ進めるため(その間に heartbeat が版を書き換えていれば、そちらを残す)。"
    (with [self.lock]
      (when (= self.after after)
        (setv self.after revision))))

  (defn #^ None confirm [self]
    "待ちが答えた(待つ口を使える)と記すため。"
    (setv self.confirmed True))

  (defn #^ None tell-watch [self #^ str key #^ str line]
    "待ちの結果の変わり目だけを stderr へ 1 行出すため(同じ失敗を拍ごとに繰り返さない)。"
    (when (!= key self.told)
      (setv self.told key)
      (print line :file sys.stderr :flush True))))


(defclass CoordinatorLink []
  "coordinator との連絡。heartbeat で生存・版・状態(終わった task の結果を含む)を送り、自分に割り当てられた job と task を受け取る。
   task の blob は task-dir の file に置き、宣言から外れた task の file は消す(この worker が書いた物だけ)。"
  (defn #^ None __init__ [self #^ str url #^ str name #^ tuple provides #^ int capacity #^ int fence-ms
                  #^ (| str None) [task-dir None] #^ (| dict None) [versions None] #^ (| httpx.BaseTransport None) [transport None] #^ (| dict None) [tools None] #^ bool [handles-envs False]
                  #^ tuple [exclusive #()] #^ str [node ""] #^ bool [watch False]]
    ;; watch = heartbeat を拍から切り離し、desired の変化を名指しの待ち(GET /watch)で受けるか(#1933 — beat_policy)。真なら返事に版を
    ;; 持つ coordinator に背景の thread で待ちを送り続け、heartbeat は beat_policy.heartbeat-due の時だけ送る。偽(既定 — 検の道具の
    ;; link)なら今までどおり拍ごとに送る。本番の worker の入口(main.hy)が真にする。
    ;; node = この worker の置かれた k8s の node の名(downward API の spec.nodeName・k8s の外の機体は空)。coordinator がその node の
    ;; label から能力(company-machine など)を導く — worker の自己申告にしない(改訂 1 の I)。
    ;; provides / exclusive = この worker が提供する能力・専用の能力の名(名の順 — cluster_model.capabilities-of・ADR-DOE-CLUSTER-001 R4b)。
    ;; tools = この worker が名乗る道具(名 → 版 — 実行環境の宣言の tools と照らして置き先を選ぶ)。
    ;; handles-envs = 実行環境の job を扱う worker か(真なら、準備済み・準備中・失敗の root と disk の条件を heartbeat で名乗り、温める表を
    ;; 受ける)。名乗りの中身 env-report は、拍ごとに coordinator-desired が root の言い換えへ EnvReport で問うて置く(#2467)。
    (setv self.name name self.provides provides self.exclusive exclusive self.node node self.capacity capacity self.tools (or tools {})
          self.handles-envs handles-envs self.env-report None
          self.last-warm #() self.warm-keys {}
          self.fence-ms fence-ms self.statuses []
          ;; 宛先は `,` で並べた物(前ほど優先)。毎拍やり直すので一巡以上は送り直さない(拍を塞がない)・接続は使い回す。
          ;; 自己停止を数える last-ok は宛先と無関係にこの link が持つので、宛先を替えても途絶の数え方は続く。
          self.endpoint (CoordinatorEndpoint url REPLY-SECONDS 0 :transport transport :actor name)
          self.task-dir (Path (or task-dir "tasks")) self.versions (or versions {})
          ;; 詰めた Program の cache(/programs/<sha> から取る — ProcessHost と同じ state dir の programs・改訂 1 の F)。
          self.program-dir (/ (. (Path (or task-dir "tasks")) parent) "programs")
          ;; 起動した時点を最後の連絡とみなす: 一度も届かない worker は fence の後に何も動かさない。
          self.last-ok (time.monotonic)
          ;; 最後に受け取った job と task の宣言(途絶の間も動かす書き手と切り離した task を選ぶ — worker_policy.kept-when-cut-off)。
          self.last-jobs #()
          self.last-tasks #()
          ;; この process の世代(heartbeat の boot)。coordinator は drain を頼まれた時の世代に付け、別の世代(Pod を作り直した後の
          ;; worker)の heartbeat で drain を解く(cluster_policy.absorb-boot・2026-09-25)。
          self.boot (. (uuid.uuid4) hex)
          ;; この process の起動時刻(heartbeat の bootAt・epoch ms — 2026-09-27)。世代を決める所で 1 回だけ決める。coordinator は
          ;; 同じ名の 2 つの世代の新旧を、両方の起動時刻を知る時はこれで決める(cluster_policy.generation-order)。
          self.boot-at (int (* (time.time) 1000))
          ;; 切り離した task の id → 置かれた時の返事の行(blob を除く)。状態の報告に写して添え、状態を失った coordinator が
          ;; 走っている task を引き取れるようにする(cluster_policy.adopt-running-detached・2026-09-27)。
          self.task-echo {}
          ;; 最後に stderr へ出した heartbeat の結果(None = まだ出していない・"" = 名乗れた・それ以外 = 名乗れない理由)— tell。
          self.told None
          ;; --- heartbeat の切り離し(#1933 — beat_policy)---
          ;; watch-state = 背景の待ちの thread と拍(poll)の間で分ける状態(lock で守る)。last-desired = 前の heartbeat の返事の desired
          ;; (届かなかったら None — 次の拍で必ず送る)・sent-statuses = 前に届けた状態の報告・beat-interval-ms = 送る間隔。
          self.watch-enabled watch
          self.watch-state (WatchState)
          self.watch-endpoint (CoordinatorEndpoint url REPLY-SECONDS 0 :transport transport :actor name)
          self.watcher None
          self.last-desired None
          self.sent-statuses None
          self.beat-interval-ms (beat-interval-ms None {}))
    ;; 世代を Pod の中の file へ書く(DOEFF_WORKER_BOOT_FILE)— readinessProbe が「coordinator の見る worker がこの Pod の物か」を
    ;; 比べる(drain_client.ready-of)。同じ node の前の Pod と名が同じなので、名だけでは見分けられない。
    (setv boot-file (os.environ.get "DOEFF_WORKER_BOOT_FILE"))
    (when boot-file
      (setv tmp (Path (+ boot-file ".tmp")))
      (.write-text tmp (+ self.boot "\n") :encoding "utf-8")
      (os.replace tmp boot-file)))

  (defn #^ tuple accept-tasks [self #^ list tasks]
    "heartbeat の返事の task の行 → 1 度だけ走らせる job。task ごとに、その Program の置き場のキーを印の file <id>.program に残し
     (返事から外れた task の Program の cache を後で消すため — accept-programs)、返事から外れた task の結果の file を消す(この worker が
     書いた物だけ)。切り離した task は返事の行をそのまま写しとして持つ(状態の報告に添える — 欄 task)。"
    (.mkdir self.task-dir :parents True :exist-ok True)
    (setv ids (sfor t tasks (get t "id")))
    (for [task tasks]
      (setv mark (/ self.task-dir (+ (get task "id") ".program")))
      ;; 同じ id の印が別の sha を指していれば書き直す(印は cache の掃除にだけ使う — 子へ渡す file は返事の行の sha で決まる)。
      (when (!= (if (.exists mark) (.read-text mark :encoding "ascii") None) (get task "program"))
        (.write-text mark (get task "program") :encoding "ascii")))
    ;; .blob = 詰めた Program を行に持っていた版の worker が書いた file(置き場 /programs の前)— 残っていれば一緒に消す。
    (for [entry (.iterdir self.task-dir)]
      (when (and (in entry.suffix #(".blob" ".result")) (not-in entry.stem ids))
        (.unlink entry :missing-ok True)))
    (setv self.task-echo (dfor task tasks :if (.get task "detached") (get task "id") (dict task)))
    (tuple (gfor task tasks (task-spec task self.task-dir))))

  (defn #^ None accept-programs [self #^ tuple specs]
    "宣言の job と task のうち、cache に無い詰めた Program を coordinator の /programs/<sha> から取って書く(改訂 1 の F — service と
     task で同じ仕組み)。中身の sha256 がキーと合わない物は書かない。取れなければ書かずに次の拍で試し直す(子は file が無いので起動の
     時に理由つきで落ちる — task は結果の file に RemoteJobFailed)。返事から外れた task の Program の cache は、今の job と task の
     どれも参照していなければ消す(task ごとの印 <id>.program から引く — service の job の Program は消さない)。"
    (.mkdir self.program-dir :parents True :exist-ok True)
    (setv wanted (sfor s specs :if s.program s.program))
    (for [sha (sorted wanted)]
      (setv path (program-file self.program-dir sha))
      (when (not (.exists path))
        (try
          (setv response (.request self.endpoint "GET" (+ "/programs/" sha)))
          (.raise-for-status response)
          (setv body (.json response))
          (when (!= (program-sha (get body "blob")) sha)
            (raise (ValueError (+ "中身の sha256 がキーと合わない: " sha))))
          (write-program-file self.program-dir sha (get body "blob") (.get body "versions" {}))
          (except [error Exception]
            (print (.format "worker: Program {} を取れない: {!r}" sha error) :file sys.stderr :flush True)))))
    (setv current (sfor s specs :if (.startswith s.name "task/") (cut s.name 5 None)))
    (when (.exists self.task-dir)
      (for [mark (.glob self.task-dir "*.program")]
        (when (not-in mark.stem current)
          (setv sha (.strip (.read-text mark :encoding "ascii")))
          (when (and sha (not-in sha wanted))
            (.unlink (program-file self.program-dir sha) :missing-ok True))
          (.unlink mark :missing-ok True)))))

  (defn #^ dict env-body [self]
    "heartbeat に足す root の名乗り(実行環境を扱う worker だけ): platform・準備済み / 準備中 / 失敗の root・disk の条件
     (形は env-heartbeat-part — sim の宿と同じ関数)。"
    (if (or (not self.handles-envs) (is self.env-report None))
        {}
        (env-heartbeat-part self.env-report (current-platform))))

  (defn #^ tuple accept-warm [self #^ list rows]
    "heartbeat の返事の温める表の行 → この worker の root のキーの WarmEnv(キーは行ごとに 1 度だけ計算する — 計算は warm-env-of-row、
     sim の宿と同じ関数)。"
    (when (not self.handles-envs) (return #()))
    (setv out [])
    (for [row rows]
      (setv text (json.dumps (get row "runtimeEnv") :sort-keys True :ensure-ascii False))
      (when (not-in text self.warm-keys)
        (setv (get self.warm-keys text) (warm-env-of-row row (current-platform))))
      (.append out (get self.warm-keys text)))
    (tuple out))

  (defn #^ list report [self #^ tuple statuses]
    "状態の報告。終わった task には結果の file の中身(無ければ None = 結果なし)を添える。切り離した task には置かれた時の返事の行
     (blob を除く — 欄 task)を添える(形は status-report — sim の宿と同じ関数)。"
    (status-report statuses self.task-echo
                   ;; task の id は 1 度だけ読んで絞る(読み直すと型検査が None を絞れない — #1690)
                   (dfor s statuses
                         :setv task-id (finished-task-id s)
                         :if task-id
                         :setv result (/ self.task-dir (+ task-id ".result"))
                         task-id (if (.exists result) (.read-text result :encoding "ascii") None))))

  (defn #^ None tell [self #^ str outcome #^ str line]
    "heartbeat の結果の変わり目(初めて名乗れた・名乗れない理由が変わった・戻った)だけを stderr へ 1 行出すため。同じ結果の繰り返しは
     出さない。以前は断りも途絶も黙っていて、13 回目の本番の切り替えでは coordinator が heartbeat を 400 で断り続けたのに、worker の
     log は「起動します」の後に 32 分何も出さなかった(fence を越えると状態の file の note も空になる — 2026-09-29・#1005)。"
    (when (!= outcome self.told)
      (setv self.told outcome)
      (print line :file sys.stderr :flush True)))

  (defn #^ bool watching [self]
    "待ちの口を使えているか(heartbeat を拍から切り離してよいか): 待ちを使う link で、待ちの thread が生きていて、口を確かめ(最初の
     待ちが答えた)、404 でない。thread が思わぬ例外で止まっていれば、1 行出して毎拍の heartbeat に戻る。"
    (setv state self.watch-state)
    (when (and self.watcher (not (.is-alive self.watcher)) (not state.unsupported) (not (.is-set state.closing)))
      (.tell-watch state (+ "thread-dead:" state.failure)
                   (+ "worker: 待ちの thread が止まっている — 拍ごとの heartbeat に戻ります: " state.failure)))
    (bool (and self.watch-enabled (is-not self.watcher None) (.is-alive self.watcher) state.confirmed (not state.unsupported))))

  (defn #^ (| DesiredJobs DesiredUnreadable) poll [self]
    "拍ごとの ReadDesired に答えるため: heartbeat を送る拍(beat_policy.heartbeat-due)なら送り、それ以外は前の返事の desired を返す。"
    (setv silent-ms (int (* 1000 (- (time.monotonic) self.last-ok))))
    (if (heartbeat-due (.watching self) (is-not self.last-desired None) (.is-set self.watch-state.woken)
                       (!= self.statuses self.sent-statuses) silent-ms self.beat-interval-ms)
        (.beat self)
        self.last-desired))

  (defn #^ None start-watch [self]
    "返事に版を持つ coordinator へ、名指しの待ちを送り続ける背景の thread を 1 度だけ起こすため(待ちを使う link だけ)。"
    (when (and self.watch-enabled (is self.watcher None) (not self.watch-state.unsupported))
      (setv self.watcher (threading.Thread :target self.watch-loop :name (+ "watch-" self.name) :daemon True))
      (.start self.watcher)))

  (defn #^ None close [self]
    "worker の終わりに待ちの thread を止めるため(次の待ちを送らない — 送っている待ちは daemon の thread ごと捨てる)。"
    (.set self.watch-state.closing)
    (.set self.watch-state.beat-done)
    (when self.watcher
      (.join self.watcher 0.2)))

  (defn #^ WatchReading watch-once [self #^ int after #^ bool confirmed]
    "名指しの待ちを 1 回送り、答えを読むため(届かない・読めない返事も WatchReading の FAILED に畳む — thread を例外で落とさない)。"
    (try
      (setv response (.request self.watch-endpoint "GET" "/watch" :params (watch-params after self.name self.boot confirmed)))
      (watch-reading response.status-code (try (.json response) (except [ValueError] response.text)))
      (except [error Exception]
        (WatchReading :kind WatchKind.FAILED :detail (repr error)))))

  (defn #^ None watch-loop [self]
    "背景の thread の本体: 前の heartbeat の版の後の変化を待ち、「変わった」なら拍に heartbeat を送らせ(woken)、その heartbeat が
     版を進めるまで待ってから次を待つ。404 なら口が無いと記して抜ける(拍ごとの heartbeat に戻る)。届かなければ間を置いて送り直す。
     思わぬ例外は理由を記して抜ける(拍が watching で気づいて戻る — 黙って待ちを失わない)。"
    (setv state self.watch-state)
    (try
      (while (not (.is-set state.closing))
        (setv after (.after-now state))
        (if (is after None)
            (.wait state.beat-done WATCH-RETRY-SECONDS)
            (do (setv reading (.watch-once self after state.confirmed))
                (match reading.kind
                  WatchKind.UNSUPPORTED
                    (do (setv state.unsupported True)
                        (.tell-watch state "unsupported" "worker: coordinator に待ちの口が無い — 拍ごとの heartbeat を続けます")
                        (return None))
                  WatchKind.FAILED
                    (do (.tell-watch state (+ "failed:" reading.detail) (+ "worker: 待ちを送れない(送り直します): " reading.detail))
                        (.wait state.closing WATCH-RETRY-SECONDS))
                  WatchKind.CHANGED
                    (do (.confirm state)
                        (.clear state.beat-done)
                        (.set state.woken)
                        (.wait state.beat-done WAKE-HOLD-SECONDS))
                  WatchKind.UNCHANGED
                    (do (.confirm state)
                        (.advance state after reading.revision))))))
      (except [error Exception]
        (setv state.failure (repr error))
        (print (+ "worker: 待ちの thread が止まりました: " (repr error)) :file sys.stderr :flush True))))

  (defn #^ (| DesiredJobs DesiredUnreadable) beat [self]
    "heartbeat を 1 回送り、返事の job・task・温める表を desired にするため。届かなければ desired-when-unreachable(fence の判断)。"
    (setv sending self.statuses)
    ;; 送る前に起こしの印を下ろす(送った後に来た変化の印を消さない)。
    (.clear self.watch-state.woken)
    (try
      (setv response (.accepted self.endpoint (.request self.endpoint "POST" "/heartbeat"
        :json (| (heartbeat-body :name self.name :provides self.provides :exclusive self.exclusive :node self.node
                                 :capacity self.capacity :versions self.versions :statuses sending
                                 :endpoint self.endpoint.url :boot self.boot :boot-at self.boot-at :tools self.tools)
                 (.env-body self)))))
      (setv self.last-ok (time.monotonic))
      (setv body (.json response))
      (write-ready-file (os.environ.get "DOEFF_WORKER_READY_FILE") (bool (.get body "draining" False)))
      ;; 自己停止の時間は coordinator の ClusterTiming が持つ(移し替えの時間と組で決まる)。受け取った値に合わせる。
      (setv timing (.get body "timing"))
      (when (and timing (in "fence_ms" timing))
        (setv self.fence-ms (int (get timing "fence_ms"))))
      (setv self.last-jobs (tuple (gfor job (get body "jobs") (declared-job-spec job))))
      (setv self.last-tasks (.accept-tasks self (.get body "tasks" [])))
      (.accept-programs self (+ self.last-jobs self.last-tasks))
      (setv self.last-warm (.accept-warm self (.get body "warm" [])))
      ;; 返事を読み終えてから出す(返事の読みが毎回落ちる時に、名乗れた・名乗れないの 2 行を拍ごとに繰り返さない)。
      (.tell self "" (.format "worker: coordinator {} に名乗りました" self.endpoint.url))
      ;; 次の拍の判断の材料(#1933): 届けた状態の報告・送る間隔・待ちの after(返事の版 — 無ければ旧い coordinator)。
      (setv self.sent-statuses sending
            self.beat-interval-ms (beat-interval-ms timing self.task-echo)
            self.last-desired (DesiredJobs (+ self.last-jobs self.last-tasks) :warm self.last-warm))
      (.beaten self.watch-state (reply-revision body))
      (when (is-not (reply-revision body) None)
        (.start-watch self))
      self.last-desired
      (except [error Exception]
        (.tell self (repr error) (+ "worker: coordinator に名乗れない: " (repr error)))
        ;; 届かない間は毎拍送り直す(前の desired を使い続けない — fence の判断を毎拍する)。
        (setv self.last-desired None)
        (desired-when-unreachable (int (* 1000 (- (time.monotonic) self.last-ok))) self.fence-ms
                                  (+ self.last-jobs self.last-tasks) self.last-warm (repr error))))))


(defhandler coordinator-desired [#^ CoordinatorLink link]
  ;; heartbeat に載せる root の名乗りは、送る前に root の言い換え(worker/protocol/env_store)へ問う(#2467)。
  (ReadDesired []
    (when link.handles-envs
      (<- report dict (EnvReport))
      (setv link.env-report report))
    (resume (.poll link))))


(defhandler status-to-coordinator [#^ CoordinatorLink link]
  ;; 状態は次の heartbeat で送る。file にも書くので、外側の status-file へ渡す。
  (PublishStatus [statuses note]
    (setv link.statuses (.report link statuses))
    (<- (PublishStatus statuses note))
    (resume None)))

(defn #^ None release-leases [#^ CoordinatorLink link #^ str job #^ str instance]
  "終わった process(job の名 job・世代の名 instance)が持っていた lease を返す。token の頭は子が名乗った担い手と同じ定義
   (lease_rules.lease-holder と holder-tokens-prefix — <job>/<世代の名>/)。外すのは coordinator(POST /leases/<名> の drop —
   2026-09-25)。drop の口を持たない旧い coordinator には、盤の行の compare-and-set で外す(以前の形)。届かない・競合が続く時は
   あきらめる(期限で切れる)。"
  (setv prefix (holder-tokens-prefix (lease-holder job instance)))
  (try
    (setv response (.request link.endpoint "GET" "/board" :params {"prefix" SEMAPHORE-PREFIX}))
    (.raise-for-status response)
    (for [#(key row) (.items (.json response))]
      (when (is (drop-holders row prefix) None) (continue))
      (setv name (cut key (len SEMAPHORE-PREFIX) None))
      (setv dropped (.request link.endpoint "POST" (+ "/leases/" (url-quote name :safe ""))
                              :json {"op" "drop" "token" prefix}))
      (cond
        (< dropped.status-code 300)
          (print (.format "worker: 終わった process {} の lease を返しました({})" instance key) :file sys.stderr :flush True)
        (= dropped.status-code 404) (release-by-board link key row prefix instance)
        True (print (.format "worker: lease を返せなかった({}・{}): {}" instance key dropped.status-code) :file sys.stderr :flush True)))
    (except [error Exception]
      (print (.format "worker: lease を返せなかった({}): {!r}" instance error) :file sys.stderr :flush True))))

(defn #^ None release-by-board [#^ CoordinatorLink link #^ str key #^ dict row #^ str prefix #^ str instance]
  "旧い coordinator(/leases の口が無い)へ: 盤の行の compare-and-set で担い手を外す。"
  (for [attempt (range 3)]
    (setv updated (drop-holders row prefix))
    (when (is updated None) (break))
    (setv put (.request link.endpoint "PUT" (+ "/board/" key) :json {"value" updated "expect" row}))
    (when (< put.status-code 300)
      (print (.format "worker: 終わった process {} の lease を返しました({})" instance key) :file sys.stderr :flush True)
      (break))
    (when (!= put.status-code 409) (break))
    ;; 競合(延長と重なった)は読み直す。
    (setv again (.request link.endpoint "GET" "/board" :params {"prefix" key}))
    (setv row (.get (.json again) key))
    (when (is row None) (break))))

(defhandler lease-release-coordinator [#^ CoordinatorLink link]
  (ReleaseLeases [job instance] (release-leases link job instance) (resume None)))


