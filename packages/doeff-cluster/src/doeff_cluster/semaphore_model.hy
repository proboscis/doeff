;;; lock は doeff の scheduler の Semaphore の effect(CreateSemaphore / AcquireSemaphore / ReleaseSemaphore)で扱う。
;;; 業務コードは threading・multiprocessing・fcntl の lock を直に触らない(規則 = repo の root の .semgrep.yaml)。
;;;
;;; 足したのは名前だけ: CreateNamedSemaphore = CreateSemaphore の子 class に名前の欄を 1 つ足した物。
;;;   * scheduler の CreateSemaphore は permits しか持たず、作るたびに新しい id を振る。cluster の別の worker で
;;;     「同じ lock」を指す手段が引数に無いので、名前を運ぶ最小の形として子 class にした(新しい effect の族は作らない)。
;;;   * 子 class なので、scheduled の handler だけの下では普通の手元の semaphore として解かれる(isinstance で拾われる)。
;;;     Acquire / Release は scheduler の effect をそのまま使う。
;;;   * cluster で効かせる時は semaphore_handlers.cluster-semaphore を scheduled の**内側**に被せる。名前付きの
;;;     semaphore を ClusterSemaphore(scheduler の Semaphore の子)として返し、その Acquire / Release だけを
;;;     共有の保存(ReadShared / WriteShared)の lease へ写す。それ以外の semaphore は scheduled へ素通し。
;;;
;;; 共有の保存の行: semaphore/<名前> = {"permits": n, "holders": {token: 期限の epoch ミリ秒}}
;;;
;;; 期限の時計(2026-09-25 に改めた — docs/decision-2026-09-25-coordinator-review-fixes.md の「lease」):
;;;   以前は取る側・延ばす側の worker が自分の時計で期限を計算して行を compare-and-set で書き、奪う側も自分の時計で「切れた」と
;;;   判じた。時計が進んだ worker は、持ち主の期限より前に奪えた(古い持ち主の柵はまだ開いている = 2 つが同時に書ける)。
;;;   いまは取る・延ばす・返すを 1 つの effect LeaseOp にし、coordinator が自分の時計だけで期限を書き・切れたかを判じる
;;;   (POST /leases/<名前>)。持ち主の柵は「要求を送る前に読んだ自分の時計 + TTL」を期限とする — coordinator が期限を書いた
;;;   瞬間は送った瞬間より後なので、柵が締まるのは coordinator の期限より必ず前(時計の進み方の差だけを前提にする・ずれは問わない)。
;;;   盤への直の書き(旧い版の process)で、coordinator の時計でまだ切れていない担い手を追い出して自分を足す書きは断る
;;;   (semaphore-write-refusal)。どちらの時計も doeff-time の GetTime で読む。
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects.scheduler [CreateSemaphore Semaphore])

(setv SEMAPHORE-PREFIX "semaphore/")


