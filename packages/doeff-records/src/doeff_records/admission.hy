;;; 書きの許可・保持・索引・頁の判断(純関数・I/O なし)。handler の組(memory・PG・写し)は全部この 1 つを呼ぶ —
;;; 判断を handler ごとに写さない(写すと 1 つだけ古い答えを返す日が来る)。
;;;
;;; 判断の順(PutRow): 期待(Conflict)→ 宣言の外で値が None の欄を差分から落とす → 鍵の形 → 宣言の外の欄 → 終端の行 → 鍵の欄の書き換え →
;;; 状態の語彙 → 上限。PutRows の束は、全部の行の期待 → 全部の行の書きの判定(1 行ずつは PutRow と同じ判断)。
;;; 書き手の名(欄の writers・founders・operator-paths・列の writers)では断らない — 書いてよい program は linter の規則と模擬環境の
;;; 失敗ケースで守る(#2994・#2989)。
;;; 期待を先に見るのは、古い版で書いた呼び手に「読み直せ」を先に返すため(読み直した後の書きが断られるかは、その時の行で決まる)。
(import dataclasses [dataclass])
(import datetime [datetime timezone])
(import json)
(import collections.abc [Mapping])
(import doeff_hy.frozen [FrozenMap frozen-json-object thaw-json])
(import doeff_records.values [RecordsSchema TableDecl StreamDecl KeepFor ByKeySuffix Row Missing Conflict Refused NotIndexed Event
                              ExpectAbsent ExpectVersion ExpectAny RowsConflict RowsRefused])


;; --- JSON の値 ------------------------------------------------------------------------------------------------

(defn #^ str canonical-json [#^ object value]
  "値の正規の綴り(鍵の昇順・空白なし・UTF-8 のまま)。上限の byte と冪等キーの本文の比べはこの綴りで測る。
   凍らせた値(FrozenMap・tuple)は thaw-json で JSON の形へ戻してから綴る — 置き場へ書く JSON もこの綴り。"
  (json.dumps (thaw-json value) :sort-keys True :separators #("," ":") :ensure-ascii False))


(defn #^ int json-bytes [#^ object value]
  (len (.encode (canonical-json value) "utf-8")))


(defn #^ bool json-equal? [#^ object a #^ object b]
  "JSON の値としての等しさ(True と 1 を等しくしない・数は数として比べる)。"
  (cond
    (or (isinstance a bool) (isinstance b bool)) (and (isinstance a bool) (isinstance b bool) (= a b))
    (and (isinstance a #(int float)) (isinstance b #(int float))) (= a b)
    (and (isinstance a Mapping) (isinstance b Mapping))
      (and (= (set (.keys a)) (set (.keys b))) (all (gfor k a (json-equal? (get a k) (get b k)))))
    (and (isinstance a #(list tuple)) (isinstance b #(list tuple)))
      (and (= (len a) (len b)) (all (gfor #(x y) (zip a b) (json-equal? x y))))
    True (and (= (type a) (type b)) (= a b))))


(defn #^ str key-text [#^ tuple key]
  "鍵の綴り(JSON の配列・ASCII だけ)。頁の順はこの綴りの符号点の順 — PG では COLLATE \"C\" の順と同じになる。"
  (json.dumps (list key) :separators #("," ":") :ensure-ascii True))


(defn #^ tuple key-from-text [#^ str text]
  (tuple (json.loads text)))


(defn #^ int epoch-ms [#^ datetime at]
  "時刻 → epoch ミリ秒(doeff-time の GetTime の答えを置き場の刻みにする)。"
  (int (round (* 1000 (.timestamp at)))))


;; --- 書きの期待 ----------------------------------------------------------------------------------------------

(defn #^ (| Conflict None) judge-expect [#^ object expect #^ (| Row None) current]
  "期待が今の行と合わなければ Conflict(current = 今の行か Missing)。ExpectAny は常に合う。"
  (setv now (if (is current None) (Missing) current))
  (cond
    (isinstance expect ExpectAny) None
    (isinstance expect ExpectAbsent) (if (is current None) None (Conflict now))
    (isinstance expect ExpectVersion)
      (if (and (is-not current None) (= current.version expect.version)) None (Conflict now))
    True (raise (TypeError (.format "知らない期待: {!r}" expect)))))


;; --- 書きの許可 ----------------------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Admitted []
  "書きを許した: value = 確定する行の値の凍らせた写像(鍵の欄と、生まれる行なら状態の initial を含む)。"
  (#^ FrozenMap value))


(defn #^ (| str None) state-of [#^ TableDecl decl #^ FrozenMap value]
  (setv word (.get value decl.state-field))
  (if (isinstance word str) word None))


(defn #^ bool terminal-row? [#^ TableDecl decl #^ FrozenMap value]
  (and (bool decl.terminal) (in (state-of decl value) decl.terminal)))


(defn #^ FrozenMap judged-diff [#^ TableDecl decl #^ FrozenMap diff]  ; defk にできない: handler(memory・PG・写し)が同期に呼ぶ judge-put の 1 段(この file の判断はすべて純関数の defn)
  "判定にかける差分: 書きの差分から、宣言に無い欄で値が None の物を落とす(残りの判定は全部この差分で判じる)。
   None は「その欄を消す」なので、行に無い欄の None は確定する中身を変えない — 行の型に既定値 None の欄を足した書き手は、その欄を
   宣言していない置き場にも書ける。宣言から外した欄が古い行に残っていても、その欄の None は落とすので欄は行に残り(消すのは欄を
   宣言から外す前の書き直し)、書き手の名簿(宣言の外の欄には無い)で照らす欄にも入らない。値が None でない宣言の外の欄は残す
   (shape-refusal が断る)・宣言に在る欄の None も残す(その欄を消す)。PutRows の束(judge-put-rows)も行ごとに judge-put を通るので、
   落とすのはここ 1 か所。"
  (frozen-json-object (FrozenMap (gfor #(name value) (.items diff) :if (or (is-not value None) (decl.declares name)) #(name value)))
                      "判定にかける差分"))


(defn #^ (| Refused None) shape-refusal [#^ TableDecl decl #^ (| Row None) current #^ tuple key #^ FrozenMap diff]
  "鍵の形・宣言の外の欄・終端の行・鍵の欄の書き換え。"
  (setv unknown (sorted (gfor name diff :if (not (decl.declares name)) name)))
  (cond
    (!= (len key) (len decl.key-fields))
      (Refused (.format "表 {} の鍵は {} 欄 {!r}: {!r}" decl.name (len decl.key-fields) decl.key-fields key))
    unknown (Refused (.format "表 {} の宣言の外の欄: {!r}" decl.name unknown))
    (and (is-not current None) (terminal-row? decl current.value))
      (Refused (.format "表 {} の行 {!r} は終端の状態 {!r} で、もう書けない" decl.name key (state-of decl current.value)))
    True
      (do (setv moved (lfor #(name part) (zip decl.key-fields key)
                            :if (and (in name diff) (not (json-equal? (get diff name) part)))
                            name))
          (if moved (Refused (.format "表 {} の鍵の欄 {!r} は鍵と違う値にできない" decl.name moved)) None))))


(defn #^ FrozenMap landed-value [#^ TableDecl decl #^ (| Row None) current #^ tuple key #^ FrozenMap diff]
  "確定する行の値: 今の値(無ければ鍵の欄)に差分を重ね、差分の値が None の欄は消し(JSON merge patch〔RFC 7396〕の null と同じ)、
   生まれる行で状態の語が無ければ(差分に無いか None — 生まれる行に消す欄は無い)initial を置く。行の値は None を持たない。答えは深く凍らせた写像。"
  (setv base (if (is current None) (dict (zip decl.key-fields key)) (dict current.value))
        merged (| base (dict diff))
        born-state (if (and (is current None) decl.states (is (.get diff decl.state-field) None))
                       {decl.state-field decl.initial}
                       {}))
  (frozen-json-object (dfor #(name v) (.items (| merged born-state)) :if (is-not v None) name v) "確定する行の値"))


(defn #^ (| Refused None) state-refusal [#^ TableDecl decl #^ FrozenMap value]
  (when (not decl.states) (return None))
  (setv word (.get value decl.state-field))
  (if (and (isinstance word str) (in word decl.states))
      None
      (Refused (.format "表 {} の状態の語 {!r} は宣言 {!r} の外" decl.name word decl.states))))


(defn #^ (| Refused None) size-refusal [#^ TableDecl decl #^ FrozenMap value]
  (when (is decl.size-budget None) (return None))
  (setv size (json-bytes value))
  (if (> size decl.size-budget)
      (Refused (.format "表 {} の行の値は {} byte で、上限 {} byte を越える" decl.name size decl.size-budget))
      None))


(defn #^ (| Admitted Refused) judge-put [#^ TableDecl decl #^ (| Row None) current #^ tuple key #^ FrozenMap diff]
  "書きを許すか: Admitted(確定する値)か Refused(理由)。期待(judge-expect)は呼び手が先に見る。
   判定は差分から宣言の外で値が None の欄を落とした差分(judged-diff)で行う。書き手の名では断らない。"
  (setv judged (judged-diff decl diff))
  (setv shape (shape-refusal decl current key judged))
  (when shape (return shape))
  (setv value (landed-value decl current key judged))
  (or (state-refusal decl value)
      (size-refusal decl value)
      (Admitted value)))


(defn #^ (| tuple RowsConflict RowsRefused) judge-put-rows [#^ RecordsSchema schema #^ tuple writes #^ tuple currents]  ; defk にできない: handler(memory・PG)が置き場の lock と transaction の中で同期に呼ぶ判断(この file の判断はすべて純関数の defn)
  "PutRows の束を全部か 0 で書けるかの判断を 1 か所に置く(memory と PostgreSQL の handler が同じ答えを返すため)。
   writes = RowWrite の tuple / currents = 各行の今の行(Row か None・writes と同じ順)。
   答え = 全部通れば各行の Admitted の tuple(writes と同じ順)/ 期待の合わない行があれば束の順で最初の行の RowsConflict /
   無ければ書きの判定で断られた最初の行の RowsRefused。
   期待を全部の行で先に見るのは PutRow と同じ理由(古い版で書いた呼び手に「読み直せ」を先に返す — 断りは読み直した後の行で決まる)。"
  (for [#(index #(write current)) (enumerate (zip writes currents :strict True))]
    (setv conflict (judge-expect write.expect current))
    (when conflict (return (RowsConflict index write.table write.key conflict.current))))
  (setv admitted [])
  (for [#(index #(write current)) (enumerate (zip writes currents :strict True))]
    (setv verdict (judge-put (schema.table write.table) current write.key write.value))
    (when (isinstance verdict Refused) (return (RowsRefused index write.table write.key verdict.reason)))
    (.append admitted verdict))
  (tuple admitted))


;; --- 保持 ------------------------------------------------------------------------------------------------

(defn #^ bool row-expired? [#^ TableDecl decl #^ FrozenMap value #^ int updated-ms #^ int now-ms]
  "保持の期限を過ぎた行か: KeepFor の表で、終端の状態の行が終端になってから seconds 秒以上経った。
   終端の行は書けない(shape-refusal)ので、最後に書かれた刻 = 終端になった刻。"
  (and (isinstance decl.retention KeepFor)
       (terminal-row? decl value)
       (>= now-ms (+ updated-ms (int (* 1000 decl.retention.seconds))))))


(defn #^ bool event-expired? [#^ StreamDecl decl #^ int at-ms #^ int now-ms]
  "保持の期限を過ぎた出来事か。at-ms = 保持を数え始める刻(組で数える列では組の最後の出来事の刻 — retention-group-of)。"
  (and (isinstance decl.retention KeepFor) (>= now-ms (+ at-ms (int (* 1000 decl.retention.seconds))))))


(defn #^ (| str None) retention-group-of [#^ StreamDecl decl #^ str idempotency-key]
  "出来事の保持の組の名(None = 出来事ごとに数える)。ByKeySuffix は冪等キーの最初の区切りより後ろ・区切りを含まないキーはキー全体。"
  (match decl.retention-group
    (ByKeySuffix :separator separator)
      (if (in separator idempotency-key)
          (get (.split idempotency-key separator 1) 1)
          idempotency-key)
    _ None))


;; --- 索引と頁 --------------------------------------------------------------------------------------------

(defn #^ (| NotIndexed None) where-refusal [#^ TableDecl decl #^ FrozenMap where]
  (setv loose (tuple (sorted (gfor name where :if (not (or (in name decl.key-fields) (in name decl.indexes))) name))))
  (if loose (NotIndexed loose) None))


(defn #^ bool row-matches? [#^ FrozenMap where #^ FrozenMap value]
  (all (gfor #(name wanted) (.items where) (and (in name value) (json-equal? (get value name) wanted)))))


(defn #^ FrozenMap projected [#^ TableDecl decl #^ (| tuple None) fields #^ FrozenMap value]
  "返す欄だけの値(fields = None は全部・鍵の欄は常に残す)。"
  (if (is fields None)
      (frozen-json-object value "一覧の行の値")
      (frozen-json-object (dfor #(name v) (.items value) :if (or (in name fields) (in name decl.key-fields)) name v)
                          "一覧の行の値")))


(defn #^ Row listed-row [#^ TableDecl decl #^ (| tuple None) fields #^ Row row]
  "一覧に出す行(返す欄だけ)。"
  (Row row.key (projected decl fields row.value) row.version))


(defn #^ int next-watch-sequence [#^ tuple items #^ int limit #^ int head]
  "WatchChanges の次の位置: 上限まで返したなら最後の変更の番号(まだ続きがある)、そうでなければ読んだ時の先頭の番号。"
  (if (>= (len items) limit) (. (get items -1) sequence) head))


;; --- 追記 ------------------------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] AppendNew []
  "新しい出来事として積む。")

(defclass [(dataclass :frozen True)] AppendReplay []
  "同じ冪等キー・同じ本文の再送 — 前の sequence を返す。"
  (#^ int sequence))


(defn #^ (| AppendNew AppendReplay Refused) judge-append [#^ StreamDecl decl #^ object body #^ (| Event None) earlier]
  "積んでよいか: 冪等キーの再送(同じ本文なら前の番号・違えば Refused)→ 上限。書き手の名では断らない。"
  (cond
    (is-not earlier None)
      (if (= (canonical-json earlier.body) (canonical-json body))
          (AppendReplay earlier.sequence)
          (Refused (.format "追記の列 {} の冪等キー {!r} は別の本文で既に使われた" decl.name earlier.idempotency-key)))
    (and (is-not decl.size-budget None) (> (json-bytes body) decl.size-budget))
      (Refused (.format "追記の列 {} の本文は {} byte で、上限 {} byte を越える" decl.name (json-bytes body) decl.size-budget))
    True (AppendNew)))
