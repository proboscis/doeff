;;; Executable ADR: 記録の service の scheduler の thread は、要求の受けと順の制御と、大きさが要求の本文か 1 行で決まる計算だけを持つ。
;;; 大きさが置き場に在る行の数で決まる計算(束の読みの行の読み解きと、束の読みの答えの綴り)は、Compute で scheduler の thread の外で回す。
;;; 1 要求の計算の上限は、答えの型(束の読みの答えか否か)と読みの形(束の読みか 1 行の読みか)で決める。
;;;
;;; 出自 = card ki-67a745ab6c1c(切り分けの card ki-974e5ae37eca の提案 1 — 混んだ時にも読みの p95 を保つ区切りの card)。
;;; 仕組みの在りか = packages/doeff-records/src/doeff_records/loop_work.hy(分けの 1 点)・pg.hy の decoded-batch(行の読み解き)・
;;; service.hy の answer-reply(答えの綴り)・main.hy の records-connected と http_server.hy の start-records-server(Compute の答え手)。
;;;
;;; 戻し方: この file と loop_work.hy を消し、pg.hy の decoded-batch の呼びを読み解きの program の直の bind に、service.hy の answer-reply を
;;; reply-of に戻す(Compute の答え手は置いたままでも害は無い — 出す effect が無くなるだけ)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import typing [get-args])
(import doeff_records.wire [WireAnswer LIST-LIMIT-MAX])
(import doeff_records.effects [DEFAULT-LIST-LIMIT])
(import doeff_records.loop_work [BATCH-READ-ANSWERS ON-LOOP-ANSWERS])


