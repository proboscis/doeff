;;; 書きの許可・保持・索引・頁の判断(純関数・I/O なし)。handler の組(memory・PG・写し)は全部この 1 つを呼ぶ —
;;; 判断を handler ごとに写さない(写すと 1 つだけ古い答えを返す日が来る)。
;;;
;;; 判断の順(PutRow): 期待(Conflict)→ 鍵の形 → 宣言の外の欄 → 終端の行 → 鍵の欄の書き換え → 書き手 → 状態の語彙 → 承認 → 上限。
;;; 期待を先に見るのは、古い版で書いた呼び手に「読み直せ」を先に返すため(読み直した後の書きが断られるかは、その時の行で決まる)。
(import dataclasses [dataclass])
(import datetime [datetime timezone])
(import json)
(import collections.abc [Callable])
(import doeff_records.values [TableDecl StreamDecl KeepFor Row Missing Conflict Refused NotIndexed Event
                              ExpectAbsent ExpectVersion ExpectAny Approval])


;; --- JSON の値 ------------------------------------------------------------------------------------------------

(defn #^ str canonical-json [#^ object value]
  "値の正規の綴り(鍵の昇順・空白なし・UTF-8 のまま)。上限の byte と冪等キーの本文の比べはこの綴りで測る。"
  (json.dumps value :sort-keys True :separators #("," ":") :ensure-ascii False))


(defn #^ int json-bytes [#^ object value]
  (len (.encode (canonical-json value) "utf-8")))


(defn #^ bool json-equal? [#^ object a #^ object b]
  "JSON の値としての等しさ(True と 1 を等しくしない・数は数として比べる)。"
  (cond
    (or (isinstance a bool) (isinstance b bool)) (and (isinstance a bool) (isinstance b bool) (= a b))
    (and (isinstance a #(int float)) (isinstance b #(int float))) (= a b)
    (and (isinstance a dict) (isinstance b dict))
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
  "書きを許した: value = 確定する行の値(鍵の欄と、生まれる行なら状態の initial を含む)。"
  (#^ dict value))


(defn #^ (| str None) refuse-every-approval [#^ Approval approval #^ str table #^ tuple key #^ tuple fields]
  "承認の確かめ方の既定: どの承認も認めない(承認を確かめる口を組んでいない置き場で、承認の欄は書けない)。"
  "承認を確かめる口が組まれていない")


(defn #^ (| str None) state-of [#^ TableDecl decl #^ dict value]
  (setv word (.get value decl.state-field))
  (if (isinstance word str) word None))


(defn #^ bool terminal-row? [#^ TableDecl decl #^ dict value]
  (and (bool decl.terminal) (in (state-of decl value) decl.terminal)))


(defn #^ bool field-changes? [#^ (| Row None) current #^ str name #^ object value]
  "差分の 1 欄が行を変えるか: None(欄を消す)は欄が在る時だけ・他の値は今の値と違う時だけ。"
  (when (is current None) (return (is-not value None)))
  (setv present (in name current.value))
  (if (is value None)
      present
      (not (and present (json-equal? (get current.value name) value)))))


(defn #^ tuple changed-fields [#^ TableDecl decl #^ (| Row None) current #^ dict diff]
  "書きが変える欄(書き手の名簿で照らす欄): 生まれる行 = 鍵の欄と、値が None でない差分の全部 / 在る行 = 値の変わる差分の欄
   (None = 欄を消す — 在る欄を消す書きも、その欄の書き手で照らす)。"
  (if (is current None)
      (tuple (+ (list decl.key-fields)
                (sorted (gfor #(name value) (.items diff) :if (and (not-in name decl.key-fields) (is-not value None)) name))))
      (tuple (sorted (gfor #(name value) (.items diff) :if (field-changes? current name value) name)))))


(defn #^ (| Refused None) shape-refusal [#^ TableDecl decl #^ (| Row None) current #^ tuple key #^ dict diff]
  "鍵の形・宣言の外の欄・終端の行・鍵の欄の書き換え。"
  (setv unknown (sorted (gfor name diff :if (not-in name decl.writers) name)))
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


(defn #^ dict landed-value [#^ TableDecl decl #^ (| Row None) current #^ tuple key #^ dict diff]
  "確定する行の値: 今の値(無ければ鍵の欄)に差分を重ね、差分の値が None の欄は消し(JSON merge patch〔RFC 7396〕の null と同じ)、
   生まれる行で状態の語が無ければ initial を置く。行の値は None を持たない。"
  (setv base (if (is current None) (dict (zip decl.key-fields key)) (dict current.value)))
  (setv value (dfor #(name v) (.items (| base diff)) :if (is-not v None) name v))
  (when (and (is current None) decl.states (not-in decl.state-field diff))
    (setv (get value decl.state-field) decl.initial))
  value)


(defn #^ (| Refused None) writer-refusal [#^ TableDecl decl #^ str writer #^ tuple changed]
  (for [name changed]
    (when (not-in writer (get decl.writers name))
      (return (Refused (.format "表 {} の欄 {} を書いてよいのは {!r} で、{!r} はその中に無い"
                                decl.name name (get decl.writers name) writer)))))
  None)


(defn #^ (| Refused None) state-refusal [#^ TableDecl decl #^ dict value]
  (when (not decl.states) (return None))
  (setv word (.get value decl.state-field))
  (if (and (isinstance word str) (in word decl.states))
      None
      (Refused (.format "表 {} の状態の語 {!r} は宣言 {!r} の外" decl.name word decl.states))))


(defn #^ (| Refused None) approval-refusal [#^ TableDecl decl #^ tuple key #^ tuple changed #^ (| Approval None) approval
                                            #^ Callable approval-check]
  (setv guarded (tuple (gfor name changed :if (in name decl.operator-paths) name)))
  (cond
    (not guarded) None
    (is approval None) (Refused (.format "表 {} の欄 {!r} を書くには承認が要る" decl.name guarded))
    True (do (setv reason (approval-check approval decl.name key guarded))
             (if (is reason None) None (Refused (.format "表 {} の欄 {!r} の承認が通らない: {}" decl.name guarded reason))))))


(defn #^ (| Refused None) size-refusal [#^ TableDecl decl #^ dict value]
  (when (is decl.size-budget None) (return None))
  (setv size (json-bytes value))
  (if (> size decl.size-budget)
      (Refused (.format "表 {} の行の値は {} byte で、上限 {} byte を越える" decl.name size decl.size-budget))
      None))


(defn #^ (| Admitted Refused) judge-put [#^ TableDecl decl #^ str writer #^ (| Row None) current #^ tuple key #^ dict diff
                                         #^ (| Approval None) approval #^ Callable approval-check]
  "書きを許すか: Admitted(確定する値)か Refused(理由)。期待(judge-expect)は呼び手が先に見る。"
  (setv shape (shape-refusal decl current key diff))
  (when shape (return shape))
  (setv changed (changed-fields decl current diff)
        value (landed-value decl current key diff))
  (or (writer-refusal decl writer changed)
      (state-refusal decl value)
      (approval-refusal decl key changed approval approval-check)
      (size-refusal decl value)
      (Admitted value)))


;; --- 保持 ------------------------------------------------------------------------------------------------

(defn #^ bool row-expired? [#^ TableDecl decl #^ dict value #^ int updated-ms #^ int now-ms]
  "保持の期限を過ぎた行か: KeepFor の表で、終端の状態の行が終端になってから seconds 秒以上経った。
   終端の行は書けない(shape-refusal)ので、最後に書かれた刻 = 終端になった刻。"
  (and (isinstance decl.retention KeepFor)
       (terminal-row? decl value)
       (>= now-ms (+ updated-ms (int (* 1000 decl.retention.seconds))))))


(defn #^ bool event-expired? [#^ StreamDecl decl #^ int at-ms #^ int now-ms]
  (and (isinstance decl.retention KeepFor) (>= now-ms (+ at-ms (int (* 1000 decl.retention.seconds))))))


;; --- 索引と頁 --------------------------------------------------------------------------------------------

(defn #^ (| NotIndexed None) where-refusal [#^ TableDecl decl #^ dict where]
  (setv loose (tuple (sorted (gfor name where :if (not (or (in name decl.key-fields) (in name decl.indexes))) name))))
  (if loose (NotIndexed loose) None))


(defn #^ bool row-matches? [#^ dict where #^ dict value]
  (all (gfor #(name wanted) (.items where) (and (in name value) (json-equal? (get value name) wanted)))))


(defn #^ dict projected [#^ TableDecl decl #^ (| tuple None) fields #^ dict value]
  "返す欄だけの値(fields = None は全部・鍵の欄は常に残す)。"
  (if (is fields None)
      (dict value)
      (dfor #(name v) (.items value) :if (or (in name fields) (in name decl.key-fields)) name v)))


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


(defn #^ (| AppendNew AppendReplay Refused) judge-append [#^ StreamDecl decl #^ str writer #^ object body
                                                         #^ (| Event None) earlier]
  "積んでよいか: 書き手 → 冪等キーの再送(同じ本文なら前の番号・違えば Refused)→ 上限。"
  (cond
    (not-in writer decl.writers)
      (Refused (.format "追記の列 {} に積んでよいのは {!r} で、{!r} はその中に無い" decl.name decl.writers writer))
    (is-not earlier None)
      (if (= (canonical-json earlier.body) (canonical-json body))
          (AppendReplay earlier.sequence)
          (Refused (.format "追記の列 {} の冪等キー {!r} は別の本文で既に使われた" decl.name earlier.idempotency-key)))
    (and (is-not decl.size-budget None) (> (json-bytes body) decl.size-budget))
      (Refused (.format "追記の列 {} の本文は {} byte で、上限 {} byte を越える" decl.name (json-bytes body) decl.size-budget))
    True (AppendNew)))
