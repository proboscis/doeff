;;; task の Program と結果を送れる形(base64 の cloudpickle)に詰める・戻す。型は doeff_cluster.shared.intent.remote_model。
;;; 送り手(declare・RemoteJob・SubmitDetached)・子の入口(job_entry)・結果の受け手が同じ詰め方を使う(定義点はここ 1 つ)。
;;;
;;; 詰めた Program の bytes は、同じ版の同じ Program なら手元の状態に依らず同じにする(bytes の sha256 = 置き場の鍵 program-sha は宣言の
;;; 行に載り、宣言し直しが cluster の行と照らして「変わったか」を決める — #3660)。揺れの元は 2 つで、どちらも詰める所で消す:
;;;   1. 宣言を組んだ手元の path: cloudpickle は値ごと詰める関数の code の co_filename と、関数の globals の __file__(package なら
;;;      __path__)を手元の絶対 path のまま書く。これを module の名から作る import の根からの相対 path(app/jobs/system.hy)に
;;;      替える(portable-reduction)。受け側は sys.path の根からこの名で source を引ける(例外の traceback に行が出る)。
;;;   2. 物の共有: 同じ値の文字列が同じ object か別の object か(Hy を今 compile した module は co_filename と __file__ が同じ object・
;;;      .pyc から読んだ module は marshal が作った別の object)で、pickle の memo の形(中身を書くか、前に書いた物を指すか)が変わる。
;;;      詰めた後に流れを正準の形に直す(canonical-pickle): 同じ値の文字列・bytes は最初の 1 か所にだけ書いて後は指し、指されない memo を
;;;      消して番号を使う順に振り直す。文字列と bytes は変えられない値なので、受け側で同じ object になっても意味は変わらない。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import base64)
(import collections)
(import collections.abc)
(import importlib.machinery)
(import io)
(import itertools)
(import pickle)
(import pickletools)
(import posixpath)
(import struct)
(import cloudpickle)
(import types [CodeType FunctionType NotImplementedType])
(import doeff [Program run])
(import doeff_cluster.shared.intent.remote_model [RemoteJobFailed UnsendableProgram TaskSucceeded TaskFailed])


(defn _refuse-file [value]
  (raise (TypeError (.format "file を捕まえている({})。cloudpickle は読みの file を中身の写し(StringIO)に黙って替え、書きの file は受け側で復元できない" (type value)))))


;; cloudpickle の規則の表。型の上では Mapping と宣言されているが実物は ChainMap(書き換えられる表)— ChainMap の親に据えるため、
;; ここで一度だけ MutableMapping と確かめる(cloudpickle の版が表の形を変えたら import の時に名指して落ちる)。
(val CLOUDPICKLE-DISPATCH cloudpickle.CloudPickler.dispatch-table)
(assert (isinstance CLOUDPICKLE-DISPATCH collections.abc.MutableMapping)
        (.format "cloudpickle の dispatch_table が書き換えられる表でない: {}" (type CLOUDPICKLE-DISPATCH)))


