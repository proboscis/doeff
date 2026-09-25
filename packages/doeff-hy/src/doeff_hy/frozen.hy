;;; 凍らせた写像 — 文字列の鍵 → 値の変えられない写像(FrozenMap)と、JSON の値を深く凍らせる・戻す関数。
;;;
;;; 何のためか: frozen の dataclass の欄に dict を置くと、dataclass は凍っていても中身は誰でも書き換えられる
;;; (effect の入力・答え・行・設定が、作った後に黙って変わる)。欄の名前と型が決まっている値は dataclass か
;;; pydantic の model で表す。それでも「鍵の集合が開いている写像」— env・CLI の settings・道具の入力・記録の行の
;;; 欄 → 値 のように、中身の形を相手(CLI・置き場の宣言)が決める値 — は写像のまま持つ。その写像をここの
;;; FrozenMap で凍らせる。
;;;
;;; JSON の値の凍らせ方: freeze-json は object → FrozenMap・array → tuple を深く行う(中の値まで変えられない)。
;;; JSON へ書く境界(json.dumps・HTTP・DB)では thaw-json で dict / list へ戻す — dict への変換はその境界の 1 か所だけ。
;;;
;;; 等しさ: collections.abc.Mapping の等しさ(同じ鍵と値の組なら dict とも等しい)。hash は中の値が hash できる時だけ
;;; (freeze-json で凍らせた JSON の値は常に hash できる)。pickle・copy は作り直しで保つ。
(import collections.abc [Mapping Iterator])
(import typing [TypeVar])

(setv V (TypeVar "V"))


(defclass FrozenMap [(get Mapping #(str V))]
  "文字列の鍵 → 値の変えられない写像。作る時に source(写像か鍵と値の対の列)を写し取り、以後は変えられない。
   中の値はそのまま持つ(JSON の値を深く凍らせるのは freeze-json)。"
  (setv __slots__ #("_entries" "_hash"))
  (#^ (get dict #(str V)) _entries)
  (#^ (| int None) _hash)

  (defn __init__ [self [source None]]
    (setv entries (if (is source None) {} (dict source)))
    (for [key entries]
      (when (not (isinstance key str))
        (raise (TypeError (.format "FrozenMap の鍵は文字列: {!r}" key)))))
    (object.__setattr__ self "_entries" entries)
    (object.__setattr__ self "_hash" None))

  (defn #^ V __getitem__ [self #^ str key]
    (get self._entries key))

  (defn #^ (get Iterator str) __iter__ [self]
    (iter self._entries))

  (defn #^ int __len__ [self]
    (len self._entries))

  (defn #^ int __hash__ [self]
    (setv cached self._hash)
    (when (is cached None)
      (setv cached (hash (frozenset (.items self._entries))))
      (object.__setattr__ self "_hash" cached))
    cached)

  (defn #^ str __repr__ [self]
    (.format "FrozenMap({!r})" self._entries))

  (defn __setattr__ [self #^ str name #^ object value]
    (raise (AttributeError (.format "FrozenMap は変えられない(欄 {!r} を書こうとした)" name))))

  (defn __delattr__ [self #^ str name]
    (raise (AttributeError (.format "FrozenMap は変えられない(欄 {!r} を消そうとした)" name))))

  (defn __reduce__ [self]
    #(FrozenMap #((dict self._entries)))))


(defn #^ object freeze-json [#^ object value]
  "JSON の値を深く凍らせる: object(写像)→ FrozenMap・array(list / tuple)→ tuple。ほかの値はそのまま。
   既に凍った値を渡しても同じ形が返る(何度撃っても同じ)。"
  (cond
    (isinstance value Mapping) (FrozenMap (gfor #(key item) (.items value) #(key (freeze-json item))))
    (isinstance value #(list tuple)) (tuple (gfor item value (freeze-json item)))
    True value))


(defn #^ object thaw-json [#^ object value]
  "凍らせた JSON の値を json.dumps が受ける形へ戻す: 写像 → dict・tuple / list → list(新しい値 — 元は変えない)。
   JSON へ書く境界だけで撃つ。"
  (cond
    (isinstance value Mapping) (dfor #(key item) (.items value) key (thaw-json item))
    (isinstance value #(list tuple)) (lfor item value (thaw-json item))
    True value))


(defn #^ FrozenMap frozen-json-object [#^ object value #^ str what]
  "JSON の object(写像)を深く凍らせた FrozenMap。写像でなければ TypeError(what = 誤りの文の欄の名)。
   frozen の dataclass の __post_init__ が、受けた写像を凍らせる時に使う。"
  (when (not (isinstance value Mapping))
    (raise (TypeError (.format "{} は写像(欄の名 → JSON の値): {!r}" what value))))
  (FrozenMap (gfor #(key item) (.items value) #(key (freeze-json item)))))


(defn #^ FrozenMap frozen-map-of [#^ object value #^ str what]
  "写像を浅く写し取った FrozenMap(中の値はそのまま — 値が dataclass などの凍った型の時)。写像でなければ TypeError。"
  (when (not (isinstance value Mapping))
    (raise (TypeError (.format "{} は写像: {!r}" what value))))
  (if (isinstance value FrozenMap) value (FrozenMap value)))
