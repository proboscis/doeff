;;; DOEFF175(宣言を切り替えた effect の古い分岐 — ADR-DOE-CORE-EFFECTS-003 R14 (4))の検体 — effect が defeffect で不在・失敗と宣言した型を、
;;; その effect の答えを束ねた変数に対して値として分ける枝(match の腕・isinstance)を鳴らし、:value と宣言した型・成功の型・宣言の無い effect・
;;; defhandler の節・別の module の同じ名の型・束ね直した名・defk を経由した答えは鳴らさない。
;;; 行末の「; 鳴る」が鳴るべき行の印: tests/stale_outcome_branch.rs が、印の行の集合と linter の当たりの行の集合を突き合わせる
;;; (規則を外す・形を狭める・広げると、どれも集合が合わなくなって赤)。宣言は同じ dir の effects.hy(app.effects)。この file は実行しない(読むだけの検体)。
(require doeff-hy.macros [defk deftest defhandler <- val var])
(import app.effects [ReadRow PostRow PlainRead Row Missing Unreachable Conflict Accepted Refused Gone])
(import app.effects :as effects)
(import app.other :as other)
(import doeff-core-effects [maybe])


;; ---------------------------------------------------------------------------
;; 鳴る: 宣言した不在・失敗の型を、答えを束ねた変数に対して値として分ける

