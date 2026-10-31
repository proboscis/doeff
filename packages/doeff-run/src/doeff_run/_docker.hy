;;; docker の handler — 公開 effect 5 つと故障の注入 RunVanish に、docker の CLI で答える(docker の在る機体 — zeus — での本物の検のため。
;;; 本番の handler は k3s)。
;;;
;;; run 1 つ = docker の container 1 つ。container の名 = run の名で、取っ手の run-id は container の label(doeff-run.run-id)に置く —
;;; 名の持ち主の label が取っ手の run-id と違えば、取っ手の run は消えた物として答える。状態は container の inspect から毎回引き直し、
;;; handler が覚えるのは docker が持たない 2 つだけ(DockerRunHost):
;;;   - requested = 止めを頼んだ run と、その刻(docker は止めが頼まれた物か自分で終わった物かを残さない — 止まった刻が頼んだ刻より後なら
;;;     REQUESTED)。handler を作り直すと忘れ、頼んだ止めの後の止まりは EXITED に読まれる。
;;;   - unmade = container を作れずに起きなかった run(image を取り寄せられない・volume が無い — NEVER-BEGAN)。片付けるまで名を持つ。
;;;
;;; 出来事は inspect から決まった形で引く: 起きた刻(StartedAt)に RunLive・止まった刻(FinishedAt)に RunStopped。番号は列の位置なので、
;;; 何度引いても同じ番号になる。読みの待ちは docker events(その container の start・die・destroy)の最初の 1 行で起き、時間で見に行かない。
;;; 読みの途中で container が消えたら、その読みは RunGone を最後の出来事として返す(消えた後の読みは、知らない取っ手として空の頁と RunGone)。
;;;
;;; volume は docker の named volume。VolumeEnsure は docker volume inspect で在るかを見て、無ければ label(host の印と、この作成だけの
;;; token)つきで docker volume create し、問い直して token が自分の物なら「作った」・他の作り手の物なら「在った」・見えなければ
;;; 「作れなかった」と答える。VolumeStatus は問うだけで作らない。docker は無い named volume を黙って作るので、RunStart は volume が無ければ
;;; container を作らず NEVER-BEGAN にする。volume を消す手は持たない。
;;;
;;; 受け付け(RunStart)は container を作って起こしてから答える — image が手元に無い時は docker create が取り寄せを待つ(取り寄せの間は名を
;;; docker が持たないため)。起こせなかった container(命令が無い など)は docker が State.Error に残し、NEVER-BEGAN として読む。
;;;
;;; 子 process は RunProcess で出す(外側に offloaded-subprocess-handler と scheduled — 待つ間も他の task が回る)。env の値は命令の引数に
;;; 載せず(ps に写る)、docker の CLI の環境変数で渡して `-e 名` で container へ写す。
(require doeff-hy.macros [defhandler defk <- val var])
(import json)
(import math)
(import re)
(import shlex)
(import uuid)
(import datetime [datetime timedelta])
(import doeff_time [GetMonotonic GetTime])
(import doeff_core_effects [RunProcess ProcessOutcome EnvEntry EnvMode])
(import doeff_run._values [RunSpec RunHandle RunPending RunLive RunStopped RunGone RunState StopCause VOLUME-NAME volume-name-refusal])
(import doeff_run._effects [RunStart RunStop RunRemove RunStatus RunReadEvents RunAccepted RunNameInUse RunRefused RefusalWhy
                            StopFailed RunStillAlive RunEvent RunEventPage VolumeEnsure VolumeStatus
                            VolumeCreated VolumeExisted VolumePresent VolumeAbsent VolumeRefused])
(import doeff_run._faults [RunVanish])