(defadr ADR-DOE-RECORDS-002
  :title "記録の service の scheduler の thread は要求の受けと順の制御だけを持ち、置き場に在る行の数で大きさが決まる計算(束の読みの行の読み解きと答えの綴り)は Compute で thread の外で回す"
  :status "accepted"
  :scope ["packages/doeff-records/src/doeff_records/loop_work.hy"
          "packages/doeff-records/src/doeff_records/pg.hy"
          "packages/doeff-records/src/doeff_records/service.hy"
          "packages/doeff-records/src/doeff_records/http_server.hy"
          "packages/doeff-records/src/doeff_records/main.hy"
          "packages/doeff-records/src/doeff_records/wire.hy"
          "docs/adr/defadr_doeff_records_002_scheduler_thread_admits_and_orders.hy"]
  :problem
    [(fact
       "記録の service の process は 1 つの run・1 つの scheduler で動き、受けの loop・要求の task・書きの門・変化の待ちの全部が 1 本の scheduler の thread の上で回る。その thread が 1 要求の計算で塞がる間、後から届いた要求は、待ち受けが受けた後、要求の task が始まるまで受けの列で待つ。"
       :evidence "packages/doeff-records/src/doeff_records/http_server.hy の頭の註(入口の形)")
     (fact
       "2026-10-22 の本番の 100 分の測り(process ごとに 200 ms ごとの probe の受けの列・1 秒ごとの計器・50 ms ごとの scheduler の thread の走りの秒): 受けの列が 60 ms 以上 跳ねた probe は、書きの詰まりの窓を除くと 97〜100% が、一覧の読みが 300 行以上を返した 1 秒と重なった。跳ねる前の 50 ms の scheduler の thread の走りは 88〜98%(全体の平均 35〜53%)で、run queue の待ちは 0.6〜2.0% — thread は CPU を貰えていて、自分の仕事で塞がっていた。"
       :evidence "card ki-974e5ae37eca の発言 ob-844fea23612759cf(表)・ob-cd4ccf8d5e81153d(訳と削る手)")
     (fact
       "1 回に 300〜500 行を返す一覧の読みの、頁の行の読み解き(PostgreSQL の payload の json.loads と深く凍らせる frozen-json-object — pg.hy の listed-rows-of)と答えの綴り(thaw-json と json.dumps — wire.hy の encode-answer・service.hy の json-answer)が scheduler の thread で同期に走り、1 頁で 40〜125 ms その thread を塞いでいた。手元の測り(1 行 2.6 KB の 500 行の頁・3.14t): 読み解き 55 ms・綴り 37 ms・UTF-8 の byte 列 1.8 ms。"
       :evidence "card ki-974e5ae37eca の発言 ob-cd4ccf8d5e81153d・card ki-67a745ab6c1c の作業の測り")
     (fact
       "doeff は純粋な program を回して答えを値で受ける汎用の effect Compute と、その答え手 thread-pool-compute-handler(呼び手の pool で回し、待つのは撃った task だけ — scheduler の他の task は回り続ける)・inline-compute-handler(その場で回す — 仮想の時計の模擬で決定的)を持つ。本番の Python は free-threaded の 3.14t で、pool の thread は scheduler の thread と並んで走る。thread の受け渡し 1 回の上乗せは手元で 約 0.5 ms(その間 scheduler の thread は空く)。"
       :evidence "packages/doeff-core-effects/doeff_core_effects/compute_effects.hy・thread_pool_compute.hy・inline_compute.hy")
     (fact
       "束の読みを Compute へ出した後も、1 要求の計算の量は「頁の行の数 × 行の大きさ」で決まり、行の大きさの側だけが型で縛られていた(表の size-budget)。wire の list-rows の limit は上限を持たず、本文の上限 16 MiB の中なら何行でも頼めた。使い手の 4 つの repo の main(2026-10-22)で一覧の読みの limit は、固定の値で最も大きいもので 5,000 行(全行の一覧の 1 頁 — 1 回の要求の固定費 約 0.2 秒との釣り合いで選ばれた頁)。外から渡る数をそのまま載せる読みが 2 つ在った(呼び手の CLI の --limit N の N + 1・cursor が古い時に窓を限り無く倍にする読み — どちらも同じ日に続きの位置で頁を送る形に直した)。手元の測りの比で、5,000 行の頁の読み解きと綴りは 1 頁 約 0.9 秒、計算の pool の thread を 1 本塞ぐ(500 行で 92 ms)。"
       :evidence "card ki-42c40cb6014d の作業の測り・packages/doeff-records/src/doeff_records/wire.hy の LIST-LIMIT-MAX")
     (fact
       "計算の pool の詰まり(大きな束の読みが pool の全部の thread を塞ぎ、後の束の読みの計算が pool の列で待つ)は、要求ごとの区間の秒(handler と encode)に混ざって数えられ、別の計器を持たなかった — 遅れが pool の列の待ちか置き場の読みかを、/metrics の数で見分けられなかった。"
       :evidence "packages/doeff-records/src/doeff_records/request_timing.hy の区間の名(queue・body・decode・handler・wait・encode・send)")]
  :context
    [(interpretation
       "受けの列の待ちを決めるのは、scheduler の thread が 1 要求のために続けて回す計算の長さ。束の読みは、長さが上限 limit までの、置き場に在る行の数で決まり、小さな要求(limit だけを書いた一覧の読み)が大きな計算を起こす — 本番で他の要求を待たせていたのはこれ。要求の本文の読み解きや 1 行の綴りも、塞ぐ間は他の要求を待たせる事に変わりは無いが、大きさは要求そのもの(本文の上限 REQUEST-MAX-BYTES = 16 MiB)か 1 行(表の size-budget — 宣言した表だけ)で決まり、置き場の行の数では増えない。本番の切り分けでは原因でなかった(書きの本文の読み解きと答えの綴りは 1 秒に 0.5〜26 ms・一覧の綴りの 2 割以下)ので、今は thread の上に残し、上限を R6 に書く。")
     (interpretation
       "今の形をそのまま thread に写す(要求ごとに thread を立てる・handler の全部を pool で回す)のではなく、scheduler の thread の持ち物を答えの型と読みの形で閉じて決める: 束の読みの答えの型(Page・Changes・Events)と束の読み(一覧の頁・変更の列・出来事の列)だけを外へ出し、ほかは thread の上に残す。小さな要求に thread の受け渡しの上乗せを払わせず、外へ出す物を型で数えられる。")
     (interpretation
       "答え手は組み立ての側(composition root)の持ち物: thread の pool は資源で、開くのも閉じるのも組み立てが持つ(SQL の答え手の pool と同じ)。入口の Program は Compute に答えない — 仮想の時計の模擬はその場で回す答え手を置き、決定的に回る。")]
  :decision
    [(rule R1 "記録の service の scheduler の thread が持つのは、要求の受け(待ち受けの出来事・本文の読みの命令)・順の制御(task の立てと待ち・書きの門・錠・変化の待ち・取り消し)・計器・答えの送りの命令と、大きさが要求そのものか 1 行で決まる計算(要求の本文の読み解き・1 行と 1 出来事の読み解きと綴り・書きの答えの綴り)だけ。")
     (rule R2 "置き場の束の読み(錠の外で流す、長さが上限 limit までの行を返す読み — 一覧の頁・変更の列・出来事の列と、手入れの送りの読み)の行の読み解きは、純粋な program(pg.hy の listed-rows-of・changes-of・events-of)を Compute で出して scheduler の thread の外で回す(decoded-batch)。行が無ければ回す物が無いので出さない。")
     (rule R3 "束の読みの答え(loop_work.hy の BATCH-READ-ANSWERS = Page・Changes・Events)で行・変更・出来事を 1 つ以上運ぶ答えは、綴り(wire の object → JSON の文字列 → UTF-8 の byte 列 — service.hy の sendable-reply-of)を Compute で scheduler の thread の外で回す(answer-reply)。送りは綴り直さない(HttpAnswer の data)。ほかの wire の答え(ON-LOOP-ANSWERS)はその場で綴る。")
     (rule R4 "wire の答えの型(wire.hy の WireAnswer)は、BATCH-READ-ANSWERS と ON-LOOP-ANSWERS のちょうど 1 つに属する。答えの型を足す変更は、同じ変更でどちらかに入れる。")
     (rule R5 "記録の service を起こす組み立ては Compute の答え手を置く: 本番の土台の口(main.hy の records-connected)は COMPUTE-WORKERS 本の thread の pool を開いて閉じ、検の殻(http_server.hy の start-records-server)も thread の pool を置く。台本の検と模擬(仮想の時計)は inline-compute-handler を置く。入口の Program(serve-records)と記録の口の流れ(service.hy の respond)は答え手を置かない。答え手は Compute と負荷の読み ReadComputeLoad の両方に答える(GET /metrics が読む — R6。その場で回す答え手は列を持たないので全部 0)。")
     (rule R6 "今は scheduler の thread に残す物と、その上限(どれも本番の切り分けで受けの列の跳ねの原因でなかった — 跳ねが出たらこの上限から外へ出す): (a) 要求の本文の読み解き(JSON の読みと凍らせ・書きの判じ)と書きの答えの綴り — 本文の上限 REQUEST-MAX-BYTES(16 MiB)まで。(b) 1 行と 1 出来事の読み解きと綴り(ReadRow・ReadEventByKey・上から読む頁の位置の行)— 上限は表の size-budget で、size-budget を宣言しない表の 1 行には上限が無い(使い手が size-budget を宣言して縛る)。(c) 書きの錠の transaction の中の読み解き(回収の期限を過ぎた行・捨てる出来事の鍵の覚えと指紋・追記の前の使い)— transaction の program は SQL の effect と純粋な計算だけを出す(pg.hy の頭の註)。回収は 1 つの transaction で EXPIRE-BATCH(500)件まで、手入れの拍ごとに回る。(d) GET /metrics の描き — 系列の数(宣言の表 × 呼び手)に比例し、読まれる間隔(計器の読みの拍)ごと。(e) memory の置き場(模擬と検の置き場)の一覧の絞りと並べ — 置き場の答えそのもの。scheduler の thread の外(計算の pool)で回す束の読みの 1 要求の量の上限と計器: (f) 一覧の読み(wire の list-rows)1 回の limit は wire.hy の LIST-LIMIT-MAX = 5,000 行まで — 越えた要求は client の綴り(encode-request — 送らずに WireMalformed)と service の読み(decode-request — 400 malformed)の両方で断る(checked-list-limit)。値は使い手の一番大きな頁に合わせた(1 頁 約 0.9 秒 pool の thread を 1 本塞ぐ — 下げるのは (g) の待ちが跳ねを名指した時で、使い手の頁を先に割る)。1 要求の量は行の数 5,000 × 行の大きさ(size-budget)で閉じる。wire を通らない読み(memory の置き場・置き場の handler を直に呼ぶ模擬と検)は縛らない。変更の列(watch-changes)と出来事の列(read-events)の limit はまだ上限を持たない(この変更の範囲の外 — 跳ねが出たら同じ形で縛る)。(g) 計算の pool の詰まりは GET /metrics の 3 系列で読む: records_compute_queued(pool の thread を待って列に並んでいる計算の数 — 今の値)・records_compute_running(回っている数 — 今の値)・records_compute_wait_seconds_sum と _count(回り始めた計算が出されてから回り始めるまで待った秒の合計と回数 — 起動からの累計)。値は Compute の答え手が ReadComputeLoad で答え(thread-pool-compute-handler が pool ごとに数える)、GET /metrics の描きの時に断面へ足す(計器へは積まない — http_server.hy の with-compute-load)。読み手は拍ごとの 2 つの断面の差で、その拍の待ちの平均(合計の差 ÷ 回数の差)と、列に並んでいた数の時間の平均(合計の差 ÷ 拍の秒)を出し、要求の遅れの p95 が跳ねた刻と同じ拍で並べて読む。")]
  :laws
    [(law the-scheduler-thread-does-no-work-sized-by-stored-rows
       :statement "for_all 記録の束の読みの要求 r: scheduler の thread が r のために回す CPU の秒 は r の答えが運ぶ行の数に依らない(行の読み解きと綴りは Compute の program の中)"
       :counterexamples
         [(counterexample "500 行の一覧の頁の行の読み解き(json.loads と深く凍らせる)と答えの綴り(thaw-json と json.dumps)を scheduler の thread で回し、1 頁で 40〜125 ms その thread を塞ぐ — その間に届いた他の要求が受けの列で待つ(2026-10-22 の本番)")
          (counterexample "読み解きを Compute に出しても、答えの綴りか UTF-8 の byte 列への変換を scheduler の thread に残す — 行の数に比例する計算が thread に残る")]
       :enforced-by ["packages/doeff-records/tests/test_loop_work.hy::test-the-scheduler-thread-spends-no-more-on-a-page-of-many-rows-than-on-a-page-of-few"
                     "packages/doeff-records/tests/test_loop_work.hy::test-a-batch-answer-is-spelled-to-its-bytes-off-the-scheduler-thread"
                     "packages/doeff-records/tests/test_loop_work.hy::test-batch-reads-decode-and-spell-their-rows-off-the-scheduler-thread"]
       :wiring "配線済み(2026-10-22)— 500 行と 5 行の頁の scheduler の thread の CPU の秒の差が、thread の pool で要求 1 つあたり 5 ms 以下・その場で回す時の 1/10 以下(手元の測り: その場 53 ms・pool 0.6 ms)。CPU の秒の差では見分けにくい小さな計算(500 行の UTF-8 の変換 1.8 ms)は型で断る — 行を運ぶ頁の答えは読み解きと綴りの Compute を 1 つずつ出し、送る byte 列 data まで thread の外で作る(SQL の答えの代役の置き場 — PostgreSQL の要らない検)。本物の PostgreSQL の上でも、束の読み 3 種の Compute の名を確かめる")
     (law every-wire-answer-has-one-spelling-place
       :statement "wire.hy の WireAnswer の型の集合 = BATCH-READ-ANSWERS ⊎ ON-LOOP-ANSWERS(重ならない)"
       :counterexamples
         [(counterexample "行の列を運ぶ答えの型を wire に足し、どちらの組にも入れない — 新しい束の答えが scheduler の thread で黙って綴られる")]
       :enforced-by ["docs/adr/defadr_doeff_records_002_scheduler_thread_admits_and_orders.hy::test-adr-doe-records-002-every-wire-answer-has-one-spelling-place"]
       :wiring "配線済み(2026-10-22)— この file のテスト")
     (law every-records-assembly-answers-compute
       :statement "for_all 記録の service を起こす組み立て a: a は Compute の答え手を置く(本番と検の殻 = thread の pool・台本の検と模擬 = その場)"
       :counterexamples
         [(counterexample "Compute の答え手の無い組み立てで記録の service を起こす — 行を運ぶ束の読みが UnhandledEffect で 500 internal になる")]
       :enforced-by ["packages/doeff-records/tests/test_pg_reconcile_indexes.hy::test-the-service-answers-while-the-reconciler-builds-and-does-not-end"
                     "packages/doeff-records/tests/test_http_list_rows_metrics.hy"
                     "packages/doeff-records/tests/test_laws.hy(tests/interpreters.hy の解釈器)"
                     "使い手の repo の記録の service の閉じの検(本番の土台で答えの無い effect を実行せずに読む)"]
       :wiring "配線済み(2026-10-22)— 本番の土台の口 records-connected の上の一覧の読み・検の殻の上の一覧の読み・台本の解釈器の法の全部")
     (law a-list-read-over-the-wire-asks-at-most-the-cap
       :statement "for_all wire を通る一覧の読み r: r.limit <= LIST-LIMIT-MAX(client は越えた読みを送らず、service は越えた本文を 400 malformed で断る)"
       :counterexamples
         [(counterexample "limit に上限の無い一覧の読みを wire に載せる — 呼び手が外から受けた数(CLI の --limit N)を 1 回の読みに載せ、置き場の行の数だけ計算の pool の thread を塞ぐ")
          (counterexample "service だけが断り、client は越えた読みを送る — 往復を払ってから 400 を受け、呼び手は頁に割る事を送った後で知る")]
       :enforced-by ["packages/doeff-records/tests/test_wire.hy::test-a-list-above-the-limit-cap-is-refused-on-both-sides"
                     "packages/doeff-records/tests/test_http_metrics.hy::test-metrics-show-the-compute-pool-and-the-wire-refuses-a-list-above-the-cap"
                     "docs/adr/defadr_doeff_records_002_scheduler_thread_admits_and_orders.hy::test-adr-doe-records-002-the-default-list-fits-the-wire-cap"]
       :wiring "配線済み(2026-10-22)— wire の綴りと読みの両方・検の殻の口の 400")
     (law the-compute-pool-wait-is-on-metrics
       :statement "GET /metrics は Compute の答え手の負荷(並び・走り・回り始めた計算の待った秒の合計と回数)を載せる — 計算の pool が塞がった時、列に並ぶ数と待ちの秒が増える"
       :counterexamples
         [(counterexample "pool の待ちを計器に持たない — 大きな束の読みが pool を塞いだ遅れが、要求ごとの区間の秒の handler と encode に混ざって見分けられない(2026-10-22 まで)")
          (counterexample "並びの数を、仕事を出した時だけ gauge に置く — 列が空いた後も最後に置いた数が残る")]
       :enforced-by ["packages/doeff-records/tests/test_http_metrics.hy::test-metrics-name-a-clogged-pool"
                     "packages/doeff-records/tests/test_http_metrics.hy::test-metrics-show-the-compute-pool-and-the-wire-refuses-a-list-above-the-cap"
                     "packages/doeff-core-effects/tests/test_compute_effects.hy::test-thread-pool-load-counts-the-queue-and-the-wait"
                     "packages/doeff-core-effects/tests/test_compute_effects.hy::test-thread-pool-load-drops-a-queued-job-that-is-cancelled"]
       :wiring "配線済み(2026-10-22)— 描きの時に答え手の ReadComputeLoad を読む(本番の土台の口・検の殻 = thread の pool・台本と模擬 = その場の 0)")]
  :enforcement
    [(deftest test-adr-doe-records-002-every-wire-answer-has-one-spelling-place
       ;; wire の答えの型は、束の読みの答え(thread の外で綴る)とほかの答え(その場で綴る)のちょうど 1 つに属する(R4)。
       (val wire (frozenset (get-args WireAnswer)))
       (val batch (frozenset BATCH-READ-ANSWERS))
       (val on-loop (frozenset ON-LOOP-ANSWERS))
       (assert (not (& batch on-loop)) (+ "両方に入っている答えの型: " (str (sorted (gfor t (& batch on-loop) t.__name__)))))
       (assert (= (| batch on-loop) wire)
               (+ "綴る所の決まっていない答えの型(ADR-DOE-RECORDS-002 R4 — loop_work.hy の BATCH-READ-ANSWERS か ON-LOOP-ANSWERS に入れる): "
                  (str (sorted (gfor t (^ (| batch on-loop) wire) t.__name__))))))
     (deftest test-adr-doe-records-002-the-default-list-fits-the-wire-cap
       ;; limit を書かない一覧の読み(ListRows の既定 DEFAULT-LIST-LIMIT)は wire の上限の内(R6 (f) — 既定の読みが wire で断られない)。
       (assert (<= DEFAULT-LIST-LIMIT LIST-LIMIT-MAX) #(DEFAULT-LIST-LIMIT LIST-LIMIT-MAX)))]
  :plans ["card ki-67a745ab6c1c"])
