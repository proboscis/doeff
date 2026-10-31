;;; 名前付きの semaphore の handler 2 つ。業務コードは scheduler の Semaphore の effect だけを使い、どちらを被せるかは
;;; composition root(env)が決める。どちらも scheduled の**内側**に置く(scheduled は CreateSemaphore の子 class も
;;; 拾ってしまうので、名前を見る handler が先に受ける必要がある。手元の semaphore・Spawn・Cancel は scheduled が解く)。
;;;
;;;   named-semaphore-local  1 つの VM の中で、同じ名前 = 同じ手元の semaphore(テストと単一の main)。
;;;   cluster-semaphore      同じ名前 = cluster 全体で同じ lock。取る・延ばす・返すは LeaseOp(判断は保存の側 = coordinator の
;;;                          時計・2026-09-25)。時計(doeff-time の GetTime / Delay)で待つ・延ばす。この handler 自身は I/O をせず、
;;;                          保存の handler(shared-http — テストでは fake の盤の上)と doeff-time の時計の handler を外側に要る。
;;;
;;;   leases-fence           書きの effect を、その書きが名指す lease の組(書きの effect → lease の名の組の関数で決める — #4072 の D-1)の
;;;                          全部を持っていて期限まで余裕がある間だけ外へ通す。どれかを持っていない・失った・期限が近い時は外へ出さずに
;;;                          WriteFenced を投げる。問い合わせ(HeldLease)は cluster-semaphore が手元の記憶から答える(書きごとに保存を
;;;                          読まない)。cluster-semaphore より内側・書きの handler より内側に置く。
;;;
;;; 置き場 = shared/core・役 program(#2332): scheduler の Semaphore を lease の約束(LeaseOp・HeldLease・LeaseStanding)で
;;; 果たす業務の流れで、業務の intent を出す。protocol(受けた intent を汎用の効果へ出し直すだけ — doeff-linter DOEFF130)ではない。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "program"})
(import collections.abc [Callable])
(import doeff [EffectBase])
(import doeff_core_effects [CreateSemaphore AcquireSemaphore ReleaseSemaphore Spawn Cancel TaskCancelledError])
(import doeff_time [Delay])
(import doeff_cluster._shared.core.clock [now-epoch-ms])
(import doeff_cluster._shared.intent.semaphore_model [CreateNamedSemaphore ClusterSemaphore LeaseLost HeldLease WriteFenced
                                                    LeaseStanding LeaseOp LeaseAnswer AwaitLeaseFree STANDBY HELD LOST])
