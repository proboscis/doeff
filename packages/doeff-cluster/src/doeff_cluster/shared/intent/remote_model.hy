;;; task(呼んだ側に寿命が縛られる短い仕事)の effect と、送る形・戻す形。
;;;
;;;   (<- result (RemoteJob (summarize foundation rows) :needs (frozenset ["net"])))
;;;
;;; RemoteJob は「未実行の Program を走らせ、戻り値(または例外)を返す」効果。Program は自分の handler を中の with-handlers で並べる
;;; (ADR-DOE-CLUSTER-001 R1・R2 — 実行先は handler を足さない)。handler の値も I/O の資源も送らない(送れば UnsendableProgram)。
;;; 送るのは cloudpickle した Program の値・要る能力・送り手の commit と版の識別だけ。
;;;
;;; 答える物:
;;;   remote-cluster(remote.hy)… coordinator へ出し、worker がその commit のコードを準備した子 process で走らせる
;;;   sim-cluster の偽の宿(local.hy)… 手元で同じ要求を本物の coordinator の模擬へ送り、task を別の process(別のスコープ)で走らせる
;;;   (呼び手の handler を継がない — 以前の remote-inline は継いでいたので消した)
;;;
;;; 意味は doeff の Spawn / Wait / Cancel と揃える: RemoteJob は「Spawn して Wait する」を 1 つにした形で、
;;; 並行に走らせたい・止めたい時は呼び手が Spawn / Cancel で包む(効果を 2 つに割らない)。
;;; 呼んだ側が止まれば task も止まる: cluster では呼び手の問い合わせが lease を延ばし、途絶えれば coordinator が task を
;;; 落として worker が子 process を止める。
;;;
;;; cloudpickle は長期保存の形式ではない。blob には必ず commit と Python / doeff の版を添え、受け側は版が違えば復元せずに断る。
;;; 詰めた Program は task の本文に載せず、coordinator の置き場 /programs/<sha>(program-sha)に版と一緒に先に置き、本文は sha だけを運ぶ
;;; (service の宣言と同じ運び方 — ADR-DOE-CLUSTER-001 R3b)。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import base64)
(import collections)
(import collections.abc)
(import io)
(import dataclasses [dataclass field])
(import hashlib)
(import traceback)
(import cloudpickle)
(import types [NotImplementedType])
(import doeff [EffectBase Program])
(import doeff.do)


