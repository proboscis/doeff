;;; 貸す側(預かり所)の途絶を runner がどう分けるかの焦点の検(card acp:kanban-issue:ki-fd0f3b234a38・設計 v2 §10.1・
;;; ADR-DOE-AGENTS-012 R63)。
;;;
;;; 実弾 2026-09-23: 預かり所の worker personal が 3 回黙り、その間の借りは master の 503 worker-unreachable で断られた。
;;; runner はこれを「別の運び手なら通る」(another-carrier)に分類して手番を終わらせ、配達は運び手の組み直し 2 回を
;;; 使い切って郵便 22 通を failed にした(4 台の機体で同じ 503 — 機体を避ける組み直しでは晴れない)。
;;; 盲検 A: 途絶は runner へ 3 つの面で届く(master の 503 / worker の引換の口の 503 master-unreachable / 接続が答えない
;;; status 0)。master が途絶に気付くまでの最初の 90 秒は後ろの 2 つしか出ない。
;;;
;;; ここで撃つのは
;;;   * 分類の純関数(custody-refusal-verdict-of)— 3 つの面・時間で晴れない理由・窓・散文を読まない・機体を読まない
;;;   * 引換の処理ステージの 409(引換券の使用済み)を time にしない(設計 v2 F3)
;;;   * fake の handler で agentd を一周: 接続が答えない借りは行に何も書かず、やり直しの刻の前は借りを撃たず、
;;;     窓の後は今日どおり another-carrier・窓の中で答えれば起こす・預かり所が途絶を名乗れば記録にする・
;;;     手番が別の試みになれば memory の項を捨てて窓を最初から数える
;;; HTTP も subprocess も無い(handler の引換の失敗で貸与を返す腕は test_sessionhost_acp_lender.py の python の検)。

(require doeff-hy.macros [deftest])

(import random)
(import dataclasses [replace])
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AgentdSettings
  CONDITION-CREDENTIAL-LENDER-UNREACHABLE
  CONDITION-CREDENTIAL-UNAVAILABLE
  CUSTODY-ANSWERER-ANOTHER-CARRIER
  CUSTODY-ANSWERER-LENDER
  CUSTODY-ANSWERER-TIME
  CUSTODY-ANSWERER-UNANSWERED
  CUSTODY-UNANSWERED-RETRY-MS
  CUSTODY-UNANSWERED-WINDOW-MS
  LIST-MODE-NONE
  LIST-MODE-WINDOW
  PHASE-BOUND
  PHASE-ENDED
  PHASE-RUNNING
  LeaseRefused
  WatchAdvance])
(import doeff_agents.sessionhost.acp.judgment [custody-refusal-verdict-of list-mode-for])
(import doeff_agents.sessionhost.acp.runtime [initial-state])
;; 世界(fake の 4 handler)は錠の検と同じもの — 第 2 の世界を組まない。
(import sessionhost_acp_lease_deftests [ACCOUNT NODE World bound-job])

(setv STATUS {"phase" PHASE-BOUND
              "binding" {"node" "agentd-pool-0" "nodeRow" "row-1" "profile" "personal" "account" ACCOUNT "attempt" 1}})
;; 09-23 の実測の本文(`ai tell lost --json` の detail の逐語から custody refused (503): を剥がしたもの)。
(setv MEASURED-503-TEXT
      "口座の worker personal へ届かない(worker の heartbeat が 90 秒来ていないか、この口座を名乗っていない)— 貸与の行を作らない")
(setv UNREACHABLE-TEXT "unreachable: [Errno 111] Connection refused")

(defn verdict-of [refusal [now 7000] [since None]]
  (run (custody-refusal-verdict-of refusal STATUS now since)))


;; ---------------------------------------------------------------- 分類の純関数(設計 v2 §10.1 の表)

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
    (assert (= (get v.held "status") "True"))
    (assert (= (get v.held "reason") refusal.error) "預かり所の逐語を畳まない")
    (assert (= (get v.held "attempt") 1))
    (assert (= (get v.held "at") 7000))
    (assert (= (get v.held "account") ACCOUNT))
    (assert (= (get v.held "nodeRow") "row-1"))
    (assert (= (get v.held "stage") refusal.stage))
    (assert (= (get v.held "code") refusal.code))
    (assert (= (.get v.held "why") refusal.why) "why は本文が名乗った時だけ(redeem の面は欄を置かない)")))


