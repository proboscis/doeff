;;; 書き換えない表 — 文字列の鍵 → 値の、作った後に決して変わらない表(Table)と、その書き(TableWrite)。
;;;
;;; 何のためか: 1 拍ごとに表(鍵 → 値)を組み直す読み手(agora の画面の投影の作業場 — agora-redesign #2183・#2253)は、
;;;   (1) 書きの後も、古い表を持つ読み手(別の thread で答え続ける処理のループ)には古い行を見せたい — 表をその場で書き換えられない。
;;;   (2) それでも 1 拍に変わる行は数十なので、拍ごとに表を丸ごと写したくない(14 万行の写しで 1 拍 約 2 ms)。
;;;   (3) 読み手の層は写像(dict・Mapping)を受け渡さず、鍵 → 値を引く関数で引く(agora の線引き 2 の (b))。
;;; Table は 3 つを満たす: 内側は「基 + 差分」の 2 層で、基は作った後に決して書き換えない。with-writes は差分だけを写して新しい
;;; Table を返す(写すのは差分だけ)。差分が基の 1/COMPACT-RATIO を超えたら、基と差分を合わせた新しい基を作る(その書きだけ O(n)・
;;; 均すと小さい)。読みは差分 → 基の 2 回引き。Table は Mapping ではない(写像として受け渡さない)— 引くのは row・keys・rows・items・
;;; size だけ。
;;; 基が MIN-DELTA 行未満の小さな表は差分の層を持たず、書くたびに基を写す — 写しは小さく、差分の層の費用(1 回の書きごとに差分の dict を
;;; 丸ごと写す・集合の演算)の方が重い(dict の 1 鍵の写し 0.3〜0.7 µs に対し差分の層の書き 約 4.7 µs — agora-redesign #2715)。
;;;
;;; 1 拍の中で書いた行を読み直す書き手は、下書き(TableDraft — 下の節)で書きを貯め、拍の終わりに 1 回だけ with-writes を当てる。
;;;
;;; FrozenMap(frozen.hy)との違い: FrozenMap は「鍵の集合が開いた値」を凍らせる写像で、書くたびに丸ごと写す。Table は拍ごとに
;;; 少しずつ変わる大きな表のための物で、写像ではない。
(import dataclasses [dataclass])
(import typing [Generic TypeVar])

(setv V (TypeVar "V"))

;; 差分が基の 1/COMPACT-RATIO を超えたら基を作り直す。
(setv COMPACT-RATIO 8)
;; 基が小さい間は差分の上限を下げすぎない(小さい表で毎回作り直さない)。
(setv MIN-DELTA 64)
;; 真偽と長さの読みを断る時の文(Table と TableDraft で同じ文 — 直し方を名指す)。
(setv TABLE-TRUTH-REFUSAL "Table / TableDraft は真偽で読まない — 空かどうかは (= (.size t) 0) か (> (.size t) 0) で読む(agora-redesign #2755)")
(setv TABLE-LEN-REFUSAL "Table / TableDraft は len で数えない — 行の数は (.size t) で読む(agora-redesign #2755)")


(defclass [(dataclass :frozen True)] TableWrite [(get Generic V)]
  "表への書き 1 つ: key の行を value にする(value = None は行を消す)。"
  (#^ str key)
  (#^ (| V None) value))


(defclass Table [(get Generic V)]
  "書き換えない表(頭の註)。作るのは table-of。引くのは row・keys・rows・items・size、書きは with-writes(新しい Table を返す)。"
  (setv __slots__ #("_base" "_delta" "_removed" "_size"))
  (#^ (get dict #(str V)) _base)
  (#^ (get dict #(str V)) _delta)
  (#^ (get frozenset str) _removed)
  (#^ int _size)

  (defn #^ None __init__ [self #^ (get dict #(str V)) base #^ (get dict #(str V)) delta #^ (get frozenset str) removed]
    (object.__setattr__ self "_base" base)
    (object.__setattr__ self "_delta" delta)
    (object.__setattr__ self "_removed" removed)
    ;; 行の数 = 基の行 − 消した基の行 + 基に無い差分の行(鍵の集合の演算で数える — 1 件ずつ Hy で数えない・agora-redesign #2412)。
    (object.__setattr__ self "_size" (+ (- (len base) (len (& removed (.keys base))))
                                        (len (- (.keys delta) (.keys base))))))

  (defn #^ (| V None) row [self #^ str key]
    "鍵 key の行(無ければ None)。"
    (cond (in key self._delta) (get self._delta key)
          (in key self._removed) None
          True (.get self._base key)))

  (defn #^ (get tuple #(str ...)) keys [self]
    "鍵の列(並びは決めない)。"
    (+ (tuple (gfor key self._base :if (and (not-in key self._removed) (not-in key self._delta)) key))
       (tuple self._delta)))

  (defn #^ (get tuple #(V ...)) rows [self]
    "行の値の列(並びは決めない — 鍵の列 keys と同じ並び)。全行を読む読み手(数え・一覧)のため。"
    (+ (tuple (gfor #(key value) (.items self._base) :if (and (not-in key self._removed) (not-in key self._delta)) value))
       (tuple (.values self._delta))))

  (defn #^ (get tuple #((get tuple #(str V)) ...)) items [self]
    "鍵と行の組の列(keys・rows と同じ並び)— 鍵が値から導けない表の全行を、鍵ごと読む読み手(保存の綴り)のため(agora-redesign #2715)。"
    (+ (tuple (gfor #(key value) (.items self._base) :if (and (not-in key self._removed) (not-in key self._delta)) #(key value)))
       (tuple (.items self._delta))))

  (defn #^ int size [self]
    "行の数。"
    self._size)

  (defn #^ "Table[V]" with-writes [self #^ (get tuple #((get TableWrite V) ...)) writes]
    "書きの列を当てた新しい Table(この Table は変わらない)。同じ鍵の書きが列に 2 度あれば後の方が残る。"
    ;; 小さな表は書きの列を写しへ直に当てる(鍵 → 行の中間の dict を組まない — _with-pending の小さな表の道と同じ答え)。
    (when (and (< (len self._base) MIN-DELTA) (not self._delta) (not self._removed))
      (setv written (dict self._base))
      (for [write writes]
        (if (is write.value None)
            (.pop written write.key None)
            (setv (get written write.key) write.value)))
      (return (_base-only-table written)))
    (_with-pending self (dfor write writes write.key write.value)))

  (defn #^ str __repr__ [self]
    (.format "Table(size={})" self._size))

  ;; 真偽と長さの読みを断る(agora-redesign #2755・#2708 の I0b)— この 2 つが無いと Python は表を常に真と読み、`(not table)` は
  ;; 空の表でも偽になる(空かどうかの早い戻りが黙って効かない)。断って、見落としを例外で見つける。読むのは size。
  (defn #^ bool __bool__ [self]
    "真偽で読ませない(上の註)。空かどうかは (= (.size t) 0) / (> (.size t) 0) で読む。"
    (raise (TypeError TABLE-TRUTH-REFUSAL)))

  (defn #^ int __len__ [self]
    "len で数えさせない(上の註)。行の数は (.size t) で読む。"
    (raise (TypeError TABLE-LEN-REFUSAL)))

  (defn #^ bool __eq__ [self #^ object other]
    "行の中身で比べる(鍵の集合が同じで、鍵ごとの行が等しい)— 表は値なので、写しと元の表は中身が同じなら等しい(基と差分の分け方は
     問わない)。表を欄に持つ凍った記録の等しさ(畳みの前後の cache の比べ)のため。"
    ;; 同じ表は中身を読まずに等しい(書きの無い拍で前後の欄が同じ object のまま — 比べを O(1) に・agora-redesign #2715)。
    (when (is self other)
      (return True))
    (when (not (isinstance other (type self)))
      (return False))
    (and (= self._size (.size other))
         (all (gfor #(key value) (zip (.keys self) (.rows self)) (= (.row other key) value)))))

  ;; 中身で比べる表は辞書の鍵にしない(書き換えない表でも、比べに全行を読む)— __eq__ だけを持つ class の __hash__ は Python が None にする。

  (defn #^ (get tuple #((get type "Table[V]") (get tuple #((get dict #(str V)) (get dict #(str V)) (get frozenset str))))) __reduce__ [self]
    "写し(copy.copy・copy.deepcopy)と pickle のため — 欄の書きを断る表を、作る口(__init__)を通して作り直す。"
    #(Table #(self._base self._delta self._removed)))

  (defn #^ None __setattr__ [self #^ str name #^ object value]
    (raise (AttributeError (.format "Table は変えられない(欄 {!r} を書こうとした)" name)))))


(defn #^ (get Table V) _base-only-table [#^ (get dict #(str V)) base]
  "差分の層を持たない表(基だけ)を、行の数を集合の演算で数え直さずに作るため — 小さな表の書きの道(行の数 = 基の行の数)。
   base はこの module の中で作った写しだけを受ける(作った後に書き換えない)。"
  (setv table (object.__new__ Table))
  (object.__setattr__ table "_base" base)
  (object.__setattr__ table "_delta" {})
  (object.__setattr__ table "_removed" (frozenset))
  (object.__setattr__ table "_size" (len base))
  table)


(defn #^ (get Table V) _with-pending [#^ (get Table V) table #^ (get dict #(str (| V None))) pending]
  "鍵 → 行(None は行を消す)の書きを 1 度に当てた新しい Table(table も pending も変わらない)— Table.with-writes と TableDraft.freeze が
   通る 1 点(この module の中だけ — 写像を受け渡す口を公開しない)。書き 1 件ごとに TableWrite を組んで Hy の繰り返しで当てると、
   下書きの freeze(数万件の書き)が dict の写しの約 4 倍重かった(agora-redesign #2412)ので、書きを dict の一括の操作(内包・集合の
   差と和・| の合わせ)で当てる。"
  ;; 小さな表(差分の層を持たない・基が MIN-DELTA 行未満)は基を写して書く — 差分の層の費用の方が写しより重い(頭の註)。
  ;; 書きは少ない(1 拍に数件)ので、内包と集合の演算を組まずに写しへ 1 件ずつ当てる。
  (when (and (< (len table._base) MIN-DELTA) (not table._delta) (not table._removed))
    (setv written (dict table._base))
    (for [#(key value) (.items pending)]
      (if (is value None)
          (.pop written key None)
          (setv (get written key) value)))
    (return (_base-only-table written)))
  (setv puts (dfor #(key value) (.items pending) :if (is-not value None) key value)
        drops (frozenset (gfor #(key value) (.items pending) :if (is value None) key))
        delta (| (dfor #(key value) (.items table._delta) :if (not-in key drops) key value) puts)
        removed (| (- table._removed (.keys puts)) (& drops (.keys table._base))))
  (if (> (+ (len delta) (len removed)) (max MIN-DELTA (// (len table._base) COMPACT-RATIO)))
      (Table (| (dfor #(key value) (.items table._base) :if (not-in key removed) key value) delta) {} (frozenset))
      (Table table._base delta (frozenset removed))))


(defn #^ (get Table V) table-of [#^ (get tuple #((get TableWrite V) ...)) rows]
  "行の列から Table を作る(value = None の行は入れない)。"
  (Table (dfor write rows :if (is-not write.value None) write.key write.value) {} (frozenset)))


;;; 下書き(TableDraft)— 1 拍の中で書いた行を同じ拍の中で読み直す書き手のための物。
;;;
;;; 何のためか: 畳み(agora の画面の投影の作業場 — agora-redesign #2254)は行を 1 つ書くたびに、同じ拍の中でその行を読み直す。
;;;   with-writes を 1 件ずつ当てると、1 件ごとに差分(最大で基の 1/COMPACT-RATIO)を写すので、一覧の拍(数万件の書き)で
;;;   写しが数億回になる。下書きは書きを手元に貯め(1 件 O(1))、読みは下書き → 元の表の 2 回引き、拍の終わりに freeze が
;;;   with-writes を 1 回だけ当てて新しい Table を返す。
;;; 下書きは書き換える物なので、作った書き手の外へ出さない(外へ渡すのは freeze の答えの Table)。元の表は下書きの書きで変わらない。

(defclass TableDraft [(get Generic V)]
  "Table への書きの下書き(上の註)。作るのは draft-of。引くのは row・keys・rows、書きは put・remove、拍の終わりに freeze。"
  (setv __slots__ #("_table" "_writes"))
  (#^ (get Table V) _table)
  (#^ (get dict #(str (| V None))) _writes)

  (defn #^ None __init__ [self #^ (get Table V) table]
    (setv self._table table
          self._writes {}))

  (defn #^ (| V None) row [self #^ str key]
    "鍵 key の行(この下書きの書きが先・無ければ None)。"
    (if (in key self._writes)
        (get self._writes key)
        (.row self._table key)))

  (defn #^ (get tuple #(str ...)) keys [self]
    "鍵の列(この下書きの書きを当てた後・並びは決めない)。"
    (+ (tuple (gfor key (.keys self._table) :if (not-in key self._writes) key))
       (tuple (gfor #(key value) (.items self._writes) :if (is-not value None) key))))

  (defn #^ (get tuple #(V ...)) rows [self]
    "行の値の列(この下書きの書きを当てた後・並びは決めない — 鍵の列 keys と同じ並び)。"
    (+ (tuple (gfor #(key value) (zip (.keys self._table) (.rows self._table)) :if (not-in key self._writes) value))
       (tuple (gfor value (.values self._writes) :if (is-not value None) value))))

  (defn #^ None put [self #^ str key #^ V value]
    "鍵 key の行を value にする。"
    (setv (get self._writes key) value))

  (defn #^ None remove [self #^ str key]
    "鍵 key の行を消す(無い鍵でもよい)。"
    (setv (get self._writes key) None))

  (defn #^ (get Table V) freeze [self]
    "書きを当てた新しい Table(書きが無ければ元の表そのもの)。何度呼んでもよい — 下書きも元の表も変わらない。"
    (if self._writes
        (_with-pending self._table self._writes)
        self._table))

  (defn #^ int size [self]
    "行の数(この下書きの書きを当てた後)— 下書きの空かどうかを真偽でなく数で読むため(agora-redesign #2755)。"
    (len (.keys self)))

  (defn #^ bool __bool__ [self]
    "真偽で読ませない(Table と同じ — 空かどうかは (= (.size d) 0))。"
    (raise (TypeError TABLE-TRUTH-REFUSAL)))

  (defn #^ int __len__ [self]
    "len で数えさせない(Table と同じ — 行の数は (.size d))。"
    (raise (TypeError TABLE-LEN-REFUSAL)))

  (defn #^ str __repr__ [self]
    (.format "TableDraft(base={!r}, writes={})" self._table (len self._writes))))


(defn #^ (get TableDraft V) draft-of [#^ (get Table V) table]
  "表 table への書きの下書きを作る(元の表は変わらない)。"
  (TableDraft table))
