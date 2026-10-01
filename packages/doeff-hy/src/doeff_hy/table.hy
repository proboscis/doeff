;;; 書き換えない表 — 文字列の鍵 → 値の、作った後に決して変わらない表(Table)と、その書き(TableWrite)。
;;;
;;; 何のためか: 1 拍ごとに表(鍵 → 値)を組み直す読み手(agora の画面の投影の作業場 — agora-redesign #2183・#2253)は、
;;;   (1) 書きの後も、古い表を持つ読み手(別の thread で答え続ける処理のループ)には古い行を見せたい — 表をその場で書き換えられない。
;;;   (2) それでも 1 拍に変わる行は数十なので、拍ごとに表を丸ごと写したくない(14 万行の写しで 1 拍 約 2 ms)。
;;;   (3) 読み手の層は写像(dict・Mapping)を受け渡さず、鍵 → 値を引く関数で引く(agora の線引き 2 の (b))。
;;; Table は 3 つを満たす: 内側は「基 + 差分」の 2 層で、基は作った後に決して書き換えない。with-writes は差分だけを写して新しい
;;; Table を返す(写すのは差分だけ)。差分が基の 1/COMPACT-RATIO を超えたら、基と差分を合わせた新しい基を作る(その書きだけ O(n)・
;;; 均すと小さい)。読みは差分 → 基の 2 回引き。Table は Mapping ではない(写像として受け渡さない)— 引くのは row・keys・size だけ。
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


(defclass [(dataclass :frozen True)] TableWrite [(get Generic V)]
  "表への書き 1 つ: key の行を value にする(value = None は行を消す)。"
  (#^ str key)
  (#^ (| V None) value))


(defclass Table [(get Generic V)]
  "書き換えない表(頭の註)。作るのは table-of。引くのは row・keys・size、書きは with-writes(新しい Table を返す)。"
  (setv __slots__ #("_base" "_delta" "_removed" "_size"))
  (#^ (get dict #(str V)) _base)
  (#^ (get dict #(str V)) _delta)
  (#^ (get frozenset str) _removed)
  (#^ int _size)

  (defn __init__ [self #^ (get dict #(str V)) base #^ (get dict #(str V)) delta #^ (get frozenset str) removed]
    (object.__setattr__ self "_base" base)
    (object.__setattr__ self "_delta" delta)
    (object.__setattr__ self "_removed" removed)
    (object.__setattr__ self "_size" (+ (- (len base) (sum (gfor key removed :if (in key base) 1)))
                                        (sum (gfor key delta :if (not-in key base) 1)))))

  (defn #^ (| V None) row [self #^ str key]
    "鍵 key の行(無ければ None)。"
    (cond (in key self._delta) (get self._delta key)
          (in key self._removed) None
          True (.get self._base key)))

  (defn #^ (get tuple #(str ...)) keys [self]
    "鍵の列(並びは決めない)。"
    (+ (tuple (gfor key self._base :if (and (not-in key self._removed) (not-in key self._delta)) key))
       (tuple self._delta)))

  (defn #^ int size [self]
    "行の数。"
    self._size)

  (defn #^ "Table[V]" with-writes [self #^ (get tuple #((get TableWrite V) ...)) writes]
    "書きの列を当てた新しい Table(この Table は変わらない)。"
    (setv delta (dict self._delta)
          removed (set self._removed))
    (for [write writes]
      (if (is write.value None)
          (do (.pop delta write.key None)
              (when (in write.key self._base)
                (.add removed write.key)))
          (do (setv (get delta write.key) write.value)
              (.discard removed write.key))))
    (if (> (+ (len delta) (len removed)) (max MIN-DELTA (// (len self._base) COMPACT-RATIO)))
        (Table (| (dfor #(key value) (.items self._base) :if (not-in key removed) key value) delta) {} (frozenset))
        (Table self._base delta (frozenset removed))))

  (defn #^ str __repr__ [self]
    (.format "Table(size={})" self._size))

  (defn __setattr__ [self #^ str name #^ object value]
    (raise (AttributeError (.format "Table は変えられない(欄 {!r} を書こうとした)" name)))))


(defn #^ (get Table V) table-of [#^ (get tuple #((get TableWrite V) ...)) rows]
  "行の列から Table を作る(value = None の行は入れない)。"
  (Table (dfor write rows :if (is-not write.value None) write.key write.value) {} (frozenset)))
