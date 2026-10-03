;;; event-loop — 出来事を待つ係のループの macro(agora-redesign #3080・設計 = agora-controllers docs/design/event-waits/README.md 4 節)。
;;;
;;;   (require doeff-events.macros [event-loop])
;;;   (event-loop [board (! (ReadBoard))]
;;;     (:stop reason)          (stop board)                      ; 止めの節(必ず 1 つ)
;;;     (BoardMoved keys)       (! (read-board-at board keys))    ; 合図の所を読み直した盤 = 次の state
;;;     (TimerFired tag)        (if (= tag END) (stop board) board))
;;;
;;; - 節の頭の型の組をそのまま WaitForEvent に渡す — 待つ型と扱う型はずれない。止めの合図(AwaitStop)と競わせ、先に来た方の節を回す。
;;; - 節の本体は do! の中(<- と ! が使える)。本体の値が次の state。(stop 値) は本体の最後の値の位置にだけ書け、ループを抜けて
;;;   event-loop の値になる。止めの節の値もそのまま event-loop の値になる。state を省いた形(節だけ)も同じ macro。
;;; - 展開の時に断る形: 止めの節が無い・2 つ在る / 待つ型の節が無い / 型を導けない節(_・名前だけ・| ・値の式)/ 同じ型の節が 2 つ /
;;;   本体の無い節 / 最後の値の位置でない stop。
;;; - 待ちの部品(止めの待ち・競わせ・出来事ごとの slog)は doeff_events/event_loop.py。展開は doeff-hy の <- と do! を
;;;   hy.R で直に参照する(使う側の require に頼らない)。節の本体と初期値は do! に包むので、defk の本体の ! がその場で
;;;   (yield …) に書き換わっても、出来事ごとに回る位置は変わらない。

(import hy)


(setv _STOP-TAIL-HEADS #{"do" "when" "unless" "let"})


(defn _el-fail [message]
  "展開の時の誤りを、書き手が直せる形(正しい形の 1 行つき)で断るため。"
  (raise (SyntaxError (+ "event-loop: " message
                         "\n  形: (event-loop [state 初期値] (:stop 理由) 本体 (型 束縛 …) 本体 …)"
                         " — 止めの節を 1 つ・待つ型の節を 1 つ以上・節ごとに本体を 1 つ"))))


(defn _el-symbol? [x]
  "節の頭や束縛が名前(記号)かを見分けるため。"
  (isinstance x hy.models.Symbol))


(defn _el-head [form]
  "式の頭の記号の綴り(式でない・頭が記号でない時は None)。"
  (if (and (isinstance form hy.models.Expression) (> (len form) 0) (_el-symbol? (get form 0)))
      (str (get form 0))
      None))


(defn _el-split-state [args]
  "#(state の名 型 初期値 節の列) — 最初が [名 初期値] か [名 型 初期値] なら state を持つ形(型を書けば state と抜けた値をその型で
   確かめ、型検査にも見える)、そうでなければ state を省いた形。"
  (if (and args (isinstance (get args 0) hy.models.List))
      (let [binding (get args 0)]
        (when (not (and (in (len binding) #{2 3}) (_el-symbol? (get binding 0))))
          (_el-fail (+ "state の束縛は [名 初期値] か [名 型 初期値] の形で書く: " (hy.repr binding))))
        (if (= (len binding) 3)
            #((get binding 0) (get binding 1) (get binding 2) (cut args 1 None))
            #((get binding 0) None (get binding 1) (cut args 1 None))))
      #(None None None args)))


(defn _el-capture? [x]
  "型の節の束縛に置ける名前か(属性の綴り .x は値の比べになるので断る)を見分けるため。"
  (and (_el-symbol? x) (not (.startswith (str x) "."))))


(defn _el-class-args-ok? [args]
  "型の節の束縛の並び: 位置の名(記号)と :欄 名 の組だけ — 値で絞る形は書けない(型の出来事は必ずどれかの節に合う)。"
  (cond
    (not args) True
    (isinstance (get args 0) hy.models.Keyword)
      (and (> (len args) 1) (_el-capture? (get args 1)) (_el-class-args-ok? (cut args 2 None)))
    (_el-capture? (get args 0)) (_el-class-args-ok? (cut args 1 None))
    True False))


(defn _el-pattern-kind [pattern]
  "節の頭の種類: \"stop\"(止めの節)か \"event\"(型の節)。どちらでもない形は展開の時に断る。"
  (setv head (if (and (isinstance pattern hy.models.Expression) (> (len pattern) 0)) (get pattern 0) None))
  (cond
    (and (isinstance head hy.models.Keyword) (= head.name "stop"))
      (if (or (= (len pattern) 1) (and (= (len pattern) 2) (_el-symbol? (get pattern 1))))
          "stop"
          (_el-fail (+ "止めの節は (:stop) か (:stop 理由の名) で書く: " (hy.repr pattern))))
    (and (_el-symbol? head) (not (in (str head) #{"_" "|"})))
      (if (_el-class-args-ok? (cut pattern 1 None))
          "event"
          (_el-fail (+ "型の節の束縛は名前(位置)か :欄 名 だけを書く — 値で絞らず、本体で分ける: " (hy.repr pattern))))
    True (_el-fail (+ "節の頭から待つ型を導けない(_・名前だけ・| ・値の式は書けない — (型 束縛 …) で書く): " (hy.repr pattern)))))


(defn _el-guard-end [items index]
  "match の pattern の後の :as 名・:if 守り の組を飛ばした位置を返すため(本体の位置を見つける)。"
  (if (and (< (+ index 1) (len items)) (isinstance (get items index) hy.models.Keyword)
           (in (. (get items index) name) #{"as" "if"}))
      (_el-guard-end items (+ index 2))
      index))


(defn _el-body-positions [items index]
  "match の節の列(pattern [:as 名] [:if 守り] 本体 …)の、本体の位置の tuple を返すため。"
  (if (>= index (len items))
      #()
      (let [body (_el-guard-end items (+ index 1))]
        #(body #* (_el-body-positions items (+ body 1))))))


(defn _el-match-bodies [items ctor]
  "match の節の本体だけを _el-tail で書き換えるため(pattern と守りは触らない)。"
  (setv positions (_el-body-positions items 0))
  (lfor [index item] (enumerate items) (if (in index positions) (_el-tail item ctor) item)))


(defn _el-try-part [part ctor]
  "try の中の except・else の節は最後の form を、finally はそのまま返すため(finally の値は try の値にならない)。"
  (setv head (_el-head part))
  (if (and (in head #{"except" "else"}) (> (len part) 1))
      (hy.models.Expression [#* (cut part 0 -1) (_el-tail (get part -1) ctor)])
      part))


(defn _el-tail [form ctor]
  "本体の最後の値の位置の (stop 値) を (ctor 値) に書き換える(do・when・unless・let・if・cond・match・try の枝を辿る)。"
  (setv head (_el-head form))
  (cond
    (= head "stop")
      (if (<= (len form) 2)
          (hy.models.Expression [ctor (if (= (len form) 2) (get form 1) (hy.models.Symbol "None"))])
          (_el-fail (+ "stop の値は 1 つだけ: " (hy.repr form))))
    (and (in head _STOP-TAIL-HEADS) (> (len form) 1))
      (hy.models.Expression [#* (cut form 0 -1) (_el-tail (get form -1) ctor)])
    (= head "if")
      (hy.models.Expression [#* (cut form 0 2) #* (lfor branch (cut form 2 None) (_el-tail branch ctor))])
    (= head "cond")
      (hy.models.Expression [(get form 0) #* (lfor [index part] (enumerate (cut form 1 None))
                                               (if (% index 2) (_el-tail part ctor) part))])
    (and (= head "match") (> (len form) 2))
      (hy.models.Expression [#* (cut form 0 2) #* (_el-match-bodies (list (cut form 2 None)) ctor)])
    (= head "try")
      (let [parts (list (cut form 1 None))
            handlers (lfor part parts :if (in (_el-head part) #{"except" "else" "finally"}) part)
            body (lfor part parts :if (not (in (_el-head part) #{"except" "else" "finally"})) part)]
        (hy.models.Expression [(get form 0)
                               #* (if body [#* (cut body 0 -1) (_el-tail (get body -1) ctor)] [])
                               #* (lfor part handlers (_el-try-part part ctor))]))
    True form))


(defn _el-found [form heads]
  "form の中に頭が heads の式が在れば、その頭の綴りを返すため(引用と入れ子の event-loop の中は見ない)。無ければ None。"
  (setv head (_el-head form))
  (cond
    (in head heads) head
    (in head #{"quote" "quasiquote" "event-loop"}) None
    (isinstance form #(hy.models.Expression hy.models.List hy.models.Tuple hy.models.Set hy.models.Dict))
      (next (gfor part form :setv found (_el-found part heads) :if (is-not found None) found) None)
    True None))


(defn _el-body [body ctor]
  "節の本体を do! の文の並びにするため: 最後の値の位置の stop を書き換え、ほかの位置に stop が残れば断る。
   (do …) の本体は中身をそのまま do! の文として並べる(<- を文の位置に書ける)。"
  (setv rewritten (_el-tail body ctor))
  (when (_el-found rewritten #{"stop"})
    (_el-fail (+ "stop は節の本体の最後の値の位置にだけ書ける(途中で抜けない): " (hy.repr body))))
  (if (and (= (_el-head rewritten) "do") (> (len rewritten) 1))
      (list (cut rewritten 1 None))
      [rewritten]))


(defmacro event-loop [#* args]
  "出来事を待つ係のループ — (event-loop [state 型 初期値] (:stop 理由) 本体 (型 束縛 …) 本体 …)。

   節の頭の型の組をそのまま WaitForEvent に渡し、止めの合図(AwaitStop)と競わせ、先に来た方の節を回す。
   本体は do! の中(<- と ! が使える)。本体の値が次の state。(stop 値) を本体の最後の値の位置(do・if・cond・match・try の枝)に
   書くとループを抜けて値を返す。止めの節の値もそのまま event-loop の値。初期値も do! の中(読みは (! (Read…)) で書く)。
   state の型を書くと([名 型 初期値])、初期値・各節の値・抜けた値をその型で確かめ、型検査にも見える(書かない形 [名 初期値] も可)。
   state を省く形は束縛を書かず節だけを並べる(本体の値は捨てる・抜けるのは stop か止めの節)。本体で外の var を := で書き換えない。
   - 型の節は書いた順に当たる: 親の型の節を子の型の節より前に書くと、子の型の出来事も親の節が受ける。
   - 止めの合図で抜ける時、待ちの途中で列から取り出された出来事が 1 つ捨てられることがある(合図は「どこが変わったか」だけなので、
     次に起きた係が記録を読み直せば揃う)。
   - 出来事ごとに slog(\"event-loop\" :event 型の名)を出す — 外に slog の handler が要る。
   待ちの部品 = doeff_events.event_loop(止めの待ちを係の寿命の間 1 つ・待つ前に StopRequested を 1 回)。"
  (import doeff_hy.match_fields [mangle_match_fields])
  (setv #(state-name state-type state-init clauses) (_el-split-state args))
  (when (% (len clauses) 2)
    (_el-fail "節は (頭) 本体 の組で並べる — 本体の無い節がある"))
  (setv pairs (lfor index (range 0 (len clauses) 2) #((get clauses index) (get clauses (+ index 1)))))
  (setv kinds (lfor [pattern _body] pairs (_el-pattern-kind pattern)))
  (setv stops (lfor [pair kind] (zip pairs kinds) :if (= kind "stop") pair))
  (setv events (lfor [pair kind] (zip pairs kinds) :if (= kind "event") pair))
  (when (not stops)
    (_el-fail "止めの節 (:stop 理由) 本体 が無い — 止められない係になる"))
  (when (> (len stops) 1)
    (_el-fail "止めの節は 1 つだけ"))
  (when (not events)
    (_el-fail "待つ型の節が無い — (型 束縛 …) 本体 を 1 つ以上書く"))
  (setv heads (lfor [pattern _body] events (str (get pattern 0))))
  (when (!= (len heads) (len (set heads)))
    (_el-fail (+ "同じ型の節が 2 つある: " (.join " " heads))))
  (setv bind 'hy.R.doeff_hy/macros.<-
        program 'hy.R.doeff_hy/macros.do!
        begin (hy.gensym "el-begin")
        next-of (hy.gensym "el-next")
        end (hy.gensym "el-end")
        arrived (hy.gensym "el-arrived")
        loop-stop (hy.gensym "el-loop-stop")
        value-of (hy.gensym "el-value")
        watch (hy.gensym "el-watch")
        got (hy.gensym "el-got")
        out (hy.gensym "el-out")
        result (hy.gensym "el-result"))
  (setv #(stop-pattern stop-body) (get stops 0))
  (setv stop-binding (if (= (len stop-pattern) 2) [`(setv ~(get stop-pattern 1) (. ~got reason))] []))
  ;; state の型を書いた形: 初期値は state の型・各節の値は state の型か (stop 値)・抜けた値は state の型で確かめる。
  (setv state-check (if state-type [state-type] [])
        out-check (if state-type [`(| ~state-type ~loop-stop)] [])
        result-check (if state-type [`(assert (isinstance ~result ~state-type)
                                               (+ "event-loop: 抜けた値の型が state の型でない: " (repr ~result)))] []))
  (setv arms (lfor [pattern body] events
               [pattern `(~bind ~out ~@out-check (~program ~@(_el-body body loop-stop)))]))
  (setv next-state (if state-name [`(setv ~state-name ~out)] []))
  (setv start-state (if state-name [`(~bind ~state-name ~@state-check (~program ~state-init))] []))
  `(do
     (import doeff_events.event_loop [begin_watch :as ~begin next_event :as ~next-of end_watch :as ~end
                                      StopArrived :as ~arrived LoopStop :as ~loop-stop stopped_value :as ~value-of])
     ~@start-state
     (~bind ~watch (~begin))
     (try
       (while True
         (~bind ~got (~next-of #(~@(lfor [pattern _body] events (get pattern 0))) ~watch))
         (when (isinstance ~got ~arrived)
           ~@stop-binding
           (~bind ~out ~@out-check (~program ~@(_el-body stop-body loop-stop)))
           (setv ~result (~value-of ~out))
           ~@result-check
           (break))
         ~(mangle_match_fields
            `(match ~got
               ~@(lfor arm arms part arm part)
               _ (raise (TypeError (+ "event-loop: どの節にも合わない出来事 " (repr ~got))))))
         (when (isinstance ~out ~loop-stop)
           (setv ~result (~value-of ~out))
           ~@result-check
           (break))
         ~@next-state)
       (finally
         (~bind (~end ~watch))))
     ~result))