;; container と volume に置く label: run-id = 取っ手の run-id・host = 作った DockerRunHost の印(片付けの的)・
;; volume-token = volume を作る 1 回ごとの印(docker は在る名への create を黙って受けるので、volume に残った印が自分の物かで「作った」と
;; 「在った」を見分ける — 作成が重なっても作ったと答えるのは 1 つだけ)。
(val RUN-ID-LABEL "doeff-run.run-id")
(val HOST-LABEL "doeff-run.host")
(val VOLUME-TOKEN-LABEL "doeff-run.volume-token")
;; docker が受けるメモリの上限の下限(6 MiB)。
(val MIN-MEMORY-BYTES (* 6 1024 1024))
;; inspect・create・rm など待たない呼びの上限の秒(docker の daemon が答えない時に読みを止めない)。create は image の取り寄せを待つので外す。
(val CALL-TIMEOUT 60.0)
;; docker stop の答えを、猶予の後に待つ余裕の秒。
(val STOP-MARGIN 30.0)
;; 読みの待ちの子(docker events)を、期限で止める時の SIGTERM から SIGKILL までの猶予の秒。
(val WATCH-STOP-GRACE 1.0)
;; 期限の比べの余裕(秒)— 期限の直前で待ちを繰り返さない。
(val DEADLINE-SLACK 1e-3)
(val UNKNOWN-HANDLE "取っ手の run を docker が知らない")
(val REMOVED "片付けた")
;; docker の時刻の文(RFC 3339 — 秒の小数はナノ秒まで)。
(val DOCKER-TIME (re.compile r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)"))


(defclass UnmadeRun []
  "container を作れずに起きなかった run(NEVER-BEGAN): handle・state(止まった状態)・event(その出来事)。"
  (defn __init__ [self #^ RunHandle handle #^ RunStopped state #^ RunEvent event]
    (setv self.handle handle self.state state self.event event)))


(defclass DockerRunHost []
  "docker の handler が覚える物(頭の註)。docker = docker の CLI の命令の頭(既定 #(\"docker\") — 別の daemon は DOCKER_HOST を環境で
   渡す)・host-id = この host が作る container の label HOST-LABEL の値(既定は新しい印 — 片付けの的)・requested = run-id → 止めを頼んだ刻・
   unmade = 名 → UnmadeRun。"
  (defn __init__ [self * #^ tuple [docker #("docker")] #^ (| str None) [host-id None]]
    (when (not (and (isinstance docker tuple) docker (all (gfor part docker (and (isinstance part str) (bool part))))))
      (raise (ValueError (.format "DockerRunHost.docker は空でない文字列の空でない tuple: {!r}" docker))))
    (when (not (or (is host-id None) (and (isinstance host-id str) (bool host-id))))
      (raise (ValueError (.format "DockerRunHost.host_id は空でない文字列か None: {!r}" host-id))))
    (setv self.docker docker)
    (setv self.host-id (if (is host-id None) (+ "doeff-run-" (cut (. (uuid.uuid4) hex) 0 12)) host-id))
    (setv #^ (get dict #(str datetime)) self.requested {})
    (setv #^ (get dict #(str UnmadeRun)) self.unmade {})))


;; --- 純な訳 --------------------------------------------------------------------------------------------------

(defn #^ (| datetime None) docker-time [#^ str text]
  "docker の時刻の文を時刻帯つきの datetime へ(マイクロ秒へ切り捨てる)。docker の 0 の刻(0001 年)と読めない文は None。"
  (setv found (DOCKER-TIME.fullmatch text))
  (when (or (is found None) (.startswith text "0001-"))
    (return None))
  (setv #(whole fraction zone) (.groups found))
  (setv micros (if fraction (cut (+ fraction "000000") 0 6) "000000"))
  (datetime.fromisoformat (+ whole "." micros (if (= zone "Z") "+00:00" zone))))

(defn #^ (| str None) unsupported-of [#^ RunSpec spec]
  "docker の handler が守れない設定の訳の文(守れるなら None): docker の named volume の名でない source(path は bind mount に読まれる)・
   --mount の値を壊す target(, を含む)・docker の下限より小さいメモリの上限。"
  (for [volume spec.volumes]
    (when (not (VOLUME-NAME.fullmatch volume.source))
      (return (.format "volume の source {!r} は docker の named volume の名でない(path は bind mount に読まれる)" volume.source)))
    (when (in "," volume.target)
      (return (.format "volume の target {!r} は , を含む(docker の --mount の値を壊す)" volume.target))))
  (setv memory spec.limits.memory-bytes)
  (when (and (is-not memory None) (< memory MIN-MEMORY-BYTES))
    (return (.format "メモリの上限 {} byte は docker の下限 {} byte より小さい" memory MIN-MEMORY-BYTES)))
  None)

(defn #^ tuple create-argv [#^ DockerRunHost host #^ str name #^ str run-id #^ RunSpec spec]
  "docker create の命令(env の値は載せない — 名だけを -e で渡し、値は CLI の環境変数で渡す)。"
  (setv argv (+ host.docker
                #("create" "--name" name "--label" (+ RUN-ID-LABEL "=" run-id) "--label" (+ HOST-LABEL "=" host.host-id)
                  "--pull" "missing")))
  (for [key (sorted spec.env)]
    (+= argv #("--env" key)))
  (for [volume spec.volumes]
    (+= argv #("--mount" (+ "type=volume,source=" volume.source ",target=" volume.target (if volume.read-only ",readonly" "")))))
  (setv limits spec.limits)
  (when (is-not limits.memory-bytes None)
    ;; swap を上限と同じにする — 上限を越えた run は swap へ逃げずに OOM で止まる。
    (+= argv #("--memory" (str limits.memory-bytes) "--memory-swap" (str limits.memory-bytes))))
  (when (is-not limits.cpu-millis None)
    (+= argv #("--cpus" (.format "{:.3f}" (/ limits.cpu-millis 1000)))))
  (+ argv #(spec.image) spec.command))

(defn #^ str last-line [#^ str text]
  "docker の CLI の誤りの文の最後の行(断りの detail に写す 1 行 — 長すぎる行は切る)。"
  (setv lines (lfor line (.splitlines text) :if (.strip line) (.strip line)))
  (if lines (cut (get lines -1) 0 300) ""))

(defn #^ bool no-such? [#^ str stderr]
  "docker の CLI の誤りが「その container が無い」か(daemon が答えない など、他の誤りと見分ける)。"
  (bool (re.search r"(?i)no such (container|object)" stderr)))

(defn #^ (| dict None) volume-info-of [#^ str name #^ ProcessOutcome outcome]
  "docker volume inspect の答えから volume の記録(無ければ None)を読むため。daemon が答えない など「無い」でない誤りは例外で上げる
   (無いと読み違えて、在る volume を作り直さない・消えたと言わない)。"
  (when (= outcome.exit-code 0)
    (return (get (json.loads outcome.stdout) 0)))
  (when (re.search r"(?i)no such volume" outcome.stderr)
    (return None))
  (raise (RuntimeError (.format "docker volume inspect {} が失敗した(code {}): {}" name outcome.exit-code (last-line outcome.stderr)))))

(defn #^ tuple volume-create-args [#^ DockerRunHost host #^ str name #^ str token]
  "docker volume create の引数(docker の後ろ): host の印と、この作成だけの token を label に置く。"
  #("volume" "create" "--label" (+ HOST-LABEL "=" host.host-id) "--label" (+ VOLUME-TOKEN-LABEL "=" token) "--" name))

(defn #^ tuple read-container [#^ DockerRunHost host #^ RunHandle handle #^ dict info]
  "inspect の答えから、run の今の状態と出来事の列(#(状態 出来事の tuple))を引く(頭の註)。"
  (setv state-info (get info "State"))
  (setv status (get state-info "Status"))
  (setv started (docker-time (.get state-info "StartedAt" "")))
  (setv finished (docker-time (.get state-info "FinishedAt" "")))
  (setv created (docker-time (.get info "Created" "")))
  (setv error (or (.get state-info "Error" "") ""))
  (setv asked (.get host.requested handle.run-id))
  (defn stopped-at [state at]
    (setv live (if (is started None) #() #((RunEvent :seq 1 :at started :state (RunLive)))))
    #(state (+ live #((RunEvent :seq (+ (len live) 1) :at (max (gfor stamp #(at started) :if (is-not stamp None) stamp)) :state state)))))
  (match status
    ;; docker rm --force は殺す前に「片付けている」の印を置くので、消える run の止まりは removing として見え、exited を経ない。
    "removing"
      #((RunGone :detail "docker が片付けている") #())
    (| "running" "paused" "restarting")
      #((RunLive) #((RunEvent :seq 1 :at started :state (RunLive))))
    ;; 起こせなかった container(命令が無い など — docker が State.Error に残す)と、起きずに終わった container。
    (| "created" "exited" "dead") :if (and (is started None) (or error (!= status "created")))
      (stopped-at (RunStopped :exit-code None :cause StopCause.NEVER-BEGAN :detail (or error "docker が起こさなかった")) (or finished created))
    "created"
      (if (is asked None)
          #((RunPending) #())
          (stopped-at (RunStopped :exit-code None :cause StopCause.REQUESTED) asked))
    (| "exited" "dead")
      (stopped-at (RunStopped :exit-code (get state-info "ExitCode")
                              :cause (cond
                                       (.get state-info "OOMKilled" False) StopCause.OUT-OF-MEMORY
                                       (and (is-not asked None) (is-not finished None) (>= finished asked)) StopCause.REQUESTED
                                       True StopCause.EXITED)
                              :detail error)
                  (or finished started))
    _ (raise (RuntimeError (.format "docker の container の状態を知らない: {!r}" status)))))

(defn #^ str watch-script [#^ DockerRunHost host #^ str container-id #^ float since]
  "読みの待ちの子: since(unix 秒)より後の container の start・die・destroy の最初の 1 行で終わる。docker events は bash の process
   substitution の子で走り、head が終わった後は RunProcess が process group ごと止める。"
  (setv events (+ host.docker
                  #("events" "--since" (.format "{:.6f}" since) "--filter" "type=container" "--filter" (+ "container=" container-id)
                    "--filter" "event=start" "--filter" "event=die" "--filter" "event=destroy" "--format" "{{.Action}}")))
  (.format "exec head -n 1 < <({} 2>/dev/null)" (.join " " (gfor part events (shlex.quote part)))))


;; --- docker の呼び -------------------------------------------------------------------------------------------

(defk docker-call [#^ DockerRunHost host #^ tuple args]
  {:pre [(: host DockerRunHost) (: args tuple)] :post [(: % ProcessOutcome)]}
  "docker の CLI を 1 回走らせ、ProcessOutcome を返すため(待たない呼び — 上限 CALL-TIMEOUT 秒)。"
  (<- outcome (RunProcess :argv (+ host.docker args) :timeout CALL-TIMEOUT :env-mode EnvMode.EXTEND :env #()))
  outcome)

(defk inspect-name [#^ DockerRunHost host #^ str name]
  {:pre [(: host DockerRunHost) (: name str)] :post [(: % (| dict None))]}
  "名の container の inspect の答え(無ければ None)。docker が答えない誤りは例外で上げる(消えた run と見分ける)。"
  (<- outcome (docker-call host #("container" "inspect" "--" name)))
  (when (= outcome.exit-code 0)
    (return (get (json.loads outcome.stdout) 0)))
  (when (no-such? outcome.stderr)
    (return None))
  (raise (RuntimeError (.format "docker container inspect {} が失敗した(code {}): {}" name outcome.exit-code (last-line outcome.stderr)))))

(defk find-run [#^ DockerRunHost host #^ RunHandle handle]
  {:pre [(: host DockerRunHost) (: handle RunHandle)] :post [(: % (| dict UnmadeRun None))]}
  "取っ手の run を引くため: 名の container の label の run-id が同じなら inspect の答え・起きなかった run なら UnmadeRun・どちらでも
   なければ None(名が空いている・走らせ直した別の run・docker の外の container)。"
  (<- info (inspect-name host handle.name))
  (when (and (is-not info None) (= (.get (or (get info "Config" "Labels") {}) RUN-ID-LABEL) handle.run-id))
    (return info))
  (val unmade (.get host.unmade handle.name))
  (if (and (is-not unmade None) (= unmade.handle handle)) unmade None))

(defk state-of [#^ DockerRunHost host #^ RunHandle handle]
  {:pre [(: host DockerRunHost) (: handle RunHandle)] :post [(: % tuple)]}
  "取っ手の run の #(状態 出来事の列 container の id か None)。知らない取っ手は #(RunGone #() None)。"
  (<- found (find-run host handle))
  (cond
    (is found None) #((RunGone :detail UNKNOWN-HANDLE) #() None)
    (isinstance found UnmadeRun) #(found.state #(found.event) None)
    True (+ (read-container host handle found) #((get found "Id")))))

(defk mark-unmade [#^ DockerRunHost host #^ RunHandle handle #^ str detail]
  {:pre [(: host DockerRunHost) (: handle RunHandle) (: detail str)] :post [(: % RunAccepted)]}
  "container を作れなかった run を、起きなかった止まり(NEVER-BEGAN)として覚えて受け付けるため。"
  (<- now-at (GetTime))
  (val state (RunStopped :exit-code None :cause StopCause.NEVER-BEGAN :detail detail))
  (setv (get host.unmade handle.name) (UnmadeRun handle state (RunEvent :seq 1 :at now-at :state state)))
  (RunAccepted :handle handle))

(defk holder-of [#^ DockerRunHost host #^ str name]
  {:pre [(: host DockerRunHost) (: name str)] :post [(: % (| RunHandle str None))]}
  "名の今の持ち主の取っ手(この handler の container か、起きなかった run)・docker の外の container が持つなら断りの文・空いていれば None。"
  (<- info (inspect-name host name))
  (when (is-not info None)
    (val run-id (.get (or (get info "Config" "Labels") {}) RUN-ID-LABEL))
    (return (if run-id
                (RunHandle :name name :run-id run-id)
                (.format "名 {!r} は doeff-run の作っていない docker の container が使っている" name))))
  (val unmade (.get host.unmade name))
  (if (is unmade None) None unmade.handle))

(defk inspect-volume [#^ DockerRunHost host #^ str name]
  {:pre [(: host DockerRunHost) (: name str)] :post [(: % (| dict None))]}
  "名の volume の inspect の答え(無ければ None)。docker が答えない誤りは例外で上げる(無いと読み違えない)。"
  (<- outcome (docker-call host #("volume" "inspect" "--" name)))
  (volume-info-of name outcome))

(defk missing-volume [#^ DockerRunHost host #^ RunSpec spec]
  {:pre [(: host DockerRunHost) (: spec RunSpec)] :post [(: % (| str None))]}
  "spec の volume のうち docker に無い物の訳の文(全部在れば None)。docker は無い named volume を黙って作るので、作る前に確かめる。"
  (for [volume spec.volumes]
    (<- found (inspect-volume host volume.source))
    (when (is found None)
      (return (.format "volume {!r} が無い(VolumeEnsure で先に作る)" volume.source))))
  None)


;; --- effect の答え -------------------------------------------------------------------------------------------

(defk docker-volume-ensure [#^ DockerRunHost host #^ VolumeEnsure request]
  {:pre [(: host DockerRunHost) (: request VolumeEnsure)] :post [(: % (| VolumeCreated VolumeExisted VolumeRefused))]}
  "volume を無ければ作るため: 名が規則に合わなければ docker を呼ぶ前に断り・在れば在るまま(create を撃たない)・無ければ印(token)つきで作って
   問い直す。問い直して見えなければ「作った」と答えない(作れなかった)・印が自分の物なら作った・他の作り手の物なら在った(作成が重なっても
   作ったと答えるのは 1 つだけ)。create が誤りでも問い直して在れば在った(重なった作成を docker が断る版)。"
  (val name request.name)
  (val refusal (volume-name-refusal name))
  (when (is-not refusal None)
    (return (VolumeRefused :name name :why RefusalWhy.UNSUPPORTED :detail refusal)))
  (<- present (inspect-volume host name))
  (when (is-not present None)
    (return (VolumeExisted :name name)))
  (val token (. (uuid.uuid4) hex))
  (<- created (docker-call host (volume-create-args host name token)))
  (<- found (inspect-volume host name))
  (cond
    (is found None)
      (VolumeRefused :name name :why RefusalWhy.NO-CAPACITY
                     :detail (if (= created.exit-code 0)
                                 "docker volume create は成功と答えたが、作ったはずの volume が見えない"
                                 (or (last-line created.stderr) (.format "docker volume create が失敗した(code {})" created.exit-code))))
    (= (.get (or (.get found "Labels") {}) VOLUME-TOKEN-LABEL) token)
      (VolumeCreated :name name)
    True
      (VolumeExisted :name name)))

(defk docker-volume-status [#^ DockerRunHost host #^ VolumeStatus request]
  {:pre [(: host DockerRunHost) (: request VolumeStatus)] :post [(: % (| VolumePresent VolumeAbsent VolumeRefused))]}
  "volume が在るかを docker に問うため(作らない): 名が規則に合わなければ docker を呼ぶ前に断る。"
  (val name request.name)
  (val refusal (volume-name-refusal name))
  (when (is-not refusal None)
    (return (VolumeRefused :name name :why RefusalWhy.UNSUPPORTED :detail refusal)))
  (<- found (inspect-volume host name))
  (if (is found None)
      (VolumeAbsent :name name)
      (VolumePresent :name name)))

(defk docker-start [#^ DockerRunHost host #^ RunStart request]
  {:pre [(: host DockerRunHost) (: request RunStart)] :post [(: % (| RunAccepted RunNameInUse RunRefused))]}
  "走らせ始めを受け付けるため: 名の持ち主が在れば取っ手を答え・守れない設定は断り・volume が無ければ起きなかった run として受け付け・
   それ以外は container を作って起こす(起こせない container は docker が State.Error に残す — 頭の註)。"
  (val name request.name)
  (val spec request.spec)
  (<- held (holder-of host name))
  (cond
    (isinstance held RunHandle) (return (RunNameInUse :handle held))
    (isinstance held str) (return (RunRefused :name name :why RefusalWhy.UNSUPPORTED :detail held)))
  (val refusal (unsupported-of spec))
  (when (is-not refusal None)
    (return (RunRefused :name name :why RefusalWhy.UNSUPPORTED :detail refusal)))
  (val handle (RunHandle :name name :run-id (+ "docker-" (. (uuid.uuid4) hex))))
  (<- absent (missing-volume host spec))
  (when (is-not absent None)
    (<- accepted (mark-unmade host handle absent))
    (return accepted))
  (<- created (RunProcess :argv (create-argv host name handle.run-id spec)
                          :env (tuple (gfor #(key value) (sorted (.items spec.env)) (EnvEntry :name key :value value)))
                          :env-mode EnvMode.EXTEND))
  (when (!= created.exit-code 0)
    ;; 名を同時に取った別の頼みが在れば、その持ち主を答える。でなければ image を取り寄せられない・設定を docker が断った等で起きなかった。
    (<- holder (holder-of host name))
    (cond
      (isinstance holder RunHandle) (return (RunNameInUse :handle holder))
      (isinstance holder str) (return (RunRefused :name name :why RefusalWhy.UNSUPPORTED :detail holder)))
    (<- accepted (mark-unmade host handle (or (last-line created.stderr) (.format "docker create が失敗した(code {})" created.exit-code))))
    (return accepted))
  (<- _started (docker-call host #("container" "start" "--" name)))
  (RunAccepted :handle handle))

(defk docker-stop [#^ DockerRunHost host #^ RunStop request]
  {:pre [(: host DockerRunHost) (: request RunStop)] :post [(: % (| RunStopped RunGone StopFailed))]}
  "run を止めるため: 止まった run にはその状態・起きる前の run は頼まれた止めで直ちに・走っている run には頼んだ刻を覚えてから docker stop
   (SIGTERM → 猶予を秒へ切り上げて → SIGKILL)を待ち、止まった状態を答える。"
  (val handle request.handle)
  (<- seen (state-of host handle))
  (val state (get seen 0))
  (val container-id (get seen 2))
  (when (isinstance state #(RunStopped RunGone))
    (return state))
  (when (not-in handle.run-id host.requested)
    (<- now-at (GetTime))
    (setv (get host.requested handle.run-id) now-at))
  (when (isinstance state RunPending)
    (<- pending (state-of host handle))
    (return (get pending 0)))
  (val grace (max 0 (math.ceil request.grace-seconds)))
  (<- stopped (RunProcess :argv (+ host.docker #("container" "stop" "--time" (str grace) "--" container-id))
                          :timeout (+ grace STOP-MARGIN) :env-mode EnvMode.EXTEND :env #()))
  (<- after (state-of host handle))
  (val ended (get after 0))
  (cond
    (isinstance ended #(RunStopped RunGone)) ended
    True (StopFailed :handle handle
                     :detail (or (last-line stopped.stderr) (.format "docker container stop の後も止まっていない(code {})" stopped.exit-code)))))

(defk docker-remove [#^ DockerRunHost host #^ RunRemove request]
  {:pre [(: host DockerRunHost) (: request RunRemove)] :post [(: % (| RunGone RunStillAlive))]}
  "止まった run の跡を片付けるため: container を消して(volume は残す)名を空ける。止まっていない run は断る。"
  (val handle request.handle)
  (<- found (find-run host handle))
  (when (is found None)
    (return (RunGone :detail UNKNOWN-HANDLE)))
  (when (isinstance found UnmadeRun)
    (del (get host.unmade handle.name))
    (return (RunGone :detail REMOVED)))
  (val state (get (read-container host handle found) 0))
  (when (not (isinstance state RunStopped))
    (return (RunStillAlive :handle handle)))
  (<- removed (docker-call host #("container" "rm" "--" (get found "Id"))))
  (when (and (!= removed.exit-code 0) (not (no-such? removed.stderr)))
    (raise (RuntimeError (.format "docker container rm {} が失敗した(code {}): {}" handle.name removed.exit-code (last-line removed.stderr)))))
  (.pop host.requested handle.run-id None)
  (RunGone :detail REMOVED))

(defk docker-status [#^ DockerRunHost host #^ RunStatus request]
  {:pre [(: host DockerRunHost) (: request RunStatus)] :post [(: % (| RunPending RunLive RunStopped RunGone))]}
  "run の今の状態を、container の inspect から引いて答えるため。"
  (<- seen (state-of host request.handle))
  (get seen 0))

(defk docker-read-events [#^ DockerRunHost host #^ RunReadEvents request]
  {:pre [(: host DockerRunHost) (: request RunReadEvents)] :post [(: % RunEventPage)]}
  "after-seq より後の出来事を、新しい出来事が起きるか終わっているか期限が来るまで待って頁にするため。待ちは docker events の最初の 1 行で
   起きる。読みの途中で container が消えたら、RunGone を最後の出来事に足す(頭の註)。"
  (val handle request.handle)
  (val after request.after-seq)
  (<- started (GetMonotonic))
  (val deadline (+ started request.wait-up-to))
  (var known None)
  (while True
    (<- since-at (GetTime))
    (<- seen (state-of host handle))
    (val state (get seen 0))
    (val container-id (get seen 2))
    (var events (get seen 1))
    (when (and (isinstance state RunGone) (is-not known None))
      ;; この読みが見ていた run が消えた(消えている途中の removing も): 見た列に消えた出来事を足す。
      (<- now-at (GetTime))
      (:= events (+ known #((RunEvent :seq (+ (len known) 1) :at now-at :state state)))))
    (:= known events)
    (val fresh (tuple (gfor event events :if (> event.seq after) event)))
    (<- now (GetMonotonic))
    (when (or fresh (isinstance state #(RunStopped RunGone)) (>= now (- deadline DEADLINE-SLACK)))
      (return (RunEventPage :events fresh :next-seq (if fresh (. (get fresh -1) seq) after) :state state)))
    (<- _woke (RunProcess :argv #("bash" "-c" (watch-script host container-id (.timestamp since-at)))
                          :timeout (- deadline now) :process-group True :stop-grace WATCH-STOP-GRACE
                          :env-mode EnvMode.EXTEND :env #()))))

(defk docker-vanish [#^ DockerRunHost host #^ RunVanish request]
  {:pre [(: host DockerRunHost) (: request RunVanish)] :post [(: % bool)]}
  "run を handler の知らない所で消すため(検の口): container を docker rm -f で消す(起きなかった run は覚えから外す)。答え = 消す run が在ったか。"
  (val handle request.handle)
  (<- found (find-run host handle))
  (when (is found None)
    (return False))
  (when (isinstance found UnmadeRun)
    (del (get host.unmade handle.name))
    (return True))
  (<- removed (docker-call host #("container" "rm" "--force" "--" (get found "Id"))))
  (when (and (!= removed.exit-code 0) (not (no-such? removed.stderr)))
    (raise (RuntimeError (.format "docker container rm --force {} が失敗した(code {}): {}" handle.name removed.exit-code (last-line removed.stderr)))))
  True)


;; --- handler -------------------------------------------------------------------------------------------------

;; 引数に残す理由: host は handler の覚え(止めを頼んだ刻・起きなかった run)を持つ資源で、検が同じ host を片付けの的に読む
;; (fake-run-handler の world と同じ形)。
(defhandler docker-run-handler [#^ DockerRunHost host]
  "DoeffRun の docker の handler: 公開 effect 7 つと故障の注入 RunVanish に、host の docker の CLI で答える。外側に RunProcess の答え手
   (offloaded-subprocess-handler)と時計(GetTime・GetMonotonic)と scheduled が要る。"
  (RunStart [name spec]
    (<- outcome (docker-start host effect))
    (resume outcome))
  (RunStop [handle grace-seconds]
    (<- outcome (docker-stop host effect))
    (resume outcome))
  (RunRemove [handle]
    (<- outcome (docker-remove host effect))
    (resume outcome))
  (RunStatus [handle]
    (<- state (docker-status host effect))
    (resume state))
  (RunReadEvents [handle after-seq wait-up-to]
    (<- page (docker-read-events host effect))
    (resume page))
  (VolumeEnsure [name]
    (<- outcome (docker-volume-ensure host effect))
    (resume outcome))
  (VolumeStatus [name]
    (<- outcome (docker-volume-status host effect))
    (resume outcome))
  (RunVanish [handle]
    (<- vanished (docker-vanish host effect))
    (resume vanished)))
