;;; 名前付きの lease(cluster の semaphore)の行の純粋な判断 — coordinator(POST /leases/<名>・盤への直の書きの断り)・テストの
;;; fake の盤(tests/board_fake.hy)・worker の返し・子の土台が同じ定義を使う(semaphore_model から分けた・#2107)。
;;; 型・effect・定数(LeaseOp・SEMAPHORE-PREFIX・FENCE-MARGIN-MS・LEASE-MAX-TTL-MS …)は doeff_cluster.shared.intent.semaphore_model。
;;; 期限の時計と担い手の名の綴りの経緯は semaphore_model の頭の註。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import collections.abc [Mapping])
(import doeff_cluster.shared.intent.protocol [BodyInvalid])
(import doeff_cluster.shared.intent.semaphore_model [SEMAPHORE-PREFIX FENCE-MARGIN-MS LEASE-OPS LEASE-MAX-TTL-MS LeaseAnswer])


(deff lease-holder [#^ str job #^ str instance]  ; defk にできない: worker の返し(worker/protocol/lease_release)も呼ぶ純粋な判断
  {:pre [(: job str) (: instance str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "名前付きの lease の担い手の名を、名乗る側(子の土台)と外す側(worker)が同じ綴りで作るため: <job>/<process の世代の名>。"
  (.format "{}/{}" job instance))


(deff holder-tokens-prefix [#^ str holder]  ; defk にできない: SemaphoreSession(手元の記憶の class)と worker の返し(Program の外)が呼ぶ純粋な判断
  {:pre [(: holder str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "担い手 holder の token の頭(<担い手>/)を、token を作る側(SemaphoreSession.next-token)と担い手の token を全部外す側(worker/protocol/lease_release)が
   同じ綴りで作るため。"
  (+ holder "/"))

(defn #^ (| str None) lease-timing-refusal [#^ (| int float) ttl-seconds #^ int margin-ms]
  "純粋: TTL と柵の余裕の組が成り立たないなら理由の文。延長は TTL の 1/3 ごとなので、余裕が TTL の半分を越えると 1 回の延長の
   遅れで柵が締まる。余裕が書きの上限(FENCE-MARGIN-MS)より短いと、期限の後に書きが着く。"
  (setv ttl-ms (int (* 1000 ttl-seconds)))
  (cond
    (< margin-ms FENCE-MARGIN-MS)
      (.format "柵の余裕 {} ms は書きが着くまでの上限 {} ms より短い(期限の後に書きが着きうる)" margin-ms FENCE-MARGIN-MS)
    (> (* 2 margin-ms) ttl-ms)
      (.format "柵の余裕 {} ms が TTL {} ms の半分を越える(1 回の延長の遅れで書きが止まる)" margin-ms ttl-ms)
    True None))

(defn #^ (| str None) fence-verdict [#^ (| dict None) hold #^ int now-ms #^ int margin-ms]
  "純粋: 書いてよいか。答え = None(通す)か、断る理由の文字列。期限の margin-ms 前で締める(時計のずれと書きの往復の分)。"
  (cond
    (is hold None) "lease を持っていない(取る前か、失った)"
    (>= (+ now-ms margin-ms) (get hold "expiresMs"))
      (.format "lease の期限が近いか過ぎた(いま {} ・期限 {} ・余裕 {} ms)" now-ms (get hold "expiresMs") margin-ms)
    True None))

(defn #^ tuple lease-op [#^ (| dict None) row #^ str op #^ str token #^ int permits #^ int ttl-ms #^ int now-ms]
  "純粋: lease の行と操作 → #(次の行 答え — LeaseAnswer)。行が変わらなければ同じ row を返す。時刻 now-ms は coordinator の時計。
   受けられない操作・期限・token は BodyInvalid(要求の誤り — coordinator の口では 400・ValueError の子)。"
  (when (not-in op LEASE-OPS) (raise (BodyInvalid (+ "知らない lease の操作: " (repr op)))))
  (when (and (in op #("claim" "renew")) (not (< 0 ttl-ms (+ LEASE-MAX-TTL-MS 1))))
    (raise (BodyInvalid (.format "ttlMs は 0 より大きく {} 以下: {}" LEASE-MAX-TTL-MS ttl-ms))))
  (when (not token) (raise (BodyInvalid "token が要る")))
  (cond
    (= op "claim")
      (do (setv updated (claim row permits token now-ms ttl-ms))
          (if (is updated None)
              #(row (LeaseAnswer :ok False :reason "空きが無い" :ttl-ms ttl-ms :dropped 0))
              #(updated (LeaseAnswer :ok True :reason None :ttl-ms ttl-ms :dropped 0))))
    (= op "renew")
      (do (setv updated (renew row token now-ms ttl-ms))
          (if (is updated None)
              #(row (LeaseAnswer :ok False :reason "lost" :ttl-ms ttl-ms :dropped 0))
              #(updated (LeaseAnswer :ok True :reason None :ttl-ms ttl-ms :dropped 0))))
    (= op "release")
      (do (setv #(updated present) (release row token now-ms))
          #(updated (LeaseAnswer :ok present :reason (if present None "lost") :ttl-ms 0 :dropped 0)))
    True
      (do (setv updated (drop-holders row token))
          (if (or (is row None) (is updated None))  ; row が None なら drop-holders は None を返す(外す担い手が無い)
              #(row (LeaseAnswer :ok True :reason None :ttl-ms 0 :dropped 0))
              #(updated (LeaseAnswer :ok True :reason None :ttl-ms 0 :dropped (- (len (get row "holders")) (len (get updated "holders")))))))))

(defn #^ (| str None) semaphore-write-refusal [#^ object before #^ object after #^ int now-ms]
  "純粋: 盤への直の書き(旧い版の compare-and-set)が、coordinator の時計でまだ切れていない担い手を追い出して新しい担い手を
   足す・permits を越えて足すなら、断る理由の文。外すだけの書き(返す・worker の drop)は通す。形の読めない値は触らない(None)。"
  (defn #^ (| dict None) holders-of [#^ object row] (if (and (isinstance row dict) (isinstance (.get row "holders") dict)) (get row "holders") None))
  (when (not (isinstance after dict)) (return None))
  (setv old (or (holders-of before) {}) new (holders-of after))
  (when (is new None) (return None))
  (setv added (lfor t new :if (not-in t old) t))
  (when (not added) (return None))
  (setv live (dfor #(t e) (.items old) :if (> e now-ms) t e)
        evicted (lfor t live :if (not-in t new) t)
        permits (.get after "permits" 1))
  (cond
    evicted (.format "coordinator の時計でまだ切れていない担い手 {} を外して {} を足す書きは断る" evicted added)
    (> (+ (len live) (len added)) permits) (.format "permits {} を越えて {} を足す書きは断る" permits added)
    True None))

(defn #^ str semaphore-key [#^ str name]
  (+ SEMAPHORE-PREFIX name))


;; 行は盤の読みの答えの値のまま受ける(写像の読みの口 Mapping — 値の型を細かく持つ行も渡せる)。答えの要素 = token → 期限の epoch ms。
(defn #^ (get dict #(str int)) live-holders [#^ (| (get Mapping #(str object)) None) row #^ int now-ms]
  "純粋: 行の担い手のうち期限が now より後の物。"
  (if (is row None)
      {}
      (dfor #(token expires) (.items (get row "holders")) :if (> expires now-ms) token expires)))


(defn #^ (| int None) lease-full-until [#^ (| dict None) row #^ int now-ms]
  "純粋: 行が今の刻で満ちていれば(期限の切れていない担い手が permits 以上)、最初に空く刻(担い手の期限のいちばん早い物 — 期限の刻に
   その担い手は live-holders から落ちる)。空きがあれば None。GET /watch?lease=<名> の待ちが起きる条件と、coordinator が起きる刻の 1 か所。"
  (setv live (live-holders row now-ms))
  (if (and (is-not row None) (>= (len live) (get row "permits")))
      (min (.values live))
      None))


(defn #^ (| dict None) claim [#^ (| dict None) row #^ int permits #^ str token #^ int now-ms #^ int ttl-ms]
  "純粋: permit を 1 つ取った後の行。空きが無ければ None。期限の切れた担い手はこの書きで落とす。
   同じ名前で permits が食い違えば BodyInvalid(要求の誤り・ValueError の子 — 同じ名前は同じ lock でなければならない)。"
  (when (and (is-not row None) (!= (get row "permits") permits))
    (raise (BodyInvalid (.format "同じ名前の semaphore の permits が食い違う: 行 {} / 要求 {}" (get row "permits") permits))))
  (setv live (live-holders row now-ms))
  (if (or (in token live) (< (len live) permits))
      {"permits" permits "holders" (| live {token (+ now-ms ttl-ms)})}
      None))


(defn #^ (| dict None) renew [#^ (| dict None) row #^ str token #^ int now-ms #^ int ttl-ms]
  "純粋: 期限を延ばした後の行。token が行に無ければ None(= lease を失った)。
   行に残っている間は、期限が過ぎていても他の誰も書いていない(書きは期限切れを落とす)ので延ばしてよい。"
  (if (or (is row None) (not-in token (get row "holders")))
      None
      {"permits" (get row "permits")
       "holders" (| (live-holders row now-ms) {token (+ now-ms ttl-ms)})}))


(defn #^ tuple release [#^ (| dict None) row #^ str token #^ int now-ms]
  "純粋: permit を返した後の行と、token が行に在ったか。"
  (if (or (is row None) (not-in token (get row "holders")))
      #(row False)
      #({"permits" (get row "permits")
         "holders" (dfor #(t e) (.items (live-holders row now-ms)) :if (!= t token) t e)}
        True)))


;; row は盤の行の値そのもの(形を確かめてから読む — 形の読めない値は触らず None)。
(defn #^ (| dict None) drop-holders [#^ object row #^ str prefix]
  "純粋: token が prefix で始まる担い手を外した後の行。外す物が無ければ None。終了を確かめた process の lease を、期限を待たずに
   返すために worker が使う(prefix = holder-tokens-prefix(lease-holder job 世代の名) — 頭の註)。
   期限の判断は入れない(外すのは名指した担い手だけで、他の担い手の期限はそのまま)。"
  (when (or (not (isinstance row dict)) (not (isinstance (.get row "holders") dict))) (return None))
  (setv kept (dfor #(t e) (.items (get row "holders")) :if (not (.startswith t prefix)) t e))
  (if (= (len kept) (len (get row "holders")))
      None
      (| row {"holders" kept})))
