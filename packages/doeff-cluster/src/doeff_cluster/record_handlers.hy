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
(require doeff-hy.macros [defhandler defk <-])
(import json)
(import os)
(import sys)
(import time)
(import urllib.request [Request urlopen])
(import doeff [EffectBase Pass])
(import doeff.do [do])
(import doeff.program [handler :as program-handler])
(import doeff_vm [GetBoundaries WithObserve Callable :as VmCallable])
(import doeff_core_effects.scheduler [Spawn Wait CreatePromise CompletePromise PRIORITY-IDLE])
(import doeff_cluster.effect_codec [READ LIVE DECISION OUTPUT LOOSE DIVERGE INTERN-MIN-CHARS BLOB-MEMORY-MAX FORMAT-VERSION BlobMemory
                                         HandleTable UnencodableValue UnrecordableEffect
                                         encode-value encode-error decode-value decode-error canonical intern-json
                                         codec-of mode-of args-of subject-of])
(import doeff_cluster.record_model [ROOT ReplayFinished ReplayDiverged match-step diff-row summarize])


;; --- task の名(記録と再生で共通) ---------------------------------------------------------------

(defclass TaskTap []
  "task の名の記憶。marked = 最後に印を通った effect・marked-label = その task の名。spawns = 親の名 → Spawn した数。"
  (defn __init__ [self]
    (setv self.marked None self.marked-label ROOT self.spawns {}))

  (defn #^ str current-label [self effect]
    (if (is self.marked effect) self.marked-label ROOT))

  (defn #^ str child-label [self #^ str parent]
    (setv n (.get self.spawns parent 0))
    (setv (get self.spawns parent) (+ n 1))
    (.format "{}.{}" parent n))

  (defn task-ended [self #^ str label #^ bool ok error] None))


(defn task-marker [#^ TaskTap tap #^ str label]
  "子の Program の一番内側に置く印。自分より内側の印(孫の task)が先に書いた effect は書き換えない。"
  (defn effect-task-marker [effect k]
    (when (is-not tap.marked effect)
      (setv tap.marked effect tap.marked-label label))
    (Pass effect k))
  effect-task-marker)


(defn [do] task-body [#^ TaskTap tap #^ str label program]
  "子の Program を走らせ、終わった(成功・失敗)ことを記憶に知らせる。"
  (try
    (setv result (yield program))
    (except [e Exception]
      (.task-ended tap label False e)
      (raise)))
  (.task-ended tap label True None)
  result)


(defn rewrap [program #^ list chain]
  "GetBoundaries の並び(内側が先・最後は受けた handler 自身)で program を包み直す(scheduler の Spawn と同じ張り直し)。"
  (setv prog program)
  (for [#(kind cb) chain]
    (setv prog (if (= kind "handler") ((program-handler cb) prog) (WithObserve (VmCallable cb) prog))))
  prog)


(defn spawn-program [#^ TaskTap tap #^ str child program #^ list chain]
  "Spawn し直す子の Program = 印 → 終わりの知らせ → 受けた handler までの並び。"
  (rewrap (task-body tap child ((program-handler (task-marker tap child)) program)) chain))


;; --- 記録の置き場 ------------------------------------------------------------------------------

(defclass MemorySink []
  "テストの置き場。lines = 書いた行(dict)。"
  (defn __init__ [self] (setv self.lines [] self.lost 0))
  (defn write [self #^ int chunk #^ dict line]
    ;; JSON を通して持つ(本物の置き場と同じく、JSON にできない物が紛れたらここで落ちる)。chunk は行に添えて残す。
    (.append self.lines (| (json.loads (json.dumps line :ensure-ascii False)) {"_chunk" chunk})))
  (defn flush [self] None))


(defclass BufferedSink []
  "行を貯めて flush-seconds か max-lines ごとに送る置き場の口の共通部分。届かない間は貯め続け(retry-seconds ごとに試す)、
   max-buffer 行を超えたら捨てて lost を数える(記録は途切れる — 本番を止めない)。送り方(post)は子 class が決める。
   buffer の 1 項 = #(区切り 行の dict 行の JSON の文字列)。"
  (defn #^ None __init__ [self #^ float [flush-seconds 2.0] #^ int [max-lines 500] #^ int [max-buffer 200000] #^ float [timeout 10.0]
                          #^ float [retry-seconds 10.0] #^ int [max-post-bytes 4000000]]
    (setv self.flush-seconds flush-seconds self.max-post-bytes max-post-bytes
          self.max-lines max-lines self.max-buffer max-buffer self.timeout timeout self.retry-seconds retry-seconds
          self.buffer [] self.last-flush (time.monotonic) self.retry-at 0.0 self.lost 0 self.sent 0 self.failures 0))

  (defn #^ None write [self #^ int chunk #^ dict line]
    (.append self.buffer #(chunk line (json.dumps line :ensure-ascii False :separators #("," ":"))))
    (setv now (time.monotonic))
    (when (and (>= now self.retry-at)
               (or (>= (len self.buffer) self.max-lines) (>= (- now self.last-flush) self.flush-seconds)))
      (.flush self))
    (when (> (len self.buffer) self.max-buffer)
      (+= self.lost (len self.buffer))
      (setv self.buffer []))
    None)

  (defn #^ int batch-size [self]
    "次の 1 回の送りに載せる行の数(先頭から max-post-bytes まで・最低 1 行)。"
    (setv n 0 size 0)
    (for [#(_c _l text) self.buffer]
      (when (and (> n 0) (> (+ size (len text)) self.max-post-bytes)) (break))
      (+= n 1)
      (+= size (len text)))
    n)

  (defn #^ None flush [self]
    (setv self.last-flush (time.monotonic))
    (when (not self.buffer) (return None))
    (try
      ;; 送れた分だけ buffer から外す(途中で失敗したら残りだけを次に送る)。
      (while self.buffer
        (setv n (.post self (cut self.buffer 0 (.batch-size self))))
        (+= self.sent n)
        (setv self.buffer (cut self.buffer n None)))
      (except [e Exception]
        ;; 送れなかった: 貯めたまま次を待つ(同じ行を 2 度送りうる — 読む側は行の e と内容の hash で重なりを捨てる)。
        (+= self.failures 1)
        (setv self.retry-at (+ (time.monotonic) self.retry-seconds))
        (print (.format "recorder: 記録の置き場に送れない({} 行を貯めている): {}: {}" (len self.buffer) (. (type e) __name__) e)
               :file sys.stderr :flush True)))
    None))


(defclass HttpSink [BufferedSink]
  "記録の置き場 effect-records(record_store.hy)の POST /append。1 回の送りは同じ区切りの行だけ。
   ⚠ 2026-09-25 から OtlpSink(OpenTelemetry → ClickHouse)へ移す途中。新しい経路が本番で動いたのを確かめたら退役する。"
  (defn #^ None __init__ [self #^ str url #^ str service #^ str run #^ float [flush-seconds 2.0] #^ int [max-buffer 200000]]
    (.__init__ (super) :flush-seconds flush-seconds :max-buffer max-buffer)
    (setv self.url (.rstrip url "/") self.service service self.run run))

  (defn #^ int post [self #^ list items]
    (setv chunk (get (get items 0) 0) texts [])
    (for [#(c _l text) items]
      (when (!= c chunk) (break))
      (.append texts text))
    (setv body (.encode (json.dumps {"service" self.service "run" self.run "chunk" chunk "lines" texts}) "utf-8"))
    (setv request (Request (+ self.url "/append") :data body :method "POST"
                           :headers {"Content-Type" "application/json" "X-Actor" (+ "recorder:" self.service)}))
    (with [response (urlopen request :timeout self.timeout)]
      (.read response))
    (len texts)))


(defn #^ dict otlp-log-record [#^ str run #^ int chunk #^ dict line #^ str text #^ int now-ms]
  "記録の 1 行 → OTLP の log record 1 件。body = 行の JSON・時刻 = 行の at(無ければ now)・属性 = 読む側が絞る鍵
   (run・区切り・行の種類・出来事の番号・effect の型・内容の hash)。ClickHouse の表はこの属性を列に持つ(deploy/effect-telemetry)。"
  (setv at (or (.get line "at") (.get line "startedMs") now-ms))
  (setv attrs [["run" run] ["chunk" (str chunk)] ["k" (str (.get line "k"))]])
  (when (in "e" line) (.append attrs ["e" (str (get line "e"))]))
  (when (in "ty" line) (.append attrs ["ty" (get line "ty")]))
  (when (in "h" line) (.append attrs ["h" (get line "h")]))
  {"timeUnixNano" (str (* (int at) 1000000))
   "observedTimeUnixNano" (str (* now-ms 1000000))
   "body" {"stringValue" text}
   "attributes" (lfor #(k v) attrs {"key" k "value" {"stringValue" v}})})


(defclass OtlpSink [BufferedSink]
  "OpenTelemetry の collector の OTLP/HTTP(JSON)の口 POST <url>/v1/logs。行 1 つ = log record 1 件・resource の service.name = service。
   collector が ClickHouse(hot / warm / cold の 3 層・期限で移して最後に消す)へ入れる(deploy/effect-telemetry.yaml)。"
  (defn #^ None __init__ [self #^ str url #^ str service #^ str run #^ float [flush-seconds 2.0] #^ int [max-buffer 200000]]
    (.__init__ (super) :flush-seconds flush-seconds :max-buffer max-buffer)
    (setv self.url (.rstrip url "/") self.service service self.run run))

  (defn #^ int post [self #^ list items]
    (setv now-ms (int (* 1000 (time.time))))
    (setv body {"resourceLogs"
                [{"resource" {"attributes" [{"key" "service.name" "value" {"stringValue" self.service}}]}
                  "scopeLogs" [{"scope" {"name" "doeff.effect-record" "version" (str FORMAT-VERSION)}
                                "logRecords" (lfor #(c line text) items (otlp-log-record self.run c line text now-ms))}]}]})
    (setv request (Request (+ self.url "/v1/logs") :data (.encode (json.dumps body :ensure-ascii False) "utf-8") :method "POST"
                           :headers {"Content-Type" "application/json"}))
    (with [response (urlopen request :timeout self.timeout)]
      (.read response))
    (len items)))


;; --- 記録 -------------------------------------------------------------------------------------

(defclass EffectLog [TaskTap]
  "1 つの process の記録の係。header = run の行の欄(service・run・版・設定 …)。wall-ms = 壁時計(ms)を返す関数。
   形の版 2: 大きな値は内容参照(effect_codec.intern-json)にし、中身は run の中で初めて出た時に blob の行で書く。問いの直後に
   (他の出来事を挟まずに)答えが返ったら、問いと答えを 1 行(call)にまとめる — 問いは答えが返るか他の出来事が来るまで手元に持つ。"
  (defn #^ None __init__ [self #^ object sink #^ dict header #^ bool [strict False] #^ float [chunk-seconds 3600.0]
                          #^ object [wall-ms None] #^ int [intern-min INTERN-MIN-CHARS] #^ int [blob-memory BLOB-MEMORY-MAX]]
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

  (defn request [self #^ str label effect #^ dict args #^ str mode subject]
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

  (defn answer [self #^ int s effect value #^ dict args child]
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

  (defn failed [self #^ int s #^ BaseException error]
    (.settle self s {"ok" False "err" (encode-error error self.handles)})
    (.check-watched self))

  (defn refresh-watched [self]
    "問いの直前: 直前までに走ったのは業務コード(この問いを出した task か、その前に動いた task)。業務コード自身の書き換えは
     再生でも業務コードがもう一度するので記録しない — 断面だけ取り直す。"
    (for [row self.watched]
      (setv (get row 2) (encode-value (get row 1) self.handles))))

  (defn check-watched [self]
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

  (defn task-ended [self #^ str label #^ bool ok error]
    (when (is self.broken None)
      (.emit self {"k" "end" "e" (.next-e self) "t" label "at" (self.wall-ms) "ok" ok}))))


(defhandler effect-recorder [#^ EffectLog log]
  (EffectBase []
    (if (is-not log.broken None)
        (reperform effect)
        (do
          (setv label (.current-label log effect) prepared None)
          ;; 問いの形を作る(登録の無い型・記録の形にできない値 → strict なら業務へ投げる・そうでなければ記録を止めて素通し)
          (try
            (setv codec (codec-of effect) mode (mode-of effect log.handles))
            (setv child (if (isinstance effect Spawn) (.child-label log label) None))
            (setv args (if (is child None) (args-of effect log.handles) (| (args-of effect log.handles) {"child" child})))
            (setv prepared #(codec mode args (subject-of effect args) child))
            (except [e [UnrecordableEffect UnencodableValue]]
              (if log.strict (setv prepared e) (.break log (.format "{}: {}" (. (type e) __name__) e)))))
          (cond
            (isinstance prepared BaseException) (raise prepared)
            (is prepared None) (reperform effect)
            True
              (do
                (setv #(codec mode args subject child) prepared)
                (setv s (.request log label effect args mode subject))
                (setv answer None error None)
                (if (is child None)
                    (try (<- answer effect) (except [e Exception] (setv error e)))
                    (do (<- chain (GetBoundaries k))
                        (try (<- answer (Spawn (spawn-program log child effect.program chain)
                                               :priority effect.priority :daemon effect.daemon))
                             (except [e Exception] (setv error e)))))
                (try
                  (if (is error None) (.answer log s effect answer args child) (.failed log s error))
                  (except [e [UnrecordableEffect UnencodableValue]]
                    (if log.strict (setv error e) (.break log (.format "答え: {}: {}" (. (type e) __name__) e)))))
                (if (is error None) (resume answer) (raise error))))))))


;; --- 再生 -------------------------------------------------------------------------------------

(defclass ReplayState [TaskTap]
  "再生の記憶。rec = 読んだ記録。cursor = 次に来るべき出来事(rec.events の位置)。skip = 番号より先に済ませた出来事。
   waiting = 出来事の番号 → その番号を待っている task の promise。"
  (defn __init__ [self rec [from-ms None] [to-ms None] [ordered True]]
    (.__init__ (super))
    ;; ordered = 偽なら番号の順を待たない(テストの対照: 順を揃えない再生が違う結果になることを示すためだけに使う)。
    (setv self.rec rec self.from-ms from-ms self.to-ms to-ms self.ordered ordered
          self.heads {} self.cursor 0 self.skip (set) self.waiting {} self.handles (HandleTable) self.watched {}
          self.counts {READ 0 LIVE 0 DECISION 0 OUTPUT 0} self.decisions [] self.outputs [] self.divergence None
          self.finished False self.driver-running False self.ended {})
    (.settle self))

  (defn current [self]
    (if (< self.cursor (len self.rec.events)) (get (get self.rec.events self.cursor) 0) None))

  (defn settle [self]
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

  (defn apply-mut [self #^ dict line]
    "handler が共有の箱に加えた変化(鍵ごとの差分)を、再生の箱に同じ位置で当てる。業務コード自身の書き換えは残る。"
    (setv obj (.get self.watched (get line "ref")) patch (get line "patch"))
    (when (is obj None) (return None))
    (if (in "all" patch)
        (do (setv value (decode-value (get patch "all")))
            (if (isinstance obj dict)
                (do (.clear obj) (.update obj value))
                (setv (cut obj None None) value)))
        (do (for [#(k v) (.items (get patch "set"))]
              (setv (get obj k) (decode-value v)))
            (for [k (get patch "del")]
              (.pop obj k None)))))

  (defn #^ list consume [self e]
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

  (defn diverge [self #^ dict info]
    (when (is self.divergence None)
      (setv self.divergence info)))

  (defn stall [self]
    "他の task が全部止まったのに番号が進まない: 記録の出来事を誰も出さない = 分岐。"
    (setv e (.current self))
    (when (or (is e None) self.finished (is-not self.divergence None)) (return None))
    (setv #(_ kind owner extra) (get self.rec.events self.cursor))
    (setv entry (.get self.rec.entries (if (= kind "req") e extra)))
    (.diverge self {"reason" (if (in owner self.ended)
                                 "記録ではこの後も問いを出す task が、再生では先に終わった"
                                 "記録の出来事を再生の業務の Program が出さないまま止まった")
                    "event" e "kind" kind "task" owner
                    "expected" (if (is entry None) None {"type" entry.type "args" entry.args})
                    "at" (if (is entry None) None entry.at)}))

  (defn task-ended [self #^ str label #^ bool ok error]
    (setv (get self.ended label) (if ok True (repr error)))))


(defk park-until [state e]
  {:pre [(: state ReplayState) (: e (| int None))] :post [(: % (type None))]}
  ;; 出来事 e の番が来るまで待つ(e = None なら記録の終わりか分岐まで)。
  (when (and state.ordered (or (is e None) (!= (.current state) e)) (not state.finished) (is state.divergence None))
    (<- promise (CreatePromise))
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


(defk replay-driver [state]
  {:pre [(: state ReplayState)] :post [(: % (type None))]}
  ;; 他の task が全部止まった時だけ動く(PRIORITY_IDLE の daemon)。それでも待っている task が居れば、番号の出来事を誰も出さない。
  (try
    (when state.waiting
      (.stall state)
      (<- (wake (.wakeable state))))
    (finally
      (setv state.driver-running False)))
  None)


(defk consume-at-turn [state e]
  {:pre [(: state ReplayState) (: e int)] :post [(: % (type None))]}
  (<- (park-until state e))
  (<- (wake (.consume state e)))
  None)


(defn deliver-recorded [#^ ReplayState state entry codec]
  "記録の答えを業務へ返す値に戻す(handle の札・共有の箱)。例外なら例外の object。"
  (when (not entry.ok)
    (return #(False (decode-error entry.error))))
  (setv v entry.value)
  (when (and (isinstance v dict) (in "$w" v))
    (return #(True (get state.watched (get v "$w")))))
  (setv value (decode-value v))
  (when (and codec.watch (isinstance value #(dict list)))
    (setv (get state.watched entry.e) value))
  #(True value))


(defhandler effect-replayer [#^ ReplayState state]
  (EffectBase []
    (setv label (.current-label state effect) rec state.rec)
    (when (is-not state.divergence None)
      (raise (ReplayDiverged (.get state.divergence "reason"))))
    (setv codec None)
    (try (setv codec (codec-of effect)) (except [UnrecordableEffect] None))
    (when (is codec None)
      (.diverge state {"reason" "記録の登録に無い effect の型" "task" label "actual" {"type" (str (type effect))}})
      (<- (wake (.wakeable state)))
      (raise (ReplayDiverged "記録の登録に無い effect の型")))
    (setv mode (mode-of effect state.handles))
    (setv child (if (isinstance effect Spawn) (.child-label state label) None))
    (setv args (if (is child None) (args-of effect state.handles) (| (args-of effect state.handles) {"child" child})))
    (setv subject (subject-of effect args) queue (.get rec.queues label []) head (.get state.heads label 0))
    (setv #(verdict pos skipped) (match-step rec queue head codec.name args mode subject (in label rec.ended)))
    ;; 記録に在って再生が出さなかった decision / output(missing)は、その出来事を済ませたことにして報告する。
    (for [e skipped]
      (setv missed (get rec.entries e))
      (.append (if (= missed.mode DECISION) state.decisions state.outputs)
               (diff-row "missing" missed missed.type missed.subject label None))
      (<- (wake (.consume state missed.e)))
      (<- (wake (.consume state missed.ans-e))))
    (setv (get state.heads label) pos)
    (cond
      (= verdict "diverge")
        (do (setv entry (if (< pos (len queue)) (get rec.entries (get queue pos)) None))
            (.diverge state {"reason" (if (is entry None) "記録ではもう問いを出さない task が問いを出した" "問いが記録と食い違った")
                             "task" label "event" (if entry entry.e None) "at" (if entry entry.at None)
                             "expected" (if entry {"type" entry.type "args" entry.args} None)
                             "actual" {"type" codec.name "args" args}})
            (<- (wake (.wakeable state)))
            (raise (ReplayDiverged (get state.divergence "reason"))))
      (or (= verdict "finish") (and (= verdict "extra") state.finished))
        ;; 記録を読み切った後に業務の Program が出した書き・報告は、比べる相手(記録)が無い — 違いに数えずに終わる
        ;; (動いている run の記録は周期の途中で切れるので、再生は切れ目の先の報告まで進むことがある。2026-09-25 実測)。
        (do (<- (park-until state None))
            (raise (ReplayFinished "記録の終わり")))
      (= verdict "extra")
        (do (setv current-entry (.current state))
            (.append (if (= mode DECISION) state.decisions state.outputs)
                     (diff-row "extra" None codec.name subject label args :at (if (is current-entry None) None (. (get rec.entries current-entry) at))))
            (when (is codec.unexecuted DIVERGE)
              (.diverge state {"reason" "記録に対の無い書き込み(実行していない時の答えが決まっていない型)" "task" label
                               "actual" {"type" codec.name "args" args}})
              (<- (wake (.wakeable state)))
              (raise (ReplayDiverged (get state.divergence "reason"))))
            (resume codec.unexecuted))
      True
        (do
          (setv entry (get rec.entries (get queue pos)))
          (setv (get state.heads label) (+ pos 1))
          (<- (consume-at-turn state entry.e))
          (+= (get state.counts mode) 1)
          (setv answer None error None)
          (when (= mode LIVE)
            (if (is child None)
                (try (<- answer effect) (except [e Exception] (setv error e)))
                (do (<- chain (GetBoundaries k))
                    (try (<- answer (Spawn (spawn-program state child effect.program chain)
                                           :priority effect.priority :daemon effect.daemon))
                         (except [e Exception] (setv error e)))))
            (when (and (is error None) (is-not codec.binds None))
              (.bind state.handles answer codec.binds (if (= codec.binds "task") child (.format "{}" entry.e)))))
          (when (is entry.ans-e None)
            ;; 記録が終わった時、この問いの答えはまだ返っていなかった。
            (<- (park-until state None))
            (raise (ReplayFinished "記録の終わり")))
          (<- (consume-at-turn state entry.ans-e))
          ;; 引数だけが違う書き(changed): 違いを報告し、記録の答え(同じ前提への engine の答え)を返す。
          (when (= verdict "changed")
            (.append (if (= mode DECISION) state.decisions state.outputs)
                     (diff-row "changed" entry codec.name subject label args)))
          (cond
            (= mode LIVE) (if (is error None) (resume answer) (raise error))
            True
              (do (setv #(ok value) (deliver-recorded state entry codec))
                  (if ok (resume value) (raise value))))))))


(defn #^ dict replay-report [#^ ReplayState state #^ str end]
  (summarize state.rec state.counts state.decisions state.outputs state.divergence end state.cursor
             :from-ms state.from-ms :to-ms state.to-ms))


;; --- 組み立て(worker の子 process の composition root が使う) ------------------------------------

(defn #^ str run-name [#^ int started-ms #^ str worker #^ str instance]
  "記録の run の名 = 始まりの時刻(UTC)・worker・process の世代(置き場の dir の名。並べると時刻の順)。"
  (.format "{}-{}-{}" (time.strftime "%Y%m%dT%H%M%SZ" (time.gmtime (/ started-ms 1000))) (or worker "local")
           (or instance (str (os.getpid)))))

(defn recording-handler [#^ dict record #^ str service #^ dict header]
  "service の設定の record 欄 → env の一番内側に足す記録係。record = {\"otlp\": collector の URL(か \"store\": 旧い置き場の URL)・
   \"chunkSeconds\"・\"flushSeconds\"}。
   header = run の行に載せる欄(版・設定・process の世代 …)。置き場に届かなくても業務は止めない(HttpSink の説明)。"
  (setv started (int (* 1000 (time.time))))
  (setv run (run-name started (.get header "worker" "") (.get header "instance" "")))
  (setv options {"flush_seconds" (float (.get record "flushSeconds" 2.0)) "max_buffer" (int (.get record "maxBufferLines" 200000))})
  ;; 置き場の口: otlp = OpenTelemetry の collector(2026-09-25 から)・store = 旧い置き場 effect-records(退役まで)。
  (setv sink (if (in "otlp" record)
                 (OtlpSink (get record "otlp") service run #** options)
                 (HttpSink (get record "store") service run #** options)))
  (setv log (EffectLog sink (| header {"service" service "run" run})
                       :chunk-seconds (float (.get record "chunkSeconds" 3600.0))
                       :wall-ms (fn [] (int (* 1000 (time.time))))))
  (print (.format "recorder: {} の effect を記録します(run {}・置き場 {})" service run (or (.get record "otlp") (.get record "store"))) :file sys.stderr :flush True)
  (effect-recorder log))
