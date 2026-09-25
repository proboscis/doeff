;;; effect の記録と再生(backtest)の符号化 — 型ごとの符号化と復号の定義点はこの file の 1 か所だけ。
;;;
;;; 記録は何日も残る長期保存なので cloudpickle ではなく JSON にする。値の符号化(encode-value / decode-value)は
;;; JSON の値・tuple・bytes・時刻(timezone つきの datetime)・dataclass・例外・handle(Task / Semaphore / Promise)を型を落とさずに往復させ、知らない物は
;;; 黙って落とさず UnencodableValue を投げる。effect の型は EFFECT-CODECS に 1 行ずつ登録し、登録の無い型は
;;; UnrecordableEffect(記録の側はそこから先を記録できないと印を置く — record_handlers.hy)。
;;;
;;; 登録の 1 行が決めること:
;;;   mode       再生での扱い。read = 記録の答えを返す(問いは型・引数・順番まで記録と同じでなければ「分岐」で止める)・
;;;              live = 再生でも本物の scheduler が解く(Spawn・Wait 等。順番だけ突き合わせる)・
;;;              decision = 判断(書き込み)。実行せず記録と突き合わせ、違いを 1 件ずつ出して続ける・
;;;              output = 報告(readiness・計器・盤の要約)。decision と同じく突き合わせて続けるが、判断の違いとは分けて数える。
;;;   args       effect → 引数の JSON(比べる形)。既定は dataclass の欄を encode-value で。
;;;   subject    decision / output の対を取る鍵(同じ型の中で「どの行への書きか」)。
;;;   unexecuted 記録に対の無い decision / output(extra)に返す答え。書きの型は「着地した」(True)と宣言する — 業務の Program を
;;;              本番が通らなかった競合・backoff の道へ逸らさず、読みを記録に揃えたまま次の判断の違いまで進めるため。
;;;              同じ位置・同じ対の鍵で引数だけが違う書き(changed)には、記録の答え(本番の書きへの engine の答え = 同じ前提の世代の
;;;              compare-and-set の結果)を返す。DIVERGE なら返さずに分岐として止める(答えの形が決まっていない型)。
;;;   binds      答えを handle として名付ける種類(named-sem / sem / task / promise)。
;;;   watch      答えが業務コードと handler が共有する可変の dict(Ask の計器の箱)なら真 — 記録は中身の変化を別の出来事で残す。
;;;
;;; 大きな値の内容参照(intern-json / resolve-refs・形の版 2): 値を下から畳み、canonical JSON が INTERN-MIN-CHARS 以上の節
;;; (dict・list)を内容の hash の参照 {"$ref": h} に置き換え、中身は run の中で初めて出た時に 1 度だけ blob の行で書く。周期ごとに
;;; 同じ一覧を読み直す controller(再同期)は、変わっていない行・頁が参照 1 つになる(実測は docs/experiment-log.md)。
;;; 既出の記憶(BlobMemory)は新しく使った物から BLOB-MEMORY-MAX 個まで — 忘れた中身がまた出たら blob を書き直す(読む側は同じ h を
;;; 同じ中身として扱うので、2 度在っても害は無い)。再生は run の始まりから読むので、区切りごとに中身を書き直さない
;;; (書き直すと、区切りごとに一覧の全行 = 大きな一覧を読む書き手で約 130 MB を書くことになる)。
;;;
;;; 大きな値の差分(delta-of / apply-delta): 形の版 1 の記録が使った(同じ問いの前の答えとの差)。版 1 の記録を読むためと、
;;; backtest の報告(記録 → 再生の差)のために残す。
(import base64)
(import collections [OrderedDict])
(import collections.abc [Callable])
(import datetime [datetime])
(import dataclasses)
(import hashlib)
(import importlib)
(import json)
(import math)
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.scheduler [Spawn Wait Gather Race Cancel CreatePromise CompletePromise FailPromise
                                      CreateSemaphore AcquireSemaphore ReleaseSemaphore Task Promise Future Semaphore])
(import doeff_time [GetTimeEffect GetMonotonicEffect DelayEffect])
(import doeff_cluster.shared_model [ReadShared WriteShared ANY])
(import doeff_cluster.semaphore_model [CreateNamedSemaphore HeldLease LeaseStanding])
(import doeff_cluster.readiness_model [ReportReady])
(import doeff_cluster.metrics_model [ReportMetrics])

