;;; Executable ADR: process をまたぐ出来事の backend(doeff-events の notice_events_handler と、その下の知らせの操作の handler =
;;; memory / Redis の Pub/Sub)の配達の法。法の筋書き(Program)は packages/doeff-events/src/doeff_events/notice_laws.py の 1 か所に
;;; 在り、memory の handler と本物の redis-server の両方が同じ筋書きを通る。上の層を 1 つ壊すと、対応する法が赤になる
;;; (packages/doeff-events/tests/test_notice_laws_memory.py)。
;;;
;;; 出自 = agora-redesign #3850。利用者の 2026-10-06 の決め(「redisでもrabbitmqでも使っていいから、イベントに即応してほしい」
;;; 「記録は記録、起動は起動」)と、その後の確定(正しさは記録が持つ・backend は保存しない知らせ = Pub/Sub だけ・確かめる列
;;; 〔Streams・consumer group〕は書かない)。ADR-DOE-EVENTS-001 の出来事の源(記録の service の合図)に、書き手が出す知らせを
;;; 運ぶ源を足す。
;;;
;;; 戻し方: この ADR と doeff-events の 5 file(effects/notices.py・handlers/memory_notices.py・handlers/redis_notices.py・
;;; handlers/notice_events.py・notice_laws.py)とその検を足した commit を revert する。既に在った口(Publish・WaitForEvent・
;;; subscribed_event_handler)は変えていないので、使い手は影響を受けない。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import pathlib [Path])


