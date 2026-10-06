;;; Executable ADR: process をまたぐ出来事の backend(doeff-events の stream_events_handler と、その下の列の操作の handler =
;;; memory / Redis)の配達の法。法の筋書き(Program)は packages/doeff-events/src/doeff_events/stream_laws.py の 1 か所に在り、
;;; memory の handler と本物の redis-server の両方が同じ筋書きを通る。handler を 1 つ壊すと、対応する法が赤になる
;;; (packages/doeff-events/tests/test_stream_laws_memory.py)。
;;;
;;; 出自 = agora-redesign #3850(設計 — 仕事を渡す出来事は中身を運ぶ・受け取りを確かめる列 = Redis Streams と consumer group・
;;; すぐ配るだけの知らせ = Pub/Sub・業務の Program の Publish と WaitForEvent の形は変えない)。ADR-DOE-EVENTS-001 の 2026-10-03 の
;;; 改め(出来事の源 = 記録の service)に、利用者の 2026-10-06 の決め(「redisでもrabbitmqでも使っていいから、イベントに即応して
;;; ほしい」「記録は記録、起動は起動」)で、書き手が出す出来事を運ぶ backend を足す。
;;;
;;; 戻し方: この ADR と doeff-events の 5 file(effects/streams.py・handlers/memory_streams.py・handlers/redis_streams.py・
;;; handlers/stream_events.py・stream_laws.py)とその検を足した commit を revert する。既に在った口(Publish・WaitForEvent・
;;; subscribed_event_handler)は変えていないので、使い手は影響を受けない。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import pathlib [Path])


