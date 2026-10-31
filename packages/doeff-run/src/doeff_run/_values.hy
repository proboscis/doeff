;;; doeff-run の値の型 — 走らせる設定(RunSpec)・取っ手(RunHandle)・状態(RunPending・RunLive・RunStopped・RunGone)。
;;; data だけで I/O を行わない。作る時に誤りを断る: 本物の handler(docker・process・k3s)は検めずに値を信じて backend の呼びへ訳せる。
;;;
;;; 「image を、この設定(命令・環境変数・volume・資源の上限)で走らせる」の設定が RunSpec。上の層が持つ身元は取っ手(名と run-id)
;;; だけで、container・Pod・process の id は handler の内側に閉じる(run-id は handler が付ける印で、上の層は中身を読まない)。
;;;
;;; 状態は 4 つ: RunPending(受け付けた・まだ起きていない)→ RunLive(起きた)→ RunStopped(止まった — 終了 code と止まった訳)。
;;; RunGone = 取っ手の run が backend から消えた(片付けた・node の死・手で消された・名にも取っ手にも覚えが無い)。
;;; 止まった run の跡(終了 code)は片付けるまで残り、RunGone は何も残らない。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord defenum])
(import dataclasses [dataclass field])
(import enum [StrEnum])
(import posixpath)
(import re)
(import doeff_hy._frozen [FrozenMap frozen-map-of])