(deftest test-standing-worker-unreachable-reasons-stay-another-carrier
  ;; 盲検 A 副次(T7): 同じ worker-unreachable でも why が「時間で晴れない」(失効・名乗りなし・名簿に無い・url なし)か
  ;; why の無い本文は今日どおり another-carrier(待てば晴れる語に畳まない — nobody へ移すかは範囲の外 F4)。
  (for [why ["worker-revoked" "account-not-advertised" "worker-unknown" "url-not-advertised" None]]
    (setv v (verdict-of (LeaseRefused 503 MEASURED-503-TEXT None "lease" "worker-unreachable" why)))
    (assert (= v.answerer CUSTODY-ANSWERER-ANOTHER-CARRIER) f"why={why}: {v.answerer}")
    (assert (= v.condition-type CONDITION-CREDENTIAL-UNAVAILABLE))
    (assert (is v.held None)))
  ;; code の無い 503(今日の預かり所の本文の形)と、処理ステージ・code の組が表に無い 503 も今日どおり。
  (for [refusal [(LeaseRefused 503 MEASURED-503-TEXT None)
                 (LeaseRefused 503 "master に届かない" None "lease" "master-unreachable")
                 (LeaseRefused 503 MEASURED-503-TEXT None "redeem" "worker-unreachable" "heartbeat-stale")]]
    (assert (= (. (verdict-of refusal) answerer) CUSTODY-ANSWERER-ANOTHER-CARRIER) f"{refusal}")))


(deftest test-an-unanswered-borrow-waits-out-the-custody-window-then-blames-the-carrier
  ;; 面 3(接続が答えない status 0): 窓の中は unanswered(行に何も書かない・やり直しの刻を返す)。窓を過ぎても
  ;; 答えないなら another-carrier(預かり所が途絶を名乗らない = この機体の道の故障 → 機体を避けて組み直す)。
  (for [stage ["lease" "redeem"]]
    (setv refusal (LeaseRefused 0 UNREACHABLE-TEXT None stage))
    (setv first (verdict-of refusal 10000))
    (assert (= first.answerer CUSTODY-ANSWERER-UNANSWERED) f"{stage}: {first.answerer}")
    (assert (is first.held None))
    (assert (= first.unanswered-since-ms 10000) "memory が覚えていない = この拍が最初(入れ替わりの後は最初から)")
    (assert (= first.retry-at-ms (+ 10000 CUSTODY-UNANSWERED-RETRY-MS)))
    (assert (in refusal.error first.reason))
    (setv inside (verdict-of refusal (+ 10000 CUSTODY-UNANSWERED-WINDOW-MS -1) first.unanswered-since-ms))
    (assert (= inside.answerer CUSTODY-ANSWERER-UNANSWERED))
    (assert (= inside.unanswered-since-ms 10000) "窓の起点は最初に答えなかった刻のまま")
    (setv after (verdict-of refusal (+ 10000 CUSTODY-UNANSWERED-WINDOW-MS) first.unanswered-since-ms))
    (assert (= after.answerer CUSTODY-ANSWERER-ANOTHER-CARRIER) f"{stage}: {after.answerer}")
    (assert (= after.condition-type CONDITION-CREDENTIAL-UNAVAILABLE))
    (assert (is after.unanswered-since-ms None))))