(defclass [(dataclass :frozen True)] RemoteJob [EffectBase]
  "program = 未実行の Program(値 — handler は Program の中の with-handlers で並べる・ADR-DOE-CLUSTER-001 R1・R2)・needs = 要る能力の名の frozenset(置く worker は needs ⊆ provides)。
   environ = 子の環境変数(名 → 文字列 — service の :environ と同じ規則・既定は空)。本番は worker が子 process の環境変数に置き、
   sim は sim の宿が同じ名の Ask に答える(Program は名の Ask で読む — 本番の土台は host_contract.environ-reader・sim の宿も同じ読みの定義で字面どおり返す)。
   結果 = Program の戻り値。Program が投げた例外はそのまま呼び手へ届く。"
  (#^ (| Program EffectBase) program)
  (setv #^ frozenset needs (frozenset))
  (setv #^ str name "")
  (setv #^ dict environ (field :default-factory dict))
  (defn #^ None __post-init__ [self]
    "needs と environ を作る時に検める(needs の空・旧い形・environ の名の形・予約・秘密の名を断る — cluster_model.effect-needs-problem・runtime_env_model.child-environ-refusal)。"
    (import doeff_cluster.shared.core.capabilities [effect-needs-problem])
    (import doeff_cluster.shared.intent.runtime_env_model [child-environ-refusal])
    (setv problem (effect-needs-problem self.needs))
    (when problem (raise (TypeError (+ "RemoteJob.needs: " problem))))
    (setv problem (child-environ-refusal self.environ))
    (when problem (raise (TypeError (+ "RemoteJob.environ: " problem))))))


(defclass RemoteJobFailed [Exception]
  "実行先で Program を走らせられなかった(版の不一致・復元不能・結果なし・コードの準備の失敗)。業務の例外ではない。")


(defclass UnsendableProgram [RemoteJobFailed]
  "送れない値(lock・file・socket・生の thread 等)を捕まえた Program。送り手の側で、送る前に断る。")


(defclass [(dataclass :frozen True)] VersionDiff []
  "版の辞書の食い違い 1 欄: field = 欄の名・sender = 送り手の値・env = 実行する側(env の root)の値(無い欄は None)。"
  (#^ str field)
  (#^ (| str None) sender)
  (#^ (| str None) env))


(defclass VersionMismatch [RemoteJobFailed]
  "送り手と受け側の Python / cloudpickle / doeff の版が違う。受け側は復元せずに断る。
   diffs = 食い違った欄(VersionDiff の tuple)・env-key = 実行する側の env のキー(env の task でなければ空)。"
  (defn #^ None __init__ [self #^ str message #^ tuple [diffs #()] #^ str [env-key ""]]  ; defk にできない: 例外の class の初期化
    (.__init__ (super) message)
    (setv self.diffs diffs self.env-key env-key)))


(defclass EnvUnavailable [RemoteJobFailed]
  "実行環境(runtime env)を準備できなかった(子 process を起こす前 — 同じ task を 2 度実行していない)。
   kind = runtime_env_model.EnvFailureKind の値・detail = 理由。"
  (defn #^ None __init__ [self #^ str kind #^ str detail]  ; defk にできない: 例外の class の初期化
    (.__init__ (super) (.format "実行環境を準備できない({}): {}" kind detail))
    (setv self.kind kind self.detail detail)))


(defclass [(dataclass :frozen True)] TaskSucceeded []
  (#^ object value))


(defclass [(dataclass :frozen True)] TaskFailed []
  "kind / message / traceback は常に文字列で持つ。error は例外そのもの(pickle できない例外なら None)。"
  (#^ str kind)
  (#^ str message)
  (#^ str traceback)
  (setv #^ (| BaseException None) error None))


(setv TaskOutcome (| TaskSucceeded TaskFailed))


;; この process の版の識別(current-versions)を読むのは io の層の process_versions.hy(#1630)。ここは突き合わせの判断だけ。
(defn #^ tuple version-diffs [#^ dict expected #^ dict actual]  ; defk にできない: 子の入口と coordinator の純粋な判断(Program の外)が呼ぶ
  "送り手の版(expected)と受け側の版(actual)の食い違った欄(VersionDiff の tuple・欄の名の順)。env のキー(envKey)は両方が名乗る
   時だけ比べて先頭に置く(送り手が env の外 — 開発の checkout — で動く時は、残りの欄と宣言の組み立ての「汚れたツリーを断る」が
   source の一致を保つ)。"
  (setv both-keyed (and (in "envKey" expected) (in "envKey" actual))
        keys (sorted (lfor k (| (set expected) (set actual)) :if (or both-keyed (!= k "envKey")) k)
                     :key (fn [k] #((!= k "envKey") k))))
  (tuple (gfor key keys :if (!= (.get expected key) (.get actual key))
               (VersionDiff key (.get expected key) (.get actual key)))))


(defn #^ str diffs-text [#^ tuple diffs]  ; defk にできない: 子の入口と coordinator の純粋な判断(Program の外)が呼ぶ
  "版の違いの列(version-diffs の答え)を 1 行で名指す。違いが在ると分かっている呼び手が使う — 答えに None を含まない(#1690)。"
  (.join "・" (gfor d diffs (.format "{}: 送り手 {} / 受け側 {}" d.field d.sender d.env))))


(defn #^ (| str None) version-mismatch [#^ dict expected #^ dict actual]  ; defk にできない: 子の入口と coordinator の純粋な判断(Program の外)が呼ぶ
  "違いを 1 行で名指す。同じなら None。"
  (setv diffs (version-diffs expected actual))
  (if diffs (diffs-text diffs) None))


(defn _refuse-file [value]
  (raise (TypeError (.format "file を捕まえている({})。cloudpickle は読みの file を中身の写し(StringIO)に黙って替え、書きの file は受け側で復元できない" (type value)))))


;; cloudpickle の規則の表。型の上では Mapping と宣言されているが実物は ChainMap(書き換えられる表)— ChainMap の親に据えるため、
;; ここで一度だけ MutableMapping と確かめる(cloudpickle の版が表の形を変えたら import の時に名指して落ちる)。
(val CLOUDPICKLE-DISPATCH cloudpickle.CloudPickler.dispatch-table)
(assert (isinstance CLOUDPICKLE-DISPATCH collections.abc.MutableMapping)
        (.format "cloudpickle の dispatch_table が書き換えられる表でない: {}" (type CLOUDPICKLE-DISPATCH)))


(defclass StrictPickler [cloudpickle.CloudPickler]
  "cloudpickle の既定から、file を運ぶ規則だけを外した pickler。file は送り手で断る(意味が黙って変わるため)。
   handler の値(doeff.program.handler が作る物 — 印 __doeff_handler_data__)も断る(ADR-DOE-CLUSTER-001 R3b・改訂 1 の D):
   handler は job の Program の中(defk の本体)で関数を呼んで作り、値として宣言や task に詰めない。送り手(declare・RemoteJob・
   SubmitDetached)はどれもここを通る。"
  (setv dispatch-table
    (collections.ChainMap
      (dfor t #(io.TextIOWrapper io.BufferedReader io.BufferedWriter io.BufferedRandom io.FileIO) t _refuse-file)
      CLOUDPICKLE-DISPATCH))

  ;; obj は pickle が詰めようとしている値そのもの(どの値にもなる)。答えは pickle の reduce の組か NotImplemented(cloudpickle の規則)。
  (defn #^ (| tuple NotImplementedType) reducer-override [self #^ object obj]  ; defk にできない: pickle の library が呼ぶ callback
    "handler の値に当たったら断り、それ以外は cloudpickle の規則に任せる。"
    (when (and (callable obj) (not (isinstance obj type)) (hasattr obj "__doeff_handler_data__"))
      (raise (TypeError (.format "handler の値 {} を捕まえている — handler は Program の本体の中で関数を呼んで作る(値として詰めない)"
                                 (getattr obj "__qualname__" (repr obj))))))
    (.reducer-override (super) obj)))


(defn #^ bytes _dumps [program]
  (setv buffer (io.BytesIO))
  (.dump (StrictPickler buffer :protocol cloudpickle.DEFAULT-PROTOCOL) program)
  (.getvalue buffer))


(defn #^ str encode-program [#^ Program program]
  "未実行の Program を cloudpickle して base64 の文字列にする。送れない値は UnsendableProgram で断る。
   送る前に手元で 1 度復元してみる(受け側で初めて復元に失敗する値を、送り手の側で名指すため)。"
  (try
    (setv data (_dumps program))
    (except [error [TypeError AttributeError ValueError cloudpickle.pickle.PicklingError]]
      (raise (UnsendableProgram (.format "Program を送れない(値として運べない物を捕まえている): {}: {}"
                                         (. (type error) __name__) error)))))
  (try
    (cloudpickle.loads data)
    (except [error Exception]
      (raise (UnsendableProgram (.format "Program を送れない(手元でも復元できない): {}: {}"
                                         (. (type error) __name__) error)))))
  (.decode (base64.b64encode data) "ascii"))


(defn #^ Program decode-program [#^ str blob]
  (cloudpickle.loads (base64.b64decode blob)))


(deff program-sha [#^ str blob]  ; defk にできない: 送り手(declare・task の client)・coordinator の置き場・worker の cache が Program の外で呼ぶ
  {:pre [(: blob str)] :post [(: % str) (= (len %) 64)] :tags {:context "doeff-cluster" :role "judgment"}}
  "詰めた Program の置き場のキー(中身の sha256 の 16 進 64 桁)。/programs/<sha> の鍵・宣言の行と task の本文の program・worker の
   cache の file の名はどれもこの値(定義点はここ 1 つ — ADR-DOE-CLUSTER-001 R3b・改訂 1 の F)。"
  (.hexdigest (hashlib.sha256 (.encode blob "ascii"))))


(defn #^ TaskFailed failed-from [#^ BaseException error]
  (TaskFailed (. (type error) __name__) (str error)
              (.join "" (traceback.format-exception error)) error))


(defn #^ str encode-outcome [#^ (| TaskSucceeded TaskFailed) outcome]
  "結果を base64 の cloudpickle にする。値や例外が pickle できなければ、文字列の記述だけを残して失敗として返す。"
  (try
    (.decode (base64.b64encode (cloudpickle.dumps outcome)) "ascii")
    (except [error Exception]
      (setv described
        (if (isinstance outcome TaskFailed)
            (TaskFailed outcome.kind outcome.message outcome.traceback None)
            (TaskFailed "UnsendableResult"
                        (.format "結果を送れない: {}: {}" (. (type error) __name__) error) "" None)))
      (.decode (base64.b64encode (cloudpickle.dumps described)) "ascii"))))


(defn #^ (| TaskSucceeded TaskFailed) decode-outcome [#^ str blob]
  (setv outcome (cloudpickle.loads (base64.b64decode blob)))
  (when (not (isinstance outcome #(TaskSucceeded TaskFailed)))
    (raise (RemoteJobFailed (.format "結果の形が違う: {}" (type outcome)))))
  outcome)
