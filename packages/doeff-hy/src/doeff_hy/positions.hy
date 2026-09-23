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
;;; 続けて上から、位置を持つ式の子のうち位置の無い物(合成した葉 = `_guard-statement-value`
;;; の関数名など)に親の位置を付ける。位置を既に持つ model(reader が作った利用者の式)
;;; には触らない。

(import hy.models [Object Sequence])

(defn _positioned? [model]
  (hasattr model "_start_line"))

(defn _copy-position [target origin]
  (setv (. target start-line) (. origin start-line)
        (. target start-column) (. origin start-column)
        (. target end-line) (. origin end-line)
        (. target end-column) (. origin end-column)))

(defn _inherit-down [tree]
  "位置を持つ式の子のうち、位置の無い物(合成した葉・利用者の式を含まない合成)に親の位置を
   付ける。`(_guard-statement-value form ...)` の関数名や `(yield (Resume k v))` の `k` が
   macro 呼び出しの位置でなく、それを包む利用者の式の位置を持つように。"
  (for [child tree]
    ;; macro が `~(str name)` で差し込んだ素の Python の値は、まだ model でない(Hy が
    ;; 後で model にする)ので属性を付けられない。model だけに付ける。
    (when (and (isinstance child Object) (not (_positioned? child)))
      (_copy-position child tree))
    (when (isinstance child Sequence)
      (_inherit-down child))))

(defn locate-synthesized [tree]
  "macro の出力に位置を付けて返す(その場で書き換える)。

   1. 下から: 位置の無い Sequence に、位置を持つ子の範囲を付ける(`_locate-up`)。
   2. 上から: 位置を持つ式の子のうち、まだ位置の無い物に親の位置を付ける(`_inherit-down`)。
   一番外の式が位置を持たない(利用者の式を 1 つも含まない)時は何もしない — Hy が macro
   呼び出しの位置で埋める。"
  (_locate-up tree)
  (cond
    (not (isinstance tree Sequence)) None
    (_positioned? tree) (_inherit-down tree)
    True (for [child tree]
           (when (and (isinstance child Sequence) (_positioned? child))
             (_inherit-down child))))
  tree)

(defn _locate-up [tree]
  "位置を持たない Sequence に、位置を持つ子の範囲を付けて tree を返す(その場で書き換える)。

   位置を持つ利用者の式の中にも合成が在りうる(`_expand-bangs` が作り直した式の子)ので、
   位置の有無によらず子へは必ず降り、自分の位置は持っていない時だけ付ける。"
  (when (isinstance tree Sequence)
    (for [child tree]
      (_locate-up child))
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
