;;; 往復の計時の行(ki-ca4b41612e2e — _http_client.hy の頭の註「往復の計時の行」と _request_timing.hy の「答えに載せる区間の秒」)。呼び手の
;;; process の中で、記録の service への往復 1 本の秒がどこで過ぎるかを要求ごとに名指すための行の形を守る。
;;;   (1) 見出し Server-Timing の綴りと読みは往き帰りで同じ区間の秒になる。答えが決まった後の区間(send・total)は綴らず、知らない名・dur の
;;;       無い項・数でない dur・見出しの無い答え(古い版の service)は読み飛ばす
;;;   (2) 本物の待ち受け(memory の置き場)へ、timed-operations に watch-changes を名指した endpoint の client で変化の読みを 2 回と行の読みを
;;;       1 回撃つと、行は変化の読みの 2 本だけで、欄は内訳の全部(接続・送る・service の受けの列・service の中・返す・本文を受ける・読み解く・
;;;       loop)を持つ。1 本目は新しい接続・2 本目は使い回し。service の受けの列と中と返すの和は、答えの頭を待った秒に等しい
;;;   (3) 名指さない endpoint は trace も行も出さない(log の答え手の無い組でも今までどおり答える)
;;; 直す前の client は行を出さず(2 が赤)、service は見出しを載せない(2 の service の欄が None で赤)。
(require doeff-hy.macros [deftest defhandler defeffect defk <- val var])
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; defeffect の展開が名指す
(import doeff [EffectBase Program run with_handlers])
(import doeff_core_effects [SlogEffect])
(import doeff_core_effects [await-handler state])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_time [async-time-handler])
(import doeff_records [ReadRow WatchChanges])
(import doeff_records [WatchCursor])
(import doeff_records [RecordsEndpoint http-records-handler records-unwaited]
        doeff_records._http_client [EXCHANGE-LOG])
(import doeff_records._http_server [records-server-config start-records-server])
(import doeff_records._laws [LAW-SCHEMA])
(import doeff_records [MemoryStore memory-records-handler])
(import doeff_records._request_timing [RequestStage StageSeconds server-timing-of])
(import doeff_records._wire [ServerSpan server-timing-spans])

;; 行の欄のうち、計時を頼んだ往復で必ず値を持つ物(内訳の全部 — _http_client.hy の logged-exchange)。
(val BREAKDOWN-FIELDS #("conn" "connect_ms" "acquire_ms" "send_ms" "service_queue_ms" "service_ms" "return_ms" "wait_ms" "receive_ms"
                        "decode_ms" "loop_ms" "http_ms" "total_ms"))
;; 返すの秒の計算(待った秒 − service の受けの列と中)の丸めの許す差(ミリ秒 — 欄は小数 2 桁に丸める)。
(val ROUNDING-MS 0.02)