(deftest test-a-redeem-409-is-not-a-held-lease
  ;; 設計 v2 F3: 引換の処理ステージの 409(引換券の使用済み voucher-spent — 使い回した接続の送り直しで生まれる)は
  ;; 時間では晴れない。断りに master の hold が付いて運ばれても time にしない(以前は time に落ちていた)。
  ;; time は貸与の処理ステージの 409 + hold だけ(今日のまま)。
  (setv spent (verdict-of (LeaseRefused 409 "引換券は使用済み" 1900000 "redeem" "voucher-spent")))
  (assert (= spent.answerer CUSTODY-ANSWERER-ANOTHER-CARRIER) f"redeem 409: {spent.answerer}")
  (assert (is spent.held None))
  (setv held (verdict-of (LeaseRefused 409 "account acct is held by borrower b" 1900000 "lease")))
  (assert (= held.answerer CUSTODY-ANSWERER-TIME) f"lease 409: {held.answerer}")
  (assert (= (get held.held "until") 1900000)))


(deftest test-the-class-never-reads-the-prose
  ;; I4': 分類は機械の語だけで決まる。本文の散文(error)を乱数に替えても class は変わらない。
  (setv rng (random.Random 20260923))
  (for [[stage status code why answerer]
        [#("lease" 503 "worker-unreachable" "heartbeat-stale" CUSTODY-ANSWERER-LENDER)
         #("lease" 503 "worker-unreachable" "worker-store-unreadable" CUSTODY-ANSWERER-LENDER)
         #("redeem" 503 "master-unreachable" None CUSTODY-ANSWERER-LENDER)
         #("lease" 503 "worker-unreachable" "worker-revoked" CUSTODY-ANSWERER-ANOTHER-CARRIER)
         #("redeem" 409 "voucher-spent" None CUSTODY-ANSWERER-ANOTHER-CARRIER)
         #("lease" 0 None None CUSTODY-ANSWERER-UNANSWERED)
         #("redeem" 0 None None CUSTODY-ANSWERER-UNANSWERED)]]
    (for [_ (range 20)]
      (setv prose (.join "" (lfor _ (range (rng.randint 0 40)) (chr (rng.randint 32 0x30ff)))))
      (setv v (verdict-of (LeaseRefused status prose 1900000 stage code why)))
      (assert (= v.answerer answerer) f"{stage} {status} {code} {why} {prose !r}: {v.answerer}"))))


(deftest test-the-class-does-not-read-the-machine
  ;; S3(hardware): 新しい種類の機体(GCP の VM・k3s の pod・Mac)が加わっても、貸す側の断りの class は機体に依らない
  ;; (09-23 の 503 は 4 台で同じ答え)。機体の道の故障は窓を過ぎた status 0 だけが名指す(上の検)。
  (for [refused [(LeaseRefused 503 MEASURED-503-TEXT None "lease" "worker-unreachable" "heartbeat-stale")
                 (LeaseRefused 503 "master に届かない" None "redeem" "master-unreachable")]]
    (for [node ["agentd-pool-0" "agentd-pool-1" "Proboscis-MBP" "CA-20038667" "ca-gcp-0"]]
      (setv on-node {"phase" PHASE-BOUND "binding" {"node" node "profile" "personal" "account" "acct" "attempt" 1}})
      (assert (= (. (run (custody-refusal-verdict-of refused on-node 7000 None)) answerer) CUSTODY-ANSWERER-LENDER) node))))


(deftest test-a-due-retry-reads-the-rows-even-on-a-quiet-beat
  ;; やり直しは受けの腕の中で撃つので、行が変わらない静かな拍(idle)でも刻が来れば行を読み直す(窓 = 変わった行だけ)。
  ;; 刻の前・待ちが無い拍は今日どおり読まない。
  (setv settings (AgentdSettings :node-name NODE :homes-root "/homes" :node-capacity 1))
  (setv idle (WatchAdvance :kind "idle" :sequence 3))
  (setv quiet (replace (initial-state) :last-resync-ms 1000))
  (assert (= (run (list-mode-for idle quiet 2000 settings)) LIST-MODE-NONE))
  (setv world (World))
  (setv world.custody.refuse-with (LeaseRefused 0 UNREACHABLE-TEXT None "lease"))
  (.put-row world.acp (bound-job "s-1"))
  (.tick world 0)
  (setv waiting (replace world.state :last-resync-ms world.local.now-ms))
  (assert (= (len waiting.unanswered-borrows) 1) f"memory の項: {waiting.unanswered-borrows}")
  (setv entry (get waiting.unanswered-borrows 0))
  (assert (= (run (list-mode-for idle waiting (- entry.retry-at-ms 1) settings)) LIST-MODE-NONE) "刻の前は読まない")
  (assert (= (run (list-mode-for idle waiting entry.retry-at-ms settings)) LIST-MODE-WINDOW) "刻が来たら窓を読む"))


;; ---------------------------------------------------------------- agentd を一周(fake の handler・D の配線)

(defn #^ dict job-status [world]
  (. (.job world "s-1") status))

(defn #^ list condition-types [#^ dict status]
  (lfor c (.get status "conditions" []) (get c "type")))


(deftest test-an-unanswered-borrow-writes-nothing-and-borrows-again-only-at-its-retry-time
  ;; 窓の中: 行に何も書かない(Running + sessionHandle のまま・条件も結末も足さない)・やり直しの刻の前は借りを撃たない・
  ;; 刻が来たら借り直す。窓(契約から導いた 120 秒)を過ぎても答えなければ今日どおり another-carrier
  ;; (CredentialUnavailable で Ended — 配達が機体を避けて組み直す)で、memory の項は消える。
  (setv world (World))
  (setv world.custody.refuse-with (LeaseRefused 0 UNREACHABLE-TEXT None "redeem"))
  (.put-row world.acp (bound-job "s-1"))
  (.tick world 0)
  (setv first-ms world.local.now-ms)
  (setv claimed (job-status world))
  (setv generation (. (.job world "s-1") generation))
  (assert (= (get claimed "phase") PHASE-RUNNING) f"claim の後の行: {claimed}")
  (assert (not-in "result" claimed))
  (assert (= (condition-types claimed) []) f"窓の中で条件を書いた: {(condition-types claimed)}")
  (assert (= (len world.custody.asked) 1))
  (assert (= world.state.jobs #()) "memory の InFlightJob には載せない(起きていない)")
  (setv entry (get world.state.unanswered-borrows 0))
  (assert (= #(entry.job-id entry.attempt entry.since-ms entry.retry-at-ms)
             #("s-1" 1 first-ms (+ first-ms CUSTODY-UNANSWERED-RETRY-MS)))
          f"memory の項: {entry}")
  ;; 刻の前の拍(5 秒ごと)は借りを撃たず、行に何も書かない。
  (for [_ (range 2)]
    (.tick world 5000)
    (assert (= (len world.custody.asked) 1) f"刻の前に借りた: {world.local.now-ms}")
    (assert (= (. (.job world "s-1") generation) generation) "刻の前の拍が行を書いた"))
  ;; 刻が来た拍に 1 度だけ借り直す(窓の起点は最初の刻のまま)。
  (.tick world 5000)
  (assert (= world.local.now-ms (+ first-ms CUSTODY-UNANSWERED-RETRY-MS)))
  (assert (= (len world.custody.asked) 2) "刻が来ても借り直さなかった")
  (assert (= (. (.job world "s-1") generation) generation) "窓の中のやり直しが行を書いた")
  (assert (= (. (get world.state.unanswered-borrows 0) since-ms) first-ms) "窓の起点が動いた")
  ;; 窓の最後まで: 行は Running のまま・書かれない。
  (while (< (+ world.local.now-ms 5000) (+ first-ms CUSTODY-UNANSWERED-WINDOW-MS))
    (.tick world 5000)
    (assert (= (get (job-status world) "phase") PHASE-RUNNING) f"窓の中で閉じた: {world.local.now-ms}")
    (assert (= (. (.job world "s-1") generation) generation) f"窓の中で行を書いた: {world.local.now-ms}"))
  (setv asked-in-window (len world.custody.asked))
  (assert (= asked-in-window (// CUSTODY-UNANSWERED-WINDOW-MS CUSTODY-UNANSWERED-RETRY-MS))
          f"窓の中の借りの数: {asked-in-window}")
  ;; 窓が閉じる拍: 答えないまま = 今日どおり another-carrier。
  (.tick world 5000)
  (assert (= world.local.now-ms (+ first-ms CUSTODY-UNANSWERED-WINDOW-MS)))
  (setv ended (job-status world))
  (assert (= (get ended "phase") PHASE-ENDED) f"窓の後も閉じない: {ended}")
  (assert (= (condition-types ended) [CONDITION-CREDENTIAL-UNAVAILABLE]) f"{(condition-types ended)}")
  (assert (= (get (get (get ended "result") "cause") "reason") CONDITION-CREDENTIAL-UNAVAILABLE))
  (assert (= world.state.unanswered-borrows #()) "閉じた手番の項が残った")
  (assert (= world.sessions.launches []) "器は起こさない")
  ;; 閉じた後の拍は借りない(項が漏れない)。
  (setv asked (len world.custody.asked))
  (.tick world 15000)
  (assert (= (len world.custody.asked) asked) "閉じた手番の借りを撃ち直した"))


(deftest test-a-borrow-that-answers-inside-the-window-starts-the-turn
  ;; 窓の中で預かり所が答えた(貸した)ら、同じ claim のまま手番を起こす — 第 2 の起こし口は無く、claim の拍と同じ
  ;; start-claimed を撃ち直す。memory の項は消え、手番は memory の InFlightJob に載る。
  (setv world (World))
  (setv world.custody.refuse-with (LeaseRefused 0 UNREACHABLE-TEXT None "lease"))
  (.put-row world.acp (bound-job "s-1"))
  (.tick world 0)
  (setv sid (.sid world "s-1"))
  (assert (= world.sessions.launches []))
  (setv world.custody.refuse-with None)
  (.tick world CUSTODY-UNANSWERED-RETRY-MS)
  (assert (= (len world.custody.borrowed) 1) "窓の中で答えた預かり所から借りなかった")
  (assert (= (len world.sessions.launches) 1) f"起こさなかった: {world.sessions.launches}")
  (assert (= (.sid world "s-1") sid) "claim の session の id のまま起こす")
  (assert (= world.state.unanswered-borrows #()) "借りた後も項が残った")
  (assert (= (lfor job world.state.jobs job.job-id) ["s-1"]) "起こした手番が memory に無い")
  (setv status (job-status world))
  (assert (= (get status "phase") PHASE-RUNNING))
  (assert (= (condition-types status) []) f"{(condition-types status)}"))


(deftest test-custody-naming-the-outage-inside-the-window-records-the-lost-attempt
  ;; 盲検 A の時系列(面 3 → 面 1): 最初は接続が答えず、窓の中で master が途絶を機械の語で名乗る —
  ;; その拍に CredentialLenderUnreachable を足して phase を離す(Ended にしない)。memory の項は消え、
  ;; 記録を持った行は次の拍で起動も拾い直しもしない(attempt-refused? の 1 点)。
  (setv world (World))
  (setv world.custody.refuse-with (LeaseRefused 0 UNREACHABLE-TEXT None "redeem"))
  (.put-row world.acp (bound-job "s-1"))
  (.tick world 0)
  (setv sid (.sid world "s-1"))
  (setv world.custody.refuse-with
        (LeaseRefused 503 MEASURED-503-TEXT None "lease" "worker-unreachable" "heartbeat-stale"))
  (.tick world CUSTODY-UNANSWERED-RETRY-MS)
  (setv status (job-status world))
  (assert (= (get status "phase") PHASE-RUNNING) f"途絶の断りで phase を動かした: {status}")
  (assert (not-in "result" status) "結末は書かない")
  (assert (= (get (get status "sessionHandle") "sessionId") sid) "sessionHandle も触らない")
  (setv records (lfor c (get status "conditions") :if (= (get c "type") CONDITION-CREDENTIAL-LENDER-UNREACHABLE) c))
  (assert (= (len records) 1) f"記録が 1 行でない: {(get status "conditions")}")
  (setv record (get records 0))
  (assert (= #((get record "status") (get record "reason") (get record "attempt") (get record "at")
               (get record "account") (get record "stage") (get record "code") (get record "why"))
             #("True" MEASURED-503-TEXT 1 world.local.now-ms ACCOUNT "lease" "worker-unreachable" "heartbeat-stale"))
          f"記録の欄: {record}")
  (assert (= world.state.unanswered-borrows #()) "記録にした後も項が残った")
  (assert (= world.sessions.launches []))
  (setv asked (len world.custody.asked))
  (.tick world CUSTODY-UNANSWERED-RETRY-MS)
  (assert (= (len world.custody.asked) asked) "記録を持った試みの借りを撃ち直した")
  (assert (!= (get (job-status world) "phase") PHASE-ENDED) "次の拍が手番を閉じた"))


(deftest test-the-memory-forgets-a-job-that-left-its-attempt-and-a-new-attempt-counts-afresh
  ;; 漏れない: 手番が別の試みになった(監督が置き直した)ら、前の試みの項は受けの拍で消える。新しい試みの
  ;; 窓は、その試みで最初に答えなかった刻から数え直す(前の試みの起点を継がない)。
  (setv world (World))
  (setv world.custody.refuse-with (LeaseRefused 0 UNREACHABLE-TEXT None "lease"))
  (.put-row world.acp (bound-job "s-1"))
  (.tick world 0)
  (setv first-ms world.local.now-ms)
  (assert (= (. (get world.state.unanswered-borrows 0) attempt) 1))
  ;; 監督の置き直し: 同じ機体へ attempt 2 で結び直す(Bound)。
  (setv row (.job world "s-1"))
  (setv placed (dict row.status))
  (setv (get placed "phase") PHASE-BOUND)
  (setv (get placed "binding") {"node" NODE "profile" "personal" "account" ACCOUNT "attempt" 2})
  (.pop placed "sessionHandle" None)
  (.put-row world.acp (replace row :status placed :generation (+ row.generation 1)))
  (.tick world 40000)
  (setv entries world.state.unanswered-borrows)
  (assert (= (len entries) 1) f"項: {entries}")
  (setv entry (get entries 0))
  (assert (= #(entry.job-id entry.attempt entry.since-ms) #("s-1" 2 world.local.now-ms))
          f"新しい試みが前の起点を継いだ: {entry} (最初 {first-ms})")
  ;; 窓の起点が新しい試みの刻なので、最初の刻から 120 秒経っても閉じない。
  (while (< world.local.now-ms (+ first-ms CUSTODY-UNANSWERED-WINDOW-MS 5000))
    (.tick world 5000))
  (assert (= (get (job-status world) "phase") PHASE-RUNNING) "前の試みの起点で窓を閉じた"))


(deftest test-a-replaced-runner-does-not-inherit-the-window
  ;; runner の入れ替わり(process の memory を捨てる — 設計 T8)。行は claim で既に Running + sessionHandle なので、
  ;; 次の runner は memory に無い自分の Running の行として拾い直しの腕に渡し、器に session が無い手番を今日どおり
  ;; SessionFailed で閉じる(配達が有界に組み直す — 前の runner の窓を継いで黙って待ち続けることは無い)。
  ;; ⚠ 設計の「入れ替われば窓を最初から数える」は、claim が借りより後に着く前提の読み。実際は claim が先なので、
  ;; 同じ機体の入れ替わりでは窓を数え直さず今日どおり閉じる(pool の pod の入れ替わりは別の行の化身 = 行を拾わず、
  ;; 監督が置き直した新しい試みの窓は最初から — 上の検)。報告に記録した食い違い。
  (setv world (World))
  (setv world.custody.refuse-with (LeaseRefused 0 UNREACHABLE-TEXT None "lease"))
  (.put-row world.acp (bound-job "s-1"))
  (.tick world 0)
  (assert (= (len world.state.unanswered-borrows) 1))
  (.restart world)
  (assert (= world.state.unanswered-borrows #()) "入れ替わりで memory が残った")
  (.tick world 5000)
  (setv status (job-status world))
  (assert (= (get status "phase") PHASE-ENDED) f"入れ替わりの後も行が Running のまま: {status}")
  (assert (= (condition-types status) ["SessionFailed"]) f"{(condition-types status)}")
  (assert (= world.state.unanswered-borrows #()))
  (assert (= world.sessions.launches [])))
