;;; Executable ADR(決定): 動き続ける process の計器(数え上げ・秒の観測・今の値)・run をまたぐ最新の値の受け渡し・heap の凍結を、業務を
;;; 知らない汎用の effect にし、本物の答え手と memory の答え手が同じ契約のテストを通す。
;;;
;;; 出自 = agora-redesign #1440(#1264 の子・milestone I)。agora-controllers の画面は、計器(Meter)・破れの台帳・起動の同期の進みを
;;; 同じ object として処理ループの run と probe の別の run に渡していた。層を分けると、翻訳の層(protocol)は土台の object を import
;;; できないので、効果で渡すしかない。agora の ADR「環境は handler の組で選ぶ」R8 は「翻訳の先は doeff の汎用の effect だけ。無ければ doeff に
;;; 足す」と決めており、doeff の origin/main に汎用の計器の effect は無かった(manager が packages の全体を名前で検索して確かめた・
;;; 2026-09-29 19:33 JST)。effect の表は #1440 の comment 5888619033、manager の決めは同じ issue の 2026-09-29 19:46 JST の comment。
;;;
;;; 戻し方: この ADR を足した commit を revert する(新しい module 9 つと検が消える。既存の公開の形は変えていないので、使い手が無ければ
;;; 何も壊れない)。agora-controllers の doeff-min-commit を前の sha へ戻す。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest val do! <-])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-CORE-EFFECTS-004
  :title "動き続ける process の計器・最新の値・heap の凍結を汎用の effect にする — 1 つの名の中の書きは 1 度に見え、名をまたぐ書きは保証しない"
  :status "accepted"
  :scope ["packages/doeff-core-effects/doeff_core_effects/meter_effects.hy"
          "packages/doeff-core-effects/doeff_core_effects/process_meter.hy"
          "packages/doeff-core-effects/doeff_core_effects/memory_meter.hy"
          "packages/doeff-core-effects/doeff_core_effects/latest_effects.hy"
          "packages/doeff-core-effects/doeff_core_effects/process_latest.hy"
          "packages/doeff-core-effects/doeff_core_effects/memory_latest.hy"
          "packages/doeff-core-effects/doeff_core_effects/heap_effects.hy"
          "packages/doeff-core-effects/doeff_core_effects/gc_freeze.hy"
          "packages/doeff-core-effects/doeff_core_effects/scripted_freeze.hy"]
  :problem
    [(fact
       "agora-controllers の画面の計器 Meter(controllers/screen/entry/meter.py・592 行)は、画面の型で答えの起点を決める判断と、counter・gauge・秒の合計を積む汎用の部分と、time.monotonic・uuid・gc.callbacks・threading の生の副作用を 1 つの class に持っていた。処理ループの run が書き、probe の別の run(/metrics・/latency/samples)が lock を待たずに読む。"
       :evidence "agora-redesign #1264 comment 5888426802 の表 2")
     (fact
       "層を分けると、翻訳の層は土台の object を import できず、土台は業務の型を読めない。run をまたいで渡すには effect が要る。doeff の origin/main には汎用の計器の effect が無く、在るのは doeff-cluster の ReportMetrics(coordinator への報告)と doeff-jev-proxy の ReadCounters(用途が決まっている)だけだった。"
       :evidence "#1440 の manager の決め(2026-09-29 19:46 JST)")
     (fact
       "handler の中の状態(session の値)は 1 つの run の中にしか無い。probe は処理ループが計算している間も答えるために別の run(別の thread)で走るので、session の値では同じ計器を読めない。"
       :evidence "agora-controllers controllers/foundation/side_run.hy の頭の註")]
  :context
    [(interpretation
       "置き場は doeff-core-effects。業務も cluster も知らない、動き続ける process の汎用の effect だから(隣の stop_signal・thread_pool_compute・scheduler_channel と同じ種類)。doeff-cluster の ReportMetrics は coordinator へ断面を送る effect で役が違うので、断面の形(counters・gauges・秒の合計と回数)だけを揃えて、後で ReadMeter の答えを ReportMetrics へ渡せるようにした。")
     (interpretation
       "本物の置き場を class にしない(ADR-DOE-HY-007 R3・R4 — lock と gc の callback を持ち、状態が変わる)。答え手の module が「名前 → 置き場」を process に 1 つ持ち、同じ名前で入れた答え手どうし(別の run・別の thread)が同じ置き場を読み書きする。process に 1 つの物を module が持つ先例 = doeff_core_effects/handlers.py の Await の橋。答え手の引数は変わらない値(名前と設定)だけ。戻せる決定(#1440 comment 5888677819)。")
     (interpretation
       "heap の凍結を計器と別の module にした。計器は観測を積んで読むだけで process の振る舞いを変えない。凍結は process の GC の状態を変える操作で、計器を持たない program も使う。一緒に置くと、計器を差し替える模擬が GC の操作まで一緒に差し替える形になる(manager の決めの条件 2)。")
     (interpretation
       "最新の値の鍵は文字列ではなく値の型ちょうど。読み手は答えの型を鍵から知れる(agora の設定の読み — Ask の鍵 = 型 — と同じ作法)。同じ型を 2 つの持ち主が置くと食い違うので、持ち主ごとに別の型を置く。戻し方 = key の欄を足す。")
     (interpretation
       "計器の書きは 1 回の effect で 1 つの名だけを変える。agora の画面の Meter は 1 回の出来事の書き(全数と内訳など、名をまたぐ)を 1 度に見せていたが、この形では名をまたぐと守れない。Prometheus の client の普通の約束と同じなのでこの形を採り、名をまたいで 1 度に見せたい読み手は 1 つの名に畳むか、食い違いを許す。まとめて書く effect(例 RecordMetrics)は、要ると分かった時に足す(足すだけで済む)。agora の側で今の性質に頼る検と不変条件の一覧は #1445 に書く(manager の決めの条件 2)。")]
  :decision
    [(rule R1 "計器の effect は 4 つ(meter_effects.hy): CountMetric(name・amount = 1.0 — counter に足す。amount = 0 は 0 を置く)・ObserveSeconds(name・seconds — 秒の合計と回数に積み、設定の桁の表の当たる桁ごとの累積の counter <name>_<label> と <name>_<inf-label> も進める)・SetGauge(name・value — 置き換える)・ReadMeter(答え = MeterSnapshot)。答えは ReadMeter の外はどれも None。設定は MeterSettings(buckets・inf-label・gc-pause-name)。")
     (rule R2 "最新の値の effect は 2 つ(latest_effects.hy): PublishLatest(value — 型 type(value) を鍵に置き換える)・ReadLatest(kind — その型ちょうどの最新の値か None)。置く値は変わらない値にする。")
     (rule R3 "heap の凍結の effect は 1 つ(heap_effects.hy): CollectAndFreeze(答え = 凍らせた object の数)。計器と module を分ける。")
     (rule R4 "答え手は本物と I/O の無い物を module で分ける: process-meter-handler(process_meter.hy)と memory-meter-handler(memory_meter.hy)・process-latest-handler(process_latest.hy)と memory-latest-handler(memory_latest.hy)・gc-freeze-handler(gc_freeze.hy)と scripted-freeze-handler(scripted_freeze.hy)。時計・gc・thread に触るのは本物の module だけで、effect の module と I/O の無い答え手の module は触らない。本物の 3 つは doeff-linter の実 I/O の handler の目録(packages/doeff-linter/data/world_handlers.json)に載せる — 使い手の repo が architecture.hy の :world-handlers の :wraps で名指せるように(載せない I/O の無い答え手は目録の決まりどおり外す)。")
     (rule R5 "本物の計器と最新の値は、同じ名前で入れた答え手どうしが別の run・別の thread でも 1 つの置き場を読み書きする。読みは書き手の lock を待たない(今の断面の参照を 1 つ取る)。GC の停止の callback は lock を取らない(lock を持つ thread の中で回収が始まると、取り直しで自分を待って止まる)。同じ名前で違う設定を入れると ValueError で断る。置き場は process の終わりまで残る。")
     (rule R6 "計器の 1 回の書きは 1 つの名の中(秒の合計・回数・その名の桁の counter)で 1 度に見える。名をまたぐ書きは 1 度に見えるとは限らない — 2 つの名への書きの間の読みは、片方だけ進んだ断面を見る。")
     (rule R7 "本物と I/O の無い答え手は同じ契約のテストを通す(agora-redesign #1107 の決め): packages/doeff-core-effects/tests の test_meter_contract.hy・test_latest_contract.hy・test_heap_freeze.hy の :interpreters。本物だけの性質(run・thread をまたぐ共有・違う設定を断る・GC の停止)は test_process_meter.hy・test_process_latest.hy。")
     (rule R8 "この変更は足すだけ。既にある doeff の型・関数・import の名を変えない。")]
  :laws
    [(law meter-write-is-whole-within-one-name
       :statement "write(meter, name) => every_read_sees(summary(name), buckets(name)) both_before or both_after"
       :counterexamples
         [(counterexample "ObserveSeconds が秒の回数を先に差し替え、桁の counter を後で差し替える — 間の読みが回数 3・inf の桁 2 を見る")
          (counterexample "別の thread の読みが書き手の途中の dict を読み、1 つの名の中が半分だけ書かれた断面を見る")])
     (law meter-writes-across-names-are-not-atomic
       :statement "write(meter, a); write(meter, b) => a_read_between may_see(a) without(b)"
       :counterexamples
         [(counterexample "読み手が全数(answers)と内訳(answers_entity_*)の和がいつも等しいと決めて検める — 間の読みで食い違い、正しい計器を赤と読む")
          (counterexample "ADR や註が「1 回の出来事の書きは名をまたいでも 1 度に見える」と約束する — この形の答え手は守れない")])
     (law real-and-memory-answer-one-contract
       :statement "effect in {meter, latest, heap} => real_answerer and io_free_answerer pass the_same_deftests"
       :counterexamples
         [(counterexample "memory の答え手だけが amount = 0 で名を置かず、模擬では「無い」と「0」が区別できない")
          (counterexample "本物だけが桁の counter を積み、模擬の検は桁の数を見られない")])
     (law raw-side-effects-stay-in-real-answerers
       :statement "module in {effects, io_free_answerers} => imports none_of(gc, threading, time)"
       :counterexamples
         [(counterexample "memory_meter.hy が time.monotonic で GC の停止を測り、模擬の答えが実時計で揺れる")
          (counterexample "meter_effects.hy の純関数が threading.Lock を取り、模擬の検が thread に触る")])]
  :enforcement
    [(deftest test-adr-doe-core-effects-004-one-name-is-written-at-once
       ;; law meter-write-is-whole-within-one-name の機械面(1 つの run の中): 観測のたびに読み、inf の桁と回数がいつも同じ。
       ;; thread をまたぐ面は packages/doeff-core-effects/tests/test_process_meter.hy の test-a-reader-in-another-thread-never-sees-half-of-one-name。
       (import doeff [run with_handlers])
       (import doeff_core_effects.handlers [state])
       (import doeff_core_effects.meter_effects [MeterBucket MeterSettings ObserveSeconds ReadMeter])
       (import doeff_core_effects.memory_meter [memory-meter-handler])
       (val settings (MeterSettings :buckets #((MeterBucket :label "le_1s" :ceiling 1.0)) :inf-label "le_inf"))
       (val snapshots (run (with_handlers [(state) (memory-meter-handler settings)]
                             (do! (<- (ObserveSeconds "fold" 0.5)) (<- after-short (ReadMeter))
                                  (<- (ObserveSeconds "fold" 2.0)) (<- after-long (ReadMeter))
                                  (<- (ObserveSeconds "fold" 0.1)) (<- after-shorter (ReadMeter))
                                  #(after-short after-long after-shorter)))))
       (for [snapshot snapshots]
         (assert (= (get snapshot.counters "fold_le_inf") (float (. (get snapshot.durations "fold") count))))))
     (deftest test-adr-doe-core-effects-004-two-names-are-not-written-at-once
       ;; law meter-writes-across-names-are-not-atomic の反例の検: 2 つの名への書きの間の読みは、片方だけ進んだ断面を見る。
       (import doeff [run with_handlers])
       (import doeff_core_effects.handlers [state])
       (import doeff_core_effects.meter_effects [CountMetric MeterSettings ReadMeter])
       (import doeff_core_effects.memory_meter [memory-meter-handler])
       (val reads (run (with_handlers [(state) (memory-meter-handler (MeterSettings))]
                         (do! (<- (CountMetric "answers"))
                              (<- between (ReadMeter))
                              (<- (CountMetric "answers_entity_board"))
                              (<- after (ReadMeter))
                              #(between after)))))
       (val between (get reads 0))
       (val after (get reads 1))
       (assert (= (get between.counters "answers") 1.0))
       (assert (not-in "answers_entity_board" between.counters))
       (assert (= (get after.counters "answers_entity_board") 1.0)))
     (deftest test-adr-doe-core-effects-004-io-free-modules-import-no-clock-gc-or-thread
       ;; law raw-side-effects-stay-in-real-answerers の機械面: effect の module と I/O の無い答え手の module の import を読む。
       (import pathlib [Path])
       (import re)
       (val root (/ (. (Path __file__) parent parent parent) "packages" "doeff-core-effects" "doeff_core_effects"))
       (val io-free ["meter_effects.hy" "memory_meter.hy" "latest_effects.hy" "memory_latest.hy" "heap_effects.hy" "scripted_freeze.hy"])
       (val raw (re.compile r"\(import\s+(gc|threading|time)\b"))
       (val found (lfor file io-free :if (.search raw (.read-text (/ root file) :encoding "utf-8")) file))
       (assert (= found []) (.format "時計・gc・thread を import している I/O の無い module: {!r}" found)))]
  :plans ["docs/adr/defadr_doeff_core_effects_004_process_meter_and_latest_values.hy"
          "packages/doeff-core-effects/tests/test_meter_contract.hy"
          "packages/doeff-core-effects/tests/test_process_meter.hy"
          "packages/doeff-core-effects/tests/test_latest_contract.hy"
          "packages/doeff-core-effects/tests/test_process_latest.hy"
          "packages/doeff-core-effects/tests/test_heap_freeze.hy"
          "agora-controllers: #1444・#1445 が画面の計器をこの effect へ移す"])
