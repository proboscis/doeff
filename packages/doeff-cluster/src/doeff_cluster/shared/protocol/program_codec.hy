;;; task の Program と結果を送れる形(base64 の cloudpickle)に詰める・戻す。型は doeff_cluster.shared.intent.remote_model。
;;; 送り手(declare・RemoteJob・SubmitDetached)・子の入口(job_entry)・結果の受け手が同じ詰め方を使う(定義点はここ 1 つ)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import base64)
(import collections)
(import collections.abc)
(import io)
(import cloudpickle)
(import types [NotImplementedType])
(import doeff [Program])
(import doeff_cluster.shared.intent.remote_model [RemoteJobFailed UnsendableProgram TaskSucceeded TaskFailed])


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
