;;; 記録の仕組みの公開 effect 10 個(lease は既存の doeff-cluster の LeaseOp / HeldLease を使い、ここには作らない)。
;;; 7 つ目の PutRows は複数行を全部か 0 で書く(書きの束の 1 行 = RowWrite — PutRow と同じ欄)。
;;; 8 つ目の ReadStreamEnd は追記の列の末尾の番号を 1 回で読む(空の列は StreamEmpty)。WatchChanges は名指した列(streams)の末尾の
;;; 番号と刻も答えに載せる(Changes.tails — #3718)。
;;; 9 つ目の ReadEventByKey は追記の列の出来事を冪等キーで 1 つ引く(列を頭から読まずに、置き場の (列, 冪等キー) の一意の索引で答える —
;;; 出自の issue は #3750)。
;;; 10 個目の AppendEvents は追記の束(束の追記 1 つ = EventAppend — AppendEvent と同じ欄)を置き場への 1 往復で積む(1 つの要求で原因と
;;; 本体の 2 つの出来事を積む書き手が、置き場の書きの錠と往復を 2 回順に待たないため — card ki-48c34994ed15)。
;;; 公開 effect の外に、追記の列の頭が進むのを待つ WatchEvents を置く(置き場の handler が自分の待ち方で答える: memory = 列の呼び鈴・
;;; PostgreSQL = ReadEvents の読み直し。出自の issue は #1019)。HTTP の口は wire の watch-events で service の中の置き場の待ちへ渡す
;;; (long-poll — #3074。前は client が ReadEvents を読み直していた)。
;;;
;;; 書き手の身元は effect の引数にしない — handler を組む時(composition root)に渡す。答えの型は values.hy。
;;; 欄 → 値の写像(PutRow.value・ListRows.where・ListRows.where-any)と出来事の本文(AppendEvent.body・EventAppend.body)は、作る時に深く
;;; 凍らせる(dict を渡してもよい — where-any の組の配列は tuple になる)。
(require doeff-hy.macros [defeffect])
(import collections [Counter])
(import dataclasses [dataclass field])
(import doeff [EffectBase])
(import doeff_hy.frozen [FrozenMap freeze-json])
(import doeff_records.values [ExpectAbsent ExpectVersion ExpectAny WatchCursor ListCursor checked-table-name
                              checked-field-name freeze-field StreamEnd StreamEmpty Unreachable checked-write-key EventsMoved EventsQuiet
                              Event EventAbsent EventRetired Changes Reset
                              Appended Refused Events NotIndexed WrittenRows RowsConflict RowsRefused])

(setv DEFAULT-LIST-LIMIT 500)
;; ListRows 1 回の limit の上限(ADR-DOE-RECORDS-002 R6 (f))。束の読みの行の読み解きと答えの綴りは、置き場が返す行の数に比例して計算の
;; pool の thread を塞ぐ — 1 要求の計算の量を「頁の行の数 × 行の大きさ」の両側で縛るため、行の数の側をこの数で縛る(行の大きさの側は表の
;; size-budget)。型が作る時に断るので、memory の置き場の模擬と検も、PostgreSQL の置き場も、HTTP の口(本文の読みの断り = 400 malformed)も
;; 同じ所で断る。呼び手は大きな一覧をこの数までの頁に割り、続きの位置(next_cursor)で送る。値は使い手の一番大きな頁(全行の一覧の 1 頁
;; 5,000 行 — 1 回の要求の固定費との釣り合いで選ばれた頁)に合わせた。
(setv LIST-LIMIT-MAX 5000)
(setv DEFAULT-WATCH-LIMIT 1000)
(setv DEFAULT-READ-EVENTS-LIMIT 1000)
;; ListRows の値の組の絞り(where-any)1 つの読みに載せる値の数の上限(全部の欄の組の値の和)— PostgreSQL の置き場は値を 1 つずつ文の
;; 引数に展げるので、1 つの文の引数と綴りの大きさをこの数で縛る。呼び手は大きな組をこの数ごとに割って読む。
(setv WHERE-ANY-LIMIT 1000)


(defn #^ tuple checked-key [#^ object key #^ str what]
  (when (not (and (isinstance key tuple) key (all (gfor part key (isinstance part str)))))
    (raise (TypeError (.format "{} は文字列の空でない tuple: {!r}" what key))))
  key)


(defn #^ int checked-limit [#^ object limit #^ str what]
  (when (or (isinstance limit bool) (not (isinstance limit int)) (< limit 1))
    (raise (ValueError (.format "{} は 1 以上の整数: {!r}" what limit))))
  limit)


(defn #^ None check-row-write [#^ object write #^ str what]  ; defk にできない: frozen dataclass の __post_init__ が作る時に同期に呼ぶ検め
  "1 行の書きの欄(table・key・value・expect)を作る時に検め、value を深く凍らせる — PutRow と RowWrite の検めを 1 つにするため。"
  (checked-table-name write.table (+ what ".table"))
  (checked-key write.key (+ what ".key"))
  (freeze-field write "value" (+ what ".value"))
  (for [name write.value] (checked-field-name name (+ what ".value の欄")))
  (when (not (isinstance write.expect #(ExpectAbsent ExpectVersion ExpectAny)))
    (raise (TypeError (+ what ".expect は ExpectAbsent | ExpectVersion | ExpectAny")))))


(defclass [(dataclass :frozen True)] ReadRow [EffectBase]
  "行を鍵で 1 つ読む。答え = Row | Missing | Unreachable。"
  (#^ str table)
  (#^ tuple key)
  (defn #^ None __post_init__ [self]
    (checked-table-name self.table "ReadRow.table")
    (checked-key self.key "ReadRow.key")))


(defclass [(dataclass :frozen True)] ListRows [EffectBase]
  "表の行を鍵の綴りの順に 1 頁読む。where = 欄 → 値の凍らせた写像(索引の欄の等号の AND)/ fields = 返す欄(None = 全部。鍵の欄は常に返す)/
   cursor = 前の頁の next-cursor(None = 最初から)/ limit = 頁の行数の上限(1 以上 LIST-LIMIT-MAX 以下 — 外は作る時に ValueError)。
   答え = Page | Reset | Unreachable | NotIndexed。
   descending-by = 欄の名(None = 鍵の綴りの順): 値が整数の行だけを、その値の大きい順(同じ値は鍵の綴りの逆順)に上から limit 件読む
   (値が整数でない行・欄の無い行は出さない)。続きが在れば答えの next-cursor は頁の最後の行の (値, 鍵の綴り) を持つ位置(ListCursor の
   after-value と after-key — keyset)で、それを cursor に渡した読みは (値, 鍵の綴り) がその組より小さい行から次の頁を読む(呼び手が新しい
   順に古い頁を送るため — 鍵が時刻の順でない表でも)。頁の間に書かれた行は、位置より小さい組なら後の頁に出て、大きい組なら出ない。
   上から読む読みの cursor は after-value を持つ位置だけ・鍵の順の読みの cursor は after-value の無い位置だけ(作る時に ValueError)。
   where-any = 欄 → 値の組(JSON の値の空でない tuple)の凍らせた写像(既定 = 空 = 絞らない): 行の欄の値が組のどれかに等しい行だけ
   (等しさは where の等号と同じ・欄どうしと where とは AND・索引の欄だけ — 他は NotIndexed)。使い手が値の組 1 つを値ごとの往復でなく
   1 回の読みで引くため。組の値の数は全部の欄の和で WHERE-ANY-LIMIT まで・空の組・where と同じ欄は作る時に断る(ValueError — 配列で
   ない組は TypeError)。頁の順・続きの位置・上から読む読み・保持の期限の除きは where だけの読みと同じ。"
  (#^ str table)
  (setv #^ FrozenMap where (field :default-factory FrozenMap))
  (setv #^ (| tuple None) fields None)
  (setv #^ (| ListCursor None) cursor None)
  (setv #^ int limit DEFAULT-LIST-LIMIT)
  (setv #^ (| str None) descending-by None)
  (setv #^ FrozenMap where-any (field :default-factory FrozenMap))
  (defn #^ None __post_init__ [self]
    (checked-table-name self.table "ListRows.table")
    (freeze-field self "where" "ListRows.where")
    (for [name self.where] (checked-field-name name "ListRows.where の欄"))
    (freeze-field self "where_any" "ListRows.where_any")
    (for [#(name members) (.items self.where-any)]
      (checked-field-name name "ListRows.where_any の欄")
      (when (not (isinstance members tuple))
        (raise (TypeError (.format "ListRows.where_any の欄 {} の組は JSON の値の配列: {!r}" name members))))
      (when (not members)
        (raise (ValueError (.format "ListRows.where_any の欄 {} の組が空(絞らない欄は写像に置かない)" name))))
      (when (in name self.where)
        (raise (ValueError (.format "ListRows の欄 {} が where と where_any の両方に在る(等号と組を同じ欄に重ねない)" name)))))
    (setv counted (sum (gfor members (.values self.where-any) (len members))))
    (when (> counted WHERE-ANY-LIMIT)
      (raise (ValueError (.format "ListRows.where_any の値の数 {} が上限 {} を越える(組を割って読む)" counted WHERE-ANY-LIMIT))))
    (when (is-not self.fields None)
      (for [name self.fields] (checked-field-name name "ListRows.fields の欄")))
    (when (not (or (is self.cursor None) (isinstance self.cursor ListCursor)))
      (raise (TypeError "ListRows.cursor は ListCursor か None")))
    (checked-limit self.limit "ListRows.limit")
    (when (> self.limit LIST-LIMIT-MAX)
      (raise (ValueError (.format "ListRows.limit {} が上限 {} を越える(一覧は続きの位置 next_cursor で {} 行までの頁に割って読む)"
                                  self.limit LIST-LIMIT-MAX LIST-LIMIT-MAX))))
    (when (is-not self.descending-by None)
      (checked-field-name self.descending-by "ListRows.descending_by"))
    (when (and (is-not self.cursor None) (!= (is self.descending-by None) (is self.cursor.after-value None)))
      (raise (ValueError (if (is self.descending-by None)
                             "ListRows の鍵の順の読みの cursor は after_value の無い位置(上から読む読みの位置を渡さない)"
                             "ListRows.descending_by の読みの cursor は after_value を持つ位置(上から読む読みが返した next_cursor)"))))))


(defclass [(dataclass :frozen True)] PutRow [EffectBase]
  "行を書く。value = 欄の差分の凍らせた写像(書く欄 → 値。書かない欄は今の値のまま・値 None = その欄を消す〔JSON merge patch の null と同じ〕)/
   expect = ExpectAbsent | ExpectVersion | ExpectAny。答え = Written | Conflict | Refused | Unreachable。
   idempotency-key = 書き 1 つの身元(冪等キー — None = 鍵の無い書き。card ki-78c6d663491f): 置き場は確定した鍵つきの書きの答えを
   WRITE-KEY-MEMORY-SECONDS の間覚え、同じ鍵・同じ本文(表・鍵・値・期待)の送り直しには期待を見ずに前の答え(Written の replayed 真)を
   返す。同じ鍵の別の本文は Refused。衝突・断りの答えは覚えない(書いていない)。HTTP の client は鍵の無い書きに要求ごとの鍵を作り、
   届かない書きの撃ち直しに同じ鍵を付ける(http_client.hy の頭の註)。
   書き手の名は欄に無い — handler を組む時に身元から入り、行と出来事に記録される(書きの判断には使わない・#2994)。"
  (#^ str table)
  (#^ tuple key)
  (#^ FrozenMap value)
  (#^ object expect)
  (setv #^ (| str None) idempotency-key None)
  (defn #^ None __post_init__ [self]
    (check-row-write self "PutRow")
    (checked-write-key self.idempotency-key "PutRow.idempotency_key")))


(defclass [(dataclass :frozen True)] RowWrite []
  "PutRows の束の書き 1 つ。欄と意味は PutRow と同じ(table・key・value = 欄の差分・expect = ExpectAbsent | ExpectVersion | ExpectAny)。
   effect ではない(束の data)— 束ごと PutRows で撃つ。"
  (#^ str table)
  (#^ tuple key)
  (#^ FrozenMap value)
  (#^ object expect)
  (defn #^ None __post_init__ [self]
    (check-row-write self "RowWrite")))


(defn #^ bool checked-put-rows [#^ object writes #^ object idempotency-key]  ; defk にできない: defeffect の :pre が作る時に同期に呼ぶ検め(:pre は真偽を assert するので、断りは TypeError / ValueError で上げ、通れば True を返す)
  "PutRows の欄(writes・idempotency-key)を作る時に検める — 冪等キーは None か空でない文字列・writes は RowWrite の空でない tuple で、
   同じ表の同じ鍵を 2 度含まない。断りの型と文言と順は defclass の時から変えない。"
  (checked-write-key idempotency-key "PutRows.idempotency_key")
  (when (not (and (isinstance writes tuple) writes (all (gfor w writes (isinstance w RowWrite)))))
    (raise (TypeError (.format "PutRows.writes は RowWrite の空でない tuple: {!r}" writes))))
  (setv repeated (sorted (sfor #(slot times) (.items (Counter (lfor w writes #(w.table w.key)))) :if (> times 1) slot)))
  (when repeated
    (raise (ValueError (.format "PutRows.writes に同じ行が 2 度出る(表・鍵): {!r}" repeated))))
  True)


(defeffect PutRows
  "複数の行を全部か 0 で書く(1 行でも通らなければ 1 行も書かない)。writes = RowWrite の空でない tuple(同じ表の同じ鍵は 1 度だけ —
   2 度出たら作る時に ValueError)。答え = WrittenRows(束の順の Written)| RowsConflict | RowsRefused(どちらも束の中の位置と行を持つ)|
   Unreachable。判定は PutRow と同じ admission で、全部の行の期待を先に見て、次に全部の行の書きの判定を見る。
   確定した時は変更の列に束の順で 1 行ずつ、続いた番号で積む。書き手の名は欄に無い(PutRow と同じく handler を組む時に入る)。
   idempotency-key = 束 1 つの身元(PutRow と同じ — 同じ鍵・同じ束の送り直しは前の WrittenRows を replayed 真で返し、同じ鍵の別の束は
   束の 0 行目の RowsRefused)。
   答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17 の追補): WrittenRows = 成功 / RowsConflict = 値(束のある行の期待が今の行と合わないという
   版の負けで、PutRow の Conflict と同じ — 呼び手が読み直して撃ち直す普通の答えなので、失敗にして Raise で飛ばさず、不在にして Absent で
   飛ばさない)/ RowsRefused = 失敗(束のある行を宣言が許さない・同じ鍵の別の束 — PutRow の Refused と同じで、<- は Raise(RowsRefused) を
   出す)/ Unreachable = 失敗(<- は Raise(Unreachable) を出す)。不在は無い。答える handler は今までどおり 4 つとも値で答える(R4)。"
  {:fields [(: writes (get tuple #(RowWrite ...))) (: idempotency-key (| str None) None)]
   :pre [(checked-put-rows writes idempotency-key)]
   :answer (| WrittenRows RowsConflict RowsRefused Unreachable)
   :failure [RowsRefused Unreachable]
   :value [RowsConflict]
   :tags {:context "records" :role "foundation"}})


(defn #^ tuple write-parts [#^ (| PutRow PutRows) ask]  ; defk にできない: handler(memory・PG)が置き場の錠の内と transaction の中で同期に呼ぶ値の読み(admission の冪等キーの判じに渡す)
  "行の書き ask を、冪等キーの判じ(admission.write-digest・judge-write-replay)が読む書きの並びにするため(PutRow は自分 1 つ・PutRows は束 —
   どれも欄 table・key・value・expect を持つ)。"
  (if (isinstance ask PutRows) ask.writes #(ask)))


(defn #^ bool checked-watch-changes [#^ object tables #^ object cursor #^ object timeout #^ object limit #^ object streams]  ; defk にできない: defeffect の :pre が作る時に同期に呼ぶ検め(:pre は真偽を assert するので、断りは TypeError / ValueError で上げ、通れば True を返す)
  "WatchChanges の欄(tables・cursor・timeout・limit・streams)を作る時に検める — tables は空でない tuple で表の名・cursor は WatchCursor・
   timeout は 0 以上の秒・limit は 1 以上の整数・streams は列の名の tuple で同じ列を 2 度含まない。断りの型と文言は defclass の時から変えない。"
  (when (not (and (isinstance tables tuple) tables)) (raise (TypeError "WatchChanges.tables は空でない tuple")))
  (for [name tables] (checked-table-name name "WatchChanges.tables の表"))
  (when (not (isinstance cursor WatchCursor)) (raise (TypeError "WatchChanges.cursor は WatchCursor")))
  (when (or (isinstance timeout bool) (not (isinstance timeout #(int float))) (< timeout 0))
    (raise (ValueError (.format "WatchChanges.timeout は 0 以上の秒: {!r}" timeout))))
  (checked-limit limit "WatchChanges.limit")
  (when (not (isinstance streams tuple))
    (raise (TypeError (.format "WatchChanges.streams は列の名の tuple: {!r}" streams))))
  (for [name streams] (checked-table-name name "WatchChanges.streams の列"))
  (when (!= (len streams) (len (set streams)))
    (raise (ValueError (.format "WatchChanges.streams に同じ列が 2 度出る: {!r}" streams))))
  True)


(defeffect WatchChanges
  "cursor より後の確定した変更を、頼んだ表の分だけ返す。無ければ timeout 秒まで待つ。limit = 1 回に返す変更の上限。
   streams = 末尾を知りたい追記の列の名(同じ名は 1 度だけ・既定 = 無し — #3718)。答えの Changes.tails に、名指した順で列ごとの末尾
   (StreamTail = 最後の生きている出来事の位置と刻 | StreamTailEmpty = 生きている出来事が無い)が載る。
   tails は答えを返す時点の名指した列の末尾。列への追記では待ち手を起こさない — 起こしたい呼び手は列を源に別に待つ。
   答え = Changes | Reset(cursor の epoch が置き場の版と違う)| Unreachable。
   答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17 の追補): Changes = 成功 / Reset = 値(cursor の世代が置き場の版と違うという普通の答えで、
   呼び手が (epoch, floor) から読み直して続ける — 失敗にして Raise で飛ばさず、不在にして Absent で飛ばさない)/ Unreachable = 失敗(<- は
   Raise(Unreachable) を出す)。不在は無い。答える handler は今までどおり 3 つとも値で答える(R4)。"
  {:fields [(: tables tuple) (: cursor WatchCursor) (: timeout float 0.0) (: limit int DEFAULT-WATCH-LIMIT)
            (: streams (get tuple #(str ...)) #())]
   :pre [(checked-watch-changes tables cursor timeout limit streams)]
   :answer (| Changes Reset Unreachable)
   :failure [Unreachable]
   :value [Reset]
   :tags {:context "records" :role "foundation"}})


(defn #^ str checked-event-key [#^ object idempotency-key #^ str what]  ; defk にできない: defeffect の :pre が作る時に同期に呼ぶ検め(:pre は真偽を assert するので、断りは ValueError で上げ、通れば鍵(空でない文字列 = 真)を返す)
  "冪等キーの欄を作る時に検める(空でない文字列)— ReadEventByKey の検め。AppendEvent と EventAppend は check-event-append が同じ検めを持つ。"
  (when (not (and (isinstance idempotency-key str) idempotency-key))
    (raise (ValueError (+ what ".idempotency_key は空でない文字列"))))
  idempotency-key)


(defn #^ None check-event-append [#^ object append #^ str what]  ; defk にできない: frozen dataclass の __post_init__ が作る時に同期に呼ぶ検め
  "追記 1 つの欄(stream・idempotency-key・body)を作る時に検め、body を深く凍らせる — AppendEvent と EventAppend の検めを 1 つにするため。"
  (object.__setattr__ append "body" (freeze-json append.body))
  (checked-table-name append.stream (+ what ".stream"))
  (when (not (and (isinstance append.idempotency-key str) append.idempotency-key))
    (raise (ValueError (+ what ".idempotency_key は空でない文字列")))))


(defn #^ bool checked-event-append [#^ object append #^ str what]  ; defk にできない: defeffect の :pre が作る時に同期に呼ぶ検め
  "check-event-append を defeffect の :pre から呼ぶ形 — :pre は真偽の条件を assert するので、検めを通れば True を返す。body を深く凍らせる
   ので、:pre には欄の値でなく effect 自身(self)を渡す。"
  (check-event-append append what)
  True)


(defeffect AppendEvent
  "追記の列に出来事を 1 つ積む。body = JSON の値(作る時に深く凍らせる — object は FrozenMap・array は tuple)。
   同じ冪等キーの再送は前の sequence を返す(本文が違えば Refused)。答え = Appended | Refused | Unreachable。
   答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17): Appended = 成功 / Refused = 失敗(相手が断った — <- は Raise(Refused) を出す)/
   Unreachable = 失敗(置き場に届かない — <- は Raise(Unreachable) を出す)。不在と値の型は無い。答える handler は今までどおり
   3 つとも値で答える(R4)。"
  {:fields [(: stream str) (: idempotency-key str) (: body object)]
   :pre [(checked-event-append self "AppendEvent")]
   :answer (| Appended Refused Unreachable)
   :failure [Refused Unreachable]
   :tags {:context "records" :role "foundation"}})


(defclass [(dataclass :frozen True)] EventAppend []
  "AppendEvents の束の追記 1 つ。欄と意味は AppendEvent と同じ(stream・idempotency-key・body — body は作る時に深く凍らせる)。
   effect ではない(束の data)— 束ごと AppendEvents で撃つ。"
  (#^ str stream)
  (#^ str idempotency-key)
  (#^ object body)
  (defn #^ None __post_init__ [self]
    (check-event-append self "EventAppend")))


(defclass [(dataclass :frozen True)] AppendEvents [EffectBase]
  "追記の束を置き場への 1 往復で積む(card ki-48c34994ed15)。appends = EventAppend の空でない tuple(同じ列の同じ冪等キーは 1 度だけ —
   2 度出たら作る時に ValueError)。答え = AppendedEvents(items = 束の順の、追記 1 つごとの答え Appended | Refused)| Unreachable。
   束の答えは、束の順に AppendEvent を 1 つずつ、間に他の書きを挟まずに撃った答えと同じ: 追記 1 つの判断は AppendEvent と同じ(冪等キーの
   再送は前の番号・別の本文と上限は Refused)で、断られた追記は束の他の追記を止めない(PutRows と違い全部か 0 ではない)。今積んだ追記の
   番号は束の順に増える。届かない(Unreachable)時は束の全部が積まれたか、どれも積まれていない(どの置き場も束を 1 つの書きで積む —
   冪等キーつきなので同じ束を撃ち直してよい)。書き手の名は欄に無い(AppendEvent と同じく handler を組む時に入る)。"
  (#^ tuple appends)
  (defn #^ None __post_init__ [self]
    (when (not (and (isinstance self.appends tuple) self.appends (all (gfor a self.appends (isinstance a EventAppend)))))
      (raise (TypeError (.format "AppendEvents.appends は EventAppend の空でない tuple: {!r}" self.appends))))
    (setv repeated (sorted (sfor #(slot times) (.items (Counter (lfor a self.appends #(a.stream a.idempotency-key)))) :if (> times 1) slot)))
    (when repeated
      (raise (ValueError (.format "AppendEvents.appends に同じ追記が 2 度出る(列・冪等キー): {!r}" repeated))))))


(defn #^ bool checked-watch-window [#^ object after #^ object timeout]
  "WatchEvents の after(0 以上の整数)と timeout(0 以上の秒)を作る時に検める。defeffect の :pre は真偽の条件を assert するので、
   検めを通れば True を返す(after = 0 や timeout = 0.0 の値を返すと偽になる)。"
  (when (or (isinstance after bool) (not (isinstance after int)) (< after 0))
    (raise (ValueError (.format "WatchEvents.after は 0 以上の整数: {!r}" after))))
  (when (or (isinstance timeout bool) (not (isinstance timeout #(int float))) (< timeout 0))
    (raise (ValueError (.format "WatchEvents.timeout は 0 以上の秒: {!r}" timeout))))
  True)


(defeffect WatchEvents
  "追記の列 stream の頭が after より進むのを、timeout 秒まで待つ(出来事を読むのは呼び手 — 起きた後に ReadEvents で読む)。
   答え = EventsMoved(進んだ — もう進んでいれば待たずに返る)| EventsQuiet(timeout まで進まなかった — after が頭より先でも誤りに
   しない)| Unreachable。列には版が無いので Reset は無い。出来事の番号は置き場の全部の列で 1 本。timeout 0 は待たずに 1 回だけ見る。
   答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17 の追補): EventsMoved = 成功 / EventsQuiet = 値(期限まで進まなかったという普通の答えで、
   待つ呼び手が毎回分けて扱う — 失敗にして Raise で飛ばさず、不在にして Absent で飛ばさない)/ Unreachable = 失敗(<- は Raise(Unreachable) を
   出す)。答える handler は今までどおり 3 つとも値で答える(R4)。"
  {:fields [(: stream str) (: after int 0) (: timeout float 0.0)]
   :pre [(checked-table-name stream "WatchEvents.stream")
         (checked-watch-window after timeout)]
   :answer (| EventsMoved EventsQuiet Unreachable)
   :failure [Unreachable]
   :value [EventsQuiet]
   :tags {:context "records" :role "foundation"}})


(defn #^ bool checked-read-window [#^ object after #^ object limit #^ object key-head]
  "ReadEvents の after(0 以上の整数)・limit(1 以上の整数)・key-head(空でない文字列か None)を作る時に検める。defeffect の :pre は
   真偽の条件を assert するので、検めを通れば True を返す(after = 0 の値を返すと偽になる)。"
  (when (or (isinstance after bool) (not (isinstance after int)) (< after 0))
    (raise (ValueError (.format "ReadEvents.after は 0 以上の整数: {!r}" after))))
  (checked-limit limit "ReadEvents.limit")
  (when (not (or (is key-head None) (and (isinstance key-head str) key-head)))
    (raise (ValueError (.format "ReadEvents.key_head は空でない文字列か None: {!r}" key-head))))
  True)


(defeffect ReadEvents
  "追記の列の after より後の出来事を limit まで読む。答え = Events | Unreachable | NotIndexed。
   key-head = 冪等キーの頭(None = 列の全部の出来事): 列の宣言の key-lookup(ByKeyHead)の区切りより前が key-head と等しい出来事だけを、
   after より後から番号の順に limit まで読む(列の他の出来事は数えない — 上限より短い頁は、その頭の出来事の終わり)。key-lookup を
   宣言していない列への頭の読みは NotIndexed(欄の名 idempotencyKey)。
   答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17 の追補): Events = 成功 / Unreachable = 失敗(<- は Raise(Unreachable) を出す)/
   NotIndexed = 失敗(<- は Raise(NotIndexed) を出す — key-lookup を宣言していない列に頭の読みを求めた誤りで、続きが無い。ListRows の
   NotIndexed と同じ割り当て)。不在と値は無い。答える handler は今までどおり 3 つとも値で答える(R4)。"
  {:fields [(: stream str) (: after int 0) (: limit int DEFAULT-READ-EVENTS-LIMIT) (: key-head (| str None) None)]
   :pre [(checked-table-name stream "ReadEvents.stream")
         (checked-read-window after limit key-head)]
   :answer (| Events Unreachable NotIndexed)
   :failure [Unreachable NotIndexed]
   :tags {:context "records" :role "foundation"}})


(defeffect ReadStreamEnd
  "追記の列 stream の末尾(今ある最後の出来事の番号)を 1 回で読む — 使い手が末尾を ReadEvents の先読みと二分で探さないため。
   答え = StreamEnd(sequence)| StreamEmpty(出来事が 1 つも無い)| Unreachable。
   答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17 の追補): StreamEnd = 成功 / StreamEmpty = 値(列に出来事が 1 つも無いという事実を持つ
   答えで、番号 0 と混ぜない — 不在にして Absent で飛ばさず、呼び手が分けて扱う)/ Unreachable = 失敗(<- は Raise(Unreachable) を出す)。
   答える handler は今までどおり 3 つとも値で答える(R4)。"
  {:fields [(: stream str)]
   :pre [(checked-table-name stream "ReadStreamEnd.stream")]
   :answer (| StreamEnd StreamEmpty Unreachable)
   :failure [Unreachable]
   :value [StreamEmpty]
   :tags {:context "records" :role "foundation"}})


(defeffect ReadEventByKey
  "追記の列 stream の、冪等キー idempotency-key で積んだ出来事を 1 つ引く — 使い手が鍵 1 つの問いのために列を頭から読み切らないため
   (置き場は追記の重複を断るための (列, 冪等キー) の一意の索引を持つ)。答え = Event(在る)| EventAbsent(その鍵では積まれていない)|
   EventRetired(積まれたが保持の期限で消えた — 消えた出来事の番号)| Unreachable。
   答えの分け方(ADR-DOE-CORE-EFFECTS-003 R5・R17 の追補): Event = 成功 / EventAbsent = 不在(その鍵では積まれていない — ReadRow の Missing と
   同じ意味で、<- は Absent を出す)/ EventRetired = 値(積まれたが保持の期限で消えた出来事の番号を持つ、呼び手が扱う普通の答え)/
   Unreachable = 失敗(<- は Raise(Unreachable) を出す)。答える handler は今までどおり 4 つとも値で答える(R4)。"
  {:fields [(: stream str) (: idempotency-key str)]
   :pre [(checked-table-name stream "ReadEventByKey.stream")
         (checked-event-key idempotency-key "ReadEventByKey")]
   :answer (| Event EventAbsent EventRetired Unreachable)
   :absent [EventAbsent]
   :failure [Unreachable]
   :value [EventRetired]
   :tags {:context "records" :role "foundation"}})


(defclass [(dataclass :frozen True)] ReadSignalSource [EffectBase]
  "この組で記録に答えている置き場の、源の工場を問う記録の effect(#3127)。答え = SignalSourceFactory(doeff_records.event_source の型)。答えるのは記録の handler 自身(memory の
   handler = 自分の置き場の書きで鳴る模擬の源・HTTP の client と PostgreSQL = RECORDS-SIGNAL-SOURCE)— 源は必ず問うた所の記録の handler の置き場に
   結ばれる。Ask にしないのは、組の内側の設定の読み手(決まった鍵だけを持ち、知らない鍵を断る)に横取りされうるため — 記録の effect なら
   設定の読み手は触らない。")


(defclass [(dataclass :frozen True)] AwaitRecordsBack [EffectBase]
  "記録の置き場が、表か列 names に再び答えるようになるまで待つ記録の effect(#3469)— 合図の源と HTTP の client(要求と答え・#3557)が置き場に
   届かない(Unreachable)時に、撃ち直しを時間で繰り返さず、戻りを出来事として待つため。答え = None(戻った)。期限は持たない — 待つ側が
   上限つきで待ち、上限を過ぎれば源は落ち、client は Unreachable を返す。答えるのは戻りを知る所: memory の handler = 止まり
   (faults.SetStoreOutage)が解けて呼び鈴が鳴った時・本番 = doeff-cluster の coordinator が記録の service の Ready を知らせた時(#3470)。
   HTTP の client と PostgreSQL の handler は答えずに外へ通す。"
  (#^ (get tuple #(str ...)) names)
  (defn #^ None __post_init__ [self]
    (when (or (not (isinstance self.names tuple)) (not self.names))
      (raise (ValueError (.format "AwaitRecordsBack.names は表か列の名前の空でない tuple: {!r}" self.names))))
    (for [name self.names]
      (checked-table-name name "AwaitRecordsBack.names"))))


(defclass [(dataclass :frozen True)] ReadSourcePatience [EffectBase]
  "置き場の止まりに耐える時間を問う記録の effect(#3469)— 問うのは止まりに耐える物(合図の源の止まりの拍・止まりの間も書き手の入力を
   process の中に持ち、戻りの合図で撃ち直す物 — client の要求の待ちは問わない・#3557)。答え = SignalSourcePatience
   (doeff_records.event_source の型・0 = 耐えない)。答えるのは組み立てが置く source-patience-handler(既定の値は無い)。HTTP の client の
   要求を待つ時間は別の問い ReadRequestPatience(#3557 — 2 つの違う問いを 1 つに載せると、handler を並べる位置で答え分けるしかなくなる)。
   Ask にしないのは ReadSignalSource と同じ理由(組の内側の設定の読み手に横取りされうる)。")


(defclass [(dataclass :frozen True)] ReadRequestPatience [EffectBase]
  "HTTP の client が、要求 1 つが置き場に届かない時に置き場の戻りを待つ時間を問う記録の effect(#3557)— 問うのは client だけ(要求のたびに)。
   答え = RequestPatience(doeff_records.http_client の型・0 = 待たない)。答えるのは組み立てが client の外側に置く request-patience-handler
   (既定の値は無い — 置き忘れは client の最初の要求で、答え手の無い effect として名指して落ちる)。待てない組み立てと呼びは、0 秒の名のある
   答え手 records-unwaited を置いて選ぶ。合図の源が止まりに耐える時間は別の問い ReadSourcePatience。")
