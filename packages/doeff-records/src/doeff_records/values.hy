;;; 記録の仕組みの値の型 — 表と追記の列の宣言・書きの期待・effect の答え(成功と失敗)。data だけで I/O を行わない。
;;;
;;; 失敗は例外ではなく答えの値で返す(成功の型と失敗の型の判別可能な union)。例外で上がるのは組み立ての誤り
;;; (宣言に無い表・列を読む — UndeclaredTable)と、handler の実装の誤りだけ。
;;;
;;; 行の鍵は key-fields の順の文字列の tuple。行の値(value)は欄の名 → JSON の値の凍らせた写像(doeff-hy の FrozenMap —
;;; object は FrozenMap・array は tuple まで深く凍らせる)で、鍵の欄も値に含む(行を作る時に handler が鍵の欄を値に置く)。
;;; 値は None を持たない — PutRow の差分の None は「その欄を消す」(JSON merge patch の null)。
;;; 値を持つ型は作る時に受けた写像を凍らせる(実行時は dict を渡しても、欄には FrozenMap が入る — 型の注記は FrozenMap なので、
;;; 静的な検査は呼び手に FrozenMap / frozen-json-object で包ませる)。JSON へ書く境界は thaw-json で戻す。
;;; この汎用の層の上に、表ごとの行の型(pydantic の model か dataclass)で読み書きする層が typed.hy に在る — 業務の呼び手はそちらを使う。
(import dataclasses [dataclass field])
(import re)
(import doeff_hy.frozen [FrozenMap freeze-json frozen-json-object frozen-map-of])

(setv TABLE-NAME-PATTERN (re.compile "^[a-z][a-z0-9_-]{0,62}$"))
(setv FIELD-NAME-PATTERN (re.compile "^[A-Za-z_][A-Za-z0-9_]{0,62}$"))


(defclass UndeclaredTable [ValueError]
  "宣言に無い表・追記の列を名指した(組み立ての誤り — 値の失敗ではない)。")

(defclass UndeclaredField [ValueError]
  "表の宣言に無い欄の書き手を尋ねた(組み立ての誤り — 書きの断りは admission が Refused で返す)。")


(defn #^ str checked-table-name [#^ str name #^ str what]
  (when (not (and (isinstance name str) (.match TABLE-NAME-PATTERN name)))
    (raise (ValueError (.format "{} は英小文字で始まる英小文字・数字・_・- の 63 字まで: {!r}" what name))))
  name)


(defn #^ str checked-field-name [#^ str name #^ str what]
  (when (not (and (isinstance name str) (.match FIELD-NAME-PATTERN name)))
    (raise (ValueError (.format "{} は英字か _ で始まる英数字と _ の 63 字まで: {!r}" what name))))
  name)


(defn #^ None freeze-field [#^ object instance #^ str name #^ str what]
  "frozen の dataclass の欄 name の写像を、深く凍らせた FrozenMap に置き換える(__post_init__ から撃つ)。"
  (object.__setattr__ instance name (frozen-json-object (getattr instance name) what))
  None)


(defn #^ tuple checked-names [#^ object names #^ str what]
  (when (not (isinstance names tuple))
    (raise (TypeError (.format "{} は tuple: {!r}" what names))))
  (for [name names] (checked-field-name name what))
  names)


;; --- 保持 ------------------------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] KeepForever []
  "行・出来事を消さない(record)。")