(defclass CreateNamedSemaphore [CreateSemaphore]
  "名前付きの semaphore を作る。cluster の handler の下では、同じ名前 = cluster 全体で同じ lock。"
  (defn __init__ [self #^ str name [permits 1]]
    (.__init__ (super) permits)
    (when (or (not (isinstance name str)) (not name) (in "/" name))
      (raise (ValueError (+ "semaphore の名前は空でない文字列で、/ を含まない: " (repr name)))))
    (setv self.name name))
  (defn __repr__ [self] (.format "CreateNamedSemaphore({!r}, permits={})" self.name self.permits)))


(defclass ClusterSemaphore [Semaphore]
  "cluster の handler が返す handle。scheduler の Semaphore の子なので、業務コードの型は変わらない。"
  (defn __init__ [self #^ str name #^ int permits]
    (.__init__ (super) (+ "cluster:" name))
    (setv self.name name self.permits permits))
  (defn __repr__ [self] (.format "ClusterSemaphore({!r}, permits={})" self.name self.permits)))


(defclass LeaseLost [RuntimeError]
  "持っていたはずの lease が、期限切れの後に他の担い手へ移っていた(Release の時に知らせる)。")


(defclass WriteFenced [RuntimeError]
  "lease を持っていない(失った・期限が近い)ので、書きの effect を外へ出さずに断った(lease-fence)。")

;; いま持っている名前付きの lease を問う(cluster-semaphore が答える)。
;; 答え = {"token": 持っている token, "expiresMs": 最後に保存へ書けた期限(epoch ミリ秒)}。持っていない・失った = None。
;; 期限は「保存に書けたと確かめた値」だけ(書けたか分からない延長は数えない)ので、手元の見積もりは保存の値より遅くならない。
(defclass [(dataclass :frozen True)] HeldLease [EffectBase]
  (#^ str name))

;; 柵の余裕(2026-09-25): 柵は書きを「出す前」にだけ確かめるので、出した書きが相手(業務の書き先)に着くのは確かめた時刻より後になる。
;; 着くまでの最長(書き先の client の 1 回の要求の上限 = 10 秒を想定)より余裕が短いと、期限の直前に出した書きが
;; 期限の後(= 次の持ち主が書き始めた後)に着く。以前の余裕 2 秒はこの穴を開けていた。余裕 = 書きの上限 10 秒 + 時計の進みの差 2 秒。
(setv FENCE-MARGIN-MS 12000)

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

(defn fence-verdict [hold #^ int now-ms #^ int margin-ms]
  "純粋: 書いてよいか。答え = None(通す)か、断る理由の文字列。期限の margin-ms 前で締める(時計のずれと書きの往復の分)。"
  (cond
    (is hold None) "lease を持っていない(取る前か、失った)"
    (>= (+ now-ms margin-ms) (get hold "expiresMs"))
      (.format "lease の期限が近いか過ぎた(いま {} ・期限 {} ・余裕 {} ms)" now-ms (get hold "expiresMs") margin-ms)
    True None))

;; lease の操作 1 つ(coordinator の時計で判じる)。op = claim(取る・持っていれば延ばす)| renew(延ばす)| release(返す)|
;; drop(token が prefix で始まる担い手を外す — worker が終わった process の lease を返す)。
;; 答え = {"ok": bool, "reason": str | None, "ttlMs": int}(drop は "dropped" の数も)。
(defclass [(dataclass :frozen True)] LeaseOp [EffectBase]
  (#^ str name)
  (#^ str op)
  (#^ str token)
  (setv #^ int permits 1)
  (setv #^ int ttl-ms 0))

(setv LEASE-OPS #("claim" "renew" "release" "drop"))
;; 1 回の取る・延ばすで与える期限の上限(coordinator が断る)。
(setv LEASE-MAX-TTL-MS (* 10 60 1000))

(defn #^ tuple lease-op [#^ (| dict None) row #^ str op #^ str token #^ int permits #^ int ttl-ms #^ int now-ms]
  "純粋: lease の行と操作 → #(次の行 答え)。行が変わらなければ同じ row を返す。時刻 now-ms は coordinator の時計。"
  (when (not-in op LEASE-OPS) (raise (ValueError (+ "知らない lease の操作: " (repr op)))))
  (when (and (in op #("claim" "renew")) (not (< 0 ttl-ms (+ LEASE-MAX-TTL-MS 1))))
    (raise (ValueError (.format "ttlMs は 0 より大きく {} 以下: {}" LEASE-MAX-TTL-MS ttl-ms))))
  (when (not token) (raise (ValueError "token が要る")))
  (cond
    (= op "claim")
      (do (setv updated (claim row permits token now-ms ttl-ms))
          (if (is updated None)
              #(row {"ok" False "reason" "空きが無い" "ttlMs" ttl-ms})
              #(updated {"ok" True "reason" None "ttlMs" ttl-ms})))
    (= op "renew")
      (do (setv updated (renew row token now-ms ttl-ms))
          (if (is updated None)
              #(row {"ok" False "reason" "lost" "ttlMs" ttl-ms})
              #(updated {"ok" True "reason" None "ttlMs" ttl-ms})))
    (= op "release")
      (do (setv #(updated present) (release row token now-ms))
          #(updated {"ok" present "reason" (if present None "lost") "ttlMs" 0}))
    True
      (do (setv updated (drop-holders row token))
          (if (is updated None)
              #(row {"ok" True "reason" None "dropped" 0 "ttlMs" 0})
              #(updated {"ok" True "reason" None "dropped" (- (len (get row "holders")) (len (get updated "holders"))) "ttlMs" 0})))))

(defn #^ (| str None) semaphore-write-refusal [#^ object before #^ object after #^ int now-ms]
  "純粋: 盤への直の書き(旧い版の compare-and-set)が、coordinator の時計でまだ切れていない担い手を追い出して新しい担い手を
   足す・permits を越えて足すなら、断る理由の文。外すだけの書き(返す・worker の drop)は通す。形の読めない値は触らない(None)。"
  (defn #^ (| dict None) holders-of [#^ object row] (if (and (isinstance row dict) (isinstance (.get row "holders") dict)) (get row "holders") None))
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


(defn #^ dict live-holders [row #^ int now-ms]
  "純粋: 行の担い手のうち期限が now より後の物。"
  (if (is row None)
      {}
      (dfor #(token expires) (.items (get row "holders")) :if (> expires now-ms) token expires)))


(defn claim [row #^ int permits #^ str token #^ int now-ms #^ int ttl-ms]
  "純粋: permit を 1 つ取った後の行。空きが無ければ None。期限の切れた担い手はこの書きで落とす。
   同じ名前で permits が食い違えば ValueError(同じ名前は同じ lock でなければならない)。"
  (when (and (is-not row None) (!= (get row "permits") permits))
    (raise (ValueError (.format "同じ名前の semaphore の permits が食い違う: 行 {} / 要求 {}" (get row "permits") permits))))
  (setv live (live-holders row now-ms))
  (if (or (in token live) (< (len live) permits))
      {"permits" permits "holders" (| live {token (+ now-ms ttl-ms)})}
      None))


(defn renew [row #^ str token #^ int now-ms #^ int ttl-ms]
  "純粋: 期限を延ばした後の行。token が行に無ければ None(= lease を失った)。
   行に残っている間は、期限が過ぎていても他の誰も書いていない(書きは期限切れを落とす)ので延ばしてよい。"
  (if (or (is row None) (not-in token (get row "holders")))
      None
      {"permits" (get row "permits")
       "holders" (| (live-holders row now-ms) {token (+ now-ms ttl-ms)})}))


(defn release [row #^ str token #^ int now-ms]
  "純粋: permit を返した後の行と、token が行に在ったか。"
  (if (or (is row None) (not-in token (get row "holders")))
      #(row False)
      #({"permits" (get row "permits")
         "holders" (dfor #(t e) (.items (live-holders row now-ms)) :if (!= t token) t e)}
        True)))


(defn drop-holders [row #^ str prefix]
  "純粋: token が prefix で始まる担い手を外した後の行。外す物が無ければ None。終了を確かめた process の lease を、期限を待たずに
   返すために worker が使う(token は「<worker>/<process の世代の名>/…」— services/envs.hy の lease-holder)。
   期限の判断は入れない(外すのは名指した担い手だけで、他の担い手の期限はそのまま)。"
  (when (or (not (isinstance row dict)) (not (isinstance (.get row "holders") dict))) (return None))
  (setv kept (dfor #(t e) (.items (get row "holders")) :if (not (.startswith t prefix)) t e))
  (if (= (len kept) (len (get row "holders")))
      None
      (| row {"holders" kept})))


;; この process の名前付きの lease の立場を問う(cluster-semaphore が答える)。
;; 答え = "standby"(一度も持っていない — 取りに行っている間の待機)・"held"(いま持っている)・"lost"(持っていたが失った)。
;; 待機の process の書きは外へ出さない(semaphore_handlers.standby-divert)。失った process の書きは柵が断る(lease-fence)。
(defclass [(dataclass :frozen True)] LeaseStanding [EffectBase]
  (#^ str name))

(setv STANDBY "standby" HELD "held" LOST "lost")
