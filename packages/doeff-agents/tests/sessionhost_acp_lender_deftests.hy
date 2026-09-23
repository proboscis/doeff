;;; 試作(card acp:kanban-issue:ki-fd0f3b234a38・設計 v2 §10.1): 貸す側(預かり所)の途絶を runner がどう分けるかの焦点の検。
;;;
;;; 実弾 2026-09-23: 預かり所の worker personal が 3 回黙り、その間の借りは master の 503 worker-unreachable で断られた。
;;; runner はこれを「別の運び手なら通る」(another-carrier)に分類して手番を終わらせ、配達は運び手の組み直し 2 回を
;;; 使い切って郵便 22 通を failed にした(4 台の機体で同じ 503 — 機体を避ける組み直しでは晴れない)。
;;; 盲検 A: 途絶は runner へ 3 つの面で届く(master の 503 / worker の引換の口の 503 master-unreachable / 接続が答えない
;;; status 0)。master が途絶に気付くまでの最初の 90 秒は後ろの 2 つしか出ない。
;;;
;;; ここで撃つのは分類の純関数(custody-refusal-verdict-of)だけ。HTTP も subprocess も無い。

(require doeff-hy.macros [deftest])

(import random)
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  CONDITION-CREDENTIAL-LENDER-UNREACHABLE
  CONDITION-CREDENTIAL-UNAVAILABLE
  CUSTODY-ANSWERER-ANOTHER-CARRIER
  CUSTODY-ANSWERER-LENDER
  CUSTODY-ANSWERER-UNANSWERED
  CUSTODY-UNANSWERED-RETRY-MS
  CUSTODY-UNANSWERED-WINDOW-MS
  PHASE-BOUND
  LeaseRefused])
(import doeff_agents.sessionhost.acp.judgment [custody-refusal-verdict-of])

(setv ACCOUNT "acct")
(setv STATUS {"phase" PHASE-BOUND
              "binding" {"node" "agentd-pool-0" "nodeRow" "row-1" "profile" "personal" "account" ACCOUNT "attempt" 1}})
;; 09-23 の実測の本文(`ai tell lost --json` の detail の逐語から custody refused (503): を剥がしたもの)。
(setv MEASURED-503-TEXT
      "口座の worker personal へ届かない(worker の heartbeat が 90 秒来ていないか、この口座を名乗っていない)— 貸与の行を作らない")

(defn verdict-of [refusal [now 7000] [since None]]
  (run (custody-refusal-verdict-of refusal STATUS now since)))


(deftest test-the-lender-faces-that-custody-names-are-lender
  ;; 面 1(master の貸与の口の 503 + 時間で晴れる why)と面 2(worker の引換の口の 503 master-unreachable)は lender。
  ;; 手番を終わらせず、数えない失った試みの記録を 1 項足す(監督の数えない表が読む型)。
  (for [refusal [(LeaseRefused 503 MEASURED-503-TEXT None "lease" "worker-unreachable" "heartbeat-stale")
                 (LeaseRefused 503 "worker の store が読めない" None "lease" "worker-unreachable" "worker-store-unreadable")
                 (LeaseRefused 503 "master に届かない" None "redeem" "master-unreachable")]]
    (setv v (verdict-of refusal))
    (assert (= v.answerer CUSTODY-ANSWERER-LENDER) f"{refusal.stage} {refusal.code} {refusal.why}: {v.answerer}")
    (assert (= v.condition-type CONDITION-CREDENTIAL-LENDER-UNREACHABLE))
    (assert (= (get v.held "type") CONDITION-CREDENTIAL-LENDER-UNREACHABLE))
    (assert (= (get v.held "reason") refusal.error) "預かり所の逐語を畳まない")
    (assert (= (get v.held "attempt") 1))
    (assert (= (get v.held "at") 7000))
    (assert (= (get v.held "account") ACCOUNT))
    (assert (= (get v.held "nodeRow") "row-1"))
    (assert (= (get v.held "stage") refusal.stage))
    (assert (= (get v.held "code") refusal.code))
    (assert (= (.get v.held "why") refusal.why))))