(defclass [(dataclass :frozen True)] KeepFor []
  "表: 終端の状態になった行を seconds 秒の後に消す(transient)。追記の列: 積んでから seconds 秒の後に消す。"
  (#^ float seconds)
  (defn __post_init__ [self]
    (when (or (isinstance self.seconds bool) (not (isinstance self.seconds #(int float))) (<= self.seconds 0))
      (raise (ValueError (.format "KeepFor.seconds は正の数: {!r}" self.seconds))))))

(setv Retention (| KeepForever KeepFor))


;; --- 表の宣言 ------------------------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] FieldDecl []
  "表の欄 1 つの宣言: name = 欄の名 / writers = その欄を書いてよい書き手の名(空でない文字列の空でない tuple)。"
  (#^ str name)
  (#^ tuple writers)
  (defn __post_init__ [self]
    (checked-field-name self.name "FieldDecl.name")
    (when (not (and (isinstance self.writers tuple) self.writers (all (gfor n self.writers (and (isinstance n str) n)))))
      (raise (ValueError (.format "FieldDecl.writers[{!r}] は空でない文字列の空でない tuple: {!r}" self.name self.writers))))))


(defclass [(dataclass :frozen True)] TableDecl []
  "表の宣言(composition root で渡す data)。
   name = 表の名 / key-fields = 鍵の欄(順つき)/ fields = 欄の宣言(FieldDecl — 欄の名とその欄を書いてよい書き手)の tuple
   (宣言した欄はこれで全部。行を作る = 鍵の欄を書く、なので鍵の欄の書き手 = 行を作ってよい書き手)/
   indexes = ListRows の where に使える欄(鍵の欄は常に使える)/
   state-field = 状態の語を持つ欄(states が空なら使わない)/ states・terminal・initial = 状態の語彙・終端の語・生まれる行の語 /
   operator-paths = 書くのに承認の要る欄 / retention = KeepForever | KeepFor / size-budget = 行の値の JSON の byte の上限(None = 無し)。"
  (#^ str name)
  (#^ tuple key-fields)
  (#^ tuple fields)
  (setv #^ tuple indexes #())
  (setv #^ str state-field "state")
  (setv #^ tuple states #())
  (setv #^ tuple terminal #())
  (setv #^ (| str None) initial None)
  (setv #^ tuple operator-paths #())
  (setv #^ object retention (KeepForever))
  (setv #^ (| int None) size-budget None)
  (defn __post_init__ [self]
    (checked-table-name self.name "TableDecl.name")
    (checked-names self.key-fields "TableDecl.key_fields")
    (when (not self.key-fields) (raise (ValueError "TableDecl.key_fields は 1 つ以上")))
    (when (not (and (isinstance self.fields tuple) (all (gfor f self.fields (isinstance f FieldDecl)))))
      (raise (TypeError (.format "TableDecl.fields は FieldDecl の tuple: {!r}" self.fields))))
    (setv names (lfor f self.fields f.name))
    (when (!= (len names) (len (set names)))
      (raise (ValueError (.format "TableDecl.fields の欄の名が重なる: {!r}" names))))
    (for [name self.key-fields]
      (when (not (self.declares name))
        (raise (ValueError (.format "鍵の欄 {!r} の書き手(= 行を作ってよい書き手)が fields に無い" name)))))
    (checked-names self.indexes "TableDecl.indexes")
    (for [name self.indexes]
      (when (not (self.declares name)) (raise (ValueError (.format "索引の欄 {!r} が fields に無い(宣言の外の欄)" name)))))
    (checked-names self.operator-paths "TableDecl.operator_paths")
    (for [name self.operator-paths]
      (when (not (self.declares name)) (raise (ValueError (.format "承認の欄 {!r} が fields に無い" name))))
      (when (in name self.key-fields) (raise (ValueError (.format "鍵の欄 {!r} を承認の欄にはできない" name)))))
    (checked-field-name self.state-field "TableDecl.state_field")
    (when self.states
      (when (not (all (gfor s self.states (and (isinstance s str) s)))) (raise (ValueError "TableDecl.states は空でない文字列")))
      (when (not (self.declares self.state-field))
        (raise (ValueError (.format "状態の欄 {!r} が fields に無い" self.state-field))))
      (when (in self.state-field self.key-fields) (raise (ValueError "状態の欄を鍵の欄にはできない")))
      (when (not-in self.initial self.states)
        (raise (ValueError (.format "initial {!r} が states {!r} に無い" self.initial self.states))))
      (when (in self.initial self.terminal) (raise (ValueError "initial を終端の語にはできない"))))
    (when (not (all (gfor s self.terminal (in s self.states))))
      (raise (ValueError (.format "terminal {!r} は states {!r} の部分" self.terminal self.states))))
    (when (and (not self.states) (is-not self.initial None)) (raise (ValueError "states が空なら initial は None")))
    (when (not (isinstance self.retention #(KeepForever KeepFor)))
      (raise (TypeError "TableDecl.retention は KeepForever | KeepFor")))
    (when (and (isinstance self.retention KeepFor) (not self.terminal))
      (raise (ValueError "KeepFor の表は終端の語(terminal)を持つ")))
    (when (and (is-not self.size-budget None)
               (or (isinstance self.size-budget bool) (not (isinstance self.size-budget int)) (<= self.size-budget 0)))
      (raise (ValueError (.format "TableDecl.size_budget は正の整数か None: {!r}" self.size-budget)))))

  (defn #^ tuple field-names [self]
    "宣言した欄の名(宣言の順)。"
    (tuple (gfor f self.fields f.name)))

  (defn #^ bool declares [self #^ str name]
    "name が宣言した欄か。"
    (any (gfor f self.fields (= f.name name))))

  (defn #^ tuple writers-of [self #^ str name]
    "欄 name を書いてよい書き手の名(宣言の外の欄は UndeclaredField)。"
    (for [f self.fields]
      (when (= f.name name) (return f.writers)))
    (raise (UndeclaredField (.format "表 {} の宣言の外の欄: {!r}" self.name name)))))


(defclass [(dataclass :frozen True)] StreamDecl []
  "追記の列の宣言。writers = 積んでよい書き手の名 / retention = KeepForever | KeepFor(積んでから秒)/
   size-budget = 本文の JSON の byte の上限(None = 無し)。"
  (#^ str name)
  (#^ tuple writers)
  (setv #^ object retention (KeepForever))
  (setv #^ (| int None) size-budget None)
  (defn __post_init__ [self]
    (checked-table-name self.name "StreamDecl.name")
    (when (not (and (isinstance self.writers tuple) self.writers (all (gfor n self.writers (and (isinstance n str) n)))))
      (raise (ValueError (.format "StreamDecl.writers は空でない文字列の空でない tuple: {!r}" self.writers))))
    (when (not (isinstance self.retention #(KeepForever KeepFor)))
      (raise (TypeError "StreamDecl.retention は KeepForever | KeepFor")))
    (when (and (is-not self.size-budget None)
               (or (isinstance self.size-budget bool) (not (isinstance self.size-budget int)) (<= self.size-budget 0)))
      (raise (ValueError (.format "StreamDecl.size_budget は正の整数か None: {!r}" self.size-budget))))))


(defclass [(dataclass :frozen True)] RecordsSchema []
  "置き場 1 つの宣言の全部: tables = 表の名 → TableDecl・streams = 列の名 → StreamDecl(どちらも凍らせた写像 —
   作る時に受けた写像を写し取る)。"
  (setv #^ (get FrozenMap TableDecl) tables (field :default-factory FrozenMap))
  (setv #^ (get FrozenMap StreamDecl) streams (field :default-factory FrozenMap))
  (defn __post_init__ [self]
    (object.__setattr__ self "tables" (frozen-map-of self.tables "RecordsSchema.tables"))
    (object.__setattr__ self "streams" (frozen-map-of self.streams "RecordsSchema.streams"))
    (for [#(name decl) (.items self.tables)]
      (when (not (and (isinstance decl TableDecl) (= decl.name name)))
        (raise (ValueError (.format "RecordsSchema.tables[{!r}] は同じ名の TableDecl" name)))))
    (for [#(name decl) (.items self.streams)]
      (when (not (and (isinstance decl StreamDecl) (= decl.name name)))
        (raise (ValueError (.format "RecordsSchema.streams[{!r}] は同じ名の StreamDecl" name))))))

  (defn #^ TableDecl table [self #^ str name]
    (when (not-in name self.tables) (raise (UndeclaredTable (.format "宣言に無い表: {!r}" name))))
    (get self.tables name))

  (defn #^ StreamDecl stream [self #^ str name]
    (when (not-in name self.streams) (raise (UndeclaredTable (.format "宣言に無い追記の列: {!r}" name))))
    (get self.streams name)))


;; --- 書きの期待と承認 --------------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] ExpectAbsent []
  "行が無い時だけ書く。")

(defclass [(dataclass :frozen True)] ExpectVersion []
  "行の版が version の時だけ書く。"
  (#^ int version)
  (defn __post_init__ [self]
    (when (or (isinstance self.version bool) (not (isinstance self.version int)) (< self.version 1))
      (raise (ValueError (.format "ExpectVersion.version は 1 以上の整数: {!r}" self.version))))))

(defclass [(dataclass :frozen True)] ExpectAny []
  "無条件に書く(同じ行の他の書きを上書きしてよい時だけ)。")

(setv Expectation (| ExpectAbsent ExpectVersion ExpectAny))

(defclass [(dataclass :frozen True)] Approval []
  "承認の欄を書く時に添える承認の印(確かめ方は handler を組む時に渡す)。"
  (#^ str token))


;; --- 位置(cursor) ------------------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] WatchCursor []
  "変更の列の位置: epoch = 置き場の版 / sequence = ここまで読んだ変更の番号。"
  (#^ int epoch)
  (#^ int sequence))

(defclass [(dataclass :frozen True)] ListCursor []
  "ListRows の次の頁の位置: epoch = 置き場の版 / after-key = 前の頁の最後の行の鍵の綴り。"
  (#^ int epoch)
  (#^ str after-key))


;; --- 成功の答え --------------------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Row []
  "行 1 つ: key = 鍵(key-fields の順)/ value = 欄 → 値の凍らせた写像(鍵の欄を含む)/
   version = 書かれるたびに 1 増える版(生まれた行は 1)。"
  (#^ tuple key)
  (#^ FrozenMap value)
  (#^ int version)
  (defn __post_init__ [self] (freeze-field self "value" "Row.value")))

(defclass [(dataclass :frozen True)] Missing []
  "行が無い。")

(defclass [(dataclass :frozen True)] Page []
  "ListRows の 1 頁: rows = 鍵の綴りの順の Row の tuple / next-cursor = 次の頁の位置(None = 終わり)/
   epoch・sequence = この頁を読んだ時の置き場の版と変更の番号(最初の頁の値から WatchChanges を始めると取りこぼしが無い)。"
  (#^ tuple rows)
  (#^ (| ListCursor None) next-cursor)
  (#^ int epoch)
  (#^ int sequence))

(defclass [(dataclass :frozen True)] Written []
  "PutRow が確定した: version = 新しい版・value = 確定した行の値(凍らせた写像)。"
  (#^ int version)
  (#^ FrozenMap value)
  (defn __post_init__ [self] (freeze-field self "value" "Written.value")))

(defclass [(dataclass :frozen True)] RowChanged []
  "変更 1 つ: 行が書かれた(作られた・更新された)。value(凍らせた写像)と version は確定した後の値。"
  (#^ str table)
  (#^ tuple key)
  (#^ int version)
  (#^ FrozenMap value)
  (#^ int sequence)
  (defn __post_init__ [self] (freeze-field self "value" "RowChanged.value")))

(defclass [(dataclass :frozen True)] RowRemoved []
  "変更 1 つ: 行が保持の期限で消えた。"
  (#^ str table)
  (#^ tuple key)
  (#^ int sequence))

(defclass [(dataclass :frozen True)] Changes []
  "WatchChanges の答え: items = 頼んだ表の変更(sequence の昇順・確定した変更ちょうど 1 回ずつ)/ cursor = 次に渡す位置。"
  (#^ tuple items)
  (#^ WatchCursor cursor))

(defclass [(dataclass :frozen True)] Appended []
  "AppendEvent が確定した(同じ冪等キーの再送は前の sequence)。"
  (#^ int sequence))

(defclass [(dataclass :frozen True)] Event []
  "追記の列の出来事 1 つ。body = JSON の値(深く凍らせる)/ at = 積んだ時刻(epoch ミリ秒)/ writer = 積んだ書き手の名。"
  (#^ str stream)
  (#^ int sequence)
  (#^ str idempotency-key)
  (#^ object body)
  (#^ str writer)
  (#^ int at)
  (defn __post_init__ [self] (object.__setattr__ self "body" (freeze-json self.body))))

(defclass [(dataclass :frozen True)] Events []
  "ReadEvents の答え: items = after より後の出来事(sequence の昇順)/ last-sequence = 次に渡す after。"
  (#^ tuple items)
  (#^ int last-sequence))


;; --- 失敗の答え --------------------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Conflict []
  "PutRow の期待が今の行と合わない。current = 今の行(Row | Missing)— 読み直して導き直すのは呼び手。"
  (#^ (| Row Missing) current))

(defclass [(dataclass :frozen True)] Refused []
  "宣言が書きを許さない(書き手でない欄・宣言の外の欄・終端の行・状態の語彙の外・上限・承認が無い・冪等キーの別の本文)。"
  (#^ str reason))

(defclass [(dataclass :frozen True)] Unreachable []
  "置き場に届かない(結末は不明 — 読みは撃ち直してよい。書きは期待つきなら撃ち直してよい)。"
  (#^ str detail))

(defclass [(dataclass :frozen True)] NotIndexed []
  "ListRows の where が索引の無い欄を名指した。"
  (#^ tuple fields))

(defclass [(dataclass :frozen True)] Reset []
  "位置の epoch が置き場の版と違う(置き場が作り直された・変更の列が刈られた)— 一覧から読み直す。"
  (#^ int epoch))


(setv ReadRowAnswer (| Row Missing Unreachable))
(setv ListRowsAnswer (| Page Reset Unreachable NotIndexed))
(setv PutRowAnswer (| Written Conflict Refused Unreachable))
(setv WatchChangesAnswer (| Changes Reset Unreachable))
(setv AppendEventAnswer (| Appended Refused Unreachable))
(setv ReadEventsAnswer (| Events Unreachable))
