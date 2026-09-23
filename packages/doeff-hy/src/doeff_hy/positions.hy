;;; macro が合成した式に、包んでいる利用者の式の位置を付ける(traceback が落ちた行を指すため)。
;;;
;;; 起きていたこと(2026-09-23 実測): `defk` / `defservice` の本体で落ちると、traceback が
;;; 本体の先頭(`defk` の行)を指し、落ちた行を指さなかった。
;;;
;;; 仕組み: Hy は macro の結果のうち位置を持たない model を、macro 呼び出しの位置で
;;; 埋める(`hy.macros.macroexpand` の `replace_hy_obj(obj, tree)` = 再帰の `replace`)。
;;; doeff-hy の macro は本体の式を作り直す(`_expand-bangs` は本体の Expression を
;;; すべて `(hy.models.Expression ...)` で組み直す・`<-` は `(setv x (yield e))` を合成する・
;;; `defhandler` は `resume` を `(yield (Resume k e))` へ書き換える)。作り直した式は位置を
;;; 持たないので、Hy がそこへ macro 呼び出しの位置(= `defk` の頭)を入れ、Python の
;;; code object の行表がその位置になる。葉(Symbol・数)は利用者の位置を保ったまま残るので、
;;; AST を見ると `c = x // 0` の文は正しい行なのに、式 `x // 0` だけが `defk` の範囲を持つ。
;;;
;;; 直し方(1 点): macro の出力を返す前に `locate-synthesized` を当てる。位置の無い
;;; Sequence に、位置を持つ子の範囲(最小の始まり〜最大の終わり)を付ける。子を先に
;;; 埋めるので、入れ子の合成(`(do (setv x (yield e)) (assert ...))`)も内側から外側へ
;;; 利用者の式の範囲を得る。利用者の式を 1 つも含まない合成(`_guard-performed` の呼び出し
;;; など)は位置を持たないまま残り、Hy が従来どおり macro 呼び出しの位置で埋める。
;;; 位置を既に持つ model(reader が作った利用者の式)には触らない。葉にも触らない
;;; (同じ Symbol の object を複数の場所で使い回す合成があるため)。

(import hy.models [Sequence])

(defn _positioned? [model]
  (hasattr model "_start_line"))

(defn locate-synthesized [tree]
  "位置を持たない Sequence に、位置を持つ子の範囲を付けて tree を返す(その場で書き換える)。

   位置を持つ利用者の式の中にも合成が在りうる(`_expand-bangs` が作り直した式の子)ので、
   位置の有無によらず子へは必ず降り、自分の位置は持っていない時だけ付ける。"
  (when (isinstance tree Sequence)
    (for [child tree]
      (locate-synthesized child))
    (when (not (_positioned? tree))
      (setv located (lfor child tree :if (_positioned? child) child))
      (when located
        (setv first (min located :key (fn [m] #((. m start-line) (. m start-column))))
              last (max located :key (fn [m] #((. m end-line) (. m end-column)))))
        (setv (. tree start-line) (. first start-line)
              (. tree start-column) (. first start-column)
              (. tree end-line) (. last end-line)
              (. tree end-column) (. last end-column)))))
  tree)
