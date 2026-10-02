;;; effect の記録と再生(backtest)の handler。
;;;
;;;   effect-recorder  記録。env の一番内側に置き、業務の Program が出す effect を受けて外側の本物の handler へ出し直し、
;;;                    問いと答えを記録の置き場(sink)へ書く。業務の Program から見た振る舞いは変えない(答えも例外もそのまま返す)。
;;;                    記録できない型・値に当たったら、strict(テスト)なら業務の Program へ投げ、そうでなければ「ここから先は記録
;;;                    していない」の印(broken)を置いて以後は素通しにする(本番の書き手を止めない)。
;;;   effect-replayer  再生。記録を読み、業務の Program の effect に記録の答えを返す。書き込み(decision)は実行せず記録と突き合わせ、
;;;                    読み(read)が記録と食い違ったらその地点を分岐として止める(推測で答えを作らない)。scheduler の effect(live)は
;;;                    本物の scheduler に解かせ、順番だけを突き合わせる。
;;;
;;; 並行(Spawn / Wait / Gather): どちらの handler も Spawn を受けたら、子の Program を「task の名の印(task-marker)」で包み、
;;; 自分までの handler の並び(GetBoundaries)を張り直してから外へ Spawn し直す。印は子の effect が通るたびに「いまの effect は
;;; この task の物」と共有の記憶に書き、記録・再生の handler はそれを読んで task の名を知る(根の task には印が無い = "root")。
;;; 再生は、問いと答えの出来事の番号の順に task を並べる: 番号がまだ来ていない task は promise を待って止まり(仮想の時計の
;;; handler と同じく scheduler の effect で待つ)、番号が進むとその番号の持ち主を起こす。他の task が全部止まった時だけ動く係
;;; (低優先度の daemon)が、それでも番号が進まない = 記録の問いを誰も出さない、を分岐として止める。
(require doeff-hy.macros [defhandler defk deff <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import copy)
(import dataclasses [dataclass])
(import itertools [takewhile])
(import json)
(import os)
(import sys)
(import time)
(import doeff [EffectBase Pass with-handlers])
(import doeff_core_effects.http_effects [HttpRequest HttpResponse HttpFailed])
(import doeff.do [do])
(import doeff.program [handler :as program-handler])
(import collections.abc [Callable Generator])
(import typing [Protocol TypeVar])
(import doeff [Program])
(import doeff_vm [GetBoundaries K WithObserve Callable :as VmCallable])
(import doeff_core_effects.scheduler [Spawn Wait CreatePromise CompletePromise Promise PRIORITY-IDLE])
(import doeff_cluster.foundation.record_codec [READ LIVE DECISION OUTPUT LOOSE DIVERGE INTERN-MIN-CHARS BLOB-MEMORY-MAX FORMAT-VERSION BlobMemory
                                         EffectCodec HandleTable UnencodableValue UnrecordableEffect
                                         encode-value encode-error decode-value decode-error canonical intern-json
                                         codec-of mode-of args-of subject-of])
(import doeff_cluster.foundation.record_log [ROOT ReplayFinished ReplayDiverged Entry Recording WatchedRef match-step diff-row summarize
                                              entry-of queue-of recorded-args])


;; --- task の名(記録と再生で共通) ---------------------------------------------------------------

(defclass TaskTap []
  "task の名の記憶。marked = 最後に印を通った effect・marked-label = その task の名。spawns = 親の名 → Spawn した数。"
  (defn #^ None __init__ [self]
    (setv #^ (| EffectBase None) self.marked None)
    (setv self.marked-label ROOT self.spawns {}))

  (defn #^ str current-label [self #^ EffectBase effect]
    (if (is self.marked effect) self.marked-label ROOT))

  (defn #^ str child-label [self #^ str parent]
    (setv n (.get self.spawns parent 0))
    (setv (get self.spawns parent) (+ n 1))
    (.format "{}.{}" parent n))

  (defn #^ None task-ended [self #^ str label #^ bool ok #^ (| BaseException None) error] None))


(defn #^ (get Callable #([EffectBase K] Pass)) task-marker [#^ TaskTap tap #^ str label]
  "子の Program の一番内側に置く印。自分より内側の印(孫の task)が先に書いた effect は書き換えない。"
  (defn #^ Pass effect-task-marker [#^ EffectBase effect #^ K k]
    (when (is-not tap.marked effect)
      (setv tap.marked effect tap.marked-label label))
    (Pass effect k))
  effect-task-marker)


;; 子の Program の答えの型。task-body・rewrap・spawn-program は答えを変えずに包むだけ。
(val T (TypeVar "T"))
;; effect の答えの型。記録係の answer は effect とその答えを対で受ける。
(val A (TypeVar "A"))


(defn [do] #^ (get Generator #((get Program T) T T)) task-body [#^ TaskTap tap #^ str label #^ (get Program T) program]
  "子の Program を走らせ、終わった(成功・失敗)ことを記憶に知らせる。"
  (try
    (setv result (yield program))
    (except [e Exception]
      (.task-ended tap label False e)
      (raise)))
  (.task-ended tap label True None)
  result)


(defn #^ (get Program T) rewrap [#^ (get Program T) program #^ list chain]
  "GetBoundaries の並び(内側が先・最後は受けた handler 自身)で program を包み直す(scheduler の Spawn と同じ張り直し)。"
  (setv prog program)
  (for [#(kind cb) chain]
    (setv prog (if (= kind "handler") ((program-handler cb) prog) (WithObserve (VmCallable cb) prog))))
  prog)


(defn #^ (get Program T) spawn-program [#^ TaskTap tap #^ str child #^ (get Program T) program #^ list chain]
  "Spawn し直す子の Program = 印 → 終わりの知らせ → 受けた handler までの並び。"
  (rewrap (task-body tap child ((program-handler (task-marker tap child)) program)) chain))


;; --- 記録の置き場 ------------------------------------------------------------------------------
;;
;; 置き場への送りは HTTP の effect(HttpRequest)で出す — 置き場の口は「何を送るか」(SinkPost)を作って送った結果を受けるだけで、
;; socket に触れない。答えるのは記録係より外側の handler: 本番は土台が積む http-production-handler、模擬は置き場の代役の handler
;; (送られた行を貯める — sim/test_recording_on_sim.hy)。送りの手順は send-records(記録係が effect ごとに・終わりに 1 度呼ぶ)。

(defrecord SinkPost
  "置き場への 1 回の送り。request = 出す HttpRequest・count = この送りで buffer の先頭から外せる項の数。"
  (#^ HttpRequest request)
  (#^ int count))


(defrecord SinkBatch
  "置き場への 1 回の送りに載せる、貯めた行の先頭の束: items = buffer の先頭の項(#(区切り 行の dict 行の JSON の文字列))・now-ms = 束を
   取った時の壁時計(ms — OTLP の観測の時刻)。口の class は束を渡すだけで、送りの本文は sink-post が口の種類ごとに綴る(#2764)。"
  (#^ tuple items)
  (#^ int now-ms))


(defclass RecordSink [Protocol]
  "記録の係(EffectLog)が置き場に求める口 — 行を書く・送る分を作る・送った結果を受ける・届かずに捨てた行の数。実体は MemorySink(検)と
   BufferedSink の族(HttpSink・OtlpSink)。EffectLog の欄 sink をこの型で宣言する(#1675 — 以前は object で
   宣言していて、write・lost の読みが型検査で絞れなかった)。送り(I/O)は口の外 — send-records が HttpRequest で出す。"
  #^ int lost
  ;; 本体は説明の文と値(Hy は最後の式を返すので、`...` や文だけだとその値を返し、返りの型と食い違う)。
  (defn #^ None write [self #^ int chunk #^ dict line] "行 line を区切り chunk に書く。" None)
  (defn #^ bool begin-send [self #^ bool force] "いま送るか(送るなら送りの最中の印を立てる)。force = 期限を待たずに送る。" False)
  (defn #^ (| SinkBatch None) next-batch [self] "次の 1 回の送りに載せる束(送る物が無ければ None)。" None)
  (defn #^ None delivered [self #^ SinkPost post] "post が届いた: その分を buffer から外す。" None)
  (defn #^ None undelivered [self #^ str reason] "送れなかった: 貯めたまま次を待つ(理由を出す)。" None)
  (defn #^ None end-send [self] "送りの最中の印を下ろす。" None)
  (defn #^ None trim [self] "送りの最中でなければ、貯めすぎた行を捨てて lost に数える。" None))


(defclass MemorySink []
  "テストの置き場。lines = 書いた行(dict)。送る物は無い(書いた時に持つ)。"
  (defn #^ None __init__ [self] (setv self.lines [] self.lost 0))
  (defn #^ None write [self #^ int chunk #^ dict line]
    ;; JSON を通して持つ(本物の置き場と同じく、JSON にできない物が紛れたらここで落ちる)。chunk は行に添えて残す。
    (.append self.lines (| (json.loads (json.dumps line :ensure-ascii False)) {"_chunk" chunk})))
  (defn #^ bool begin-send [self #^ bool force] False)
  (defn #^ (| SinkBatch None) next-batch [self] None)
  (defn #^ None delivered [self #^ SinkPost post] None)
  (defn #^ None undelivered [self #^ str reason] None)
  (defn #^ None end-send [self] None)
  (defn #^ None trim [self] None))


(defclass BufferedSink []
  "行を貯めて flush-seconds か max-lines ごとに送る置き場の口の共通部分。届かない間は貯め続け(retry-seconds ごとに試す)、
   max-buffer 行を超えたら捨てて lost を数える(記録は途切れる — 本番を止めない)。送りの本文は sink-post が口の種類(子 class)ごとに
   綴る(#2764 — 以前は子 class の method post-request)。buffer の 1 項 = #(区切り 行の dict 行の JSON の文字列)。送りそのものは
   send-records が HttpRequest で出す。"
  (defn #^ None __init__ [self #^ float [flush-seconds 2.0] #^ int [max-lines 500] #^ int [max-buffer 200000] #^ float [timeout 10.0]
                          #^ float [retry-seconds 10.0] #^ int [max-post-bytes 4000000]]
    (setv self.flush-seconds flush-seconds self.max-post-bytes max-post-bytes
          self.max-lines max-lines self.max-buffer max-buffer self.timeout timeout self.retry-seconds retry-seconds
          self.buffer [] self.last-flush (time.monotonic) self.retry-at 0.0 self.lost 0 self.sent 0 self.failures 0 self.sending False))

  (defn #^ None write [self #^ int chunk #^ dict line]
    (.append self.buffer #(chunk line (json.dumps line :ensure-ascii False :separators #("," ":"))))
    None)

  (defn #^ int batch-size [self]
    "次の 1 回の送りに載せる行の数(先頭から max-post-bytes まで・最低 1 行)。"
    (setv n 0 size 0)
    (for [#(_c _l text) self.buffer]
      (when (and (> n 0) (> (+ size (len text)) self.max-post-bytes)) (break))
      (+= n 1)
      (+= size (len text)))
    n)

  (defn #^ bool begin-send [self #^ bool force]
    ;; 送る時 = max-lines 行貯まった・flush-seconds 経った(届かなかった後は retry-seconds を待つ)・終わりの送り(force)。
    ;; 別の task が送りの最中なら送らない(同じ先頭の行を 2 度送らず、届いた分を外す位置も狂わせない)。
    (setv now (time.monotonic))
    (when (or self.sending
              (not (or force (and (>= now self.retry-at)
                                  (or (>= (len self.buffer) self.max-lines) (>= (- now self.last-flush) self.flush-seconds))))))
      (return False))
    (setv self.last-flush now self.sending True)
    True)

  (defn #^ (| SinkBatch None) next-batch [self]
    "次の 1 回の送りに載せる束(buffer の先頭から batch-size 行)と、束を取った時の壁時計。送る物が無ければ None。"
    (if self.buffer
        (SinkBatch :items (tuple (cut self.buffer 0 (.batch-size self))) :now-ms (int (* 1000 (time.time))))
        None))

  (defn #^ None delivered [self #^ SinkPost post]
    ;; 送れた分だけ buffer から外す(途中で失敗したら残りだけを次に送る)。
    (+= self.sent post.count)
    (setv self.buffer (cut self.buffer post.count None))
    None)

  (defn #^ None undelivered [self #^ str reason]
    ;; 送れなかった: 貯めたまま次を待つ(同じ行を 2 度送りうる — 読む側は行の e と内容の hash で重なりを捨てる)。
    (+= self.failures 1)
    (setv self.retry-at (+ (time.monotonic) self.retry-seconds))
    (print (.format "recorder: 記録の置き場に送れない({} 行を貯めている): {}" (len self.buffer) reason)
           :file sys.stderr :flush True)
    None)

  (defn #^ None end-send [self]
    (setv self.sending False)
    None)

  (defn #^ None trim [self]
    (when (and (not self.sending) (> (len self.buffer) self.max-buffer))
      (+= self.lost (len self.buffer))
      (setv self.buffer []))
    None))


(defclass HttpSink [BufferedSink]
  "記録の置き場 effect-records(record_store.hy)の POST /append。1 回の送りは同じ区切りの行だけ。
   ⚠ 2026-09-25 から OtlpSink(OpenTelemetry → ClickHouse)へ移す途中。新しい経路が本番で動いたのを確かめたら退役する。"
  (defn #^ None __init__ [self #^ str url #^ str service #^ str run #^ float [flush-seconds 2.0] #^ int [max-buffer 200000]]
    (.__init__ (super) :flush-seconds flush-seconds :max-buffer max-buffer)
    (setv self.url (.rstrip url "/") self.service service self.run run)))


(defclass OtlpSink [BufferedSink]
  "OpenTelemetry の collector の OTLP/HTTP(JSON)の口 POST <url>/v1/logs。行 1 つ = log record 1 件・resource の service.name = service。
   collector が ClickHouse(hot / warm / cold の 3 層・期限で移して最後に消す)へ入れる(deploy/effect-telemetry.yaml)。本文の綴りは
   otlp-logs-post。"
  (defn #^ None __init__ [self #^ str url #^ str service #^ str run #^ float [flush-seconds 2.0] #^ int [max-buffer 200000]]
    (.__init__ (super) :flush-seconds flush-seconds :max-buffer max-buffer)
    (setv self.url (.rstrip url "/") self.service service self.run run)))


(defk store-append-post [sink batch]
  {:pre [(: sink HttpSink) (: batch SinkBatch)] :post [(: % SinkPost)] :tags {:context "doeff-cluster" :role "foundation" :spells "json"}}
  "貯めた行の束を、記録の置き場 effect-records の POST /append の 1 回の送りに綴るため(1 回の送りは束の先頭と同じ区切りの行だけ —
   以前は HttpSink の method post-request・#2764)。"
  (val chunk (get (get batch.items 0) 0))
  (val texts (lfor #(_c _l text) (takewhile (fn [item] (= (get item 0) chunk)) batch.items) text))
  ;; 本文は JSON の境界の dict(HTTP の答え手が JSON に綴る)。
  (SinkPost :request (HttpRequest "POST" (+ sink.url "/append")
                                  :headers {"Content-Type" "application/json" "X-Actor" (+ "recorder:" sink.service)}
                                  :body {"service" sink.service "run" sink.run "chunk" chunk "lines" texts}
                                  :timeout-seconds sink.timeout :max-retries 0 :failures-as-values True)
            :count (len texts)))


(defk otlp-logs-post [sink batch]
  {:pre [(: sink OtlpSink) (: batch SinkBatch)] :post [(: % SinkPost)] :tags {:context "doeff-cluster" :role "foundation" :spells "json"}}
  "貯めた行の束を OTLP/HTTP(JSON)の 1 回の送りに綴るため。行 1 つ = log record 1 件: body = 行の JSON・時刻 = 行の at(無ければ束の
   now)・属性 = 読む側が絞る鍵(run・区切り・行の種類・出来事の番号・effect の型・内容の hash — ClickHouse の表はこの属性を列に持つ・
   deploy/effect-telemetry)。以前は OtlpSink の method が行ごとに素で呼ぶ deff otlp-log-record だった(#2764 — 行ごとの綴りは束の内包表記の
   中の式にし、行ごとに Program を作らない)。"
  (val records
    (lfor #(chunk line text) batch.items
          :setv at (or (.get line "at") (.get line "startedMs") batch.now-ms)
          :setv attrs (+ #(#("run" sink.run) #("chunk" (str chunk)) #("k" (str (.get line "k"))))
                         (if (in "e" line) #(#("e" (str (get line "e")))) #())
                         (if (in "ty" line) #(#("ty" (get line "ty"))) #())
                         (if (in "h" line) #(#("h" (get line "h"))) #()))
          {"timeUnixNano" (str (* (int at) 1000000))
           "observedTimeUnixNano" (str (* batch.now-ms 1000000))
           "body" {"stringValue" text}
           "attributes" (lfor #(k v) attrs {"key" k "value" {"stringValue" v}})}))
  ;; 本文は JSON の境界の dict(HTTP の答え手が JSON に綴る)。
  (SinkPost :request (HttpRequest "POST" (+ sink.url "/v1/logs")
                                  :headers {"Content-Type" "application/json"}
                                  :body {"resourceLogs"
                                         [{"resource" {"attributes" [{"key" "service.name" "value" {"stringValue" sink.service}}]}
                                           "scopeLogs" [{"scope" {"name" "doeff.effect-record" "version" (str FORMAT-VERSION)}
                                                         "logRecords" records}]}]}
                                  :timeout-seconds sink.timeout :max-retries 0 :failures-as-values True)
            :count (len batch.items)))


(defk sink-post [sink batch]
  {:pre [(: sink BufferedSink) (: batch SinkBatch)] :post [(: % SinkPost)] :tags {:context "doeff-cluster" :role "foundation"}}
  "貯めた行の束を、置き場の口の種類ごとの送り 1 回に綴るため(綴りは口の class の外 — 口は束を渡し、送れた分を外すだけ・#2764)。"
  (match sink
    (OtlpSink) (! (otlp-logs-post sink batch))
    (HttpSink) (! (store-append-post sink batch))
    _ (raise (TypeError (.format "送りの綴りの無い置き場の口: {}" (. (type sink) __name__))))))


(defk post-records [post]
  {:pre [(: post SinkPost)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "foundation"}}
  "1 回の送り post を HttpRequest で出し、届かなかった理由を返す(届いたら None)。答え手が何を投げても理由にする(業務は止めない)。"
  (try
    (<- answer (| HttpResponse HttpFailed) post.request)
    (match answer
      (HttpFailed) answer.detail
      (HttpResponse) :if (>= answer.status 400) (.format "HttpError: HTTP {} {}" answer.status answer.url)
      (HttpResponse) None
      _ (.format "HTTP の答えでない: {!r}" answer))
    (except [e Exception]
      (.format "{}: {}" (. (type e) __name__) e))))


(defk send-records [sink force]
  {:pre [(: sink (| MemorySink BufferedSink)) (: force bool)] :post [(: % (type None))] :tags {:context "doeff-cluster" :role "foundation"}}
  "置き場の口 sink が送る時なら(force = 期限を待たずに)、貯めた行を先頭から送り切るか、届かなくなるまで送る。その後、貯めすぎた
   行を捨てる(max-buffer — 記録は途切れる)。"
  (when (.begin-send sink force)
    (try
      (var batch (.next-batch sink))
      (while (is-not batch None)
        (<- post SinkPost (sink-post sink batch))
        (<- reason (| str None) (post-records post))
        (if (is reason None)
            (do (.delivered sink post)
                (:= batch (.next-batch sink)))
            (do (.undelivered sink reason)
                (:= batch None))))
      (finally
        (.end-send sink))))
  (.trim sink)
  None)


;; --- 記録 -------------------------------------------------------------------------------------

(defclass EffectLog [TaskTap]
  "1 つの process の記録の係。header = run の行の欄(service・run・版・設定 …)。wall-ms = 壁時計(ms)を返す関数。
   形の版 2: 大きな値は内容参照(record_codec.intern-json)にし、中身は run の中で初めて出た時に blob の行で書く。問いの直後に
   (他の出来事を挟まずに)答えが返ったら、問いと答えを 1 行(call)にまとめる — 問いは答えが返るか他の出来事が来るまで手元に持つ。"
  (defn #^ None __init__ [self #^ RecordSink sink #^ dict header #^ bool [strict False] #^ float [chunk-seconds 3600.0]
                          #^ (| Callable None) [wall-ms None] #^ int [intern-min INTERN-MIN-CHARS] #^ int [blob-memory BLOB-MEMORY-MAX]]
    (.__init__ (super))
    (setv self.sink sink self.header header self.strict strict self.chunk-ms (int (* 1000 chunk-seconds))
          self.wall-ms (or wall-ms (fn [] (int (* 1000 (time.time)))))
          self.handles (HandleTable) self.e 0 self.chunk -1 self.seen (BlobMemory blob-memory) self.watched [] self.watched-ids {}
          self.broken None self.held None self.intern-min intern-min)
    (setv self.started (self.wall-ms))
    (.emit self (| {"k" "run" "format" FORMAT-VERSION "startedMs" self.started} header) :numbered False))

  (defn #^ int next-e [self]
    (setv e self.e)
    (+= self.e 1)
    e)

  (defn #^ None roll [self #^ bool numbered]
    "区切りが変わったら区切りの頭を書く(区切りは読む範囲の目印。内容参照の記憶は run の間ずっと持つ)。"
    (setv now (self.wall-ms) chunk (max 0 (// (- now self.started) (max 1 self.chunk-ms))))
    (when (!= chunk self.chunk)
      (setv self.chunk chunk)
      (when numbered
        (.write self.sink chunk {"k" "chunk" "n" chunk "at" now "run" (.get self.header "run")}))))

  (defn #^ None release-held [self]
    "手元に持っている問いを 1 行で書く(答えより先に他の出来事が来た・記録を止める)。"
    (when (is-not self.held None)
      (setv #(chunk line) self.held self.held None)
      (.write self.sink chunk line)))

  (defn #^ None emit [self #^ dict line #^ bool [numbered True]]
    "行を書く(持っている問いがあれば先に書く)。"
    (.release-held self)
    (.roll self numbered)
    (.write self.sink self.chunk line)
    (.check-lost self))

  (defn #^ None check-lost [self]
    (when (> self.sink.lost 0)
      (.break self (.format "記録の置き場に届かず {} 行を捨てた" self.sink.lost))))

  (defn #^ dict big [self #^ str tag #^ object value]
    "値の欄 {tag: 値}。大きな節は内容参照にし、記憶に無い中身は blob の行で先に書く。"
    (defn #^ None blob [#^ str h #^ object form]
      (.write self.sink self.chunk {"k" "blob" "h" h "v" form}))
    {tag (intern-json value self.seen blob self.intern-min)})

  (defn #^ None break [self #^ str why]
    "ここから先は記録しない(印を 1 行置く)。"
    (when (is self.broken None)
      (setv self.broken why)
      (try (.release-held self)
           (.write self.sink (max self.chunk 0) {"k" "broken" "e" (.next-e self) "at" (self.wall-ms) "why" why})
           (except [Exception] None))
      (print (+ "recorder: 記録を止めた: " why) :file sys.stderr :flush True)))

  (defn #^ int request [self #^ str label #^ EffectBase effect #^ dict args #^ str mode #^ (| str None) subject]
    "問いを 1 行作って手元に持ち、問いの番号を返す(答えが続けば call の 1 行にまとめる)。"
    (.refresh-watched self)
    (.release-held self)
    (.roll self True)
    (setv e (.next-e self) codec (codec-of effect))
    ;; 空の欄は書かない(読む側は sj = None・a = {}・dt = 0 と読む)。時計の読みのように 1 秒に何十回も出る問いの行を短くする。
    (setv line {"k" "req" "e" e "t" label "at" (self.wall-ms) "ty" codec.name "m" mode})
    (when (is-not subject None) (setv (get line "sj") subject))
    (when args (.update line (.big self "a" args)))
    (setv self.held #(self.chunk line))
    e)

  (defn #^ None settle [self #^ int s #^ dict fields]
    "答えの欄を書く: 手元の問いが s なら 1 行(call・答えの番号は s + 1)に、そうでなければ ans の行に。"
    (setv e (.next-e self) at (self.wall-ms))
    (if (and (is-not self.held None) (= (get (get self.held 1) "e") s) (= e (+ s 1)) (= (get self.held 0) self.chunk))
        (do (setv #(chunk line) self.held self.held None)
            (setv dt (- at (get line "at")))
            (.write self.sink chunk (| line {"k" "call"} (if dt {"dt" dt} {}) fields))
            (.check-lost self))
        (.emit self (| {"k" "ans" "e" e "s" s "at" at} fields))))

  (defn #^ None answer [self #^ int s #^ (get EffectBase A) effect #^ A value #^ dict args #^ (| str None) child]
    (setv codec (codec-of effect))
    (when (is-not codec.binds None)
      (.bind self.handles value codec.binds (if (= codec.binds "task") child (.format "{}" s))))
    (if (and codec.watch (isinstance value #(dict list)) (in (id value) self.watched-ids))
        (setv encoded {"$w" (get self.watched-ids (id value))})
        (do (setv encoded (encode-value value self.handles))
            (when (and codec.watch (isinstance value #(dict list)))
              (setv (get self.watched-ids (id value)) s)
              (.append self.watched [s value encoded]))))
    (.settle self s (| {"ok" True} (.big self "v" encoded)))
    (.check-watched self))

  (defn #^ None failed [self #^ int s #^ BaseException error]
    (.settle self s {"ok" False "err" (encode-error error self.handles)})
    (.check-watched self))

  (defn #^ None refresh-watched [self]
    "問いの直前: 直前までに走ったのは業務コード(この問いを出した task か、その前に動いた task)。業務コード自身の書き換えは
     再生でも業務コードがもう一度するので記録しない — 断面だけ取り直す。"
    (for [row self.watched]
      (setv (get row 2) (encode-value (get row 1) self.handles))))

  (defn #^ None check-watched [self]
    "答えの直前に走ったのは handler(Ask で渡した共有の箱を handler が書き換える — 書きの計器等)。前の断面から変わった所を
     mut の 1 行(鍵ごとの差分)で書く。再生はその差分を同じ番号の位置で箱に当てる。"
    (for [row self.watched]
      (setv #(ref obj last) row)
      (setv now (encode-value obj self.handles))
      (when (!= (canonical now) (canonical last))
        (setv (get row 2) now)
        (setv patch (if (and (isinstance now dict) (isinstance last dict))
                        {"set" (dfor #(k v) (.items now) :if (or (not-in k last) (!= (canonical v) (canonical (get last k)))) k v)
                         "del" (lfor k last :if (not-in k now) k)}
                        {"all" now}))
        (.emit self {"k" "mut" "e" (.next-e self) "ref" ref "at" (self.wall-ms) "patch" patch}))))

  (defn #^ None task-ended [self #^ str label #^ bool ok #^ (| BaseException None) error]
    (when (is self.broken None)
      (.emit self {"k" "end" "e" (.next-e self) "t" label "at" (self.wall-ms) "ok" ok}))))


(defhandler effect-recorder [#^ EffectLog log]
  (EffectBase []
    (if (is-not log.broken None)
        (reperform effect)
        (do
          (val label (.current-label log effect))
          (var prepared None)
          ;; 問いの形を作る(登録の無い型・記録の形にできない値 → strict なら業務へ投げる・そうでなければ記録を止めて素通し)
          (try
            (val tried-codec (codec-of effect))
            (val tried-mode (mode-of effect log.handles))
            (val tried-child (if (isinstance effect Spawn) (.child-label log label) None))
            (<- effect-args dict (args-of effect log.handles))
            (val tried-args (if (is tried-child None) effect-args (| effect-args {"child" tried-child})))
            (:= prepared #(tried-codec tried-mode tried-args (subject-of effect tried-args) tried-child))
            (except [e [UnrecordableEffect UnencodableValue]]
              (if log.strict (:= prepared e) (.break log (.format "{}: {}" (. (type e) __name__) e)))))
          (cond
            (isinstance prepared BaseException) (raise prepared)
            (is prepared None) (reperform effect)
            True
              (do
                (val codec (get prepared 0))
                (val mode (get prepared 1))
                (val args (get prepared 2))
                (val subject (get prepared 3))
                (val child (get prepared 4))
                (val s (.request log label effect args mode subject))
                (var answer None)
                (var error None)
                (if (is child None)
                    (try (do (<- performed effect) (:= answer performed)) (except [e Exception] (:= error e)))
                    (do (assert (isinstance effect Spawn) "子の名札は Spawn の時だけ付く")
                        (<- chain list (GetBoundaries k))
                        (try (:= answer !(Spawn (spawn-program log child effect.program chain)
                                               :priority effect.priority :daemon effect.daemon))
                             (except [e Exception] (:= error e)))))
                (try
                  (if (is error None) (.answer log s effect answer args child) (.failed log s error))
                  (except [e [UnrecordableEffect UnencodableValue]]
                    (if log.strict (:= error e) (.break log (.format "答え: {}: {}" (. (type e) __name__) e)))))
                ;; 送る時なら貯めた行を置き場へ送る(HttpRequest — この handler より外側の答え手が答える)。届かなくても業務は止めない。
                (<- (send-records log.sink False))
                (.check-lost log)
                (if (is error None) (resume answer) (raise error))))))))


(defk close-log [log]
  {:pre [(: log EffectLog)] :post [(: % (type None))] :tags {:context "doeff-cluster" :role "foundation"}}
  "記録の終わり: 手元に持っている問いを書き、置き場に貯めた行を期限を待たずに送る。置き場の口(BufferedSink)は flush-seconds か
   max-lines に達した時にしか送らないので、これが無いと短い job の記録は 1 行も届かず、長く動く Program も終わる直前の行を失う
   (recorded-run が記録する Program の終わりに呼ぶ)。届かなければ置き場の口が理由を出して貯めたまま(業務は止めない)。"
  (.release-held log)
  (<- (send-records log.sink True))
  None)


(defk recorded-run [log body]
  {:pre [(: log EffectLog) (: body (| Program EffectBase))] :post [(: % "body の答え")] :tags {:context "doeff-cluster" :role "foundation"}}
  "body を記録係(effect-recorder)の下で走らせ、終わった時(例外でも)に残りの行を送る(close-log)。"
  (try
    (<- answer (with-handlers [(effect-recorder log)] body))
    answer
    (finally
      (<- (close-log log)))))


(defclass RecordingInstaller []
  "記録係を body に被せる口(with-handlers の list に置く物)— body を recorded-run で包む。記録の終わりの送りを Program の中で
   出すため、effect の handler(effect-recorder)そのものではなく body を包む関数にする(http-production-handler の client の寿命と同じ形)。"
  (setv _doeff_is_handler_fn True)
  (defn #^ None __init__ [self #^ EffectLog log] (setv self.log log))
  (deff __call__ [self body]  ; defk にできない: with-handlers が body を渡して呼ぶ素の口
    {:pre [(: self RecordingInstaller) (: body (| Program EffectBase))] :post [(: % Program)] :tags {:context "doeff-cluster" :role "foundation"}}
    (recorded-run self.log body)))


;; --- 再生 -------------------------------------------------------------------------------------

(defclass ReplayState [TaskTap]
  "再生の記憶。rec = 読んだ記録。cursor = 次に来るべき出来事(rec.events の位置)。skip = 番号より先に済ませた出来事。
   waiting = 出来事の番号 → その番号を待っている task の promise。"
  (defn #^ None __init__ [self #^ Recording rec #^ (| int None) [from-ms None] #^ (| int None) [to-ms None] #^ bool [ordered True]]
    (.__init__ (super))
    ;; ordered = 偽なら番号の順を待たない(テストの対照: 順を揃えない再生が違う結果になることを示すためだけに使う)。
    (setv self.rec rec self.from-ms from-ms self.to-ms to-ms self.ordered ordered
          self.heads {} self.cursor 0 self.skip (set) self.waiting {} self.handles (HandleTable) self.watched {}
          self.counts {READ 0 LIVE 0 DECISION 0 OUTPUT 0} self.decisions [] self.outputs [] self.divergence None
          self.finished False self.driver-running False self.ended {})
    (.settle self))

  (defn #^ (| int None) current [self]
    (if (< self.cursor (len self.rec.events)) (get (get self.rec.events self.cursor) 0) None))

  (defn #^ None settle [self]
    "cursor を、済んだ出来事・共有の箱の変化(ここで当てる)・task の終わりの上を進める。進み切ったら finished。"
    (while (< self.cursor (len self.rec.events))
      (setv #(e kind owner extra) (get self.rec.events self.cursor))
      (cond
        (in e self.skip) (do (.discard self.skip e) (+= self.cursor 1))
        (= kind "mut") (do (.apply-mut self extra) (+= self.cursor 1))
        (= kind "end") (+= self.cursor 1)
        True (break)))
    (when (>= self.cursor (len self.rec.events))
      (setv self.finished True)))

  (defn #^ None apply-mut [self #^ dict line]
    "handler が共有の箱に加えた変化(鍵ごとの差分)を、再生の箱に同じ位置で当てる。業務コード自身の書き換えは残る。"
    (setv obj (.get self.watched (get line "ref")) patch (get line "patch"))
    (when (is obj None) (return None))
    (if (in "all" patch)
        (do (setv value (decode-value (get patch "all")))
            ;; 箱と記録の中身は同じ形(dict か list)のはず — 違えば記録と箱の食い違いとして断る。
            (match #(obj value)
              #((dict) :as box (dict) :as recorded) (do (.clear box) (.update box recorded))
              #((list) :as box (list) :as recorded) (setv (cut box None None) recorded)
              _ (raise (TypeError (.format "共有の箱 {!r} と記録の中身 {!r} の形が違う" obj value)))))
        (do (for [#(k v) (.items (get patch "set"))]
              (setv (get obj k) (decode-value v)))
            (for [k (get patch "del")]
              (.pop obj k None)))))

  (defn #^ list consume [self #^ (| int None) e]
    "出来事 e を済ませる。番号の順ならその場で進め、先に済んだ物は skip に置く。起こすべき promise の列を返す。"
    (when (is e None) (return []))
    (if (= e (.current self))
        (do (+= self.cursor 1) (.settle self))
        (.add self.skip e))
    (.wakeable self))

  (defn #^ list wakeable [self]
    (cond
      (or self.finished (is-not self.divergence None))
        (do (setv out (list (.values self.waiting))) (.clear self.waiting) out)
      (in (.current self) self.waiting) [(.pop self.waiting (.current self))]
      True []))

  (defn #^ dict diverge [self #^ dict info]
    "最初の分岐だけを残す。返り値 = 残っている分岐(いまの info か、先に立っていた物)— 呼び手は None を絞らずに読める。"
    (when (is self.divergence None)
      (setv self.divergence info))
    self.divergence)

  (defn #^ (| (get tuple #(int str str int)) None) stall-point [self]
    "他の task が全部止まったのに番号が進まない時、誰も出さない記録の出来事の地点 #(番号 種類 task 問いの番号) を返す
     (分岐の報告は defk stall-divergence が綴る)。進み切った・分岐が立っている時は None。"
    (setv e (.current self))
    (when (or (is e None) self.finished (is-not self.divergence None)) (return None))
    (setv #(_ kind owner extra) (get self.rec.events self.cursor))
    #(e kind owner (if (= kind "req") e extra)))

  (defn #^ None task-ended [self #^ str label #^ bool ok #^ (| BaseException None) error]
    (setv (get self.ended label) (if ok True (repr error)))))


(defk park-until [state e]
  {:pre [(: state ReplayState) (: e (| int None))] :post [(: % (type None))]}
  ;; 出来事 e の番が来るまで待つ(e = None なら記録の終わりか分岐まで)。
  (when (and state.ordered (or (is e None) (!= (.current state) e)) (not state.finished) (is state.divergence None))
    (<- promise Promise (CreatePromise))
    (setv (get state.waiting (if (is e None) #("end" (id promise)) e)) promise)
    (when (not state.driver-running)
      (setv state.driver-running True)
      (<- (Spawn (replay-driver state) :priority PRIORITY-IDLE :daemon True)))
    (<- (Wait promise.future)))
  (when (is-not state.divergence None)
    (raise (ReplayDiverged (.get state.divergence "reason"))))
  (when (and (or state.finished (not state.ordered)) (or (is e None) (and state.finished (!= (.current state) e))))
    (raise (ReplayFinished "記録の終わり")))
  None)


(defk wake [promises]
  {:pre [(: promises list)] :post [(: % (type None))]}
  (for [p promises]
    (<- (CompletePromise p None)))
  None)


(defk stall-divergence [state]
  {:pre [(: state ReplayState)] :post [(: % (| (get dict #(str object)) None))] :tags {:context "doeff-cluster" :role "foundation" :spells "json"}}
  "他の task が全部止まったのに番号が進まない時の分岐の報告(理由・地点・記録が待っていた問い)を綴るため — 記録の出来事を誰も出さない = 分岐。
   止まった地点が無ければ None。"
  (match (.stall-point state)
    None None
    #(e kind owner asked)
      (do (<- entry (| Entry None) (entry-of state.rec asked))
          (<- expected (| (get dict #(str object)) None) (expected-of entry))
          ;; 型の注釈つきで 1 度受ける(dict の literal は欄ごとの型で推され、そのままでは答えの dict[str, object] に合わない)。
          (setv #^ (get dict #(str object)) report
                {"reason" (if (in owner state.ended)
                              "記録ではこの後も問いを出す task が、再生では先に終わった"
                              "記録の出来事を再生の業務の Program が出さないまま止まった")
                 "event" e "kind" kind "task" owner "expected" expected "at" (if (is entry None) None entry.at)})
          report)))


(defk expected-of [entry]
  {:pre [(: entry (| Entry None))] :post [(: % (| (get dict #(str object)) None))] :tags {:context "doeff-cluster" :role "foundation" :spells "json"}}
  "分岐の報告に載せる、記録が待っていた問い(型と引数)を綴るため。待っていた問いが無ければ None。"
  (if (is entry None)
      None
      (do (<- args (get dict #(str object)) (recorded-args entry))
          (setv #^ (get dict #(str object)) expected {"type" entry.type "args" args})
          expected)))


(defk replay-driver [state]
  {:pre [(: state ReplayState)] :post [(: % (type None))]}
  ;; 他の task が全部止まった時だけ動く(PRIORITY_IDLE の daemon)。それでも待っている task が居れば、番号の出来事を誰も出さない。
  (try
    (when state.waiting
      ;; 分岐は state.divergence に残る(最初の 1 つだけ)。ここは印を付けて、待っている task を起こすだけ。
      (<- stalled (| (get dict #(str object)) None) (stall-divergence state))
      (when (is-not stalled None)
        (.diverge state stalled))
      (<- (wake (.wakeable state))))
    (finally
      (setv state.driver-running False)))
  None)


(defk consume-at-turn [state e]
  {:pre [(: state ReplayState) (: e int)] :post [(: % (type None))]}
  (<- (park-until state e))
  (<- (wake (.consume state e)))
  None)


;; 成功の答えの値は read-recording が記録を読む時に戻した値(record_log.RestoredAnswer — #1693・#2581)。ここは JSON を読まない。
(defk deliver-recorded [state entry codec]
  {:pre [(: state ReplayState) (: entry Entry) (: codec EffectCodec)] :post [(: % (get tuple #(bool object)))]
   :tags {:context "doeff-cluster" :role "foundation" :reads "json"}}
  "記録の答えを、再生が業務へ返す値にするため(共有の箱の参照は再生の箱へ)。答え = #(True 値) か、失敗の答えなら #(False 例外の object)。"
  (cond
    entry.ok
      (match entry.value
        (WatchedRef :entry watched) #(True (get state.watched watched))
        ;; 渡すのは読んだ値の写し — 業務と再生の箱が書き換えても Entry.value(記録の読みの答え)は変わらない
        ;; (以前は渡すたびに JSON から作り直していた。同じ Recording を 2 度再生しても同じ値を返す・#2581)。
        restored (do (val value (copy.deepcopy restored))
                     (when (and codec.watch (isinstance value #(dict list)))
                       (setv (get state.watched entry.e) value))
                     #(True value)))
    ;; 失敗の答えは err の欄を持つ(read-recording が ok = 偽の答えの行から入れる)— 無ければ記録が壊れている。
    (is entry.error None)
      (raise (ValueError (.format "記録の失敗の答え(問い {})に err が無い" entry.e)))
    True
      #(False (decode-error (json.loads entry.error.text)))))


(defhandler effect-replayer [#^ ReplayState state]
  (EffectBase []
    (setv label (.current-label state effect) rec state.rec)
    (when (is-not state.divergence None)
      (raise (ReplayDiverged (.get state.divergence "reason"))))
    (var codec None)
    (try (:= codec (codec-of effect)) (except [UnrecordableEffect] None))
    (when (is codec None)
      (.diverge state {"reason" "記録の登録に無い effect の型" "task" label "actual" {"type" (str (type effect))}})
      (<- (wake (.wakeable state)))
      (raise (ReplayDiverged "記録の登録に無い effect の型")))
    (setv mode (mode-of effect state.handles))
    (setv child (if (isinstance effect Spawn) (.child-label state label) None))
    (<- effect-args dict (args-of effect state.handles))
    (setv args (if (is child None) effect-args (| effect-args {"child" child})))
    (<- queue tuple (queue-of rec label))
    (setv subject (subject-of effect args) head (.get state.heads label 0))
    ;; 引数の比べる形はこの問いにつき 1 度だけ作る(記録の側は read-recording が作った文字列 — #2727)。
    (setv #(verdict pos skipped) (match-step rec queue head codec.name (canonical args) mode subject (in label rec.ended)))
    ;; 記録に在って再生が出さなかった decision / output(missing)は、その出来事を済ませたことにして報告する。
    (for [e skipped]
      (setv missed (get rec.entries e))
      (<- missing dict (diff-row "missing" missed missed.type missed.subject label None))
      (.append (if (= missed.mode DECISION) state.decisions state.outputs) missing)
      (<- (wake (.consume state missed.e)))
      (<- (wake (.consume state missed.ans-e))))
    (setv (get state.heads label) pos)
    (cond
      (= verdict "diverge")
        (do (setv entry (if (< pos (len queue)) (get rec.entries (get queue pos)) None))
            (<- expected (| (get dict #(str object)) None) (expected-of entry))
            (val divergence (.diverge state {"reason" (if (is entry None) "記録ではもう問いを出さない task が問いを出した" "問いが記録と食い違った")
                             "task" label "event" (if entry entry.e None) "at" (if entry entry.at None)
                             "expected" expected
                             "actual" {"type" codec.name "args" args}}))
            (<- (wake (.wakeable state)))
            (raise (ReplayDiverged (get divergence "reason"))))
      (or (= verdict "finish") (and (= verdict "extra") state.finished))
        ;; 記録を読み切った後に業務の Program が出した書き・報告は、比べる相手(記録)が無い — 違いに数えずに終わる
        ;; (動いている run の記録は周期の途中で切れるので、再生は切れ目の先の報告まで進むことがある。2026-09-25 実測)。
        (do (<- (park-until state None))
            (raise (ReplayFinished "記録の終わり")))
      (= verdict "extra")
        (do (setv current-entry (.current state))
            (<- extra dict (diff-row "extra" None codec.name subject label args :at (if (is current-entry None) None (. (get rec.entries current-entry) at))))
            (.append (if (= mode DECISION) state.decisions state.outputs) extra)
            (when (is codec.unexecuted DIVERGE)
              (val unpaired-divergence (.diverge state {"reason" "記録に対の無い書き込み(実行していない時の答えが決まっていない型)" "task" label
                                                "actual" {"type" codec.name "args" args}}))
              (<- (wake (.wakeable state)))
              (raise (ReplayDiverged (get unpaired-divergence "reason"))))
            (resume codec.unexecuted))
      True
        (do
          (setv entry (get rec.entries (get queue pos)))
          (setv (get state.heads label) (+ pos 1))
          (<- (consume-at-turn state entry.e))
          (+= (get state.counts mode) 1)
          (var answer None)
          (var error None)
          (when (= mode LIVE)
            (if (is child None)
                (try (do (<- performed effect) (:= answer performed)) (except [e Exception] (:= error e)))
                (do (assert (isinstance effect Spawn) "子の名札は Spawn の時だけ付く")
                    (<- chain list (GetBoundaries k))
                    (try (:= answer !(Spawn (spawn-program state child effect.program chain)
                                           :priority effect.priority :daemon effect.daemon))
                         (except [e Exception] (:= error e)))))
            (when (and (is error None) (is-not codec.binds None))
              (.bind state.handles answer codec.binds (if (= codec.binds "task") child (.format "{}" entry.e)))))
          (when (is entry.ans-e None)
            ;; 記録が終わった時、この問いの答えはまだ返っていなかった。
            (<- (park-until state None))
            (raise (ReplayFinished "記録の終わり")))
          (<- (consume-at-turn state entry.ans-e))
          ;; 引数だけが違う書き(changed): 違いを報告し、記録の答え(同じ前提への engine の答え)を返す。
          (when (= verdict "changed")
            (<- changed dict (diff-row "changed" entry codec.name subject label args))
            (.append (if (= mode DECISION) state.decisions state.outputs) changed))
          (cond
            (= mode LIVE) (if (is error None) (resume answer) (raise error))
            True
              (do (<- delivered (get tuple #(bool object)) (deliver-recorded state entry codec))
                  ;; 失敗の答えは decode-error が作った例外(deliver-recorded の約束)— 例外でなければ記録が壊れている。
                  (match delivered
                    #(True answer) (resume answer)
                    #(False (BaseException) :as error) (raise error)
                    _ (raise (TypeError (.format "記録の失敗の答えが例外でない: {!r}" delivered))))))))))


(defk replay-report [#^ ReplayState state #^ str end]
  {:pre [(: state ReplayState) (: end str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation" :spells "json"}}
  "再生を終えた状態と終わり方 end → 再生の報告(一致・判断の違い・分岐を 1 つの dict)— 再生の道具と検が同じ報告を読むため。"
  (<- report dict (summarize state.rec state.counts state.decisions state.outputs state.divergence end state.cursor
                             :from-ms state.from-ms :to-ms state.to-ms))
  report)


;; --- 組み立て(worker の子 process の composition root が使う) ------------------------------------

(defn #^ str run-name [#^ int started-ms #^ str worker #^ str instance]
  "記録の run の名 = 始まりの時刻(UTC)・worker・process の世代(置き場の dir の名。並べると時刻の順)。"
  (.format "{}-{}-{}" (time.strftime "%Y%m%dT%H%M%SZ" (time.gmtime (/ started-ms 1000))) (or worker "local")
           (or instance (str (os.getpid)))))

(defk recording-handler [record service header]
  {:pre [(: record dict) (: service str) (: header dict)] :post [(: % RecordingInstaller)] :tags {:context "doeff-cluster" :role "foundation" :reads "json"}}
  "記録の置き場の設定 record → 記録係(境目の記録係 boundary-recorder の record の枝と、cluster の外の process が使う)。
   record = {\"otlp\": collector の URL(か \"store\": 旧い置き場の URL)・
   \"chunkSeconds\"・\"flushSeconds\"}。
   header = run の行に載せる欄(版・設定・process の世代 …)。置き場に届かなくても業務は止めない(HttpSink の説明)。
   置き場への送りは HttpRequest(send-records)— 答える handler(本番は http-production-handler)を記録係より外側に置く。"
  (val started (int (* 1000 (time.time))))
  (val run (run-name started (.get header "worker" "") (.get header "instance" "")))
  (val options {"flush_seconds" (float (.get record "flushSeconds" 2.0)) "max_buffer" (int (.get record "maxBufferLines" 200000))})
  ;; 置き場の口: otlp = OpenTelemetry の collector(2026-09-25 から)・store = 旧い置き場 effect-records(退役まで)。
  (val sink (if (in "otlp" record)
                (OtlpSink (get record "otlp") service run #** options)
                (HttpSink (get record "store") service run #** options)))
  (val log (EffectLog sink (| header {"service" service "run" run})
                      :chunk-seconds (float (.get record "chunkSeconds" 3600.0))
                      :wall-ms (fn [] (int (* 1000 (time.time))))))
  (print (.format "recorder: {} の effect を記録します(run {}・置き場 {})" service run (or (.get record "otlp") (.get record "store"))) :file sys.stderr :flush True)
  ;; 記録する Program の終わりに残りの行を送る(close-log の説明)。
  (RecordingInstaller log))


;; --- 境目の記録係(ADR-DOE-CLUSTER-001 R5・R5b)----------------------------------------------------------
;;
;; 記録と再生は job の Program の中の with-handlers に置く(runner は記録係を差し込まない)。置き場は翻訳の handler と土台の handler の
;; 間(外の世界との境目 — 汎用の effect だけを記録する)。記録係より内側の handler は決定的でなければならない(R5b)。
;; with-handlers の list は先が外側なので、記録係を翻訳の handler より先に書く(後に書くと記録係が翻訳より内側に入り、業務の effect を
;; 受けてしまう)。組(boundary-recorder)と記録の頭書き(recording-header)は宿の契約の run-context を読むので入口の側
;; (doeff_cluster.shared.entry.boundary_recorder — #2981 でここから移した)。ここに残るのは記録係・再生係と、下の選びの鍵。
;;
;;   (defk job-program [foundation]                  ; 土台 = 本体を受けて自分の handler の下で走らせる関数(計画 10.1)
;;     (<- answer (foundation (do! (<- recorder list (boundary-recorder HOST-CONTRACT))  ; off / record / replay を Ask で選ぶ
;;                                 (<- translation list (translation-handlers))
;;                                 (<- r (with-handlers [#* recorder #* translation] (loop)))
;;                                 r)))
;;     answer)
;;
;; 記録か再生かは Ask RECORD-MODE-KEY の答え(本番は宣言の :environ を土台の host_contract.environ-reader — 子の os.environ を字面どおり読む — が答える)。
;;   off    = 記録係を置かない(空の組)
;;   record = effect-recorder。置き場は Ask RECORD-OTLP-KEY(OpenTelemetry の collector の URL)。header は宿の契約(HOST-CONTRACT)の
;;            run-context と Program の path(置き場のキー = file の名)と版。
;;   replay = effect-replayer。状態(ReplayState)は Ask REPLAY-STATE-KEY の答え — 再生の道具(replay_main)が Program の外から答え、
;;            終わった後に同じ状態から再生の報告を作る。本番の宿はこの鍵に答えないので、本番で replay を選ぶと答えの無い effect で落ちる。

(val RECORD-MODE-KEY "EFFECT_RECORD_MODE")
(val RECORD-OTLP-KEY "EFFECT_RECORD_OTLP")
(val REPLAY-STATE-KEY "doeff.record.replay-state")
(val RECORD-MODES #("off" "record" "replay"))