(defeffect KeptLines
  "ここまでに出た log の行を出た順に読む — 検だけの問い(slog-kept が答える)。"
  {:fields [] :answer (get tuple #(SlogEffect ...)) :tags {:context "records" :role "intent"}})


(defhandler slog-kept
  "外の世界の log の代役: 出た行(SlogEffect)を出た順に覚え、KeptLines で読ませる。"
  {:tags {:context "records" :role "foundation"}}
  (session var lines #())
  (SlogEffect []
    (:= lines (+ lines #(effect)))
    (resume None))
  (KeptLines []
    (resume lines)))


(defk read-then-kept [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % tuple)] :tags {:context "records" :role "foundation"}}
  "body を走らせてから、それまでに出た log の行の列を読むため。"
  (<- _answer body)
  (<- lines (get tuple #(SlogEffect ...)) (KeptLines))
  lines)


(defk reads-through [url timed body]
  {:pre [(: url str) (: timed (get frozenset str)) (: body (| Program EffectBase))] :post [(: % tuple)] :tags {:context "records" :role "foundation"}}
  "口 url の client(timed-operations = timed)の下で body を本物の時計と HTTP の答え手で走らせ、出た log の行の列を返すため。"
  (val endpoint (RecordsEndpoint url :timed-operations timed))
  (<- lines tuple (with_handlers [(await-handler) (http-production-handler) (async-time-handler) (state) slog-kept records-unwaited]
                                 (read-then-kept (with_handlers [(http-records-handler endpoint)] body))))
  lines)


(defk two-watches-and-a-read []
  {:pre [] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "表 parts の変化の読み(待ち 0 秒)を 2 回と、行の読みを 1 回撃つため(答えの種は問わない — 位置の外れの Reset も往復 1 本)。"
  (<- first (WatchChanges #("parts") (WatchCursor 0 0)))
  (<- second (WatchChanges #("parts") (WatchCursor 0 0)))
  (<- row (ReadRow "parts" #("p1")))
  #(first second row))


(defk served-reads [timed]
  {:pre [(: timed (get frozenset str))] :post [(: % tuple)] :tags {:context "records" :role "foundation"}}
  "memory の置き場の上に口を開き、two-watches-and-a-read を client(timed-operations = timed)で撃って閉じ、出た log の行を返すため。"
  (val store (MemoryStore LAW-SCHEMA))
  (val server (start-records-server (run (records-server-config LAW-SCHEMA (fn [writer] (memory-records-handler store writer))))))
  (try
    (<- lines tuple (reads-through server.url timed (two-watches-and-a-read)))
    (finally (.close server)))
  lines)


(deftest test-the-server-timing-header-reads-back-the-stages-it-spells
  ;; (1) 綴りと読みの往き帰り: 送る前に決まる区間は同じ秒(ミリ秒の小数 3 桁の丸めの内)で読める。send・total は綴らない。
  (val stages #((StageSeconds :stage RequestStage.QUEUE :seconds 0.0012) (StageSeconds :stage RequestStage.HANDLER :seconds 0.0105)
                (StageSeconds :stage RequestStage.SEND :seconds 0.002) (StageSeconds :stage RequestStage.TOTAL :seconds 0.02)))
  (<- spelled str (server-timing-of stages))
  (assert (= spelled "queue;dur=1.200, handler;dur=10.500") spelled)
  (<- read (get tuple #(ServerSpan ...)) (server-timing-spans spelled))
  (assert (= (tuple (gfor s read s.name)) #("queue" "handler")) read)
  (assert (all (gfor #(got sent) (zip read stages) (< (abs (- got.seconds sent.seconds)) 1e-6))) read)
  ;; 読み飛ばす形: 見出しが無い・知らない名・dur の無い項・数でない dur・答えの後の区間の名。
  (<- absent (get tuple #(ServerSpan ...)) (server-timing-spans None))
  (assert (= absent #()) absent)
  (<- odd (get tuple #(ServerSpan ...)) (server-timing-spans "db;dur=3, queue, handler;dur=x, total;dur=9, encode;desc=a;dur=0.5"))
  (assert (= (tuple (gfor s odd #(s.name s.seconds))) #(#("encode" 0.0005))) odd))


(deftest test-a-timed-change-read-logs-one-line-with-every-part-of-its-round-trip
  ;; (2) 名指した操作(watch-changes)の往復 2 本だけが行を出し、行は内訳の欄を全部持つ。1 本目は新しい接続・2 本目は使い回し。
  (<- lines tuple (served-reads (frozenset #("watch-changes"))))
  (val exchanges (lfor line lines :if (= line.msg EXCHANGE-LOG) line.kwargs))
  (assert (= (len exchanges) 2) lines)
  (for [fields exchanges]
    (assert (= (get fields "operation") "watch-changes") fields)
    (assert (= (get fields "tables") "parts") fields)
    (assert (= (get fields "status") 200) fields)
    (assert (all (gfor name BREAKDOWN-FIELDS (is-not (get fields name) None))) fields)
    ;; 返す = 待った秒 − service の受けの列と中(内訳が待った秒を割り切る)。
    (assert (<= (abs (- (+ (get fields "service_queue_ms") (get fields "service_ms") (get fields "return_ms")) (get fields "wait_ms")))
                ROUNDING-MS)
            fields)
    (assert (<= (get fields "http_ms") (get fields "total_ms")) fields))
  (assert (= (tuple (gfor fields exchanges (get fields "conn"))) #("new" "reused")) exchanges)
  (assert (> (get (get exchanges 0) "connect_ms") 0.0) exchanges)
  (assert (= (get (get exchanges 1) "connect_ms") 0.0) exchanges))


(deftest test-an-endpoint-that-names-no-operation-logs-no-exchange
  ;; (3) 既定の endpoint(timed-operations 空)は同じ往復で行を 1 つも出さない。
  (<- lines tuple (served-reads (frozenset)))
  (assert (= (lfor line lines :if (= line.msg EXCHANGE-LOG) line) []) lines))
