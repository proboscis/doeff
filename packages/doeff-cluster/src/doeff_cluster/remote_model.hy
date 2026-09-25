;;; task(呼んだ側に寿命が縛られる短い仕事)の effect と、送る形・戻す形。
;;;
;;;   (<- result (RemoteJob (summarize conv rows) :env "myapp.envs:board_env"))
;;;
;;; RemoteJob は「未実行の Program を、名前で指した handler の組の下で走らせ、戻り値(または例外)を返す」効果。
;;; handler そのものも I/O の資源も送らない。送るのは cloudpickle した Program の値・env の名前(import path)・
;;; 送り手の commit と版の識別だけ。
;;;
;;; 2 つの handler(remote.hy):
;;;   remote-inline  … 同じ VM で Spawn して待つ(テスト用。外側の handler をそのまま継承する)
;;;   remote-cluster … coordinator へ出し、worker がその commit のコードを準備した子 process で走らせる
;;;
;;; 意味は doeff の Spawn / Wait / Cancel と揃える: RemoteJob は「Spawn して Wait する」を 1 つにした形で、
;;; 並行に走らせたい・止めたい時は呼び手が Spawn / Cancel で包む(効果を 2 つに割らない)。
;;; 呼んだ側が止まれば task も止まる: cluster では呼び手の問い合わせが lease を延ばし、途絶えれば coordinator が task を
;;; 落として worker が子 process を止める。
;;;
;;; cloudpickle は長期保存の形式ではない。blob には必ず commit と Python / doeff の版を添え、受け側は版が違えば復元せずに断る。
(import base64)
(import collections)
(import io)
(import dataclasses [dataclass])
(import hashlib)
(import importlib.metadata)
(import pathlib [Path])
(import sys)
(import traceback)
(import cloudpickle)
(import doeff [EffectBase])
(import doeff.do)


(defclass [(dataclass :frozen True)] RemoteJob [EffectBase]
  "program = 未実行の Program(値)・env = 実行先で組む handler の組の import path・requires = 実行先の条件(label)。
   結果 = Program の戻り値。Program が投げた例外はそのまま呼び手へ届く。"
  (#^ object program)
  (#^ str env)
  (setv #^ tuple requires #())
  (setv #^ str name ""))


(defclass RemoteJobFailed [Exception]
  "実行先で Program を走らせられなかった(版の不一致・復元不能・結果なし・コードの準備の失敗)。業務の例外ではない。")


(defclass UnsendableProgram [RemoteJobFailed]
  "送れない値(lock・file・socket・生の thread 等)を捕まえた Program。送り手の側で、送る前に断る。")


(defclass VersionMismatch [RemoteJobFailed]
  "送り手と受け側の Python / cloudpickle / doeff の版が違う。受け側は復元せずに断る。")


(defclass [(dataclass :frozen True)] TaskSucceeded []
  (#^ object value))


(defclass [(dataclass :frozen True)] TaskFailed []
  "kind / message / traceback は常に文字列で持つ。error は例外そのもの(pickle できない例外なら None)。"
  (#^ str kind)
  (#^ str message)
  (#^ str traceback)
  (setv #^ object error None))


(setv TaskOutcome (| TaskSucceeded TaskFailed))


(defn #^ str _source-fingerprint [#^ str module-name]
  ;; 同じ dist の版でも source が違えば cloudpickle が値として運ぶ内部の関数(doeff.do の thunk 等)は食い違う。
  ;; 版の名だけでは足りないので、その file の hash も添える。
  (setv module (importlib.import-module module-name))
  (setv path (Path module.__file__))
  (cut (.hexdigest (hashlib.sha256 (.read-bytes path))) 0 12))


(defn #^ dict current-versions []
  "この process の版の識別。送り手が blob に添え、受け側が突き合わせる。"
  {"python" (.format "{}.{}.{}{}" sys.version-info.major sys.version-info.minor sys.version-info.micro
                     (if (getattr sys "_is_gil_enabled" None) (if (sys._is-gil-enabled) "" "t") ""))
   "cloudpickle" cloudpickle.__version__
   "doeff" (importlib.metadata.version "doeff")
   "doeff-vm" (importlib.metadata.version "doeff-vm")
   "doeff-do" (_source-fingerprint "doeff.do")})


(defn #^ (| str None) version-mismatch [#^ dict expected #^ dict actual]
  "違いを 1 行で名指す。同じなら None。"
  (setv diffs (lfor key (sorted (| (set expected) (set actual)))
                    :if (!= (.get expected key) (.get actual key))
                    (.format "{}: 送り手 {} / 受け側 {}" key (.get expected key) (.get actual key))))
  (if diffs (.join "・" diffs) None))


(defn _refuse-file [value]
  (raise (TypeError (.format "file を捕まえている({})。cloudpickle は読みの file を中身の写し(StringIO)に黙って替え、書きの file は受け側で復元できない" (type value)))))


(defclass StrictPickler [cloudpickle.CloudPickler]
  "cloudpickle の既定から、file を運ぶ規則だけを外した pickler。file は送り手で断る(意味が黙って変わるため)。"
  (setv dispatch-table
    (collections.ChainMap
      (dfor t #(io.TextIOWrapper io.BufferedReader io.BufferedWriter io.BufferedRandom io.FileIO) t _refuse-file)
      cloudpickle.CloudPickler.dispatch-table)))


(defn #^ bytes _dumps [program]
  (setv buffer (io.BytesIO))
  (.dump (StrictPickler buffer :protocol cloudpickle.DEFAULT-PROTOCOL) program)
  (.getvalue buffer))


(defn #^ str encode-program [program]
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


(defn decode-program [#^ str blob]
  (cloudpickle.loads (base64.b64decode blob)))


(defn #^ TaskFailed failed-from [#^ BaseException error]
  (TaskFailed (. (type error) __name__) (str error)
              (.join "" (traceback.format-exception error)) error))


(defn #^ str encode-outcome [outcome]
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


(defn decode-outcome [#^ str blob]
  (setv outcome (cloudpickle.loads (base64.b64decode blob)))
  (when (not (isinstance outcome #(TaskSucceeded TaskFailed)))
    (raise (RemoteJobFailed (.format "結果の形が違う: {}" (type outcome)))))
  outcome)