(defk source-module-name [names file]
  {:pre [(: names dict) (: file str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "値ごと詰める関数の module の、import の根からの名(点で区切る)を知るため。names = 関数の globals・file = その __file__。
   `-m` で走らせた __main__ は __spec__ の名、file を直に走らせた __main__ は file の名(拡張子を除く)。"
  (val spec (.get names "__spec__"))
  (val name (.get names "__name__"))
  (cond
    (isinstance spec importlib.machinery.ModuleSpec) spec.name
    (and (isinstance name str) (!= name "__main__")) name
    True (get (posixpath.splitext (posixpath.basename file)) 0)))


(defk portable-source-path [module-name file]
  {:pre [(: module-name str) (: file str)] :post [(: % str) (not (posixpath.isabs %))] :tags {:context "doeff-cluster" :role "protocol"}}
  "値ごと詰める module の source の、置き場に依らない名を作るため: module の名を `/` で繋ぎ(package の __init__ は名の下に __init__)、
   file の拡張子を付ける。sys.path の根から import した module では、根からの相対 path と同じ(app/jobs/system.hy)。
   宣言を組んだ checkout の場所・.pyc の置き場は入らない。"
  (val parts (posixpath.splitext (posixpath.basename file)))
  (+ (.join "/" (+ (.split module-name ".") (if (= (get parts 0) "__init__") ["__init__"] []))) (get parts 1)))


(defk portable-code [code portable]
  {:pre [(: code CodeType) (: portable str)] :post [(: % CodeType)] :tags {:context "doeff-cluster" :role "protocol"}}
  "値ごと詰める関数の code と、その中の入れ子の code(内側の関数・lambda — 深さは決まらない)の co_filename を、置き場に依らない名
   portable へ替えた写しを作るため(元の code は替えない)。"
  (val rewritten (fn [inner]
                   (.replace inner :co-filename portable
                             :co-consts (tuple (gfor const inner.co-consts (if (isinstance const CodeType) (rewritten const) const))))))
  (rewritten code))


(defk portable-reduction [func reduced]
  {:pre [(: func FunctionType) (: reduced tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "protocol"}}
  "値ごと詰める関数 func の reduce の組 reduced を、宣言を組んだ手元の path に依らない組へ替えるため(頭注の 1)。reduced は cloudpickle の
   形 — 6 つ組・2 つ目が関数を作る引数 (code base_globals None None closure)・3 つ目が状態 (state slotstate)。code(と入れ子の code)は
   co_filename を替えた写しに差し替え、受け側の関数の globals に入る __file__・__path__ は名を替える。base_globals は pickler が module
   ごとに 1 つ持つ写しで、slotstate の __globals__ は関数ごとの写し(どちらも module の globals そのものではない)なので、その場で書き
   換える(同じ module の関数どうしが受け側でも globals を共有する形は崩さない)。__file__ の無い module の関数はそのまま返す。"
  (when (not (and (= (len reduced) 6) (isinstance (get reduced 1) tuple) (isinstance (get reduced 1 0) CodeType)
                  (isinstance (get reduced 1 1) dict) (isinstance (get reduced 2) tuple) (isinstance (get reduced 2 1) dict)
                  (isinstance (.get (get reduced 2 1) "__globals__") dict)))
    (raise (TypeError (.format "cloudpickle の値ごとの関数の詰め方の形が想定と違う(cloudpickle {}): {!r}" cloudpickle.__version__ reduced))))
  (val file (.get func.__globals__ "__file__"))
  (if (not (isinstance file str))
      reduced
      (do (<- module-name str (source-module-name func.__globals__ file))
          (<- portable str (portable-source-path module-name file))
          (<- code CodeType (portable-code (get reduced 1 0) portable))
          (for [names #((get reduced 1 1) (get reduced 2 1 "__globals__"))]
            (when (in "__file__" names)
              (setv (get names "__file__") portable))
            (when (in "__path__" names)
              (setv (get names "__path__") [(posixpath.dirname portable)])))
          (+ #((get reduced 0) (+ #(code) (cut (get reduced 1) 1 None))) (cut reduced 2 None)))))


(defclass StrictPickler [cloudpickle.CloudPickler]
  "cloudpickle の既定から、file を運ぶ規則だけを外した pickler。file は送り手で断る(意味が黙って変わるため)。
   handler の値(doeff.program.handler が作る物 — 印 __doeff_handler_data__)も断る(ADR-DOE-CLUSTER-001 R3b・改訂 1 の D):
   handler は job の Program の中(defk の本体)で関数を呼んで作り、値として宣言や task に詰めない。送り手(declare・RemoteJob・
   SubmitDetached)はどれもここを通る。値ごと詰める関数は source の名を置き場に依らない名へ替えて詰める(頭注の 1)。"
  (setv dispatch-table
    (collections.ChainMap
      (dfor t #(io.TextIOWrapper io.BufferedReader io.BufferedWriter io.BufferedRandom io.FileIO) t _refuse-file)
      CLOUDPICKLE-DISPATCH))

  ;; obj は pickle が詰めようとしている値そのもの(どの値にもなる)。答えは pickle の reduce の組か NotImplemented(cloudpickle の規則)。
  (defn #^ (| tuple NotImplementedType) reducer-override [self #^ object obj]  ; defk にできない: pickle の library が呼ぶ callback
    "handler の値に当たったら断り、それ以外は cloudpickle の規則に任せる。値ごと詰める関数(cloudpickle の規則が reduce の組を返した
     関数)は、その組を置き場に依らない組へ替える(portable-reduction)。"
    (when (and (callable obj) (not (isinstance obj type)) (hasattr obj "__doeff_handler_data__"))
      (raise (TypeError (.format "handler の値 {} を捕まえている — handler は Program の本体の中で関数を呼んで作る(値として詰めない)"
                                 (getattr obj "__qualname__" (repr obj))))))
    (setv reduced (.reducer-override (super) obj))
    (if (and (isinstance obj FunctionType) (isinstance reduced tuple))
        (run (portable-reduction obj reduced))
        reduced)))


;; pickle の命令の名の組(pickletools の名)。文字列を積む命令・bytes を積む命令・memo に覚える命令・memo から積む命令。
(val TEXT-OPCODES (frozenset ["SHORT_BINUNICODE" "BINUNICODE" "BINUNICODE8"]))
(val BYTES-OPCODES (frozenset ["SHORT_BINBYTES" "BINBYTES" "BINBYTES8"]))
(val PUT-OPCODES (frozenset ["MEMOIZE" "PUT" "BINPUT" "LONG_BINPUT"]))
(val GET-OPCODES (frozenset ["GET" "BINGET" "LONG_BINGET"]))


(defk pickle-ops [data]
  {:pre [(: data bytes)] :post [(: % list)] :tags {:context "doeff-cluster" :role "protocol"}}
  "詰めた流れ data を命令の並びに割るため: 命令 1 つ = #(名 引数 その命令の bytes)。frame の命令は落とす(並べ直した流れの frame は
   pickletools.optimize が組み直す)。"
  (val listed (list (pickletools.genops data)))
  (val ends (+ (lfor #(_ _ pos) (cut listed 1 None) pos) [(len data)]))
  (lfor #(#(op arg pos) end) (zip listed ends) :if (!= op.name "FRAME") #(op.name arg (cut data pos end))))


(defk memo-indices [ops]
  {:pre [(: ops list)] :post [(: % list)] :tags {:context "doeff-cluster" :role "protocol"}}
  "命令の並び ops(pickle-ops の答え)の各命令が memo に覚える番号(memo に覚えない命令は None)を知るため。MEMOIZE はそれまでに覚えた
   数(pickle の Unpickler と同じ数え方 — pickler の出力は同じ番号を 2 度覚えない)・ほかの覚える命令は引数の番号。"
  (val before (list (itertools.accumulate (gfor #(name _ _) ops (int (in name PUT-OPCODES))) :initial 0)))
  (lfor #(i #(name arg _)) (enumerate ops)
        (cond (= name "MEMOIZE") (get before i)
              (in name PUT-OPCODES) arg
              True None)))


(defk memo-aliases [ops indices]
  {:pre [(: ops list) (: indices list)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "protocol"}}
  "同じ値の文字列・bytes を重ねて覚えた memo の番号 → その値を最初に覚えた番号の表を作るため(canonical-pickle の読み替えの表)。
   ops = 命令の並び(pickle-ops の答え)・indices = 各命令が覚える番号(memo-indices の答え)。文字列・bytes を積む命令と、その直後の
   覚える命令の組だけを見る。"
  (val stored (lfor i (range (- (len ops) 1))
                    :if (and (or (in (get ops i 0) TEXT-OPCODES) (in (get ops i 0) BYTES-OPCODES)) (is-not (get indices (+ i 1)) None))
                    #(#((in (get ops i 0) TEXT-OPCODES) (get ops i 1)) (get indices (+ i 1)))))
  ;; #(文字列か 値) → その値を最初に覚えた番号(逆順に重ねるので、先に覚えた番号が残る)
  (val first-index (dfor #(key index) (reversed stored) key index))
  (dfor #(key index) stored :if (!= (get first-index key) index) index (get first-index key)))


(defk canonical-pickle [data]
  {:pre [(: data bytes)] :post [(: % bytes)] :tags {:context "doeff-cluster" :role "protocol"}}
  "詰めた流れ data を、物の共有に依らない正準の形にするため(頭注の 2)。同じ値の文字列・bytes を 2 度目からは書かず、最初に覚えた memo
   を指す命令に替え、その覚える命令は消す(読み替えの表 = memo-aliases)。並べ直した流れは memo の番号を明示する命令(LONG_BINPUT・
   LONG_BINGET)で書き、pickletools.optimize が指されない memo を消し、番号を使う順に振り直し、frame を組み直す。同じ値の木なら、
   どの文字列が同じ object だったかに依らず同じ bytes になる。"
  (<- ops list (pickle-ops data))
  (<- indices list (memo-indices ops))
  (<- aliases dict (memo-aliases ops indices))
  (val last (- (len ops) 1))
  (val rewritten
    (gfor #(i #(name arg raw)) (enumerate ops)
          (cond
            (in name PUT-OPCODES)
              (if (in (get indices i) aliases) b"" (+ pickle.LONG-BINPUT (struct.pack "<I" (get indices i))))
            (in name GET-OPCODES)
              (+ pickle.LONG-BINGET (struct.pack "<I" (.get aliases arg arg)))
            (and (< i last) (in (get indices (+ i 1)) aliases))
              (+ pickle.LONG-BINGET (struct.pack "<I" (get aliases (get indices (+ i 1)))))
            True raw)))
  (pickletools.optimize (.join b"" rewritten)))


(defn #^ bytes _dumps [program]
  "Program を cloudpickle で詰め、正準の形(canonical-pickle)にした bytes を作るため(詰め方の定義点 — encode-program が呼ぶ)。"
  (setv buffer (io.BytesIO))
  (.dump (StrictPickler buffer :protocol cloudpickle.DEFAULT-PROTOCOL) program)
  (run (canonical-pickle (.getvalue buffer))))


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
