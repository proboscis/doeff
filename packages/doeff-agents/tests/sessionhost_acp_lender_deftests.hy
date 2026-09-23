;;; 試作(card acp:kanban-issue:ki-fd0f3b234a38・設計 v2 D2): 貸す側が今は貸せない断りを「誰が答えられるか」で分ける。
;;;
;;; 盲検 A の反例: 貸す側の途絶は runner に 3 つの面で届く — master の 503 worker-unreachable・worker の引換の口の
;;; 503 master-unreachable・接続が答えない status 0。設計 v1 の D2 は 1 つ目しか拾わず、残る 2 面は今日どおり
;;; 運び手を終わらせて数える組み直しになる(預かり所が 90 秒で気付くまでに郵便が死ぬ)。
;;; ここで固定するのは分類の純関数だけ(呼び手の拍・HTTP は無い)。

(require doeff-hy.macros [deftest])

(import random)
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  LeaseRefused PHASE-BOUND
  CONDITION-CREDENTIAL-LENDER-UNREACHABLE CONDITION-CREDENTIAL-UNAVAILABLE
  CUSTODY-ANSWERER-ANOTHER-CARRIER CUSTODY-ANSWERER-LENDER CUSTODY-ANSWERER-UNANSWERED
  CUSTODY-UNANSWERED-WINDOW-MS REFUSED-ATTEMPT-CONDITION-TYPES])
(import doeff_agents.sessionhost.acp.judgment [custody-refusal-verdict-of])

(setv STATUS {"phase" PHASE-BOUND
              "binding" {"node" "mac-1" "profile" "personal" "account" "acct" "attempt" 2 "nodeRow" "nr-1"}})

;; 09-23 の実測の本文(15:08:54Z〜・4 台の機体で同じ)。
(setv MEASURED-503-TEXT "口座の worker personal へ届かない(worker の heartbeat が 90 秒来ていないか、この口座を名乗っていない)— 貸与の行を作らない")

(defn verdict [refusal [since None] [now 7000]]
  (run (custody-refusal-verdict-of refusal STATUS now since)))


(deftest test-the-lender-faces-that-custody-names-are-lender
  ;; 面 1: master の 503 — code と why(heartbeat が古い)を名乗る。
  (setv face1 (verdict (LeaseRefused 503 MEASURED-503-TEXT None "lease" "worker-unreachable" "heartbeat-stale")))
  (assert (= face1.answerer CUSTODY-ANSWERER-LENDER) face1.answerer)
  (assert (= face1.condition-type CONDITION-CREDENTIAL-LENDER-UNREACHABLE))
  (assert (= face1.held {"type" "CredentialLenderUnreachable" "status" "True" "reason" MEASURED-503-TEXT
                         "attempt" 2 "at" 7000 "stage" "lease" "code" "worker-unreachable"
                         "why" "heartbeat-stale" "account" "acct" "nodeRow" "nr-1"})
          face1.held)
  ;; 面 2(盲検 A): worker の引換の口が master へ届かない — 預かり所自身の機械の語。
  (setv face2 (verdict (LeaseRefused 503 "master へ届かない(connection refused)— 引換券の一回限りは master だけが保証するので token を渡さない"
                                     None "redeem" "master-unreachable" None)))
  (assert (= face2.answerer CUSTODY-ANSWERER-LENDER) face2.answerer)
  ;; 記録の型は runner が次の拍で同じ試みを起こさない集合の中(監督が数えずに置き直す)。
  (assert (in CONDITION-CREDENTIAL-LENDER-UNREACHABLE REFUSED-ATTEMPT-CONDITION-TYPES)))