(defadr ADR-DOE-EVENTS-002
  :title "process をまたぐ知らせの backend の配達の法: 購読は Program の最初の effect より前に始まる・購読が成ってから起動と繋ぎ直しを 1 度 知らせる・知らせは出された時に繋がっている読み手にだけ届く(繋がっていない間の分は届かない)"
  :status "accepted"
  :scope ["packages/doeff-events/src/doeff_events/notice_laws.py"
          "packages/doeff-events/src/doeff_events/handlers/notice_events.py"
          "packages/doeff-events/src/doeff_events/handlers/memory_notices.py"
          "packages/doeff-events/src/doeff_events/handlers/redis_notices.py"
          "packages/doeff-events/src/doeff_events/effects/notices.py"
          "docs/adr/defadr_doeff_events_002_notice_delivery_laws.hy"]
  :problem
    [(fact
       "利用者の決め 2026-10-06 23:0x〜23:2x(逐語): 「記録に書いて記録をポーリングする設計を本当にやめてくれ」「redisでもrabbitmqでも使っていいから、イベントに即応してほしい。」「記録は記録、起動は起動」"
       :evidence "agora-redesign #3850 の頭の引用")
     (fact
       "本物の redis-server 7.2.7 で測った(2026-10-06): 読まない購読者へ 256 KB の知らせを続けて出し、client-output-buffer-limit pubsub を 1 MB にすると、Redis はその購読者の接続を切る(server の log「scheduled to be closed ASAP for overcoming of output buffer limits」)。PUBLISH が「受け手 1」と答えた 18 通のうち、購読者が読めたのは 13 通で、残りは接続の切れと共に失われた。途中の知らせだけが抜けて接続が残る事は無かった(読めた分は頭から連続)。"
       :evidence "packages/doeff-events/tests/test_notice_laws_redis.py::test_subscriber_that_does_not_read_is_disconnected_on_redis")
     (interpretation
       "保存しない知らせは、受け手が繋がっていない間の分を後から届けない。だから backend が約束するのは「いつから届くか」(購読が成った後)と「届かなくなった事を知らせる」(接続の切れ → SourceStalled → SourceResumed)の 2 つで、失った分を埋めるのは、受けた Program が SourceStarted と SourceResumed で 1 度 記録から追いつく事。PUBLISH の受け手の数は「その時に購読していた数」で、受け手が読んだ事の証にはならない。")]
  :decision
    [(rule R1 "配達の法の筋書きは doeff_events.notice_laws の 1 か所に置く。下の層の handler(memory・Redis)と上の層(notice_events_handler)を変える変更は、同じ筋書きを memory と本物の redis-server の両方で通す。")
     (rule R2 "業務の Program には channel の名を出さない。出すのは Publish・WaitForEvent と、出来事の値だけ。")
     (rule R3 "待ちは blocking の取り(期限なし — 止めるのは task の Cancel)と、broker の戻りを待つ期限つきの待ち 1 つ(WaitWithin)だけ。間隔で起きて確かめる形を足さない。")
     (rule R4 "backend は受けた知らせを黙って捨てない: 受けた知らせは全部 process の中の購読者の列へ Publish し、道の表で読めない知らせは源を名指しで落とす。出した知らせの受け手の数(PUBLISH の答え)は Publish の答え NoticeSent に載せて出し手へ返す。")
     (rule R5 "出し損ねの扱いは notice_events_handler の 1 か所だけに置く(2026-10-07・agora-redesign #3864 — cisco-c8・cc1-w24 と確定): broker に届かなかった出来事は、道の held_key(必須の欄)の鍵ごとに持ち(同じ鍵は後の値で置き換え)、Publish は NoticeHeld で答えて Program を止めない。task 1 本が AwaitBrokerBack の答えを待って、残った出来事を出した順に出す。持っている間の Publish はその後ろに並ぶ。時間で出し直す所・回数の上限は置かない。出し手は Publish の答えを見て自分で出し直さない。process が終われば持った物は消え、出し手の起動の時の出し直しと受け手の追いつきで埋める。")]
  :laws
    [(law subscription-precedes-the-body
       :statement "for_all 読み手 r: r の購読は、包んだ Program の最初の effect より前に、broker の側で成っている — Program が最初に出した知らせを、Program 自身が受ける。"
       :counterexamples
         [(counterexample "購読を Program の最初の待ちまで遅らせる上の層 — Program が状態を読んでから待つまでの間に出た知らせが落ち、Program は起こされない")]
       :enforced-by ["packages/doeff-events/tests/test_notice_laws_memory.py::test_event_law_holds_on_memory"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_shared_signal_invariant_holds_across_processes"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_subscribing_after_the_first_read_breaks_the_shared_gap_invariant"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_event_law_holds_on_redis"
                     "test-adr-doe-events-002-laws-are-declared"]
       :wiring "配線済み(2026-10-06)— 筋書き notice_laws.law_subscription_precedes_the_body と、共有の不変条件 tests/event_signal_invariants.py の「読みと待ちの間の出来事を落とさない」。壊した上の層では後者が scheduler の行き止まりで赤になる。")
     (law start-is-told-after-the-subscription
       :statement "for_all 読み手 r: r の源が SourceStarted(初めて繋がった)と SourceResumed(繋ぎ直した)を出すのは、購読が broker の側で成った後だけ — Program がその知らせを受けた後に出された知らせは、r に届く。"
       :counterexamples
         [(counterexample "購読が成る前に SourceStarted を出す上の層 — Program が SourceStarted で記録から追いついた後、購読が成るまでの間に出た知らせが落ちる(追いつきにも知らせにも乗らない)")]
       :enforced-by ["packages/doeff-events/tests/test_notice_laws_memory.py::test_event_law_holds_on_memory"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_telling_the_start_before_the_subscription_breaks_the_start_law"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_event_law_holds_on_redis"]
       :wiring "配線済み(2026-10-06)— 筋書き notice_laws.law_start_is_told_after_the_subscription(起動)と law_start_and_return_are_told_once の中の「繋ぎ直しの後に出した知らせの受け手が 1 以上」(繋ぎ直し)。繋ぎ直しの側を壊した上の層で赤になる検は無い。")
     (law start-and-return-are-told-once
       :statement "for_all 読み手 r: r の源は、初めて購読が成った時に SourceStarted を 1 度、broker への接続が切れて購読を作り直した時に(止まり 1 つにつき)SourceStalled と SourceResumed を 1 度ずつ出す。"
       :counterexamples
         [(counterexample "上の層が、繋ぎ直しても SourceResumed を出さない — Program は、繋がっていない間に落ちた知らせを記録で追いつく合図を受けられない")]
       :enforced-by ["packages/doeff-events/tests/test_notice_laws_memory.py::test_event_law_holds_on_memory"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_not_telling_the_return_breaks_the_start_and_return_law"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_event_law_holds_on_redis"]
       :wiring "配線済み(2026-10-06)— 筋書き notice_laws.law_start_and_return_are_told_once。本物では接続を全部 切って止まりを作る。")
     (law notice-reaches-only-current-subscribers
       :statement "for_all 知らせ n・channel c: n は、出された時に c を購読していて broker に繋がっている読み手にだけ届き、出し手はその数を知る。購読の前・繋がっていない間に出された n は、後から届かない(Pub/Sub の意味)。失った分は、受ける Program が SourceStarted と SourceResumed を受けた時に 1 度 記録から追いつく前提で、backend は埋めない。受け手の数は「読んだ」の証ではない(読まない購読者は上限で接続を切られ、数えられた知らせを失う)。"
       :counterexamples
         [(counterexample "知らせを「必ず届く物」として扱い、仕事の受け渡しを知らせだけに載せる(記録に残さない)— 読み手が繋がっていない間の仕事が消える")
          (counterexample "PUBLISH の受け手の数が 1 以上なら届いたと見なして、記録への書きを省く — 読み手が読む前に接続を切られた知らせが消える")]
       :enforced-by ["packages/doeff-events/tests/test_notice_laws_memory.py::test_broker_law_holds_on_memory"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_notice_announced_during_an_outage_is_not_delivered_later"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_broker_law_holds_on_redis"
                     "packages/doeff-events/tests/test_notice_laws_redis.py::test_subscriber_that_does_not_read_is_disconnected_on_redis"]
       :wiring "配線済み(2026-10-06)— 筋書き notice_laws.law_notice_reaches_only_current_subscribers(購読の前は受け手 0 で届かず、後は受け手 1 で届く)と、止まりの間の知らせが後から届かない事の検(memory)、読まない購読者が接続を切られる事の検(本物)。")
     (law held-notice-is-sent-once-the-broker-is-back
       :statement "for_all 出し手 s・出来事 e: broker が s から e を受けられなかった時、e は捨てられずに s の手元に鍵ごとに持たれ、broker が戻った時(AwaitBrokerBack の答え)に、その時に繋がっている読み手へ届く — 読み手が繋がったままで出し手だけが届かなかった場合も届く。持っている間に同じ鍵の出来事が出れば前の物は出ない。届く順は、残った出来事を出した順。"
       :counterexamples
         [(counterexample "出し損ねを Program の例外にして捨てる上の層 — 読み手は繋がったままなので SourceResumed を受けず、記録から追いつく合図も無いまま、失った出来事が届かない(居ない worker へ仕事を配り続ける)")
          (counterexample "持った物を AwaitBrokerBack の答えでなく時間を置いて出し直す上の層 — 間隔で起きて確かめる形(R3)")
          (counterexample "同じ鍵の新しい出来事を、前の出来事の位置に置き換える上の層 — 戻った後の順が出した順と食い違う")]
       :enforced-by ["packages/doeff-events/tests/test_notice_laws_memory.py::test_held_law_holds_on_memory"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_not_telling_the_return_breaks_the_held_law"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_publish_answers_held_with_the_brokers_words_while_it_is_unreachable"
                     "test-adr-doe-events-002-laws-are-declared"]
       :wiring "配線済み(2026-10-07)— 筋書き notice_laws.law_held_notice_arrives_once_the_broker_is_back と law_held_notices_keep_the_latest_per_key_in_order(HELD_LAWS)。出し手の側だけ broker を失う層(tests/notice_law_support.py の refuses_senders)の下で回す。本物の redis-server では回していない(出し手の側だけの止まりは broker の種類によらず同じ層で作るため)。")]
  :enforcement
    [(deftest test-adr-doe-events-002-laws-are-declared
       ;; 針: 法の筋書きが notice_laws.py の 1 か所に在り、法の組 EVENT_LAWS・HELD_LAWS・BROKER_LAWS に載っている。
       ;; doeff-events は repo の根の環境に入っていないので、import せず file を読む(ADR-DOE-EVENTS-001 と同じ)。
       (val repo-root (. (Path __file__) parent parent parent))
       (val text (.read-text (/ repo-root "packages/doeff-events/src/doeff_events/notice_laws.py") :encoding "utf-8"))
       (for [name ["law_subscription_precedes_the_body" "law_start_is_told_after_the_subscription"
                   "law_start_and_return_are_told_once" "law_notice_reaches_only_current_subscribers"
                   "law_held_notice_arrives_once_the_broker_is_back" "law_held_notices_keep_the_latest_per_key_in_order"]]
         (assert (in (+ "def " name "(") text)
                 (+ "配達の法の筋書きが notice_laws.py に無い(ADR-DOE-EVENTS-002 R1): " name))
         (assert (in (+ name ",") text)
                 (+ "配達の法の筋書きが法の組(EVENT_LAWS / HELD_LAWS / BROKER_LAWS)に載っていない(ADR-DOE-EVENTS-002 R1): " name))))]
  :plans ["agora-redesign #3850" "agora-redesign #3864"])
