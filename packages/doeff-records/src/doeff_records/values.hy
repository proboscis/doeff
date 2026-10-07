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
(require doeff-hy.macros [val deff])
(require doeff-hy.record [defrecord])
(import copy)
(import dataclasses [dataclass field])
(import re)
(import doeff_hy.frozen [FrozenMap freeze-json frozen-json-object frozen-map-of])

(setv TABLE-NAME-PATTERN (re.compile "^[a-z][a-z0-9_-]{0,62}$"))
(setv FIELD-NAME-PATTERN (re.compile "^[A-Za-z_][A-Za-z0-9_]{0,62}$"))
;; 組で数える列の区切り(ByKeySuffix.separator)の字 — PostgreSQL の置き場は区切りを組の名の式の索引と文の式に文字列の literal で直に置く
;; (pg_sql.key-suffix-of・#3614)ので、引用符・逆斜線・空白・%・$・波括弧・ASCII の外を含まない 1〜16 字だけを許す(欄の名の FIELD-NAME-PATTERN と
;; 同じ考え — 宣言で断り、文の組み立ては検めた値だけを受ける)。
(val SEPARATOR-PATTERN (re.compile "^[A-Za-z0-9_.:;,/|#@=+~^!?*&<>-]{1,16}$"))


(defclass UndeclaredTable [ValueError]
  "宣言に無い表・追記の列を名指した(組み立ての誤り — 値の失敗ではない)。tables・streams = 宣言に無いと分かった表・追記の列の名
   (分からない時は空 — 文だけで作った旧い形・どの名か決められない断り)。読み手はどの表の断りかを欄で照らし、文の綴りを読まない
   (使い手が、無くても仕事を続けられる表の断りだけを「出していない」答えに畳み、他の表の断りでは落ちるため)。"
  (defn #^ None __init__ [self #^ str message * #^ tuple [tables #()] #^ tuple [streams #()]]
    (.__init__ (super) message)
    (setv self.tables (tuple tables)
          self.streams (tuple streams)))
  (defn #^ tuple __reduce__ [self]
    ;; 欄は __init__ の引数(キーワードだけ)なので、例外の既定の写し(args だけで作り直す)では落ちる — 欄を状態として運ぶ。
    #(UndeclaredTable #((get self.args 0)) {"tables" self.tables "streams" self.streams})))

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
  "表: 終端の状態になった行を seconds 秒の後に消す(transient)。追記の列: 積んでから seconds 秒の後に消す。
   期限を過ぎた行と出来事は、その刻からどの読みにも出ない(ReadRow は Missing・ListRows と ReadEvents は除く・WatchChanges は行の今の値が
   期限を過ぎた行の変わりを出さない・WatchEvents は動かない・ReadStreamEnd は数えない・ReadEventByKey は EventRetired)。置き場から消して変更の列に RowRemoved を積むのは
   回収で、回収は SweepExpired(手入れの係)の時だけ走る。書き(PutRow・PutRows・AppendEvent)は自分が触る行と出来事だけを回収と同じく
   片付けてから判じる(#3605 の D — 期限を過ぎた行への書きは、その行の RowRemoved を積んでから無い行として判じる)。RowRemoved は期限の刻
   でも読みの時でもなく、期限の後の SweepExpired(かその行への書き)の時に積まれ、既に WatchChanges で行を受け取った読み手の写しには
   その時まで行が残り得る(#3561)。"
  (#^ float seconds)
  (defn #^ None __post_init__ [self]
    (when (or (isinstance self.seconds bool) (not (isinstance self.seconds #(int float))) (<= self.seconds 0))
      (raise (ValueError (.format "KeepFor.seconds は正の数: {!r}" self.seconds))))))

(setv Retention (| KeepForever KeepFor))

;; 追記の列の保持を数える単位(StreamDecl.retention-group)— 結末が残る間に要求だけが消えると、要求の再送が新しい出来事になり、結末の再送は古い番号を返す。
(defclass [(dataclass :frozen True)] EachEvent []
  "出来事ごとに、積んだ刻から保持の秒を数える(既定)。")

(defclass [(dataclass :frozen True)] ByKeySuffix []
  "冪等キーの区切り separator より後ろが同じ出来事を 1 組にし、組の最後の出来事を積んだ刻から保持の秒を数える — 組の出来事は
   同時に消える(例: 区切り「:」で request:<id> と settled:<id> を 1 組にすると、結末が残る間は要求も残る)。区切りを含まない冪等キーは
   キー全体が組の名。区切りの字は SEPARATOR-PATTERN(英数字と記号 _.:;,/|#@=+~^!?*&<>- の 1〜16 字)。"
  (#^ str separator)
  (defn #^ None __post_init__ [self]
    (when (not (and (isinstance self.separator str) (.match SEPARATOR-PATTERN self.separator)))
      (raise (ValueError (.format "ByKeySuffix.separator は英数字と記号 _.:;,/|#@=+~^!?*&<>- の 1〜16 字: {!r}" self.separator))))))

(setv RetentionGroup (| EachEvent ByKeySuffix))


;; --- 表の宣言 ------------------------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] FieldDecl []
  "表の欄 1 つの宣言: name = 欄の名 / writers = その欄を書いてよい書き手の名(空でない文字列の空でない tuple)/
   founders = 行の誕生の書き(行がまだ無い時)に限ってその欄を書いてよい書き手(空でない文字列の tuple・既定 = 無し)。
   writers と founders は宣言だけで、置き場の書きの判断は読まない(書き手の名では断らない・#2994)。"
  (#^ str name)
  (#^ tuple writers)
  (setv #^ tuple founders #())
  (defn #^ None __post_init__ [self]
    (checked-field-name self.name "FieldDecl.name")
    (when (not (and (isinstance self.writers tuple) self.writers (all (gfor n self.writers (and (isinstance n str) n)))))
      (raise (ValueError (.format "FieldDecl.writers[{!r}] は空でない文字列の空でない tuple: {!r}" self.name self.writers))))
    (when (not (and (isinstance self.founders tuple) (all (gfor n self.founders (and (isinstance n str) n)))))
      (raise (ValueError (.format "FieldDecl.founders[{!r}] は空でない文字列の tuple: {!r}" self.name self.founders))))))


(defclass [(dataclass :frozen True)] TableDecl []
  "表の宣言(composition root で渡す data)。
   name = 表の名 / key-fields = 鍵の欄(順つき)/ fields = 欄の宣言(FieldDecl — 欄の名とその欄を書いてよい書き手)の tuple
   (宣言した欄はこれで全部。行を作る = 鍵の欄を書く、なので鍵の欄の書き手 = 行を作ってよい書き手)/
   indexes = ListRows の where に使える欄(鍵の欄は常に使える)/
   state-field = 状態の語を持つ欄(states が空なら使わない)/ states・terminal・initial = 状態の語彙・終端の語・生まれる行の語 /
   operator-paths = operator の宣言の欄(宣言だけ — 置き場の書きの判断は読まない・#2994)/ retention = KeepForever | KeepFor / size-budget = 行の値の JSON の byte の上限(None = 無し)。"
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
  (defn #^ None __post_init__ [self]
    (checked-table-name self.name "TableDecl.name")
    (checked-names self.key-fields "TableDecl.key_fields")
    (when (not self.key-fields) (raise (ValueError "TableDecl.key_fields は 1 つ以上")))
    (when (not (and (isinstance self.fields tuple) (all (gfor f self.fields (isinstance f FieldDecl)))))
      (raise (TypeError (.format "TableDecl.fields は FieldDecl の tuple: {!r}" self.fields))))
    (setv names (lfor f self.fields f.name))
    (when (!= (len names) (len (set names)))
      (raise (ValueError (.format "TableDecl.fields の欄の名が重なる: {!r}" names))))
    ;; 欄の名 → 欄の宣言の索引を宣言 1 つに 1 度だけ作る(下の declares・writers-of・founders-of が引く)— 書きの判断が行ごと・欄ごとに
    ;; 欄の tuple を全部なめ直さないため(#2670 根 E — 5 万行の置き場への書きで欄の数の 2 乗の比べが走っていた)。dataclass の欄には
    ;; しない(等しさ・hash・表示は宣言の欄だけで決まる)。pickle と copy は __setstate__ が作り直す。
    (object.__setattr__ self "_by_name" (dict (zip names self.fields)))
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

  (deff __setstate__ [self state]  ; defk にできない: pickle と copy が呼ぶ class の口(同期の呼び — Program を実行しない)
    {:pre [(: self TableDecl) (: state dict)] :post [(: % None)] :tags {:context "records" :role "type"}}
    "pickle / copy から戻す時に中身を入れ、欄の名の索引を作り直すため(索引を持つ前に pickle した宣言も引けるように)。"
    (.update self.__dict__ state)
    (object.__setattr__ self "_by_name" (dict (gfor f self.fields #(f.name f)))))

  (defn #^ bool declares [self #^ str name]
    "name が宣言した欄か。"
    (in name self._by-name))

  (defn #^ tuple writers-of [self #^ str name]
    "欄 name を書いてよい書き手の名(宣言の外の欄は UndeclaredField)。"
    (setv found (.get self._by-name name))
    (when (is found None)
      (raise (UndeclaredField (.format "表 {} の宣言の外の欄: {!r}" self.name name))))
    found.writers)

  (defn #^ tuple founders-of [self #^ str name]
    "欄 name を行の誕生の書きに限って書いてよい書き手の名(FieldDecl.founders・宣言の外の欄は UndeclaredField)。"
    (setv found (.get self._by-name name))
    (when (is found None)
      (raise (UndeclaredField (.format "表 {} の宣言の外の欄: {!r}" self.name name))))
    found.founders))


(defclass [(dataclass :frozen True)] StreamDecl []
  "追記の列の宣言。writers = 積んでよい書き手の名 / retention = KeepForever | KeepFor(積んでから秒)/
   size-budget = 本文の JSON の byte の上限(None = 無し)/ retention-group = 保持を数える単位(EachEvent | ByKeySuffix — ByKeySuffix は
   KeepFor の列だけ)。"
  (#^ str name)
  (#^ tuple writers)
  (setv #^ object retention (KeepForever))
  (setv #^ (| int None) size-budget None)
  (setv #^ object retention-group (EachEvent))
  (defn #^ None __post_init__ [self]
    (checked-table-name self.name "StreamDecl.name")
    (when (not (and (isinstance self.writers tuple) self.writers (all (gfor n self.writers (and (isinstance n str) n)))))
      (raise (ValueError (.format "StreamDecl.writers は空でない文字列の空でない tuple: {!r}" self.writers))))
    (when (not (isinstance self.retention #(KeepForever KeepFor)))
      (raise (TypeError "StreamDecl.retention は KeepForever | KeepFor")))
    (when (not (isinstance self.retention-group #(EachEvent ByKeySuffix)))
      (raise (TypeError "StreamDecl.retention_group は EachEvent | ByKeySuffix")))
    (when (and (isinstance self.retention-group ByKeySuffix) (not (isinstance self.retention KeepFor)))
      (raise (ValueError "StreamDecl.retention_group の ByKeySuffix は KeepFor の列だけ(消えない列に組は要らない)")))
    (when (and (is-not self.size-budget None)
               (or (isinstance self.size-budget bool) (not (isinstance self.size-budget int)) (<= self.size-budget 0)))
      (raise (ValueError (.format "StreamDecl.size_budget は正の整数か None: {!r}" self.size-budget))))))


(defclass [(dataclass :frozen True)] RecordsSchema []
  "置き場 1 つの宣言の全部: tables = 表の名 → TableDecl・streams = 列の名 → StreamDecl(どちらも凍らせた写像 —
   作る時に受けた写像を写し取る)/ operators = operator の主体の名(身元の名簿の名 = 書き手の名)の tuple。
   operators と表の operator-paths は宣言だけで、置き場の書きの判断は読まない(書き手の名では断らない・#2994)。"
  (setv #^ (get FrozenMap TableDecl) tables (field :default-factory FrozenMap))
  (setv #^ (get FrozenMap StreamDecl) streams (field :default-factory FrozenMap))
  (setv #^ tuple operators #())
  (defn #^ None __post_init__ [self]
    (object.__setattr__ self "tables" (frozen-map-of self.tables "RecordsSchema.tables"))
    (object.__setattr__ self "streams" (frozen-map-of self.streams "RecordsSchema.streams"))
    (when (not (and (isinstance self.operators tuple)
                    (all (gfor n self.operators (and (isinstance n str) n (not-in ":" n))))))
      (raise (ValueError (.format "RecordsSchema.operators は「:」を含まない空でない文字列の tuple: {!r}" self.operators))))
    (for [#(name decl) (.items self.tables)]
      (when (not (and (isinstance decl TableDecl) (= decl.name name)))
        (raise (ValueError (.format "RecordsSchema.tables[{!r}] は同じ名の TableDecl" name))))
      ;; operator の欄の書き手に operator の主体が 1 人も居ない宣言は、誰も書けない欄を黙って作る — 宣言の時に止める。
      (for [path decl.operator-paths]
        (when (not (& (set (decl.writers-of path)) (set self.operators)))
          (raise (ValueError (.format "表 {} の operator の欄 {!r} の書き手 {!r} に operator の主体 {!r} が居ない"
                                      name path (decl.writers-of path) self.operators))))))
    (for [#(name decl) (.items self.streams)]
      (when (not (and (isinstance decl StreamDecl) (= decl.name name)))
        (raise (ValueError (.format "RecordsSchema.streams[{!r}] は同じ名の StreamDecl" name))))))

  (defn #^ TableDecl table [self #^ str name]
    (when (not-in name self.tables) (raise (UndeclaredTable (.format "宣言に無い表: {!r}" name) :tables #(name))))
    (get self.tables name))

  (defn #^ StreamDecl stream [self #^ str name]
    (when (not-in name self.streams) (raise (UndeclaredTable (.format "宣言に無い追記の列: {!r}" name) :streams #(name))))
    (get self.streams name)))


;; --- 書きの期待 -----------------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] ExpectAbsent []
  "行が無い時だけ書く。")

(defclass [(dataclass :frozen True)] ExpectVersion []
  "行の版が version の時だけ書く。"
  (#^ int version)
  (defn #^ None __post_init__ [self]
    (when (or (isinstance self.version bool) (not (isinstance self.version int)) (< self.version 1))
      (raise (ValueError (.format "ExpectVersion.version は 1 以上の整数: {!r}" self.version))))))

(defclass [(dataclass :frozen True)] ExpectAny []
  "無条件に書く(同じ行の他の書きを上書きしてよい時だけ)。")

(setv Expectation (| ExpectAbsent ExpectVersion ExpectAny))


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
  (defn #^ None __post_init__ [self] (freeze-field self "value" "Row.value"))
  (defn #^ "Row" __deepcopy__ [self #^ (get dict #(int object)) memo]
    "深い写し: 値は作る時に深く凍らせてあり(__post_init__)、鍵が文字列と整数だけなら行は中まで変えられないので自分を返す — 置き場を
     丸ごと deepcopy する使い手(5 万行の memory の置き場を検ごとに写す検の土台)が、変えられない行を作り直さないため(#2670)。
     鍵に他の型が在れば鍵を深く写した行を作る。"
    (if (all (gfor part self.key (in (type part) #(str int))))
        self
        (Row (copy.deepcopy self.key memo) self.value self.version))))

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
  (defn #^ None __post_init__ [self] (freeze-field self "value" "Written.value")))

(defclass [(dataclass :frozen True)] WrittenRows []
  "PutRows の束が全部確定した: items = 束の順の Written の tuple(束の i 番目の書きの答え = items の i 番目)。"
  (#^ tuple items)
  (defn #^ None __post_init__ [self]
    (when (not (and (isinstance self.items tuple) (all (gfor item self.items (isinstance item Written)))))
      (raise (TypeError (.format "WrittenRows.items は Written の tuple: {!r}" self.items))))))

(defclass [(dataclass :frozen True)] RowChanged []
  "変更 1 つ: 行が書かれた(作られた・更新された)。value(凍らせた写像)と version は確定した後の値 /
   at = その変更が置き場に確定した刻(epoch ミリ秒)。"
  (#^ str table)
  (#^ tuple key)
  (#^ int version)
  (#^ FrozenMap value)
  (#^ int sequence)
  (#^ int at)
  (defn #^ None __post_init__ [self] (freeze-field self "value" "RowChanged.value")))

(defclass [(dataclass :frozen True)] RowRemoved []
  "変更 1 つ: 行が保持の期限で消えた。積むのは回収(期限の後の SweepExpired か、その行への書き)— 期限の刻でも読みの時でもない(KeepFor の註)。"
  (#^ str table)
  (#^ tuple key)
  (#^ int sequence))

(defrecord StreamTail
  "WatchChanges の答えの tails の 1 つ — 名指した列 1 つの末尾(#3718): stream = 列の名 / sequence = 列に今ある、保持の期限を過ぎていない
   最後の出来事の番号(1 以上 — ReadStreamEnd の StreamEnd と同じ番号)/ at = その出来事を積んだ刻(epoch ミリ秒 — ReadEvents の Event.at と同じ)。"
  {:tags {:context "records" :role "type"}
   :check [(isinstance stream str)
           (and (isinstance sequence int) (not (isinstance sequence bool)) (>= sequence 1))
           (and (isinstance at int) (not (isinstance at bool)))]}
  #^ str stream
  #^ int sequence
  #^ int at)

(defrecord StreamTailEmpty
  "WatchChanges の答えの tails の 1 つ — 名指した列 stream に、保持の期限を過ぎていない出来事が 1 つも無い(まだ積んでいない・全部が期限を
   過ぎた — ReadStreamEnd の StreamEmpty と同じ。番号 0 と混ぜずに型で分ける・#3718)。"
  {:tags {:context "records" :role "type"}
   :check [(isinstance stream str)]}
  #^ str stream)

(defclass [(dataclass :frozen True)] Changes []
  "WatchChanges の答え: items = 頼んだ表の変更(sequence の昇順・確定した変更ちょうど 1 回ずつ)/ cursor = 次に渡す位置 /
   tails = 要求が名指した列(WatchChanges.streams)ごとの末尾(StreamTail | StreamTailEmpty — 名指した順・名指さなければ空・#3718)。
   tails は答えを返す時点の名指した列の末尾で、items と同じ置き場の断面から読む。列への追記では待ち手を起こさない — 起こしたい呼び手は
   列を源に別に待つ。"
  (#^ tuple items)
  (#^ WatchCursor cursor)
  (#^ (get tuple #((| StreamTail StreamTailEmpty) ...)) tails))

(defclass [(dataclass :frozen True)] Appended []
  "AppendEvent が確定した。sequence = 出来事の番号(同じ冪等キーの再送は前の sequence)/ replayed = 同じ冪等キーの再送で、今は積んでいない(真)か、今積んだ(偽)か(#3850 — 書き手は今積んだ時だけ知らせを出す)。"
  (#^ int sequence)
  (#^ bool replayed))

(defclass [(dataclass :frozen True)] Event []
  "追記の列の出来事 1 つ。body = JSON の値(深く凍らせる)/ at = 積んだ時刻(epoch ミリ秒)/ writer = 積んだ書き手の名。"
  (#^ str stream)
  (#^ int sequence)
  (#^ str idempotency-key)
  (#^ object body)
  (#^ str writer)
  (#^ int at)
  (defn #^ None __post_init__ [self] (object.__setattr__ self "body" (freeze-json self.body))))


(defrecord RetiredKey
  "保持の期限で出来事を消した冪等キーの覚え(#3022): idempotency-key = 冪等キー / sequence = 消した出来事の番号 /
   body-digest = その本文の指紋(admission.body-digest)。置き場(memory・PG)は出来事を消しても鍵の覚えは消さない — 消した後の
   同じ鍵の追記を、生きた出来事と同じ規則(admission.judge-append)で判じるため(覚えは育ち続けてよい — 消す仕組みは持たない)。
   置き場の判断(admission)と SQL の文(pg_sql)の両方が読む値なので、この値の module に置く。"
  #^ str idempotency-key
  #^ int sequence
  #^ str body-digest)

(defclass [(dataclass :frozen True)] Events []
  "ReadEvents の答え: items = after より後の出来事(sequence の昇順)/ last-sequence = 次に渡す after。
   items が上限 limit より少なければ、この答えを作った時に after より後の出来事は items の他に無い(上限より短い頁は列の終わり —
   法 law-short-page-ends-the-stream・#3986)。上限ちょうどの頁の後には出来事が残っていてよい。"
  (#^ tuple items)
  (#^ int last-sequence))

(defclass [(dataclass :frozen True)] EventsMoved []
  "WatchEvents の答え: 列の頭が after より進んだ(出来事は運ばない — 読み手が ReadEvents で読む)。")

(defclass [(dataclass :frozen True)] EventsQuiet []
  "WatchEvents の答え: timeout まで列の頭が after より進まなかった(読み手は読み直さずに待ちを掛け直す)。")

(defclass [(dataclass :frozen True)] StreamEnd []
  "ReadStreamEnd の答え: sequence = 列に今ある、保持の期限を過ぎていない最後の出来事の番号(期限を過ぎた出来事は回収の前でも数えない —
   1 以上)。"
  (#^ int sequence)
  (defn #^ None __post_init__ [self]
    (when (or (isinstance self.sequence bool) (not (isinstance self.sequence int)) (< self.sequence 1))
      (raise (ValueError (.format "StreamEnd.sequence は 1 以上の整数: {!r}" self.sequence))))))

(defclass [(dataclass :frozen True)] StreamEmpty []
  "ReadStreamEnd の答え: 列に出来事が 1 つも無い(まだ積んでいない・全部が保持の期限を過ぎた)— 番号 0 と混ぜずに型で分ける。")

(defclass [(dataclass :frozen True)] EventAbsent []
  "ReadEventByKey の答え: 列にその冪等キーで積んだ出来事が無い(まだ積んでいない — 消えた出来事とは EventRetired で分ける)。")

(defclass [(dataclass :frozen True)] EventRetired []
  "ReadEventByKey の答え: その冪等キーの出来事は積まれたが、保持の期限を過ぎた(回収の前でも過ぎた刻から — 他の読みと同じ)。
   sequence = 消えた出来事の番号(1 以上)。まだ積んでいない鍵(EventAbsent)と分けて、使い手が「来ていない」と「来て消えた」を混ぜないため。"
  (#^ int sequence)
  (defn #^ None __post_init__ [self]
    (when (or (isinstance self.sequence bool) (not (isinstance self.sequence int)) (< self.sequence 1))
      (raise (ValueError (.format "EventRetired.sequence は 1 以上の整数: {!r}" self.sequence))))))


;; --- 失敗の答え --------------------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Conflict []
  "PutRow の期待が今の行と合わない。current = 今の行(Row | Missing)— 読み直して導き直すのは呼び手。"
  (#^ (| Row Missing) current))

(defclass [(dataclass :frozen True)] Refused []
  "宣言が書きを許さない(書き手でない欄・宣言の外の欄・終端の行・状態の語彙の外・上限・承認が無い・冪等キーの別の本文)。"
  (#^ str reason))

(defclass [(dataclass :frozen True)] RowsConflict []
  "PutRows の束のある行の期待が今の行と合わない(束は 1 行も書いていない)。index = 束の中の位置(0 から)/ table・key = その行 /
   current = その行の今の値(Row | Missing)。期待の合わない行が 2 つ以上あれば、束の順で最初の行。"
  (#^ int index)
  (#^ str table)
  (#^ tuple key)
  (#^ (| Row Missing) current))

(defclass [(dataclass :frozen True)] RowsRefused []
  "PutRows の束のある行を宣言が許さない(束は 1 行も書いていない)。index = 束の中の位置(0 から)/ table・key = その行 /
   reason = PutRow の Refused と同じ理由の文。断られる行が 2 つ以上あれば、束の順で最初の行。"
  (#^ int index)
  (#^ str table)
  (#^ tuple key)
  (#^ str reason))

(defclass [(dataclass :frozen True)] Unreachable []
  "置き場に届かない(結末は不明 — 読みは撃ち直してよい。書きは期待つきなら撃ち直してよい)。"
  (#^ str detail))

(defclass [(dataclass :frozen True)] NotIndexed []
  "ListRows の where が索引の無い欄を名指した。"
  (#^ tuple fields))

(defclass [(dataclass :frozen True)] Reset []
  "位置の epoch が置き場の版と違う(置き場が作り直された・変更の列が刈られた)— 一覧から読み直す。
   epoch = 置き場の今の版 / floor = その置き場で読める最も古い変更の位置(変更の列を保持の期限で刈った位置)。
   WatchCursor(epoch, floor) から読めば、残っている変更を頭から全部読める。"
  (#^ int epoch)
  (#^ int floor))

(defrecord WaitsClosed
  "置き場の変化の待ち(WatchChanges・WatchEvents の呼び鈴の待ち)を起こした止めの印(#3713): 待ちを抱える記録の service の入口が止めの
   合図を受けた — 待ち手は読み直さず、手元の静かな答え(空の Changes・EventsQuiet)で直ぐに返る。reason = 止めの理由。呼び鈴の待ち
   (doeff-time の WaitWithin)の答えとして届き、呼び鈴の値(memory の None・PostgreSQL の True)とも時間切れの None とも型で見分ける。"
  #^ str reason)


(setv ReadRowAnswer (| Row Missing Unreachable))
(setv ListRowsAnswer (| Page Reset Unreachable NotIndexed))
(setv PutRowAnswer (| Written Conflict Refused Unreachable))
(setv WatchChangesAnswer (| Changes Reset Unreachable))
(setv AppendEventAnswer (| Appended Refused Unreachable))
(setv ReadEventsAnswer (| Events Unreachable))
(val WatchEventsAnswer (| EventsMoved EventsQuiet Unreachable))
(val ReadStreamEndAnswer (| StreamEnd StreamEmpty Unreachable))
(val ReadEventByKeyAnswer (| Event EventAbsent EventRetired Unreachable))
(val PutRowsAnswer (| WrittenRows RowsConflict RowsRefused Unreachable))