(defadr ADR-DOE-EVENTS-002
  :title "process をまたぐ出来事の backend の配達の法: 処理を終える前に落ちても失われない・同じ group の 1 人にだけ渡る・繋がった時と繋ぎ直した時に 1 度 知らせる・購読は Program の最初の effect より前に始まる・列の頭が切られたら知らせる"
  :status "accepted"
  :scope ["packages/doeff-events/src/doeff_events/stream_laws.py"
          "packages/doeff-events/src/doeff_events/handlers/stream_events.py"
          "packages/doeff-events/src/doeff_events/handlers/memory_streams.py"
          "packages/doeff-events/src/doeff_events/handlers/redis_streams.py"
          "packages/doeff-events/src/doeff_events/effects/streams.py"
          "docs/adr/defadr_doeff_events_002_broker_delivery_laws.hy"]
  :problem
    [(fact
       "利用者の決め 2026-10-06 23:0x〜23:2x(逐語): 「記録に書いて記録をポーリングする設計を本当にやめてくれ」「redisでもrabbitmqでも使っていいから、イベントに即応してほしい。」「記録は記録、起動は起動」「送る側がホストを知っている必要がある設計なんてありえないよね」"
       :evidence "agora-redesign #3850 の頭の引用")
     (fact
       "doeff-events の handler は memory(process の中)と timer だけで、process をまたぐ知らせは「記録に書く → 記録の変更の long-poll → 合図 → 読み直す」を通っていた。"
       :evidence "agora-redesign #3850 の 0 節(origin/main の code で確かめた形)")
     (interpretation
       "仕事を渡す出来事が中身を運ぶなら、出来事を落とすと仕事が消える。だから配達の約束(落ちても失われない・二重に渡らない・切れた時に知らせる)を、backend の法として 1 か所に書き、memory の handler と本物の Redis の両方が同じ筋書きで満たす事を確かめる。")]
  :decision
    [(rule R1 "配達の法の筋書きは doeff_events.stream_laws の 1 か所に置く。下の層の handler(memory・Redis)と上の層(stream_events_handler)を変える変更は、同じ筋書きを memory と本物の redis-server の両方で通す。")
     (rule R2 "業務の Program には、列の名・group と consumer の名・確かめ(ack)を出さない。出すのは Publish・WaitForEvent と、出来事の値だけ。確かめは、Program が次の WaitForEvent へ戻った時(か例外なく終わった時)に上の層が行う。")
     (rule R3 "待ちは blocking の取り(期限なし — 止めるのは task の Cancel)と、broker の戻りを待つ期限つきの待ち 1 つ(WaitWithin)だけ。間隔で起きて確かめる形を足さない。")]
  :laws
    [(law unfinished-event-comes-again
       :statement "for_all 確かめる列の出来事 e・読み手 r: r の Program が e を受けてから次の WaitForEvent へ戻る前に落ちたなら、同じ名で起動し直した r は e をもう 1 度 受ける。r が処理を終えた e(次の WaitForEvent へ戻った・例外なく終わった)は、起動し直しても受けない。"
       :counterexamples
         [(counterexample "上の層が、列から出来事を取った瞬間に確かめる(XACK を処理の前にする)— Program が処理の途中で落ちると、起動し直した読み手に出来事が渡らず、仕事が消える")]
       :enforced-by ["packages/doeff-events/tests/test_stream_laws_memory.py::test_event_law_holds_on_memory"
                     "packages/doeff-events/tests/test_stream_laws_memory.py::test_acknowledging_on_delivery_breaks_the_redelivery_law"
                     "packages/doeff-events/tests/test_stream_laws_redis.py::test_event_law_holds_on_redis"
                     "test-adr-doe-events-002-laws-are-declared"]
       :wiring "配線済み(2026-10-06)— 筋書き stream_laws.law_unfinished_event_comes_again を memory で常に、redis-server の実行 file が在る機体では本物でも走らせる。")
     (law entry-reaches-one-consumer-of-a-group
       :statement "for_all 列の entry x・group g: x は g の consumer のうち 1 人にだけ渡る。渡った x は確かめるまで g の未確かめの一覧に残り、g の別の consumer が引き取れる。"
       :counterexamples
         [(counterexample "memory の handler が、同じ group の 2 人の consumer の両方へ同じ entry を渡す — 同じ仕事が二重に走る")]
       :enforced-by ["packages/doeff-events/tests/test_stream_laws_memory.py::test_broker_law_holds_on_memory"
                     "packages/doeff-events/tests/test_stream_laws_memory.py::test_giving_an_entry_to_two_consumers_of_a_group_breaks_the_one_consumer_law"
                     "packages/doeff-events/tests/test_stream_laws_redis.py::test_broker_law_holds_on_redis"]
       :wiring "配線済み(2026-10-06)— 筋書き stream_laws.law_entry_reaches_one_consumer_of_a_group と law_unacked_entry_stays_and_can_be_claimed。")
     (law start-and-return-are-told-once
       :statement "for_all 読み手 r: r の源は、初めて繋がって購読を始めた時に SourceStarted を 1 度、broker が届かなくなって戻った時に(止まり 1 つにつき)SourceResumed を 1 度 出す。"
       :counterexamples
         [(counterexample "上の層が、繋ぎ直しても SourceResumed を出さない — Program は止まりの間に落ちた出来事を記録で追いつく合図を受けられない")]
       :enforced-by ["packages/doeff-events/tests/test_stream_laws_memory.py::test_event_law_holds_on_memory"
                     "packages/doeff-events/tests/test_stream_laws_memory.py::test_not_telling_the_return_breaks_the_start_and_return_law"
                     "packages/doeff-events/tests/test_stream_laws_redis.py::test_event_law_holds_on_redis"]
       :wiring "配線済み(2026-10-06)— 筋書き stream_laws.law_start_and_return_are_told_once。本物では接続を全部 切って止まりを作る。")
     (law subscription-precedes-the-body
       :statement "for_all 読み手 r: r の購読(group の用意・channel の購読)は、包んだ Program の最初の effect より前に始まる — Program が最初に出した出来事を、Program 自身が受ける。"
       :counterexamples
         [(counterexample "購読を源の task の中で始める — Program が先に走って状態を読み、その後から購読が始まるので、読みと購読の間の出来事が落ちる")]
       :enforced-by ["packages/doeff-events/tests/test_stream_laws_memory.py::test_event_law_holds_on_memory"
                     "packages/doeff-events/tests/test_stream_laws_memory.py::test_shared_signal_invariant_holds_across_processes"
                     "packages/doeff-events/tests/test_stream_laws_memory.py::test_subscribing_after_the_first_read_breaks_the_shared_gap_invariant"
                     "packages/doeff-events/tests/test_stream_laws_redis.py::test_event_law_holds_on_redis"]
       :wiring "配線済み(2026-10-06)— 筋書き stream_laws.law_subscription_precedes_the_body と、共有の不変条件 tests/event_signal_invariants.py の「読みと待ちの間の出来事を落とさない」。購読を Program の最初の待ちまで遅らせた上の層では、後者が scheduler の行き止まりで赤になる。")
     (law notice-reaches-only-current-subscribers
       :statement "for_all 知らせ n・channel c: n は、出された時に c を購読していて broker に繋がっている読み手にだけ届く。購読の前・繋がっていない間に出された n は、後から届かない(Pub/Sub の意味)。失った分は、受ける Program が SourceStarted と SourceResumed を受けた時に 1 度 記録から追いつく前提で、backend は埋めない。"
       :counterexamples
         [(counterexample "知らせを「必ず届く物」として扱い、仕事の受け渡しを知らせだけに載せる(記録にも確かめる列にも残さない)— 読み手が繋がっていない間の仕事が消える")]
       :enforced-by ["packages/doeff-events/tests/test_stream_laws_memory.py::test_broker_law_holds_on_memory"
                     "packages/doeff-events/tests/test_stream_laws_redis.py::test_broker_law_holds_on_redis"]
       :wiring "一部配線(2026-10-06)— 筋書き stream_laws.law_notice_reaches_only_current_subscribers は「購読の前の知らせは届かず、後の知らせは届く」まで。繋がっていない間の知らせが届かない事は、memory の broker の cut が購読者の列を捨てる形で持つが、筋書きでは確かめていない。")
     (law cut-head-is-told
       :statement "for_all 読み手 r: r がまだ受けていない出来事が列の長さの上限で頭から切られていたら、r の源は起動と繋ぎ直しの時に SourceGap を、残りの出来事より先に出す。"
       :counterexamples
         [(counterexample "上の層が、切られたのに SourceGap を出さない — Program は切られた出来事を記録で追いつく合図を受けられず、その仕事が消える")]
       :enforced-by ["packages/doeff-events/tests/test_stream_laws_memory.py::test_event_law_holds_on_memory"
                     "packages/doeff-events/tests/test_stream_laws_memory.py::test_not_telling_the_cut_head_breaks_the_gap_law"
                     "packages/doeff-events/tests/test_stream_laws_redis.py::test_event_law_holds_on_redis"]
       :wiring "配線済み(2026-10-06)— 筋書き stream_laws.law_cut_head_is_told と law_cut_head_is_visible。判じ方は effects/streams.py の head_was_cut の 1 か所(切られていないのに知らせる境目が 1 つ在る — その関数の註)。")]
  :enforcement
    [(deftest test-adr-doe-events-002-laws-are-declared
       ;; 針: 法の筋書きが stream_laws.py の 1 か所に在り、上の層の法の組 EVENT_LAWS と下の層の法の組 BROKER_LAWS に載っている。
       ;; doeff-events は repo の根の環境に入っていないので、import せず file を読む(ADR-DOE-EVENTS-001 と同じ)。
       (val repo-root (. (Path __file__) parent parent parent))
       (val text (.read-text (/ repo-root "packages/doeff-events/src/doeff_events/stream_laws.py") :encoding "utf-8"))
       (for [name ["law_unfinished_event_comes_again" "law_subscription_precedes_the_body" "law_start_and_return_are_told_once"
                   "law_cut_head_is_told" "law_entry_reaches_one_consumer_of_a_group" "law_unacked_entry_stays_and_can_be_claimed"
                   "law_notice_reaches_only_current_subscribers" "law_cut_head_is_visible"]]
         (assert (in (+ "def " name "(") text)
                 (+ "配達の法の筋書きが stream_laws.py に無い(ADR-DOE-EVENTS-002 R1): " name))
         (assert (in (+ "    " name ",") text)
                 (+ "配達の法の筋書きが法の組(EVENT_LAWS / BROKER_LAWS)に載っていない(ADR-DOE-EVENTS-002 R1): " name))))]
  :plans ["agora-redesign #3850"])
