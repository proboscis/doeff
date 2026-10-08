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
;;;
;;; 担い手の名と token の綴り(2026-09-29 に 1 つにした): 担い手 = lease-holder(<job>/<process の世代の名> — cluster で一意・同じ job の
;;; 新旧の世代を分ける)・token = <担い手>/<番号>。子の土台(cluster_foundation.lease-holder-of → SemaphoreSession.next-token)が名乗り、
;;; worker の返し(worker/protocol/lease_release・sim の local.release-leases)が同じ定義で頭を作って外す。以前は名乗りが <job>/<世代>、外しが
;;; <worker>/<世代>/ の頭で食い違い、終わった process の lease が期限まで残った(入れ替えの新しい版が期限まで置けなかった)。
;;;
;;; この module は型・effect・定数だけを持つ(SDK の型の置き場・#2107)。lease の行の純粋な判断(lease-op・claim・
;;; fence-verdict・担い手の名の綴り …)は doeff_cluster.shared.core.lease_rules に在る。
(require doeff-hy.macros [val])
(require doeff-hy.record [defwire])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import typing [ClassVar])
(import doeff_cluster.shared.intent.record_spec [RecordSpec RecordMode HandleKind])
(import doeff_core_effects.scheduler [CreateSemaphore Semaphore])

(setv SEMAPHORE-PREFIX "semaphore/")


(defclass CreateNamedSemaphore [CreateSemaphore]
  "名前付きの semaphore を作る。cluster の handler の下では、同じ名前 = cluster 全体で同じ lock。"
  ;; 記録の引数は名前と数(答えの handle は記録の中の名で名付ける)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__
        (RecordSpec :mode RecordMode.READ :args #("name" "permits") :binds HandleKind.NAMED-SEM))
  (defn #^ None __init__ [self #^ str name #^ int [permits 1]]
    (.__init__ (super) permits)
    (when (or (not (isinstance name str)) (not name) (in "/" name))
      (raise (ValueError (+ "semaphore の名前は空でない文字列で、/ を含まない: " (repr name)))))
    (setv self.name name))
  (defn #^ str __repr__ [self] (.format "CreateNamedSemaphore({!r}, permits={})" self.name self.permits)))


(defclass ClusterSemaphore [Semaphore]
  "cluster の handler が返す handle。scheduler の Semaphore の子なので、業務コードの型は変わらない。"
  (defn #^ None __init__ [self #^ str name #^ int permits]
    (.__init__ (super) (+ "cluster:" name))
    (setv self.name name self.permits permits))
  (defn #^ str __repr__ [self] (.format "ClusterSemaphore({!r}, permits={})" self.name self.permits)))


(defclass LeaseLost [RuntimeError]
  "持っていたはずの lease が、期限切れの後に他の担い手へ移っていた(Release の時に知らせる)。")


(defclass WriteFenced [RuntimeError]
  "lease を持っていない(失った・期限が近い)ので、書きの effect を外へ出さずに断った(leases-fence)。")

;; いま持っている名前付きの lease を問う(cluster-semaphore が答える)。
;; 答え = {"token": 持っている token, "expiresMs": 最後に保存へ書けた期限(epoch ミリ秒)}。持っていない・失った = None。
;; 期限は「保存に書けたと確かめた値」だけ(書けたか分からない延長は数えない)ので、手元の見積もりは保存の値より遅くならない。
(defclass [(dataclass :frozen True)] HeldLease [EffectBase]
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.READ))
  (#^ str name))

;; 柵の余裕(2026-09-25): 柵は書きを「出す前」にだけ確かめるので、出した書きが相手(業務の書き先)に着くのは確かめた時刻より後になる。
;; 着くまでの最長(書き先の client の 1 回の要求の上限 = 10 秒を想定)より余裕が短いと、期限の直前に出した書きが
;; 期限の後(= 次の持ち主が書き始めた後)に着く。以前の余裕 2 秒はこの穴を開けていた。余裕 = 書きの上限 10 秒 + 時計の進みの差 2 秒。
(setv FENCE-MARGIN-MS 12000)

;; lease の操作 1 つ(coordinator の時計で判じる)。op = claim(取る・持っていれば延ばす)| renew(延ばす)| release(返す)|
;; drop(token が prefix で始まる担い手を外す — worker が終わった process の lease を返す)。
;; 答え = LeaseAnswer(下 — #2523 で素の dict を型の値にした)。
(defclass [(dataclass :frozen True)] LeaseOp [EffectBase]
  (#^ str name)
  (#^ str op)
  (#^ str token)
  (setv #^ int permits 1)
  (setv #^ int ttl-ms 0))

(setv LEASE-OPS #("claim" "renew" "release" "drop"))


;; 名前付きの lease に空きが出るまで待つ(coordinator の GET /watch?lease=<名> — 今空いていればすぐ・担い手が返した時と期限が切れた時に
;; 起きる・#3865 の後の単位)。答え = bool(真 = 空きを見た・偽 = 待ちの上限で返った)。空きを見ても取れたとは限らない(他の待つ側が
;; 先に取りうる)ので、待つ側は claim し直す。
(defclass [(dataclass :frozen True)] AwaitLeaseFree [EffectBase]
  (#^ str name))


(defwire LeaseAnswer
  "LeaseOp の答え(coordinator の POST /leases/<名> の返事の本文と同じ形 — 欄の綴りは camel): ok = 操作が通ったか・reason = 通らなかった
   理由(空きが無い・lost — 通れば None)・ttl-ms = 与えた期限(ms — 返す・外すは 0)・dropped = drop で外した担い手の数(ほかの操作は 0)。
   どの欄も既定値を持たない — 書き出し(doeff_hy.wire の dump)は既定値と同じ欄を省くので、旧い版の読み手(返事の dict の欄を引く)が
   欄を失わないよう、いつも 4 つとも書く(#2523)。"
  {:tags {:context "doeff-cluster" :role "type"} :names :camel :unknown :ignore}
  (#^ bool ok)
  (#^ (| str None) reason)
  (#^ int ttl-ms)
  (#^ int dropped))
;; 1 回の取る・延ばすで与える期限の上限(coordinator が断る)。
(setv LEASE-MAX-TTL-MS (* 10 60 1000))


;; この process の名前付きの lease の立場を問う(cluster-semaphore が答える)。
;; 答え = "standby"(一度も持っていない — 取りに行っている間の待機)・"held"(いま持っている)・"lost"(持っていたが失った)。
;; 待機の process の書きは外へ出さない(semaphore_handlers.standby-divert)。失った process の書きは柵が断る(leases-fence)。
(defclass [(dataclass :frozen True)] LeaseStanding [EffectBase]
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.READ))
  (#^ str name))

(setv STANDBY "standby" HELD "held" LOST "lost")
