;;; 名前付きの semaphore の handler 2 つ。業務コードは scheduler の Semaphore の effect だけを使い、どちらを被せるかは
;;; composition root(env)が決める。どちらも scheduled の**内側**に置く(scheduled は CreateSemaphore の子 class も
;;; 拾ってしまうので、名前を見る handler が先に受ける必要がある。手元の semaphore・Spawn・Cancel は scheduled が解く)。
;;;
;;;   named-semaphore-local  1 つの VM の中で、同じ名前 = 同じ手元の semaphore(テストと単一の main)。
;;;   cluster-semaphore      同じ名前 = cluster 全体で同じ lock。取る・延ばす・返すは LeaseOp(判断は保存の側 = coordinator の
;;;                          時計・2026-09-25)。時計(doeff-time の GetTime / Delay)で待つ・延ばす。この handler 自身は I/O をせず、
;;;                          保存の handler(shared-memory / shared-http)と doeff-time の時計の handler を外側に要る。
;;;
;;;   lease-fence            書きの effect を、名前付きの lease を持っていて期限まで余裕がある間だけ外へ通す。持っていない・
;;;                          失った・期限が近い時は外へ出さずに WriteFenced を投げる。問い合わせ(HeldLease)は cluster-semaphore が
;;;                          手元の記憶から答える(書きごとに保存を読まない)。cluster-semaphore より内側・書きの handler より内側に置く。
(require doeff-hy.macros [defhandler defk <-])
(import doeff [EffectBase])
(import doeff_core_effects.scheduler [CreateSemaphore AcquireSemaphore ReleaseSemaphore Spawn Cancel])
(import doeff_time [Delay])
(import doeff_cluster.clock [now-epoch-ms])
(import .shared_model [ReadShared WriteShared])
(import .semaphore_model [CreateNamedSemaphore ClusterSemaphore LeaseLost HeldLease WriteFenced LeaseStanding LeaseOp
                          STANDBY HELD LOST fence-verdict])


;; --- 1 つの VM の中の名前の表 ------------------------------------------------------------------

