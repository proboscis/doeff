;;; 記録の service の要求ごとの計時(純粋 — #3688)。要求 1 つに打った刻(単調時計の秒)を区間の秒に割り、計器の秒の観測
;;; (doeff の ObserveSeconds)の名を綴る。刻を打つのは入口(http_server.hy の要求の task と、記録の handler の包み)で、ここは計算と綴りだけ。
;;;
;;; 何のためか: 書き 1 回が本番で 0.05〜0.11 秒かかるのに、service の側に要求ごとの秒が無く、どの区間が遅いかを読めなかった。区間ごとの
;;; 秒の和と数を GET /metrics で読み、2 回読んだ差を数の差で割れば、その間の区間ごとの平均の秒になる。
;;;
;;; 刻(RequestMark):
;;;   started      要求の task が始まった
;;;   read         本文を読み終えた(本文を持たない method も同じ所で打つ)
;;;   handler-in   記録の handler に公開 effect を渡す直前
;;;   handler-out  記録の handler が答えた直後
;;;   wait-in      handler の中の待ち(doeff-time の WaitWithin — 変化の待ちの long-poll の置き場の待ち)に入った
;;;   woke         その待ちから起きた
;;;   decided      答えが決まった
;;;   sent         答えを待ち受けへ渡し終えた(HttpRespond が戻った — 本物の待ち受けは答えを待ち受けの loop へ積むだけで戻り、待ち受けの
;;;                thread が書くので、相手が答えを受けるのはこれより前のことがある・#3688 の子 (3) の 3b)
;;; 受けた刻は待ち受けの答え手が出来事に載せる値(HttpRequestArrived の received-at — time.monotonic と同じ物差し)で、刻ではなく引数で
;;; 受ける(台本の待ち受けは載せないことがある — None)。
;;;
;;; 区間(RequestStage — queue・body・decode・handler・wait・encode・send の 7 つは重ならず、和が total に等しい):
;;;   queue    受けた → task が始まった(待ち受けの列と scheduler の待ち)
;;;   body     task が始まった → 本文を読み終えた
;;;   decode   本文を読み終えた → handler に入る(JSON の読み・要求の読み・書き手の名・宣言の照らし)
;;;   handler  handler に入ってから出るまでのうち、待ちを除いた秒(起きている間 — 置き場の錠・SQL の往復・COMMIT・合図を含む)
;;;   wait     handler の中で待った秒の和(変化の待ちの要求だけ — 待たなかった要求は観測しない)
;;;   encode   handler から出た → 答えが決まった(答えの綴り)
;;;   send     答えが決まった → 送った(答えを byte にして待ち受けの loop へ積む — 本物の待ち受けは書き終わりを待たない)
;;;   total    受けた → 送った(受けた刻が無ければ task が始まった → 送った)
;;;   woke     最後に起きた → 送った(待った要求だけ。起きてから相手へ答えを渡すまで — total の内訳ではなく handler・encode・send に重なる)
;;; 両端の刻のどちらかが欠けた区間は観測しない(本文を断った 400 と、handler に届かない 404 / 400 は decode・handler・encode が無い —
;;; 本文を読み終えてから答えが決まるまでを内訳に割れない。落ちた 500 は欠けた刻に触る区間が無い)。total はどの答えにも在る。
;;;
;;; 計器の名: records_stage_<操作>_<区間>(操作の名の - は _ — Prometheus の名に - を使えない)。描く名は doeff の render-prometheus が
;;; 末尾に _seconds_sum と _seconds_count を付ける。系列は操作 9 × 区間 9 で閉じている(STAGE-METRIC-HELPS の鍵)。秒の観測は 0 で置くと数が 1 増える
;;; ので、起動の時には置かない(初めて観測した時に現れる)。
(require doeff-hy.macros [defk val])
(require doeff-hy.record [defenum defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import enum [StrEnum])  ; defenum の展開が名指す
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.wire [OPERATIONS])

(val MODULE-TAGS {:context "records" :role "judgment"})

;; 刻の名(頭の註)。
(defenum RequestMark STARTED READ HANDLER-IN HANDLER-OUT WAIT-IN WOKE DECIDED SENT)

;; 区間の名(頭の註)。
(defenum RequestStage QUEUE BODY DECODE HANDLER WAIT ENCODE SEND TOTAL WOKE)

;; 計器の秒の観測の名の綴り(頭の註): records_stage_<操作>_<区間>。
(val STAGE-METRIC "records_stage_{}_{}")


(defrecord MarkAt
  "刻 1 つ: mark = 刻の名・at = 打った時の単調時計の秒。"
  {:tags {:context "records" :role "type"}}
  (#^ RequestMark mark)
  (#^ float at))


(defrecord StageSeconds
  "区間 1 つの秒: stage = 区間の名・seconds = 秒。"
  {:tags {:context "records" :role "type"}}
  (#^ RequestStage stage)
  (#^ float seconds))


(defrecord WaitSum
  "handler の中の待ちの読み: seconds = 待った秒の和・last-woke = 最後に起きた刻(待たなかった要求は None)。"
  {:tags {:context "records" :role "type"}}
  (#^ float seconds)
  (#^ (| float None) last-woke))


(defk stage-metric [operation stage]
  {:pre [(: operation str) (: stage RequestStage)] :post [(: % str)] :tags {:context "records" :role "judgment"}}
  "操作 operation の区間 stage の秒を積む計器の名を綴るため(STAGE-METRIC — 操作の名の - は _ にする)。"
  (when (not-in operation OPERATIONS)
    (raise (ValueError (.format "計時する操作は {} のどれか: {!r}" OPERATIONS operation))))
  (.format STAGE-METRIC (.replace operation "-" "_") stage))


(defrecord StageMeaning
  "区間 1 つの説明(計器の # HELP に載せる): stage = 区間の名・meaning = 何から何までの秒か。"
  {:tags {:context "records" :role "type"}}
  (#^ RequestStage stage)
  (#^ str meaning))


;; 区間ごとの説明(頭の註の区間と同じ並び)。
(val STAGE-MEANINGS
  #((StageMeaning :stage RequestStage.QUEUE :meaning "受けた → 要求の task が始まった")
    (StageMeaning :stage RequestStage.BODY :meaning "要求の task が始まった → 本文を読み終えた")
    (StageMeaning :stage RequestStage.DECODE :meaning "本文を読み終えた → 記録の handler に入る")
    (StageMeaning :stage RequestStage.HANDLER :meaning "記録の handler に入ってから出るまでのうち、待ちを除いた間")
    (StageMeaning :stage RequestStage.WAIT :meaning "記録の handler の中で待った間の和 — 待った要求だけ")
    (StageMeaning :stage RequestStage.ENCODE :meaning "記録の handler から出た → 答えが決まった")
    (StageMeaning :stage RequestStage.SEND :meaning "答えが決まった → 送った")
    (StageMeaning :stage RequestStage.TOTAL :meaning "受けた → 送った")
    (StageMeaning :stage RequestStage.WOKE :meaning "最後に起きた → 送った — 待った要求だけ")))

;; 閉じた系列の全部(操作 × 区間)→ 各系列の # HELP の説明。
(val STAGE-METRIC-HELPS
  (FrozenMap (gfor operation OPERATIONS described STAGE-MEANINGS
                   #((.format STAGE-METRIC (.replace operation "-" "_") described.stage)
                     (.format "記録の service が操作 {} の要求に答えた区間 {}({})の秒(起動からの和と数)"
                              operation described.stage described.meaning)))))


(defk first-at [marks mark]
  {:pre [(: marks (get tuple #(MarkAt ...))) (: mark RequestMark)] :post [(: % (| float None))] :tags {:context "records" :role "judgment"}}
  "刻の列から名 mark の最初の刻を読むため(打っていなければ None)。"
  (next (gfor stamped marks :if (= stamped.mark mark) stamped.at) None))


(defk waited [marks]
  {:pre [(: marks (get tuple #(MarkAt ...)))] :post [(: % WaitSum)] :tags {:context "records" :role "judgment"}}
  "刻の列から、handler の中の待ち(wait-in と直後の woke の組)の秒の和と最後に起きた刻を読むため(起きる前に終わった待ちは数えない)。
   待っている間はその要求の task が止まっているので、1 つの札の刻の列では wait-in の次の刻は必ずその待ちの woke になる。"
  (val naps (tuple (gfor #(went came) (zip marks (cut marks 1 None))
                         :if (and (= went.mark RequestMark.WAIT-IN) (= came.mark RequestMark.WOKE))
                         (- came.at went.at))))
  (val woken (tuple (gfor stamped marks :if (= stamped.mark RequestMark.WOKE) stamped.at)))
  (WaitSum :seconds (float (sum naps)) :last-woke (if woken (get woken -1) None)))


(defk between [since until]
  {:pre [(: since (| float None)) (: until (| float None))] :post [(: % (| float None))] :tags {:context "records" :role "judgment"}}
  "2 つの刻の差の秒を読むため(どちらかが欠ければ None — その区間は観測しない)。"
  (if (or (is since None) (is until None)) None (- until since)))


(defk request-stages [received-at marks]
  {:pre [(: received-at (| float None)) (: marks (get tuple #(MarkAt ...)))] :post [(: % (get tuple #(StageSeconds ...)))]
   :tags {:context "records" :role "judgment"}}
  "要求 1 つの受けた刻と刻の列を、観測する区間の秒の列にするため(頭の註の区間 — 両端の刻が欠けた区間は入れない)。"
  (val started (! (first-at marks RequestMark.STARTED)))
  (val read (! (first-at marks RequestMark.READ)))
  (val handler-in (! (first-at marks RequestMark.HANDLER-IN)))
  (val handler-out (! (first-at marks RequestMark.HANDLER-OUT)))
  (val decided (! (first-at marks RequestMark.DECIDED)))
  (val sent (! (first-at marks RequestMark.SENT)))
  (val wait (! (waited marks)))
  (val handled (! (between handler-in handler-out)))
  (val slept (is-not wait.last-woke None))
  (val spans #(#(RequestStage.QUEUE (! (between received-at started)))
               #(RequestStage.BODY (! (between started read)))
               #(RequestStage.DECODE (! (between read handler-in)))
               #(RequestStage.HANDLER (if (is handled None) None (- handled wait.seconds)))
               #(RequestStage.WAIT (if slept wait.seconds None))
               #(RequestStage.ENCODE (! (between handler-out decided)))
               #(RequestStage.SEND (! (between decided sent)))
               #(RequestStage.TOTAL (! (between (if (is received-at None) started received-at) sent)))
               #(RequestStage.WOKE (if slept (! (between wait.last-woke sent)) None))))
  (tuple (gfor #(stage seconds) spans :if (is-not seconds None) (StageSeconds :stage stage :seconds seconds))))