(setv FORMAT-VERSION 2)
;; 読める形の版(1 = 差分・2 = 内容参照と問いと答えの 1 行)。
(setv READABLE-FORMATS #(1 2))
;; 版 1 の差分の閾値(canonical JSON の文字数)。
(setv DELTA-MIN-CHARS 2048)
;; この長さ(canonical JSON の文字数)以上の節を内容参照にする。参照 1 つ({"$ref": 24 桁})は約 35 文字なので、
;; 閾値が小さすぎると blob の行の数だけが増える。
(setv INTERN-MIN-CHARS 256)
;; 既出の中身の記憶の上限(1 つ約 170 byte = 約 35 MB)。大きな一覧を読む書き手の行の数(約 16 万 — 2026-09-25 実測)より大きく取る。
(setv BLOB-MEMORY-MAX 200000)

(setv READ "read" LIVE "live" DECISION "decision" OUTPUT "output")
(setv LOOSE #(DECISION OUTPUT))


(defclass UnencodableValue [TypeError]
  "記録の形(JSON)にできない値。記録を黙って落とさないために投げる。")

(defclass UnrecordableEffect [TypeError]
  "EFFECT-CODECS に登録の無い effect の型。")

(defclass _Diverge []
  (defn __repr__ [self] "DIVERGE"))

(setv DIVERGE (_Diverge))


(defclass ReplayHandle []
  "再生で記録の答えから作る handle(named semaphore 等)。業務コードは受け取って引数に戻すだけなので、同じ名を持つ札で足りる。"
  (defn __init__ [self #^ str kind ref]
    (setv self.kind kind self.ref ref))
  (defn __eq__ [self other]
    (and (isinstance other ReplayHandle) (= #(self.kind self.ref) #(other.kind other.ref))))
  (defn __hash__ [self] (hash #("ReplayHandle" self.kind self.ref)))
  (defn __repr__ [self] (.format "ReplayHandle({!r}, {!r})" self.kind self.ref)))


(defclass ReplaySemaphore [ReplayHandle Semaphore]
  "semaphore の札(業務コードの契約が Semaphore の型を求めるので、その子にする)。"
  (defn __init__ [self #^ str kind ref]
    (ReplayHandle.__init__ self kind ref)
    (Semaphore.__init__ self (.format "replay:{}" ref)))
  (defn __eq__ [self other] (ReplayHandle.__eq__ self other))
  (defn __hash__ [self] (ReplayHandle.__hash__ self)))


(defn handle-for [#^ str kind ref]
  "記録の handle の印 → 再生の札。semaphore は Semaphore の子の札。"
  (if (in kind #("named-sem" "sem")) (ReplaySemaphore kind ref) (ReplayHandle kind ref)))


(defclass RecordedError [Exception]
  "記録の例外の型を今の版で import できない時の代わり(型の名と文を持つ)。"
  (defn __init__ [self #^ str type-name #^ str message]
    (.__init__ (super) (.format "{}: {}" type-name message))
    (setv self.type-name type-name self.message message)))


;; --- 型の名 ------------------------------------------------------------------------------------

(defn #^ str type-name [cls]
  (.format "{}:{}" cls.__module__ cls.__qualname__))

(defn resolve-type [#^ str name]
  "型の名 → class。import できなければ None。"
  (setv #(module qualname) (.split name ":" 1))
  (try
    (setv obj (importlib.import-module module))
    (for [part (.split qualname ".")]
      (setv obj (getattr obj part)))
    obj
    (except [e [ImportError AttributeError ValueError]] None)))


;; --- handle の名 --------------------------------------------------------------------------------

(defclass HandleTable []
  "handle(Task・Semaphore・Promise / Future)に記録の中の名を振る表。記録と再生が同じ規則で名付けるので、Wait(task) の引数が
   両方で同じ JSON になる。by-id は object の id → (種類, 名)。object は keep に持って id の使い回しを防ぐ。
   Promise と Future は scheduler の promise の番号で引く(Future は .future のたびに新しい object になる)。"
  (defn __init__ [self]
    (setv self.by-id {} self.keep [] self.promises {}))

  (defn bind [self obj #^ str kind ref]
    (when (isinstance obj ReplayHandle) (return None))
    (if (in kind #("promise"))
        (setv (get self.promises (getattr obj "promise_id" None)) ref)
        (do (setv (get self.by-id (id obj)) #(kind ref))
            (.append self.keep obj)))
    None)

  (defn name-of [self obj]
    (cond
      (isinstance obj ReplayHandle) #(obj.kind obj.ref)
      (isinstance obj #(Promise Future))
        (do (setv ref (.get self.promises (getattr obj "promise_id" None)))
            (if (is ref None) None #((if (isinstance obj Future) "future" "promise") ref)))
      True (.get self.by-id (id obj)))))


;; --- 値の符号化 ---------------------------------------------------------------------------------

(defn _plain-key? [k]
  (and (isinstance k str) (not (.startswith k "$"))))

(defn encode-value [v [handles None]]
  "値 → JSON の値。型を落とさない(tuple・bytes・dataclass・例外・handle に印を付ける)。知らない物は UnencodableValue。"
  (cond
    (is v None) None
    (isinstance v bool) v
    (isinstance v int) (int v)
    (isinstance v float) (if (math.isfinite v) v {"$f" (repr v)})
    (isinstance v str) v
    (isinstance v bytes) {"$b" (.decode (base64.b64encode v) "ascii")}
    ;; 時刻(GetTime の答え)は ISO 8601 の文字列で。timezone を落とさない(timezone の無い時刻は GetTime が返さないので断る)。
    (isinstance v datetime)
      (if (is v.tzinfo None)
          (raise (UnencodableValue (.format "timezone の無い時刻は記録の形にしない: {!r}" v)))
          {"$dt" (.isoformat v)})
    (isinstance v ReplayHandle) {"$h" v.kind "id" v.ref}
    (and (is-not handles None) (is-not (.name-of handles v) None))
      (do (setv #(kind ref) (.name-of handles v)) {"$h" kind "id" ref})
    (isinstance v #(Task Semaphore Promise Future))
      (raise (UnencodableValue (.format "名の無い handle: {!r}" v)))
    (isinstance v list) (lfor x v (encode-value x handles))
    (isinstance v tuple) {"$t" (lfor x v (encode-value x handles))}
    (isinstance v dict)
      (if (all (gfor k v (_plain-key? k)))
          (dfor #(k x) (.items v) k (encode-value x handles))
          {"$d" (lfor #(k x) (.items v) [(encode-value k handles) (encode-value x handles)])})
    (isinstance v BaseException) (encode-error v handles)
    (and (dataclasses.is-dataclass v) (not (isinstance v type)))
      {"$c" (type-name (type v))
       "f" (dfor f (dataclasses.fields v) f.name (encode-value (getattr v f.name) handles))}
    True (raise (UnencodableValue (.format "記録の形にできない値の型: {}" (type-name (type v)))))))

(defn encode-error [#^ BaseException e [handles None]]
  "例外 → JSON。型の名・args・文・JSON にできる属性(__ で始まる物を除く — doeff の traceback 等)。"
  (setv args [])
  (for [a e.args]
    (.append args (try (encode-value a handles) (except [UnencodableValue] {"$repr" (repr a)}))))
  (setv attrs {})
  (for [#(k x) (.items (getattr e "__dict__" {}))]
    (when (not (.startswith k "__"))
      (try (setv (get attrs k) (encode-value x handles)) (except [UnencodableValue] None))))
  {"$e" (type-name (type e)) "args" args "msg" (str e) "attrs" attrs})

(defn decode-value [j]
  "encode-value の逆。handle は ReplayHandle(再生の札)になる。"
  (cond
    (isinstance j list) (lfor x j (decode-value x))
    (not (isinstance j dict)) j
    (in "$f" j) (float (get j "$f"))
    (in "$b" j) (base64.b64decode (get j "$b"))
    (in "$dt" j) (datetime.fromisoformat (get j "$dt"))
    (in "$h" j) (handle-for (get j "$h") (get j "id"))
    (in "$t" j) (tuple (lfor x (get j "$t") (decode-value x)))
    (in "$d" j) (dfor #(k x) (get j "$d") (decode-value k) (decode-value x))
    (in "$e" j) (decode-error j)
    (in "$repr" j) (get j "$repr")
    (in "$c" j) (do (setv cls (resolve-type (get j "$c")))
                    (when (is cls None)
                      (raise (UnencodableValue (+ "記録の dataclass を import できない: " (get j "$c")))))
                    (cls #** (dfor #(k x) (.items (get j "f")) k (decode-value x))))
    True (dfor #(k x) (.items j) k (decode-value x))))

(defn decode-error [j]
  "記録の例外を同じ型・同じ args・同じ属性で作り直す(__init__ は呼ばない — 独自の __init__ を持つ例外も同じ物になる)。
   型を import できなければ RecordedError(型の名と文)。"
  (setv cls (resolve-type (get j "$e")))
  (when (or (is cls None) (not (isinstance cls type)) (not (issubclass cls BaseException)))
    (return (RecordedError (get j "$e") (get j "msg"))))
  (setv obj (.__new__ cls cls))
  (BaseException.__init__ obj #* (lfor a (get j "args") (decode-value a)))
  (for [#(k x) (.items (.get j "attrs" {}))]
    (try (setattr obj k (decode-value x)) (except [Exception] None)))
  obj)

(defn #^ str canonical [j]
  "JSON の値の比べる形(鍵の順を揃えた文字列)。"
  (json.dumps j :sort-keys True :ensure-ascii False :separators #("," ":")))

(defn #^ str short-hash [#^ str text]
  (cut (.hexdigest (hashlib.sha1 (.encode text "utf-8"))) 0 16))


;; --- 差分(大きな値) ---------------------------------------------------------------------------

(defn _list-ops [prev new]
  "prev の list から new の list を作る手順: [\"r\" 始め 長さ](prev の連続した区間)と [\"v\" 値](新しい項)。"
  (setv index {})
  (for [#(i x) (enumerate prev)]
    (.append (.setdefault index (canonical x) []) i))
  (setv ops [] run-start None run-len 0)
  (defn flush []
    (nonlocal run-start run-len)
    (when (> run-len 0) (.append ops ["r" run-start run-len]))
    (setv run-start None run-len 0))
  (for [x new]
    (setv candidates (.get index (canonical x) []))
    (setv want (if (is run-start None) None (+ run-start run-len)))
    (cond
      (and (is-not want None) (in want candidates)) (+= run-len 1)
      candidates (do (flush) (setv run-start (get candidates 0) run-len 1))
      True (do (flush) (.append ops ["v" x]))))
  (flush)
  ops)

(defn delta-of [prev new]
  "JSON の値 prev から new への差分(どの節も印付きの dict)。"
  (cond
    (= (canonical prev) (canonical new)) {"$=" 1}
    (and (isinstance prev dict) (isinstance new dict))
      {"$o" (dfor #(k x) (.items new) k (if (in k prev) (delta-of (get prev k) x) {"$v" x}))
       "$del" (lfor k prev :if (not-in k new) k)}
    (and (isinstance prev list) (isinstance new list)) {"$a" (_list-ops prev new)}
    True {"$v" new}))

(defn apply-delta [prev delta]
  (cond
    (in "$=" delta) prev
    (in "$v" delta) (get delta "$v")
    (in "$o" delta)
      (do (setv out (dfor #(k x) (.items prev) :if (not-in k (get delta "$del")) k x))
          (for [#(k d) (.items (get delta "$o"))]
            (setv (get out k) (apply-delta (.get prev k) d)))
          ;; 鍵の順は new の順に揃える(比べる形は順を見ないが、復号した dict の順も元に近づける)
          (dfor k (get delta "$o") k (get out k)))
    (in "$a" delta)
      (do (setv out [])
          (for [op (get delta "$a")]
            (if (= (get op 0) "r")
                (.extend out (cut prev (get op 1) (+ (get op 1) (get op 2))))
                (.append out (get op 1))))
          out)
    True (raise (ValueError (+ "差分の形を読めない: " (canonical delta))))))


;; --- 内容参照(大きな値) ---------------------------------------------------------------------

(defn #^ str content-hash [#^ str text]
  "内容参照の鍵(blake2b 96 bit の 16 進 24 桁)。"
  (.hexdigest (hashlib.blake2b (.encode text "utf-8") :digest-size 12)))

(defclass BlobMemory []
  "既出の中身の hash の記憶(新しく使った物から max-size 個)。in で引くと使った印を付け直す。"
  (defn #^ None __init__ [self #^ int [max-size BLOB-MEMORY-MAX]]
    (setv self.max-size max-size self.order (OrderedDict)))
  (defn #^ bool __contains__ [self #^ str h]
    (when (in h self.order)
      (.move-to-end self.order h)
      (return True))
    False)
  (defn #^ None add [self #^ str h]
    (setv (get self.order h) True)
    (while (> (len self.order) self.max-size)
      (.popitem self.order :last False))
    None)
  (defn #^ int __len__ [self] (len self.order)))

(defn #^ object intern-json [#^ object j #^ object seen #^ Callable emit #^ int [min-chars INTERN-MIN-CHARS]]
  "JSON の値 j を下から畳む: canonical が min-chars 以上の dict / list の節を {\"$ref\": h} に置き換え、seen に無い h なら
   (emit h 畳んだ節)を呼んで中身を書かせ、seen に足す。子が先に畳まれるので、親の比べる形の長さは子の参照の長さで測る。
   答え = 畳んだ値(小さければ j と同じ形)。"
  (setv form (cond
               (isinstance j dict) (dfor #(k v) (.items j) k (intern-json v seen emit min-chars))
               (isinstance j list) (lfor v j (intern-json v seen emit min-chars))
               True j))
  (when (not (isinstance form #(dict list)))
    (return form))
  (setv text (canonical form))
  (when (< (len text) min-chars)
    (return form))
  (setv h (content-hash text))
  (when (not-in h seen)
    (.add seen h)
    (emit h form))
  {"$ref" h})

(defn #^ object resolve-refs [#^ object j #^ dict blobs #^ (| dict None) [memo None]]
  "intern-json の逆。blobs = h → 畳んだ節(blob の行の v)。無い参照は ValueError(記録が欠けている)。"
  (setv memo (if (is memo None) {} memo))
  (cond
    (isinstance j list) (lfor v j (resolve-refs v blobs memo))
    (not (isinstance j dict)) j
    (and (= (len j) 1) (in "$ref" j))
      (do (setv h (get j "$ref"))
          (when (not-in h memo)
            (when (not-in h blobs)
              (raise (ValueError (+ "内容参照の中身(blob)が記録に無い: " h))))
            (setv (get memo h) (resolve-refs (get blobs h) blobs memo)))
          ;; 同じ参照は同じ object を共有する(読む側は JSON の値として読むだけ — 業務コードへ渡す値は decode-value が新しく作る)。
          (get memo h))
    True (dfor #(k v) (.items j) k (resolve-refs v blobs memo))))


;; --- effect の登録 -----------------------------------------------------------------------------

(defclass EffectCodec []
  (defn __init__ [self cls mode [args None] [subject None] [unexecuted DIVERGE] [binds None] [watch False]]
    (setv self.cls cls self.name (type-name cls) self.mode mode self.args-fn args self.subject-fn subject
          self.unexecuted unexecuted self.binds binds self.watch watch)))


(defn _fields-args [effect handles]
  (dfor f (dataclasses.fields effect) f.name (encode-value (getattr effect f.name) handles)))

(defn _loose-value [v handles]
  "live の effect の引数・答えは順番の突き合わせと報告にしか使わないので、JSON にできない値は repr の印にする。"
  (try (encode-value v handles) (except [UnencodableValue] {"$repr" (repr v)})))

(defn _handle-mode [attr]
  "semaphore の effect: 名前付き(記録の札・cluster の lease)なら read、scheduler の手元の semaphore なら live。"
  (fn [effect handles]
    (setv named (.name-of handles (getattr effect attr)))
    (if (and (is-not named None) (= (get named 0) "named-sem")) READ LIVE)))

(defn _key-subject [args] (str (.get args "key")))

(setv _REGISTRY {})

(defn register [#^ EffectCodec codec]
  (setv (get _REGISTRY codec.cls) codec)
  codec)

(defn codec-of [effect]
  "effect の登録。型そのもので引く(子 class を親の登録で黙って扱わない)。無ければ UnrecordableEffect。"
  (setv codec (.get _REGISTRY (type effect)))
  (when (is codec None)
    (raise (UnrecordableEffect (.format "effect の型 {} は記録の登録(effect_codec.EFFECT-CODECS)に無い" (type-name (type effect))))))
  codec)

(defn #^ str mode-of [effect handles]
  (setv mode (. (codec-of effect) mode))
  (if (callable mode) (mode effect handles) mode))

(defn #^ dict args-of [effect handles]
  (setv codec (codec-of effect))
  (if (is codec.args-fn None) (_fields-args effect handles) (codec.args-fn effect handles)))

(defn subject-of [effect #^ dict args]
  (setv codec (codec-of effect))
  (if (is codec.subject-fn None) None (codec.subject-fn args)))

(defn registered-types []
  "登録済みの型の名の一覧(README とテストの材料)。"
  (sorted (gfor c (.values _REGISTRY) c.name)))

;; 汎用(doeff・worker の基盤)
(register (EffectCodec Ask READ :args (fn [e h] {"key" (_loose-value e.key h)}) :watch True))
;; 時計は doeff-time の 3 つ(GetTime / GetMonotonic / Delay)。再生では記録の答え(時刻・秒・None)を返し、眠らない。
(for [cls [GetTimeEffect GetMonotonicEffect DelayEffect]]
  (register (EffectCodec cls READ)))
(register (EffectCodec ReadShared READ))
(register (EffectCodec HeldLease READ))
(register (EffectCodec LeaseStanding READ))
(register (EffectCodec CreateNamedSemaphore READ :args (fn [e h] {"name" e.name "permits" e.permits}) :binds "named-sem"))
(register (EffectCodec AcquireSemaphore (_handle-mode "semaphore") :args (fn [e h] {"semaphore" (_loose-value e.semaphore h)})))
(register (EffectCodec ReleaseSemaphore (_handle-mode "semaphore") :args (fn [e h] {"semaphore" (_loose-value e.semaphore h)})))
(register (EffectCodec CreateSemaphore LIVE :args (fn [e h] {"permits" e.permits}) :binds "sem"))
(register (EffectCodec Spawn LIVE :args (fn [e h] {"priority" e.priority "daemon" e.daemon}) :binds "task"))
(register (EffectCodec Wait LIVE :args (fn [e h] {"on" (_loose-value e.task h)})))
(register (EffectCodec Gather LIVE :args (fn [e h] {"on" (lfor t e.tasks (_loose-value t h))})))
(register (EffectCodec Race LIVE :args (fn [e h] {"on" (lfor t e.tasks (_loose-value t h))})))
(register (EffectCodec Cancel LIVE :args (fn [e h] {"on" (_loose-value e.task h)})))
(register (EffectCodec CreatePromise LIVE :args (fn [e h] {}) :binds "promise"))
(register (EffectCodec CompletePromise LIVE :args (fn [e h] {"promise" (_loose-value e.promise h) "value" (_loose-value e.value h)})))
(register (EffectCodec FailPromise LIVE :args (fn [e h] {"promise" (_loose-value e.promise h) "error" (_loose-value e.error h)})))
(register (EffectCodec ReportReady OUTPUT :unexecuted None))
(register (EffectCodec ReportMetrics OUTPUT :unexecuted None))
(register (EffectCodec WriteShared OUTPUT :subject _key-subject :unexecuted True
                       :args (fn [e h] {"key" e.key "value" (encode-value e.value h)
                                        "expect" (if (is e.expect ANY) {"$any" 1} (encode-value e.expect h))})))

;; process の memory の gauge(metrics_model.ReadProcessGauges)は読み。
(import doeff_cluster.metrics_model [ReadProcessGauges])
(register (EffectCodec ReadProcessGauges READ))

;; --- 業務コードの effect ------------------------------------------------------------------------------
;; 業務の effect の型は、業務の側の module が import の時に register で足す(この package は業務の型を知らない)。
;; 記録の係(job_entry)と再生の入口(replay_main)は Service の宣言の env の module を import してから記録・再生を始めるので、
;; env の module(か、それが import する module)で登録すれば、記録と再生の両方に届く。登録の無い型は UnrecordableEffect。
