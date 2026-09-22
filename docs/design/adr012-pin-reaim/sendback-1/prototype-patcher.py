import pathlib, sys
import os
W = pathlib.Path(os.environ.get("SB_WT", str(pathlib.Path.home() / ".worktrees/doeff-wt-adr012-sendback")))
p = W / "docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy"
s = p.read_text(encoding="utf-8")

def sub(old, new, times=1):
    global s
    n = s.count(old)
    assert n == times, f"anchor hit {n} != {times}: {old[:90]!r}"
    s = s.replace(old, new)

# (A) import: hy の reader と models・dataclass
sub("(import dataclasses [replace])\n(import pathlib [Path])",
    "(import dataclasses [dataclass replace])\n(import pathlib [Path])\n(import hy)\n"
    "(import hy.models [Expression List Sequence Symbol])")

# (B) form で読む部品(call-args-of の後・名簿の節の手前)
HELPERS = r'''

;; --- form で読む口(行の折れ方・空白・局所の名に依らない) --------------------
;; 上の 3 つ(collapsed-code / readers-of / call-args-of)は**行**を均して読むので、
;; 「同じ意味を別の綴りで書く」正当な便には強いが、**行をまたぐ構造**(束ねた名がその後
;; どう読まれたか)は読めない。⇒ 構造を撃つ針はここから下の口で、Hy 自身の reader
;; (hy.read-many)が返す form を読む。第 2 の reader をこの冊に書かない。
;; ⚠ 先例の読み方: hy_003 の冊も hy.read-many を使うが、読む対象は test の中に書いた**文字列**。
;;    **file を読む軸**(path・存在・解析)はここが初めて建てる。file が無い / 名が違う ⇒ Path.read-text が
;;    例外 = 赤・解析できない ⇒ hy.read-many が例外 = 赤(どちらも安全な向き)。緑へ倒れうる軸は
;;    「読めて解析もできるが狙いの呼び・読みが居ない」の 1 つだけで、それは針の側の『呼びは 1 つ』
;;    『名簿の読み手が現に読んでいる』の 2 つの assert が空の母集団を赤にする(0 件は緑ではない)。
;; 実弾 2026-09-22(依頼者 c-D6AFCPB1VRMNTVN9ECSZMCAS3T の差し戻し): R16 の「第 2 の合成点」の
;; 針は 2 つの正規表現で**1 行の字面**を読んでいたので、`(setv text …)` を 2 行に折る・`<-` で
;; 束ね直す・呼びの行を折る・送り先の引数でその場で組む、のどれでも素通りした(6 形を実射で確認)。
;; 針それ自体が law spelling-pins-proxy-for-shape ①(b) の族だった。
;; form にすると同時に死ぬ族が 3 つ(字面の針の残り約 405 本を畳む時の根拠): (1) 行の折れ方で
;; 素通り/誤検出する族 (2) 註が「その綴りを避けた」と説明している行を使用として数える族(reader は
;; `;;` を捨てる) (3) 文字列 literal の中の綴りを数える族(String と Symbol は別の節点)。

(defn #^ str form-use-head [node]
  "その form が『どの読み手として』読んでいるかの綴り(名簿と突き合わせる 1 点)。
   `(f …)` → \"f\"・`(.m recv …)` → \"recv.m\"・属性 `x.y` → \".\"・列 `[…]` → \"(列)\"。"
  (cond
    (isinstance node List) "(列)"
    (not (isinstance node Expression)) "(値)"
    (not (len node)) "(空)"
    True (do
           (setv head (get node 0))
           (cond
             (isinstance head Symbol) (str head)
             ;; `(.m recv …)` の頭は `(. None m)` — 受け手の名を付けて "recv.m" と綴る
             ;; (名簿が『どこへ積むか』まで名指せるように)。
             (and (isinstance head Expression) (= (len head) 3)
                  (= (str (get head 0)) ".") (= (str (get head 1)) "None"))
               (+ (if (and (> (len node) 1) (isinstance (get node 1) Symbol)) (str (get node 1)) "?")
                  "." (str (get head 2)))
             True "(式)"))))


(defn #^ str top-form-name-of [form]
  "頂点の form の名(`(defk f …)` → \"f\"・`(defn #^ str f …)` → \"f\")。名を持たない form は \"\"。"
  (when (or (not (isinstance form Expression)) (< (len form) 2)) (return ""))
  (when (not-in (str (get form 0)) #("defk" "deff" "defn" "defmacro" "defclass")) (return ""))
  (setv named (get form 1))
  (when (and (isinstance named Expression) (>= (len named) 2) (= (str (get named 0)) "annotate"))
    (setv named (get named 1)))
  (if (isinstance named Symbol) (str named) ""))


(defn #^ list child-parent-pairs [node]
  "form の木を #(親 子) で平らに並べる(models の列 = Expression / List / Dict / Set / Tuple /
   FString の中だけへ潜る — f-string の {…} の中も code なので潜る)。並びはソースの順。"
  (setv out [])
  (when (isinstance node Sequence)
    (for [child node]
      (.append out #(node child))
      (.extend out (child-parent-pairs child))))
  out)


(defn #^ bool assignment-target? [parent child]
  "child が `(setv 名 …)` / `(<- 名 型 …)` の**名の座**(書き込みの先)か。"
  (and (isinstance parent Expression) (>= (len parent) 2)
       (isinstance (get parent 0) Symbol) (in (str (get parent 0)) #("setv" "<-"))
       (is (get parent 1) child)))


(defn #^ set carriers-of [#^ list pairs #^ str bound introducer]
  "束ねた名から始めて、その文を**運ぶ名**の閉包を返す(同じ頂点の form の中だけ)。
   名簿の読み手が `(.m recv 文)` の形で受け手へ積んだら recv も運ぶ名・`(setv 名 文)` /
   `(<- 名 型 文)` で写したら 名も運ぶ名。introducer = 束ねた `<-` の form(そこは読みに数えない)。
   反例(盲検 B 2026-09-22): 束ねた text を bodies へ積んだ後、bodies を歩いて見出しを書き足す形は
   text の読みだけを見る針を素通りした — 積んだ先まで追わないと第 2 の合成点は閉じない。"
  (setv carriers #{bound})
  (setv grew True)
  (while grew
    (setv grew False)
    (for [#(p c) pairs]
      (when (and (isinstance c Symbol) (in (str c) carriers) (is-not p introducer)
                 (isinstance p Expression) (>= (len p) 2))
        (setv head (get p 0))
        (setv target None)
        (cond
          ;; `(.m recv 文)` — 頭は `(. None m)`・受け手は index 1・文はそれより後ろ
          (and (isinstance head Expression) (= (len head) 3) (= (str (get head 0)) ".")
               (= (str (get head 1)) "None") (isinstance (get p 1) Symbol) (is-not (get p 1) c))
            (setv target (str (get p 1)))
          ;; `(setv 名 文)` / `(<- 名 型 文)` — 文は末尾・名は index 1
          (and (isinstance head Symbol) (in (str head) #("setv" "<-")) (>= (len p) 3)
               (is (get p -1) c) (isinstance (get p 1) Symbol))
            (setv target (str (get p 1))))
        (when (and (is-not target None) (not-in target carriers))
          (.add carriers target)
          (setv grew True)))))
  carriers)


(defclass [(dataclass :frozen True :kw-only True)] BoundCall []
  "`(name …)` の呼び 1 か所を **form** で読んだ結果。
   top = 呼びを含む頂点の form の名・parent = 呼びを包む form の頭の綴り(束ねているなら \"<-\")・
   bound = 呼びの返りを束ねた名(親が `(<- 名 型 呼び)` でなければ None)・
   carriers = 文を運ぶ名の閉包(束ねた名 + 積んだ先 + 写した名・ソート済み)・
   uses = 運ぶ名の**読み** #(読み手の頭の綴り その form の綴り) の列(束ねた form と、運ぶ名ごとの
   最初の書き込み = 導入の座は除く)。"
  #^ str top
  #^ str parent
  #^ (| str None) bound
  #^ tuple carriers
  #^ tuple uses)


(defn #^ list bound-calls-of [#^ Path path #^ str name]
  "`(name …)` の呼びを form で読み、呼びごとに BoundCall を返す。
   『呼びを何が包んでいるか』と『束ねた名(とそれを運ぶ名)をその頂点の form の中で誰が読んだか』の
   2 点を構造で出すので、行の折れ方・空白・局所変数の名・引数の数に依らない。
   ⚠ 読めるのは**同じ頂点の form の中**だけ(別の form へ渡った後の組み替え・handler の実 I/O の
   中の組み替えは読めない)。針はこの限界を註に書き、覆っていない範囲を『塞いだ』と名乗らない。"
  (setv out [])
  (for [form (hy.read-many (.read-text path :encoding "utf-8") :filename (str path))]
    (when (isinstance form Expression)
      (setv top (top-form-name-of form))
      (setv pairs (child-parent-pairs form))
      (for [#(parent child) pairs]
        (when (and (isinstance child Expression) (len child)
                   (isinstance (get child 0) Symbol) (= (str (get child 0)) name))
          (setv parent-head (form-use-head parent))
          (setv bound None)
          (when (and (isinstance parent Expression) (= parent-head "<-") (>= (len parent) 3)
                     (is (get parent -1) child) (isinstance (get parent 1) Symbol))
            (setv bound (str (get parent 1))))
          (setv carriers (if (is bound None) #{} (carriers-of pairs bound parent)))
          (setv uses [])
          ;; 運ぶ名ごとに最初の書き込みの座(導入・例 `(setv bodies [])`)だけは読みに数えない。
          ;; 束ねた名の導入は `<-` の form そのもの(parent)なので最初から済みにする ⇒ 束ねた名への
          ;; 2 度目の書き込み `(setv text …)` は読みに数える(読まずに置き換える形も赤)。
          (setv introduced (if (is bound None) #{} #{bound}))
          (for [#(p c) pairs]
            (when (and (isinstance c Symbol) (in (str c) carriers) (is-not p parent))
              (if (and (assignment-target? p c) (not-in (str c) introduced))
                  (.add introduced (str c))
                  (.append uses #((form-use-head p) (cut (hy.repr p) 0 160))))))
          (.append out (BoundCall :top top :parent parent-head :bound bound
                                  :carriers (tuple (sorted carriers)) :uses (tuple uses)))))))
  out)
'''
sub("\n\n;; ---------------------------------------------------------------------------\n"
    ";; 集合の宣言(名簿)",
    HELPERS + "\n\n;; ---------------------------------------------------------------------------\n"
    ";; 集合の宣言(名簿)")

