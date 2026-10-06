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
     (rule R3 "待ちは blocking の取り(期限なし — 止めるのは task の Cancel)と、broker の戻りを待つ期限つきの待ち 1 つ(WaitWithin)だけ。間隔で起きて確かめる形を足さない(唯一の例外は R6 の 1 か所)。")
     (rule R4 "backend は受けた知らせを黙って捨てない: 受けた知らせは全部 process の中の購読者の列へ Publish し、道の表で読めない知らせは源を名指しで落とす。出した知らせの受け手の数(PUBLISH の答え)は Publish の答え NoticeSent に載せて出し手へ返す。")
     (rule R5 "出し損ねの扱いは notice_events_handler の 1 か所だけ(2026-10-07・agora-redesign #3864 — cisco-c8 の見直しの答え #3850 issuecomment-6021750691): Publish は届かなくても例外を上げず、閉じた型の 3 つ(NoticeSent・NoticeGapMarked・NoticeDropped)で答える。道は必須の欄 when_unsent で MarkGap(channel に欠けの印)か Drop(持たない)を選ぶ。持つのは channel の印だけで出来事は持たない。欠けは定まった知らせ GAP_NOTICE 1 通で、broker が戻った時・次に通る Publish の前・出し手の起動の時(MarkGap.start_channels)に出し、受ける包みは SourceMissed にする(受け手は記録から 1 度追いつく)。欠けを出している間の Publish は、終わるのを待ってから出る。次の Publish が欠けを全部出したら戻りを待つ task は止まる。出し手は Publish の答えを見て自分で出し直さない。")
     (rule R6 "Redis の戻りの知り方(2026-10-07 の Mac の調整役の決定・agora-redesign #3850 の comment): 欠けの印か止まった購読が戻りを待つ間だけ、broker_back_by_retry(redis_notices.py の 1 か所)が組み立ての名指す間隔(既定なし)で ProbeBroker(繋がるかだけ — Redis では PING・data を読み書きしない)を試し、繋がった所で AwaitBrokerBack に答える。待つ者が居ない間の試しは 0。採らなかった案 = Kubernetes の EndpointSlice の watch(本番の権限を変える)。利用者の原文 2026-10-06 \"so anything that require polling, are to be fixed. polling is a last resort\" を「落ちた相手からは知らせが来ない場合は最後の手段に当たる」と読んだのは Mac の調整役の解釈で、利用者の言葉そのものではない。戻し方 = broker_back_by_retry を消し、AwaitBrokerBack に答える別の物を組み立てに置く。")]
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
     (law missed-notice-is-told-as-a-gap
       :statement "for_all 出し手 s・channel c: broker が s から c の出来事を受けられなかった時、s は例外を上げず c に欠けの印を付け、broker が戻った時か、次に s の Publish が通る時(その前に)、c を読んで繋がったままの読み手へ欠け(SourceMissed)を知らせる。欠けの後に出した出来事は欠けより先に届かない。broker がすぐまた落ちて欠けを出せなかった時は、印は残り次の戻りで出る。"
       :counterexamples
         [(counterexample "出し損ねを Program の例外にする上の層 — 読み手は繋がったままなので SourceResumed を受けず、追いつく合図も無いまま出来事が欠ける(居ない worker へ仕事を配り続ける)")
          (counterexample "出来事そのものを鍵ごとに持ち、最後の値だけを出し直す上の層 — 変化を運ぶ出来事(入力)は別の入力を消し、同じ boot の WorkerGone が WorkerBack の後に届く")
          (counterexample "欠けを出している間の Publish をそのまま出す上の層 — 欠けより先に後の出来事が届く")]
       :enforced-by ["packages/doeff-events/tests/test_notice_laws_memory.py::test_gap_law_holds_on_memory"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_not_telling_the_return_breaks_the_missed_notice_law"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_publish_answers_a_marked_gap_with_the_brokers_words_and_the_body_goes_on"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_a_dropped_route_marks_nothing_and_says_so"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_many_missed_notices_on_one_channel_are_told_as_one_gap"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_a_gap_left_when_a_body_ends_does_not_reach_the_next_body"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_a_sender_tells_a_gap_on_its_start_channels_when_it_starts"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_a_return_nobody_can_tell_ends_the_body_with_that_error_after_the_next_publish_told_the_gap"
                     "packages/doeff-events/tests/test_notice_laws_memory.py::test_the_wait_for_the_return_stops_once_the_next_publish_told_the_gap"
                     "test-adr-doe-events-002-laws-are-declared"]
       :wiring "配線済み(2026-10-07)— 筋書き notice_laws の GAP_LAWS 5 本を、出し手の側だけ broker を失う層(tests の refuses_senders — 読み手は繋がったまま)の下で memory で回す。戻りを知らせない層では行き止まりで赤。本物の redis-server では回していない(出し手の側だけの止まりは broker の種類によらず同じ層で作る)。")
     (law broker-return-is-tried-only-while-waited
       :statement "for_all 時刻の区間 I: I の間に AwaitBrokerBack を待つ者が居なければ、broker_back_by_retry の試し(ProbeBroker)は 0 度。待つ者が居る間は組み立ての名指す間隔ごとに 1 度試し、繋がった最初の試しで答える。次の Publish が欠けを全部出した後は試さない。"
       :counterexamples
         [(counterexample "印が無い間も時間で繋ぎ直しを試す答え手 — 時間で起きる loop が常に残る")
          (counterexample "戻りを待つ task を、次の Publish が欠けを出した後も止めない上の層 — 要らない試しが続く")]
       :enforced-by ["packages/doeff-events/tests/test_broker_back_by_retry.py::test_nothing_is_tried_while_no_gap_is_held"
                     "packages/doeff-events/tests/test_broker_back_by_retry.py::test_a_held_gap_is_tried_every_interval_and_told_at_the_first_try_after_the_return"
                     "packages/doeff-events/tests/test_broker_back_by_retry.py::test_trying_stops_once_the_next_publish_told_the_gap"]
       :wiring "配線済み(2026-10-07)— memory の broker(ProbeBroker に切られているかで答える)と仮想の時計の下で、試しの刻を数える。本物の Redis で出し手の接続だけを切って戻す検は、まだ無い(redis-server の在る機体の日次の検証で足す — #3864)。")]
  :enforcement
    [(deftest test-adr-doe-events-002-laws-are-declared
       ;; 針: 法の筋書きが notice_laws.py の 1 か所に在り、法の組 EVENT_LAWS・GAP_LAWS・BROKER_LAWS に載っている。
       ;; doeff-events は repo の根の環境に入っていないので、import せず file を読む(ADR-DOE-EVENTS-001 と同じ)。
       (val repo-root (. (Path __file__) parent parent parent))
       (val text (.read-text (/ repo-root "packages/doeff-events/src/doeff_events/notice_laws.py") :encoding "utf-8"))
       (for [name ["law_subscription_precedes_the_body" "law_start_is_told_after_the_subscription"
                   "law_start_and_return_are_told_once" "law_notice_reaches_only_current_subscribers"
                   "law_missed_notice_is_told_once_the_broker_is_back" "law_gap_is_told_before_the_next_notice"
                   "law_latest_state_wins_after_a_gap" "law_gap_keeps_the_order_of_later_notices" "law_gap_survives_a_second_cut"]]
         (assert (in (+ "def " name "(") text)
                 (+ "配達の法の筋書きが notice_laws.py に無い(ADR-DOE-EVENTS-002 R1): " name))
         (assert (in (+ name ",") text)
                 (+ "配達の法の筋書きが法の組(EVENT_LAWS / GAP_LAWS / BROKER_LAWS)に載っていない(ADR-DOE-EVENTS-002 R1): " name))))]
  :plans ["agora-redesign #3850" "agora-redesign #3864"])