(deftest test-standing-worker-unreachable-reasons-stay-another-carrier
  ;; 盲検 A 副次: 同じ worker-unreachable でも why が「時間で晴れない」(失効・名乗りなし・名簿に無い・url なし)か
  ;; why の無い本文は今日どおり another-carrier(待てば晴れる語に畳まない — nobody へ移すかは範囲の外 F4)。
  (for [why ["worker-revoked" "account-not-advertised" "worker-unknown" "url-not-advertised" None]]
    (setv v (verdict-of (LeaseRefused 503 MEASURED-503-TEXT None "lease" "worker-unreachable" why)))
    (assert (= v.answerer CUSTODY-ANSWERER-ANOTHER-CARRIER) f"why={why}: {v.answerer}")
    (assert (= v.condition-type CONDITION-CREDENTIAL-UNAVAILABLE))
    (assert (is v.held None)))
  ;; code の無い 503(今日の預かり所の本文の形)も今日どおり。
  (assert (= (. (verdict-of (LeaseRefused 503 MEASURED-503-TEXT None)) answerer) CUSTODY-ANSWERER-ANOTHER-CARRIER)))


(deftest test-an-unanswered-borrow-waits-out-the-custody-window-then-blames-the-carrier
  ;; 面 3(接続が答えない status 0): 窓の中は unanswered(行に何も書かない・撃ち直しの刻を返す)。窓を過ぎても
  ;; 答えないなら another-carrier(預かり所が途絶を名乗らない = この機体の道の故障 → 機体を避けて組み直す)。
  (for [stage ["lease" "redeem"]]
    (setv refusal (LeaseRefused 0 "unreachable: [Errno 111] Connection refused" None stage))
    (setv first (verdict-of refusal 10000))
    (assert (= first.answerer CUSTODY-ANSWERER-UNANSWERED) f"{stage}: {first.answerer}")
    (assert (is first.held None))
    (assert (= first.unanswered-since-ms 10000))
    (assert (= first.retry-at-ms (+ 10000 CUSTODY-UNANSWERED-RETRY-MS)))
    (assert (in refusal.error first.reason))
    (setv inside (verdict-of refusal (+ 10000 CUSTODY-UNANSWERED-WINDOW-MS -1) first.unanswered-since-ms))
    (assert (= inside.answerer CUSTODY-ANSWERER-UNANSWERED))
    (assert (= inside.unanswered-since-ms 10000) "窓の起点は最初に答えなかった刻のまま")
    (setv after (verdict-of refusal (+ 10000 CUSTODY-UNANSWERED-WINDOW-MS) first.unanswered-since-ms))
    (assert (= after.answerer CUSTODY-ANSWERER-ANOTHER-CARRIER) f"{stage}: {after.answerer}")
    (assert (is after.unanswered-since-ms None))))


(deftest test-the-class-never-reads-the-prose
  ;; I4': 分類は機械の語だけで決まる。本文の散文(error)を乱数に替えても class は変わらない。
  (setv rng (random.Random 20260923))
  (for [[stage status code why answerer]
        [#("lease" 503 "worker-unreachable" "heartbeat-stale" CUSTODY-ANSWERER-LENDER)
         #("redeem" 503 "master-unreachable" None CUSTODY-ANSWERER-LENDER)
         #("lease" 503 "worker-unreachable" "worker-revoked" CUSTODY-ANSWERER-ANOTHER-CARRIER)
         #("lease" 0 None None CUSTODY-ANSWERER-UNANSWERED)]]
    (for [_ (range 20)]
      (setv prose (.join "" (lfor _ (range (rng.randint 0 40)) (chr (rng.randint 32 0x30ff)))))
      (setv v (verdict-of (LeaseRefused status prose None stage code why)))
      (assert (= v.answerer answerer) f"{stage} {status} {code} {why} {prose!r}: {v.answerer}"))))


(deftest test-the-class-does-not-read-the-machine
  ;; S3(hardware): 新しい種類の機体(GCP の VM・k3s の pod・Mac)が加わっても、貸す側の断りの class は機体に依らない
  ;; (09-23 の 503 は 4 台で同じ答え)。機体の道の故障は窓を過ぎた status 0 だけが名指す(上の検)。
  (setv refused (LeaseRefused 503 MEASURED-503-TEXT None "lease" "worker-unreachable" "heartbeat-stale"))
  (for [node ["agentd-pool-0" "agentd-pool-1" "Proboscis-MBP" "CA-20038667" "ca-gcp-0"]]
    (setv on-node {"phase" PHASE-BOUND "binding" {"node" node "profile" "personal" "account" "acct" "attempt" 1}})
    (assert (= (. (run (custody-refusal-verdict-of refused on-node 7000)) answerer) CUSTODY-ANSWERER-LENDER) node)))