# (C) 名簿: 手番の文の読み手(IO-FAILURE-EDGES の後)
ROSTER = r'''

;; R16: judgment.mail-turn-text-of が組んだ手番の文を、**束ねた名(とそれを運ぶ名)のまま**受け取る
;; 読み手(file → {読み手の頭の綴り: その読み手が何をするか})。針は form で読んだ読みの集合をこの
;; 名簿と突き合わせる ⇒ 名簿の外の読み(その場で組み替える・見出しを足し直す・別の器へ渡す・
;; 積んだ先を歩いて書き足す)は赤、名簿の読み手が読まなくなった便も赤。読み手を足す/替える便は
;; ここへ 1 行宣言する(針は読み手の数も綴りも知らない — 知っているのはこの名簿だけ)。
;; 向きの規則(1 行): 禁止の形の列挙は**開いた集合**(新しい綴りは常に列の外から入る)・許す読み手の
;; 名簿は**閉じた集合**(名簿の外の読みは定義上赤)。字面の針を直す向きは常に後者への反転。
(setv MAIL-TURN-TEXT-CONSUMERS
      {"judgment.hy" {"bodies.append" "1 手番目の畳みと温かい send が読む同じ bodies へ、順のまま積む"
                      "tuple" "積み終えた bodies を組み替えずそのまま返す(運ぶ名の閉包の読み)"}
       "agentd.hy" {"SessionInterject" "走っている手番への注入の要求へ、組み替えずそのまま渡す"}})
'''
sub('''       "record flush" "拍の終わりに spool を会話の記録の service へ送る(段 9f lane 9f-2)"})
''',
    '''       "record flush" "拍の終わりに spool を会話の記録の service へ送る(段 9f lane 9f-2)"})
''' + ROSTER)