(deftest test-standing-worker-unreachable-reasons-stay-another-carrier
  ;; 同じ code でも、待っても晴れない why(失効・口座を名乗っていない・名簿に無い)と why の無い本文(古い預かり所)は
  ;; 今日どおり(時間で晴れる語に畳まない — 盲検 A の副次の所見)。
  (for [why ["worker-revoked" "account-not-advertised" "worker-unknown" "url-not-advertised" None]]
    (setv v (verdict (LeaseRefused 503 MEASURED-503-TEXT None "lease" "worker-unreachable" why)))
    (assert (= v.answerer CUSTODY-ANSWERER-ANOTHER-CARRIER) f"{why}: {v.answerer}")
    (assert (= v.condition-type CONDITION-CREDENTIAL-UNAVAILABLE)))
  ;; code の無い 503(runner の手元の宣言の欠け)も今日どおり。
  (setv undeclared (verdict (LeaseRefused 503 "custody URL is not declared" None)))
  (assert (= undeclared.answerer CUSTODY-ANSWERER-ANOTHER-CARRIER)))


(deftest test-an-unanswered-borrow-waits-out-the-custody-window-then-blames-the-carrier
  ;; 面 3(盲検 A): worker の pod が落ちている / この機体からの道が切れている — status 0。
  (setv refused (LeaseRefused 0 "unreachable: [Errno 111] Connection refused" None "redeem"))
  (setv first (verdict refused None 7000))
  (assert (= first.answerer CUSTODY-ANSWERER-UNANSWERED) first.answerer)
  (assert (= first.unanswered-since-ms 7000))
  (assert (is first.held None) "窓の中は行に何も書かない")
  (assert (> first.retry-at-ms 7000))
  ;; 窓の中の撃ち直しは最初の刻を保つ。
  (setv again (verdict refused 7000 (+ 7000 CUSTODY-UNANSWERED-WINDOW-MS -1)))
  (assert (= again.answerer CUSTODY-ANSWERER-UNANSWERED))
  (assert (= again.unanswered-since-ms 7000))
  ;; 窓を過ぎても答えない = 預かり所は worker を生きていると言い続けている = この機体の道 → 今日どおり別の運び手。
  (setv past (verdict refused 7000 (+ 7000 CUSTODY-UNANSWERED-WINDOW-MS)))
  (assert (= past.answerer CUSTODY-ANSWERER-ANOTHER-CARRIER) past.answerer)
  ;; master の段の status 0(09-23 13:01Z の形)も同じ窓。
  (setv master (verdict (LeaseRefused 0 "unreachable: [Errno 54] Connection reset by peer" None "lease")))
  (assert (= master.answerer CUSTODY-ANSWERER-UNANSWERED)))


(deftest test-the-class-never-reads-the-prose
  ;; 散文を乱数に替えても class は変わらない(分類の鍵は段・status・code・why だけ)。
  (setv rng (random.Random 20260923))
  (for [_ (range 50)]
    (setv text (.join "" (lfor _ (range (rng.randint 0 40)) (chr (rng.randint 0x3041 0x30ff)))))
    (assert (= (. (verdict (LeaseRefused 503 text None "lease" "worker-unreachable" "heartbeat-stale")) answerer)
               CUSTODY-ANSWERER-LENDER))
    (assert (= (. (verdict (LeaseRefused 503 text None "lease" "worker-unreachable" "worker-revoked")) answerer)
               CUSTODY-ANSWERER-ANOTHER-CARRIER))
    (assert (= (. (verdict (LeaseRefused 503 text None "redeem" "master-unreachable" None)) answerer)
               CUSTODY-ANSWERER-LENDER))))


(deftest test-the-class-does-not-read-the-machine
  ;; S3(hardware): 新しい種類の機体(GCP の VM・k3s の pod・Mac)が加わっても、貸す側の断りの class は機体に依らない
  ;; (09-23 の 503 は 4 台で同じ答え)。機体の道の故障は窓を過ぎた status 0 だけが名指す(上の検)。
  (setv refused (LeaseRefused 503 MEASURED-503-TEXT None "lease" "worker-unreachable" "heartbeat-stale"))
  (for [node ["agentd-pool-0" "agentd-pool-1" "Proboscis-MBP" "CA-20038667" "ca-gcp-0"]]
    (setv on-node {"phase" PHASE-BOUND "binding" {"node" node "profile" "personal" "account" "acct" "attempt" 1}})
    (assert (= (. (run (custody-refusal-verdict-of refused on-node 7000)) answerer) CUSTODY-ANSWERER-LENDER) node)))