;; run の名: docker の container 名と k8s の資源名(RFC 1123 の label)の両方が受ける形 — 小文字・数字・- の 1〜63 字で、前後は - でない。
(val RUN-NAME (re.compile r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?"))
;; named volume の名: 英数字で始まる 2 字以上の英数字と _ . -(docker の named volume の名の規則。この形でない source を docker は
;; bind mount の path に読む)。volume の効果(VolumeEnsure・VolumeStatus)が作る前に照らす名の形で、この形に合わない名は backend に
;; 渡さず UNSUPPORTED で断る。
(val VOLUME-NAME (re.compile r"[A-Za-z0-9][A-Za-z0-9_.-]+"))
;; 環境変数の名: shell が受ける名。
(val ENV-NAME (re.compile r"[A-Za-z_][A-Za-z0-9_]*"))
;; image の名: 空白・NUL を含まない 1 語(tag か digest つきの参照 — digest で指すかは使い手が決める)。
(val IMAGE-NAME (re.compile r"[^\s\x00]+"))


(defrecord RunHandle
  "走らせた run の取っ手。name = 呼び手が付けた名(RUN-NAME の形)・run-id = handler が付けた、その run 1 回分の印(同じ名で走らせ直した
   run とは違う)。止める・読む・片付けるは取っ手で頼み、取っ手の run が今の名の持ち主でなければ、その run は消えた物として答える。"
  {:tags {:context "run" :role "type"}
   :check [(RUN-NAME.fullmatch name) (and (isinstance run-id str) (bool run-id))]}
  (#^ str name)
  (#^ str run-id))


(defrecord ResourceLimits
  "資源の上限: cpu-millis = CPU の千分の 1 単位(1000 = 1 core)・memory-bytes = メモリの byte。None = 上限を置かない。
   大・中・小のような区分は上の層が持ち、ここへは単位へ訳した値で渡す。handler が守れない上限は走らせずに RunRefused(UNSUPPORTED)で断る。"
  {:tags {:context "run" :role "type"}
   :check [(or (is cpu-millis None) (and (isinstance cpu-millis int) (not (isinstance cpu-millis bool)) (> cpu-millis 0)))
           (or (is memory-bytes None) (and (isinstance memory-bytes int) (not (isinstance memory-bytes bool)) (> memory-bytes 0)))]}
  (setv #^ (| int None) cpu-millis None)
  (setv #^ (| int None) memory-bytes None))


(defrecord VolumeMount
  "volume を run の中の path に置く。source = handler が解く volume の名(docker の named volume・k8s の PVC の名など — 中身は run を
   片付けても消えない)・target = run の中の絶対 path(根でなく、// も ..・末尾の / も無い)・read-only = 読むだけで置くか。"
  {:tags {:context "run" :role "type"}
   :check [(and (isinstance source str) (bool source))
           (and (isinstance target str) (.startswith target "/") (not (.startswith target "//")) (!= target "/")
                (= (posixpath.normpath target) target) (not (in "\x00" target)))
           (isinstance read-only bool)]}
  (#^ str source)
  (#^ str target)
  (setv #^ bool read-only False))


(defn #^ (| str None) volume-name-refusal [#^ str name]
  "volume の名が VOLUME-NAME の形でない時の断りの 1 行(合えば None)。backend を呼ぶ前に照らす。"
  (when (VOLUME-NAME.fullmatch name)
    (return None))
  (.format "volume の名 {!r} は named volume の名でない(英数字で始まる 2 字以上の英数字と _ . - — 他の形は path と読まれる)" name))

(defn #^ (get FrozenMap str) checked-env [#^ object value]
  "走らせる env の検め: 名が shell の名で、値が NUL を含まない文字列の写像を凍らせた FrozenMap(外れは TypeError・ValueError)。"
  (setv env (frozen-map-of value "RunSpec.env"))
  (for [#(key item) (.items env)]
    (when (not (ENV-NAME.fullmatch key))
      (raise (ValueError (.format "RunSpec.env の名は shell の名(英字か _ で始まる英数字と _): {!r}" key))))
    (when (not (isinstance item str))
      (raise (TypeError (.format "RunSpec.env の値は文字列(名 {!r})" key))))
    (when (in "\x00" item)
      (raise (ValueError (.format "RunSpec.env の値は NUL を含まない(名 {!r})" key)))))
  env)


(defclass [(dataclass :frozen True :kw-only True)] RunSpec []
  "image を走らせる設定: image = 走らせる image の参照 / command = 命令の引数の並び(空 = image の既定の命令)/ env = 環境変数
   (凍らせた写像 — 作る時に写し取る。資格を運びうるので repr に出さず、断りの文にも値を写さない)/ volumes = 置く volume の並び
   (target は重ならない)/ limits = 資源の上限。呼び手の業務の語は持たない。"
  (#^ str image)
  (setv #^ (get tuple #(str ...)) command #())
  ;; env は資格を運びうるので repr に出さない(例外・log に値が写らない)。
  (setv #^ (get FrozenMap str) env (field :default-factory FrozenMap :repr False))
  (setv #^ (get tuple #(VolumeMount ...)) volumes #())
  (setv #^ ResourceLimits limits (field :default-factory ResourceLimits))
  (defn __post_init__ [self]
    (when (not (and (isinstance self.image str) (IMAGE-NAME.fullmatch self.image)))
      (raise (ValueError (.format "RunSpec.image は空白を含まない 1 語: {!r}" self.image))))
    (when (not (isinstance self.command #(tuple list)))
      (raise (TypeError (.format "RunSpec.command は文字列の並び: {!r}" self.command))))
    (when (not (all (gfor part self.command (isinstance part str))))
      (raise (TypeError (.format "RunSpec.command の中身は文字列: {!r}" self.command))))
    (object.__setattr__ self "command" (tuple self.command))
    (object.__setattr__ self "env" (checked-env self.env))
    (when (not (isinstance self.volumes #(tuple list)))
      (raise (TypeError (.format "RunSpec.volumes は VolumeMount の並び: {!r}" self.volumes))))
    (when (not (all (gfor volume self.volumes (isinstance volume VolumeMount))))
      (raise (TypeError (.format "RunSpec.volumes の中身は VolumeMount: {!r}" self.volumes))))
    (object.__setattr__ self "volumes" (tuple self.volumes))
    (setv targets (lfor volume self.volumes volume.target))
    (when (!= (len targets) (len (set targets)))
      (raise (ValueError (.format "RunSpec.volumes の target が重なる: {!r}" targets))))
    (when (not (isinstance self.limits ResourceLimits))
      (raise (TypeError (.format "RunSpec.limits は ResourceLimits: {!r}" self.limits))))))


;; --- 状態 ---------------------------------------------------------------------------------------------

;; 止まった訳: EXITED = run が自分で終わった(終了 code が正しい終わりかは使い手が読む)/ REQUESTED = 止めを頼まれて止まった /
;; OUT-OF-MEMORY = メモリの上限を越えて殺された / NEVER-BEGAN = 起きる前に止まった(image を取り寄せられない・命令が起こせない・
;; volume が無い など — 終了 code は無い。止めを頼まれる前の受け付けだけの run を止めた時は REQUESTED)。
(defenum StopCause EXITED REQUESTED OUT-OF-MEMORY NEVER-BEGAN)


(defclass [(dataclass :frozen True)] RunPending []
  "受け付けた。まだ起きていない(image を取り寄せている・置き場を探している)。")

(defclass [(dataclass :frozen True)] RunLive []
  "起きた(命令が走っている)。")


(defrecord RunStopped
  "止まった。exit-code = 終了 code(起きなかった run は None)・cause = 止まった訳(StopCause)・detail = 何が起きたかの 1 行(backend が
   名乗った文 — 無ければ空)。自分で終わった(EXITED)・メモリで殺された(OUT-OF-MEMORY)run は終了 code を持ち、起きずに止まった
   (NEVER-BEGAN)run は持たない。頼まれて止めた(REQUESTED)run は、走っていれば持ち、起きる前なら持たない。"
  {:tags {:context "run" :role "type"}
   :check [(or (is exit-code None) (and (isinstance exit-code int) (not (isinstance exit-code bool))))
           (isinstance cause StopCause)
           (or (is-not exit-code None) (in cause #(StopCause.REQUESTED StopCause.NEVER-BEGAN)))
           (or (is exit-code None) (!= cause StopCause.NEVER-BEGAN))
           (isinstance detail str)]}
  (#^ (| int None) exit-code)
  (#^ StopCause cause)
  (setv #^ str detail ""))


(defrecord RunGone
  "消えた(backend に無い)。detail = 訳の 1 行(片付けた・取っ手の run を backend が知らない など — 何が起きたか分からなければ空)。
   終了 code は残らない。名は空き、同じ名で走らせ直すと別の run(取っ手の run-id が違う)になる。"
  {:tags {:context "run" :role "type"}
   :check [(isinstance detail str)]}
  (setv #^ str detail ""))


(val RunState (| RunPending RunLive RunStopped RunGone))