(import doeff_cluster._shared.core.lease_rules [fence-verdict holder-tokens-prefix])


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
   held = 名前 → 持っている token の列(permit は区別しないので、Release は古い物から返す)。
   空きの無い claim は coordinator の待ち AwaitLeaseFree で空きを待つ(時間で問い直す間は持たない — 2026-10-07 の決定 B)。
   retry-seconds = coordinator に届かない時に延長を試し直す間(落ちた相手の戻りを知る試し)。"
  (defn #^ None __init__ [self #^ str holder #^ float [ttl-seconds 15.0] #^ float [retry-seconds 0.5]]
    ;; expires = token → 保存に書けたと確かめた期限(epoch ミリ秒)。leases-fence はこれと時計だけで判じる。
    (setv self.holder holder self.ttl-seconds ttl-seconds self.retry-seconds retry-seconds
          self.seq 0 self.held {} self.lost (set) self.renewers {} self.expires {}
          ;; 一度でも持った名前(LeaseStanding の standby と lost を分ける)。
          self.ever-held (set)))

  (defn #^ str next-token [self]
    ;; token = <担い手>/<番号>(頭は worker が終わった process の lease を外す時と同じ定義 — semaphore_model の頭の註)。
    (+= self.seq 1)
    (+ (holder-tokens-prefix self.holder) (str self.seq)))

  (defn #^ int ttl-ms [self] (int (* 1000 self.ttl-seconds)))

  (defn #^ None hold [self #^ str name #^ str token]
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

  (defn #^ (| str None) take [self #^ str name]
    (setv tokens (.get self.held name []))
    (if tokens (.pop tokens 0) None))

  (defn #^ (| dict None) hold-of [self #^ str name]
    "HeldLease の答え: 名前の最も古い token と、その確かめた期限。持っていない・失った = None。"
    (setv tokens (.get self.held name []))
    (when (not tokens) (return None))
    (setv token (get tokens 0))
    (if (in token self.lost)
        None
        {"token" token "expiresMs" (.get self.expires token 0)})))


(defk acquire-lease [session semaphore]
  {:pre [(: session SemaphoreSession) (: semaphore ClusterSemaphore)] :post [(: % str)]}
  ;; 空きが出るまで待って取り直す(先着順の保証は無い)。判断は coordinator が自分の時計で 1 か所で行う(LeaseOp)。待ちは coordinator の
  ;; 知らせ(AwaitLeaseFree — 返した時・期限が切れた時に起きる)。
  ;; 柵の期限 = 送る前に読んだ自分の時計 + TTL(coordinator が書いた期限より必ず前 — semaphore_model の冒頭)。
  ;; coordinator に届かない(通信の失敗・作り直しの途中)時は、延長と同じく retry-seconds ごとに同じ token で取り直す(claim は同じ token なら
  ;; 何度送っても同じ意味)。届かない例外を呼び手へ投げると、取りに行った task ごと終わり、それを待つ常駐の process が落ちる
  ;; (2026-10-11 02:45 JST の coordinator の作り直しの最中の実例)。空きの待ちが届かない時も同じく retry-seconds おいて取り直す(待ちが
  ;; 終わった task は、空いた後も lease を取らない — 2026-10-12 01:00 JST ごろの /watch の ConnectTimeout の実例)。取り消し
  ;; (TaskCancelledError — scheduler が task の中へ投げる)は届かない失敗として飲まずに上げる(飲むと取り消した task が待ち続ける)。
  ;; 取り消しが、最後の claim が盤に通ったか分からない間(返事を待つ間・届かずに試し直す間)に届いたら、上げる前に同じ token の返しを
  ;; 1 度送る(claim-withdrawn — 盤に通っていた token を期限まで残さない。手元の記録は返事が ok と分かった後にしか付かないので、呼び手は
  ;; この token を知らない)。断られた後の空きの待ちの間は、盤に token が無いので送らない。
  (setv token (.next-token session))
  (var unsure False)
  (try
    (while True
      (<- sent int (now-epoch-ms))
      (var answer None)
      (:= unsure True)
      (try
        (<- claimed LeaseAnswer (LeaseOp semaphore.name "claim" token semaphore.permits (.ttl-ms session)))
        (:= answer claimed)
        (except [cancelled TaskCancelledError] (raise cancelled))
        (except [e Exception] (:= answer None)))
      (cond
        (is answer None) (<- (Delay session.retry-seconds))
        answer.ok (do (setv (get session.expires token) (+ sent (.ttl-ms session)))
                      (.hold session semaphore.name token)
                      (return token))
        True (do (:= unsure False)
                 (try
                   (<- _seen bool (AwaitLeaseFree semaphore.name))
                   (except [cancelled TaskCancelledError] (raise cancelled))
                   (except [e Exception] (<- (Delay session.retry-seconds)))))))
    (except [cancelled TaskCancelledError]
      (when unsure
        (<- (claim-withdrawn semaphore token)))
      (raise cancelled))))


(defk claim-withdrawn [semaphore token]
  {:pre [(: semaphore ClusterSemaphore) (: token str)] :post [(: % (type None))]}
  ;; 取り消した取りの、盤に通ったか分からない claim の token を返す 1 度だけの送り(acquire-lease の取り消しの時)。盤に通っていなければ
  ;; 盤は断るだけ(返しは同じ token なら何度送っても同じ意味)。届かない時は例外を飲む — 通っていた token は期限で解ける(release-lease と
  ;; 同じ終わり)。取り消しの片付けなので、答えを読まない。
  (try
    (<- _released LeaseAnswer (LeaseOp semaphore.name "release" token semaphore.permits 0))
    (except [e Exception] None))
  None)


(defk renew-lease [session semaphore token]
  {:pre [(: session SemaphoreSession) (: semaphore ClusterSemaphore) (: token str)] :post [(: % (type None))]}
  ;; 持っている間、TTL の 1/3 ごとに期限を延ばす。返した後(holds が偽)は書かない — 取り消しが届かなくても止まる。
  ;; coordinator に届かない(通信の失敗・作り直しの途中)時は retry-seconds ごとに試し直す(延長の係が例外で消えると、途絶が直っても
  ;; 延ばせず書きが止まったままになる)。届かない間の柵の期限は、最後に延ばせた時の値のまま進まない。
  ;; 延ばしたかどうか分からない(返事の前に切れた)時も期限は進めない(柵は早めに締まる側に外れる)。
  (while True
    (<- (Delay (/ session.ttl-seconds 3)))
    (var done False)
    (while (not done)
      (when (not (.holds session token)) (return None))
      (<- sent int (now-epoch-ms))
      (var answer None)
      (try
        (<- renewed LeaseAnswer (LeaseOp semaphore.name "renew" token semaphore.permits (.ttl-ms session)))
        (:= answer renewed)
        (except [e Exception] (:= answer None)))
      (cond
        (is answer None) (<- (Delay session.retry-seconds))
        answer.ok (do (setv (get session.expires token) (+ sent (.ttl-ms session)))
                              (:= done True))
        True (do (.add session.lost token)
                 (return None))))))


(defk release-lease [session semaphore]
  {:pre [(: session SemaphoreSession) (: semaphore ClusterSemaphore)] :post [(: % (type None))]}
  ;; 返す前に手元の記憶(token・延ばす係・柵の期限)を片付ける。coordinator に届かない(通信の失敗・作り直しの途中)時は例外を投げずに
  ;; 終える — 延ばす係は止まっているので、lease は最後に延ばせた期限で coordinator の側で解ける(待っている次の持ち手はその時に起きる)。
  ;; 届かない例外を呼び手へ投げると、lease を返して進む常駐の process が返しの 1 回で落ちる。
  (setv token (.take session semaphore.name))
  (when (is token None)
    (raise (RuntimeError (.format "semaphore {} を持っていないのに返した" semaphore.name))))
  (setv renewer (.pop session.renewers token None))
  (when (is-not renewer None) (<- (Cancel renewer)))
  (.pop session.expires token None)
  (when (in token session.lost)
    (.discard session.lost token)
    (raise (LeaseLost (.format "semaphore {} の lease {} は期限切れで他へ移っていた" semaphore.name token))))
  (var answer None)
  (try
    (<- released LeaseAnswer (LeaseOp semaphore.name "release" token semaphore.permits 0))
    (:= answer released)
    (except [e Exception] (:= answer None)))
  (when (and (is-not answer None) (not answer.ok))
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

(val FENCE-PATIENCE-STEP-SECONDS 0.5)  ; 待つ柵が判断を読み直す間(延ばす係が届かない時に試し直す既定の間 retry-seconds と同じ)


(defhandler leases-fence [#^ Callable leases-of #^ tuple write-types #^ int margin-ms #^ int patience-ms]
  ;; 引数に残す理由: どの書きをどの lease で守るかは組み立ての側の業務の判断で、同じ組に別の名の決め方で並べ得る(Ask で読む設定ではない)。
  ;; write-types の effect だけを見る。それ以外は素通し。守る lease の名の組は書きの effect から leases-of(書きの effect → lease の名の組を
  ;; 答える Program — 区画ごとの書きは区画の lease・入口と区画の両方を要る書きは 2 つ、を書き手の側が決める・#4072 の D-1)で決め、組の全部を
  ;; 持っていて期限まで余裕がある間だけ外へ通す。空の組は守りの無い書きを黙って通す事になるので断る。問い合わせと時計は外側
  ;; (cluster-semaphore・時計の handler)へ。
  ;; patience-ms = 期限が近いだけ(手元の記憶はまだ持っている — 延ばす係が保存に届かず試し直している)の間、書きを外へ出さずに待つ上限。
  ;; FENCE-PATIENCE-STEP-SECONDS ごとに判断を読み直し、延ばしが通れば通す。失ったと分かった時(HeldLease が None)と上限を越えた時は
  ;; 断る。待つ間も書きは外へ出ないので、柵の意味は変わらない(0 = 待たずに断る)。保存の作り直し(数十秒)で書きが即座に断られ、
  ;; 書き手が試し直しを使い切るのを避ける。
  (EffectBase []
    :when (isinstance effect write-types)
    (<- names tuple (leases-of effect))
    (when (not names)
      (raise (ValueError (.format "{} を守る lease の名の組が空(leases-of が名を 1 つも答えない)" (. (type effect) __name__)))))
    (<- began int (now-epoch-ms))
    (for [name names]
      (<- hold (| dict None) (HeldLease name))
      (<- now int (now-epoch-ms))
      (var held hold)
      (var refusal (fence-verdict hold now margin-ms))
      (var waited-until now)
      (while (and (is-not refusal None) (is-not held None) (< waited-until (+ began patience-ms)))
        (<- (Delay FENCE-PATIENCE-STEP-SECONDS))
        (<- again (| dict None) (HeldLease name))
        (<- later int (now-epoch-ms))
        (:= held again)
        (:= refusal (fence-verdict again later margin-ms))
        (:= waited-until later))
      (when (is-not refusal None)
        (raise (WriteFenced (.format "{} を断った — lease {}: {}" (. (type effect) __name__) name refusal)))))
    (<- answer effect)
    (resume answer)))


;; --- 待機の process の書き ---------------------------------------------------------------------

(defhandler standby-divert [#^ str name #^ tuple write-types]
  ;; lease を一度も持っていない(取りに行って待っている)process の業務の書きは、外へ出さずに「書けた」(True)と答える。
  ;; 入れ替え(handoff)の新しい process は、旧が lease を持っている間も本物の拍を回して「準備できた」を示し(書きは捨てる)、
  ;; 旧が止まって lease を取ってから、拍の状態を作り直して本当に書く(業務の書き手の側)。
  ;; 一度持った後に失った process の書きは素通しにし、外側の leases-fence が断る(柵の意味は変えない)。
  ;; env の一番内側に置く(書きを数える handler も待機の書きを数えない)。write-types は答えが真偽の業務の書きだけにする。
  (EffectBase []
    :when (isinstance effect write-types)
    (<- standing (LeaseStanding name))
    (if (= standing STANDBY)
        (resume True)
        (do (<- answer effect)
            (resume answer)))))