(defk match-an-absent-arm [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "ReadRow は Missing を不在と宣言した — found は成功の Row だけなので、この腕は通らない。"
  (<- found (ReadRow key))
  (match found
    (Missing) (return None)  ; 鳴る
    (Row) found.value))


(defk match-a-failure-arm [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "Unreachable は ReadRow の失敗の宣言(欄を取る pattern でも、型の頭で当たる)。"
  (<- found (ReadRow key))
  (match found
    (Unreachable :detail why) (return why)  ; 鳴る
    _ found.value))


(defk match-an-alternation [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "`(| (A) (B))` の選択肢は 1 つずつ数える(同じ行に 2 件)。"
  (<- found (ReadRow key))
  (match found
    (| (Missing) (Unreachable)) (return None)  ; 鳴る
    (Row) found.value))


(defk isinstance-an-absent [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "isinstance で不在の型を分ける。"
  (<- found (ReadRow key))
  (when (isinstance found Missing)  ; 鳴る
    (return None))
  found.value)


(defk isinstance-a-tuple [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "型の tuple は 1 つずつ数える(同じ行に 2 件)。"
  (<- found (ReadRow key))
  (if (isinstance found #(Missing Unreachable))  ; 鳴る
      None
      found.value))


(defk isinstance-a-union [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "(| A B) の型の列も 1 つずつ数える(Row は成功の型なので数えない)。"
  (<- found (ReadRow key))
  (if (isinstance found (| Row Unreachable))  ; 鳴る
      found
      None))


(defk cond-branch [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "cond の枝の isinstance。"
  (<- found (ReadRow key))
  (cond
    (isinstance found Unreachable) None  ; 鳴る
    True found.value))


(defk typed-bind [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "型を注釈した束ね `(<- x T (E …))` も、effect の答えを束ねている。"
  (<- found (| Row Missing Unreachable) (ReadRow key))
  (match found
    (Unreachable) None  ; 鳴る
    (Row) found.value))


(defk bang-bound [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "(val x (! (E …))) で束ねた名。"
  (val found (! (ReadRow key)))
  (match found
    (Missing) None  ; 鳴る
    (Row) found.value))


(defk bang-subject [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "束ねずに (! (E …)) を直に分ける形(match の主・isinstance の主)。"
  (match (! (ReadRow key))
    (Missing) None  ; 鳴る
    (Row) "row")
  (when (isinstance (! (ReadRow key)) Unreachable)  ; 鳴る
    (return None))
  key)


(defk absent-suffix-bind [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "(<- x (E …) :absent F) で束ねた x も成功の値だけ(不在は F の失敗になる)。"
  (<- found (ReadRow key) :absent (Conflict :detail "行が無い"))
  (match found
    (Missing) None  ; 鳴る
    (Row) found.value))


(defk module-alias [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "import の別名(module の名を前に付けた effect と型)も、module まで解いて比べる。"
  (<- found (effects.ReadRow key))
  (match found
    (effects.Missing) None  ; 鳴る
    (effects.Row) found.value))


(defk a-second-effect [id]
  {:pre [(: id str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "PostRow は Refused を失敗・Gone を不在と宣言した(effect ごとの宣言)。"
  (<- ack (PostRow id))
  (match ack
    (Refused) None  ; 鳴る
    (Gone) None  ; 鳴る
    (Accepted) ack.id))


(defk nested-in-a-branch [key ready]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "束ねも分岐も、when や if の中でも読む。"
  (when ready
    (<- found (ReadRow key))
    (match found
      (Missing) (return None)  ; 鳴る
      (Row) (return found.value)))
  None)


(deftest reads-a-row-in-a-test
  "検の呼び手も、effect の切り替えで古い分岐が残る。"
  (<- found (ReadRow "k"))
  (assert (not (isinstance found Missing)))  ; 鳴る
  (assert (= found.value "v")))


;; ---------------------------------------------------------------------------
;; 鳴らない

(defk value-arm [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  ":value と宣言した型(Conflict)の枝は、答えとして束ねられる値なので通る。"
  (<- found (ReadRow key))
  (match found
    (Conflict :detail why) why
    (Row) found.value))


(defk success-arm [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "成功の型(Row)の枝は通る。"
  (<- found (ReadRow key))
  (when (isinstance found Row)
    (return found.value))
  (if (isinstance found #(Row Conflict)) found None))


(defk plain-effect [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "宣言の無い effect の答えは変換せずそのまま束ねるので、Missing も Unreachable も値として来る。"
  (<- found (PlainRead key))
  (when (isinstance found Unreachable)
    (return None))
  (match found
    (Missing) None
    (Row) found.value))


(defhandler row-handler
  "defhandler の節は effect を受ける側(答えを作る側)— 節の中の match も、節の中で束ねた名への分岐も読まない。"
  {:tags {:context "sample" :role "foundation"}}
  (ReadRow [key]
    (match (lookup-row key)
      (Missing) (resume (Missing :key key))
      (Unreachable) (resume (Unreachable :detail "down"))
      row (resume row)))
  (PostRow [id]
    (<- inner (ReadRow id))
    (match inner
      (Missing) (resume (Gone :id id))
      (Row) (resume (Accepted :id id)))))


(defk another-modules-type [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "別の module の同じ名の型(app.other.Missing)は、宣言した型ではない。"
  (<- found (ReadRow key))
  (when (isinstance found other.Missing)
    (return None))
  (match found
    (other.Missing) None
    (Row) found.value))


(defk a-type-this-effect-did-not-declare [id]
  {:pre [(: id str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "Missing は ReadRow が宣言した型で、PostRow は宣言していない(effect ごとの宣言)。"
  (<- ack (PostRow id))
  (match ack
    (Missing) None
    (Accepted) ack.id))


(defk a-rebound-name [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "束ね直した名は、もう effect の答えではない。"
  (<- found (ReadRow key))
  (setv found (Missing :key key))
  (match found
    (Missing) None
    (Row) found.value))


(defk a-shadowing-parameter [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "sample" :role "program"}}
  "関数の引数で同じ名を束ねた中は、effect の答えではない。"
  (<- found (ReadRow key))
  (val score (fn [found] (match found (Missing) 0 _ 1)))
  (score found.value))


(defk wrapped-in-maybe [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "maybe で包んだ答えは Maybe の値で、effect の答えではない(頭が effect でない)。"
  (<- wrapped (maybe (ReadRow key)))
  (match wrapped
    (Missing) None
    _ wrapped))


(defk a-nested-pattern [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "pattern の欄の中の型は答えの型ではない(Row の欄 value が Missing の形)。"
  (<- found (ReadRow key))
  (match found
    (Row :value (Missing)) None
    (Row) found.value))


(defk through-a-defk [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "defk を経由した答えは追わない(束ねた式の頭が effect でない)。"
  (<- found (fetch-row key))
  (match found
    (Missing) None
    (Row) found.value))


(defk quoted-form [key]
  {:pre [(: key str)] :post [(: % (| str None))] :tags {:context "sample" :role "program"}}
  "quote した form は読まない。"
  (<- found (ReadRow key))
  '(match found (Missing) None)
  found.value)