(defhandler named-semaphore-local [#^ dict names]
  (CreateSemaphore [permits]
    :when (isinstance effect CreateNamedSemaphore)
    (setv known (.get names effect.name))
    (if (is-not known None)
        (resume known)
        (do
          (<- created (CreateSemaphore permits))
          (setv (get names effect.name) created)
          (resume created)))))


;; --- cluster ----------------------------------------------------------------------------------

(defclass SemaphoreSession []
  "cluster-semaphore の手元の記憶。holder = この process を cluster で一意に指す名(composition root が決める)。
   held = 名前 → 持っている token の列(permit は区別しないので、Release は古い物から返す)。"
  (defn __init__ [self #^ str holder [ttl-seconds 15.0] [poll-seconds 0.5]]
    ;; expires = token → 保存に書けたと確かめた期限(epoch ミリ秒)。lease-fence はこれと時計だけで判じる。
    (setv self.holder holder self.ttl-seconds ttl-seconds self.poll-seconds poll-seconds
          self.seq 0 self.held {} self.lost (set) self.renewers {} self.expires {}
          ;; 一度でも持った名前(LeaseStanding の standby と lost を分ける)。
          self.ever-held (set)))

  (defn #^ str next-token [self]
    (+= self.seq 1)
    (.format "{}/{}" self.holder self.seq))

  (defn #^ int ttl-ms [self] (int (* 1000 self.ttl-seconds)))

  (defn hold [self #^ str name #^ str token]
    (.add self.ever-held name)
    (.append (.setdefault self.held name []) token))

  (defn #^ str standing-of [self #^ str name]
    "LeaseStanding の答え: 持っている = held・一度も持っていない = standby・持っていたが失った / 返した = lost。"
    (cond
      (is-not (self.hold-of name) None) HELD
      (in name self.ever-held) LOST
      True STANDBY))

  (defn #^ bool holds [self #^ str token]
    (any (gfor tokens (.values self.held) (in token tokens))))

  (defn take [self #^ str name]
    (setv tokens (.get self.held name []))
    (if tokens (.pop tokens 0) None))

  (defn hold-of [self #^ str name]
    "HeldLease の答え: 名前の最も古い token と、その確かめた期限。持っていない・失った = None。"
    (setv tokens (.get self.held name []))
    (when (not tokens) (return None))
    (setv token (get tokens 0))
    (if (in token self.lost)
        None
        {"token" token "expiresMs" (.get self.expires token 0)})))


(defk acquire-lease [session semaphore]
  {:pre [(: session SemaphoreSession) (: semaphore ClusterSemaphore)] :post [(: % str)]}
  ;; 空きが出るまで poll ごとに問い直す(先着順の保証は無い)。判断は coordinator が自分の時計で 1 か所で行う(LeaseOp)。
  ;; 柵の期限 = 送る前に読んだ自分の時計 + TTL(coordinator が書いた期限より必ず前 — semaphore_model の冒頭)。
  (setv token (.next-token session))
  (while True
    (<- sent int (now-epoch-ms))
    (<- answer dict (LeaseOp semaphore.name "claim" token semaphore.permits (.ttl-ms session)))
    (if (get answer "ok")
        (do (setv (get session.expires token) (+ sent (.ttl-ms session)))
            (.hold session semaphore.name token)
            (return token))
        (<- (Delay session.poll-seconds)))))


(defk renew-lease [session semaphore token]
  {:pre [(: session SemaphoreSession) (: semaphore ClusterSemaphore) (: token str)] :post [(: % (type None))]}
  ;; 持っている間、TTL の 1/3 ごとに期限を延ばす。返した後(holds が偽)は書かない — 取り消しが届かなくても止まる。
  ;; coordinator に届かない(通信の失敗・作り直しの途中)時は poll ごとに試し直す(延長の係が例外で消えると、途絶が直っても
  ;; 延ばせず書きが止まったままになる)。届かない間の柵の期限は、最後に延ばせた時の値のまま進まない。
  ;; 延ばしたかどうか分からない(返事の前に切れた)時も期限は進めない(柵は早めに締まる側に外れる)。
  (while True
    (<- (Delay (/ session.ttl-seconds 3)))
    (setv done False)
    (while (not done)
      (when (not (.holds session token)) (return None))
      (<- sent int (now-epoch-ms))
      (setv answer None)
      (try
        (<- answer dict (LeaseOp semaphore.name "renew" token semaphore.permits (.ttl-ms session)))
        (except [e Exception] (setv answer None)))
      (cond
        (is answer None) (<- (Delay session.poll-seconds))
        (get answer "ok") (do (setv (get session.expires token) (+ sent (.ttl-ms session)))
                              (setv done True))
        True (do (.add session.lost token)
                 (return None))))))


(defk release-lease [session semaphore]
  {:pre [(: session SemaphoreSession) (: semaphore ClusterSemaphore)] :post [(: % (type None))]}
  (setv token (.take session semaphore.name))
  (when (is token None)
    (raise (RuntimeError (.format "semaphore {} を持っていないのに返した" semaphore.name))))
  (setv renewer (.pop session.renewers token None))
  (when (is-not renewer None) (<- (Cancel renewer)))
  (.pop session.expires token None)
  (when (in token session.lost)
    (.discard session.lost token)
    (raise (LeaseLost (.format "semaphore {} の lease {} は期限切れで他へ移っていた" semaphore.name token))))
  (<- answer dict (LeaseOp semaphore.name "release" token semaphore.permits 0))
  (when (not (get answer "ok"))
    (raise (LeaseLost (.format "semaphore {} の lease {} は期限切れで他へ移っていた" semaphore.name token))))
  None)


(defhandler cluster-semaphore [#^ SemaphoreSession session]
  (CreateSemaphore [permits]
    :when (isinstance effect CreateNamedSemaphore)
    (resume (ClusterSemaphore effect.name permits)))
  (AcquireSemaphore [semaphore]
    :when (isinstance semaphore ClusterSemaphore)
    (<- token (acquire-lease session semaphore))
    (<- renewer (Spawn (renew-lease session semaphore token) :daemon True))
    (setv (get session.renewers token) renewer)
    (resume None))
  (ReleaseSemaphore [semaphore]
    :when (isinstance semaphore ClusterSemaphore)
    (<- (release-lease session semaphore))
    (resume None))
  (HeldLease [name]
    (resume (.hold-of session name)))
  (LeaseStanding [name]
    (resume (.standing-of session name))))


;; --- 書きの柵 ---------------------------------------------------------------------------------

(defhandler lease-fence [#^ str name #^ tuple write-types #^ int margin-ms]
  ;; write-types の effect だけを見る。それ以外は素通し。問い合わせと時計は外側(cluster-semaphore・時計の handler)へ。
  (EffectBase []
    :when (isinstance effect write-types)
    (<- hold (HeldLease name))
    (<- now int (now-epoch-ms))
    (setv refusal (fence-verdict hold now margin-ms))
    (when (is-not refusal None)
      (raise (WriteFenced (.format "{} を断った — lease {}: {}" (. (type effect) __name__) name refusal))))
    (<- answer effect)
    (resume answer)))


;; --- 待機の process の書き ---------------------------------------------------------------------

(defhandler standby-divert [#^ str name #^ tuple write-types]
  ;; lease を一度も持っていない(取りに行って待っている)process の業務の書きは、外へ出さずに「書けた」(True)と答える。
  ;; 入れ替え(handoff)の新しい process は、旧が lease を持っている間も本物の拍を回して「準備できた」を示し(書きは捨てる)、
  ;; 旧が止まって lease を取ってから、拍の状態を作り直して本当に書く(業務の書き手の側)。
  ;; 一度持った後に失った process の書きは素通しにし、外側の lease-fence が断る(柵の意味は変えない)。
  ;; env の一番内側に置く(書きを数える handler も待機の書きを数えない)。write-types は答えが真偽の業務の書きだけにする。
  (EffectBase []
    :when (isinstance effect write-types)
    (<- standing (LeaseStanding name))
    (if (= standing STANDBY)
        (resume True)
        (do (<- answer effect)
            (resume answer)))))