# (D)+(E) 針の本体を form の読みへ・註を主張の範囲へ揃える
OLD = '''       (for [#(name lines) [#("judgment.hy" judgment-lines) #("agentd.hy" (code-lines (/ ACP-DIR "agentd.hy")))]]
         (setv calls (call-args-of lines "mail-turn-text-of"))
         (assert (= (len calls) 1) f"{name} は手番の文を 1 度だけ組む(R16): 実測 {(len calls)}")
         (assert (in "body" (get calls 0))
                 f"{name} の合成の呼びに本文の役が渡っていない(R16): {(get calls 0) !r}")
         ;; 呼んだ**後で**その文を作り直さない(第 2 の合成点を置かない)。
         ;; 盲検 B(2026-09-22): 呼びは 1 つのまま、返った文に agentd が見出しを足す実装は
         ;; 旧い字面の針でも「呼び先と役」の針でも通る。条件つき(priority = urgent 等)なので
         ;; 挙動の例でも当たらない ⇒ 構造で撃つ。束ねた名がその form の中で setv され直したら赤。
         ;; ⚠ この穴は**元から在った**(旧い字面の針でも B の反例は緑で通った — 4 本の赤の原因ではない)。
         ;;    見つけたのは再照準の設計の盲検 B(2026-09-22)で、塞いだのはその再照準の便(card
         ;;    acp:kanban-issue:ki-7a7dd5cc6727)。以後この針を消すなら、代わりに何が第 2 の合成点を
         ;;    捕まえるかを先に置く。
         (setv bound None)
         (setv rebinds [])
         (for [line lines]
           (when (and (.startswith line "(") (is-not (.search TOP-FORM-RE line) None))
             (setv bound None))
           (setv hit (re.search r"\\(<-\\s+(\\S+)\\s+\\S+\\s+\\(mail-turn-text-of " line))
           (cond
             (is-not hit None) (setv bound (.group hit 1))
             (and (is-not bound None)
                  (is-not (re.search (+ r"\\(setv\\s+" (re.escape bound) r"\\s") line) None))
               (.append rebinds (.strip line))))
         (assert (= rebinds [])
                 (+ f"{name} が合成した文を呼びの後で作り直している —— 手番の文を組む座は "
                    f"judgment.mail-turn-text-of の 1 点(R16): {rebinds !r}")))
'''
NEW = '''       (for [#(name path) [#("judgment.hy" (/ ACP-DIR "judgment.hy")) #("agentd.hy" (/ ACP-DIR "agentd.hy"))]]
         (setv calls (call-args-of (code-lines path) "mail-turn-text-of"))
         (assert (= (len calls) 1) f"{name} は手番の文を 1 度だけ組む(R16): 実測 {(len calls)}")
         (assert (in "body" (get calls 0))
                 f"{name} の合成の呼びに本文の役が渡っていない(R16): {(get calls 0) !r}")
         ;; 呼んだ**後で**その文を作り直さない(第 2 の合成点を置かない)。**form で読む** —
         ;; 呼びを包む form が `<-` でなければ赤(包む・その場で組む)。束ねた名と、それを運ぶ名の閉包
         ;; (名簿の読み手が `(.append recv 文)` で積んだ先の recv・`(setv 名 文)` で写した名)の読みが
         ;; 冊の名簿 MAIL-TURN-TEXT-CONSUMERS と食い違えば赤(組み替える読み手が増えた / 名簿の読み手が
         ;; 読まなくなった)。禁止の綴り(setv / setx / let …)を針の中に列挙**しない** — 禁止の列は
         ;; 開いた集合で新しい綴りは常に列の外から入る(それ自体が law spelling-pins-proxy-for-shape ①(b)
         ;; の族・この針の前の版がそれだった)。読み手の名簿は閉じた集合で、名簿の外は定義上赤。
         ;; 由来: 穴は**元から在った**(旧い字面の針でも通った — 4 本の赤の原因ではない)。見つけたのは
         ;; 再照準の設計の盲検 B(2026-09-22)、最初に置いた針は 1 行の `setv` だけを捕まえる字面の針で、
         ;; 依頼者 c-D6AFCPB1VRMNTVN9ECSZMCAS3T の差し戻し(同日)が 3 形の素通りを実射で示し、この form の
         ;; 読みへ作り替えた。作り替えの盲検 B(同日)が「積んだ先の bodies を後から歩いて見出しを書き足す」
         ;; 形で素通りしたので、運ぶ名の閉包まで読む形にした(card acp:kanban-issue:ki-7a7dd5cc6727)。
         ;; ⚠ **赤にする範囲** = 同じ頂点の form の中で、運ぶ名を名簿の外の読み手が読む/書き直す形。
         ;;    **赤にしない範囲**(この針は捕まえない — 名指しで): 別の頂点の form へ渡った後の組み替え・
         ;;    handler の実 I/O の中での組み替え・mail-turn-text-of を呼ばずに文を作る形(呼びの数 = 1 と
         ;;    R16 の挙動の反例が別の側から押さえる)。
         ;;    **過剰に赤くする範囲**(正当でも赤になる — その時は検査を緩めず、名簿を人が 1 行直す):
         ;;    組み替えない読み(文の長さを測る `(len 文)` 等 — 盲検 A 2026-09-22)・積む先の名の改名
         ;;    (`bodies` → 別名は名簿の鍵 "bodies.append" と食い違う)・読み手の追加。数や範囲を書き換えて
         ;;    緑にする直しは禁止。以後この針を消すなら、代わりに何が第 2 の合成点を捕まえるかを先に置く。
         (setv bound-calls (bound-calls-of path "mail-turn-text-of"))
         (assert (= (len bound-calls) 1)
                 f"{name} の form 上の合成の呼びは 1 つ(R16): 実測 {(len bound-calls)}")
         (setv call (get bound-calls 0))
         (assert (= call.parent "<-")
                 (+ f"{name} の {call.top} が合成の呼びを `{call.parent}` で包んでいる —— 返った文は "
                    f"その場で組み替えず `(<- <名> str (mail-turn-text-of …))` で束ねる(R16)"))
         (setv allowed (get MAIL-TURN-TEXT-CONSUMERS name))
         (setv stray (sorted (lfor use call.uses :if (not-in (get use 0) allowed) (get use 1))))
         (assert (= stray [])
                 (+ f"{name} の {call.top} が合成した文(運ぶ名 {call.carriers !r})を、冊の名簿の外の読み手が"
                    f"読んでいる —— 手番の文を組む座は judgment.mail-turn-text-of の 1 点で、読み手の宣言は "
                    f"MAIL-TURN-TEXT-CONSUMERS の 1 点(R16): {stray !r}"))
         (setv seen (sorted (sfor use call.uses (get use 0))))
         (assert (= seen (sorted allowed))
                 (+ f"{name} の名簿の読み手が実際には読んでいない(読み手を足す/替える便は名簿へ "
                    f"1 行宣言する・R16): 名簿 {(sorted allowed) !r} 実測 {seen !r}")))
'''
sub(OLD, NEW)
p.write_text(s, encoding="utf-8")
print("ok")
