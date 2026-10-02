;;; effect の記録と再生(backtest)の型と純粋な判断。I/O をしない(記録の行を作る・読む・突き合わせる・結果をまとめる)。
;;;
;;; 記録 = 1 つの process(run)が出した effect の問いと答えの出来事の列。行は JSON で、欄 "k" が種類:
;;;
;;;   run    先頭の 1 行。形の版・service・run の名・worker・process の世代・版(revision)・Program の置き場のキー(program)・送り手の版(versions)・始まりの時刻
;;;   chunk  区切り(chunk-seconds ごと)の頭。区切りごとに差分の元を忘れるので、各区切りの最初の値は丸ごと(断面)
;;;   req    問い。e = 出来事の番号(run の中で 0 から 1 ずつ・問いの id を兼ねる)・t = task の名・at = 壁時計の ms・
;;;          ty = effect の型の名・a = 引数(大きければ ad = 差分・ab = 元の出来事の番号・ak = 差分の鍵)
;;;   ans    答え。s = 問いの e・ok = 真なら v(大きければ vd / vb / vk)、偽なら err(例外の符号)
;;;   call   (形の版 2)問いと答えを 1 行にまとめた物。問いの欄に ok・v / err・dt(答えの時刻 - 問いの時刻)を足し、答えの番号は e + 1
;;;   blob   (形の版 2)内容参照の中身。h = 内容の hash・v = 畳んだ節。a / v の中の {"$ref": h} をこの中身に戻す(record_codec.resolve-refs)
;;;   mut    業務コードと handler が共有する可変の箱(Ask の答え)の中身が変わった。ref = その箱を渡した問いの e・v = 新しい中身
;;;   end    task が終わった(t・ok)
;;;   broken 記録の登録に無い型・記録の形にできない値に当たった。ここから先は記録していない(再生は記録の終わりとして扱う)
;;;
;;; 並行: 出来事の番号は問いと答えの両方に振る(scheduler が task を切り替える順 = 答えが返った順も再生で同じにするため)。
;;; task の名は親の名 + 「.」+ 親の中で何番目に Spawn したか(scheduler の番号に依らないので記録と再生で同じ)。根は "root"。
(require doeff-hy.macros [defk deff <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import collections.abc [Callable])
(import dataclasses)
(import dataclasses [dataclass field])
(import json)
(import typing [get-args])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_hy.table [Table TableWrite table-of])
(import doeff_cluster.foundation.record_codec [READ LIVE DECISION OUTPUT LOOSE READABLE-FORMATS JsonValue RestoredValue
                                                canonical apply-delta delta-of resolve-refs resolve-type encode-value decode-value])

(setv ROOT "root")

;; 旧い記録の綴りを今の綴りへ揃える(#2543・#2579 — 揃え方を知るのは read-recording だけ)。書きの条件を付けない印 ANY は、欄の無い値の型
;; (shared_model.AnyExpect)にする前は {"$any": 1} と綴っていた。今は値の汎用の綴り(encode-value の $c — 一致は tests/test_effect_record.hy が縛る)。
(val LEGACY-ANY {"$any" 1})
(val CURRENT-ANY {"$c" "doeff_cluster.shared.intent.shared_model:AnyExpect" "f" {}})


(defclass ReplayFinished [Exception]
  "記録の終わりに着いた(再生の正常な終わり)。業務コードが捕まえても、次の effect でまた投げる。")

(defclass ReplayDiverged [Exception]
  "問いが記録と食い違った(分岐)。推測で答えを作らずに止める。")


;; --- 記録の読み ---------------------------------------------------------------------------------

(defrecord WatchedRef
  "記録の答えの印 {\"$w\": n}: 答えは出来事 n の答えと同じ共有の箱(Ask の計器の箱 — 業務コードと handler が同じ object を持つ)。
   箱そのものは再生が出来事 n の答えを返した時に作るので、記録を読む時には戻せない — 再生の側(record_handlers.deliver-recorded)が
   その時の箱を引く。entry = 箱を最初に返した問いの出来事の番号。"
  {:tags {:context "doeff-cluster" :role "foundation"}}
  (#^ int entry))

;; 読んだ記録の成功の答え(Entry.value)の閉じた和: 記録から戻した値(record_codec.decode-value の答え)か、先の答えの共有の箱の参照(#2581)。
(val RestoredAnswer (| WatchedRef RestoredValue))


(defclass [(dataclass)] Entry []
  "問い 1 つ(req)と、その答え(ans)。ans-e が None = 記録が終わった時にまだ答えが返っていなかった。
   args-text = 引数(名 → 値の JSON)の比べる形(record_codec.canonical — 鍵を並べた文字列)。read-recording が今の綴りへ揃えてから 1 度だけ
   作り、match-step は文字列のまま比べる(#2727 — 以前は dict を持ち、比べるたびに canonical を作り直した)。中を読むのは違いの報告だけ
   (recorded-args)。
   value = 成功の答えを read-recording が読む時に戻した値(RestoredAnswer)。再生は業務コードへその写しを渡す
   (record_handlers.deliver-recorded)ので、同じ Recording を何度再生しても value は書き換わらない。
   error = 失敗の答えの例外の記録の綴り(中継 — 中を読むのは再生が渡す時の decode-error だけ。読む時に例外へ戻すと、同じ object を
   再生のたびに投げ直すことになる)。"
  (#^ int e)
  (#^ str task)
  (#^ int at)
  (#^ str type)
  (#^ str args-text)
  (#^ str mode)
  (setv #^ object subject None)
  (setv #^ object ans-e None)
  (setv #^ object ans-at None)
  (setv #^ bool ok True)
  (setv #^ RestoredAnswer value None)
  (setv #^ (| OpaqueJson None) error None))


(defrecord RunHeader
  "記録の run の行(先頭の 1 行)のうち、再生と報告が読む欄。ほかの欄(worker・世代・版・設定)は読む所が無い(#2727)。"
  {:tags {:context "doeff-cluster" :role "foundation"}}
  (#^ int format)
  (setv #^ (| str None) service None)
  (setv #^ (| str None) run None)
  (setv #^ (| str None) revision None)
  (setv #^ (| str None) program None))


(defclass [(dataclass)] Recording []
  "読んだ記録。events = 出来事の番号の昇順の #(番号 種類 task の名 付帯)。entries = 問いの番号で引く Entry の列(位置 = 出来事の番号・
   答えの番号の位置は None — entry-of で引く)。queues = task の名 → その task の問いの番号の tuple(問いの順)の表。
   ended = 記録の中で終わった task の名の集合(#2727 — 以前は名 → ok の dict だったが、ok を読む所は無い)。"
  (#^ RunHeader header)
  (#^ list events)
  (#^ (get tuple #((| Entry None) ...)) entries)
  (#^ Table queues)
  (#^ frozenset ended)
  (setv #^ object broken None)
  (setv #^ list muts (field :default-factory list)))


(deff entry-of [#^ Recording rec #^ int e]  ; defk にできない: 再生係(handler — Program の外の object の method)が問いごとに呼ぶ純粋な引き
  {:pre [(: rec Recording) (: e int)] :post [(: % (| Entry None))] :tags {:context "doeff-cluster" :role "foundation"}}
  "出来事の番号 e の問い(Entry)。答えの番号・記録に無い番号は None — 再生係が「次に来るべき出来事」の問いを引くため。"
  (if (< -1 e (len rec.entries)) (get rec.entries e) None))


(defk queue-of [rec task]
  {:pre [(: rec Recording) (: task str)] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "foundation"}}
  "task の問いの番号の列(問いの順)。記録に無い task は空 — 再生係が task の次の問いを突き合わせるため。"
  (or (.row rec.queues task) #()))


(deff recorded-args [#^ Entry entry]  ; defk にできない: 再生係(handler)と報告の綴り(Program の外)が違いを報告する時に呼ぶ純粋な読み
  {:pre [(: entry Entry)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation" :reads "json"}}
  "記録の引数(名 → 値の JSON)— 違いの報告と差分のため、比べる形の文字列から読み直す。"
  (json.loads entry.args-text))


(defn _resolve [line bases prefix full-key delta-key base-key key-key]
  "行の値を差分から戻す。bases = 差分の鍵 → #(出来事の番号 値)(鍵ごとに最新 1 つだけ持つ)。"
  (setv key (.get line key-key))
  (setv value
    (if (in delta-key line)
        (do (setv base (.get bases key))
            (when (or (is base None) (!= (get base 0) (get line base-key)))
              (raise (ValueError (.format "差分の元が無い: 出来事 {} の {} は {} を元にする" (get line prefix) delta-key (get line base-key)))))
            (apply-delta (get base 1) (get line delta-key)))
        (get line full-key)))
  (when (is-not key None)
    (setv (get bases key) #((get line prefix) value)))
  value)


(defk _expand-calls [#^ list lines]
  {:pre [(: lines list)] :post [(: % list)] :tags {:context "doeff-cluster" :role "foundation" :reads "json"}}
  "call の行(問いと答えの 1 行)を req と ans の 2 行に戻す。他の行はそのまま。"
  (lfor l lines
        row (if (!= (.get l "k") "call")
                [l]
                [(| (dfor #(k v) (.items l) :if (not-in k #("ok" "v" "err" "dt")) k v) {"k" "req"})
                 (| {"k" "ans" "e" (+ (get l "e") 1) "s" (get l "e") "at" (+ (get l "at") (.get l "dt" 0)) "ok" (get l "ok")}
                    (if (get l "ok") {"v" (.get l "v")} {"err" (get l "err")}))])
        row))


(defk read-recording [#^ list lines #^ (| int None) [until-ms None] #^ (| (get Callable #([str dict] str)) None) [mode-of-type None]]
  {:pre [(: lines list) (: until-ms (| int None)) (: mode-of-type (| Callable None))] :post [(: % Recording)] :tags {:context "doeff-cluster" :role "foundation" :reads "json"}}
  "記録の行(dict の列・順不同でよい)→ Recording。until-ms = この時刻より後の出来事を捨てる(範囲の終わり)。
   mode-of-type = 型の名 → 登録の mode(再生の側の record_codec から渡す。無ければ行の mode 欄)。"
  (var header None)
  (var broken None)
  (val events [])
  (val entries {})
  (val queues {})
  (val ended {})
  (val arg-bases {})
  (val val-bases {})
  (val muts [])
  (val blobs {})
  (val memo {})
  (<- expanded list (_expand-calls (lfor l lines :if (in "e" l) l)))
  (val ordered (sorted expanded :key (fn [l] (get l "e"))))
  (for [l lines]
    (match (.get l "k")
      "run" (:= header l)
      ;; 内容参照の中身は順に依らない(同じ h は同じ中身)ので先に全部集める。
      "blob" (setv (get blobs (get l "h")) (get l "v"))
      _ None))
  (when (is header None)
    (raise (ValueError "記録に run の行が無い")))
  (when (not-in (.get header "format") READABLE-FORMATS)
    (raise (ValueError (.format "記録の形の版が違う: {}(読めるのは {})" (.get header "format") READABLE-FORMATS))))
  (val refs (fn [v] (if blobs (resolve-refs v blobs memo) v)))
  ;; 型の名 → その型の OpaqueJson の欄の名(型ごとに 1 度だけ引く — 型を import できない名は揃える欄を持たない)。
  (val opaque-fields {})
  (val seen {})
  (for [l ordered]
    (val e (get l "e"))
    (val kind (get l "k"))
    ;; 置き場へ送り直した行は同じ中身で 2 度在りうる(届いたか分からずに送り直した)。同じなら 1 つにし、違えば壊れた記録。
    (val body (canonical (dfor #(key v) (.items l) :if (not-in key #("_chunk")) key v)))
    (when (in e seen)
      (if (= (get seen e) body)
          (continue)
          (raise (ValueError (.format "出来事の番号 {} に違う中身が 2 つある" e)))))
    (setv (get seen e) body)
    (when (and (is-not until-ms None) (> (.get l "at" 0) until-ms))
      (break))
    (cond
      (= kind "broken") (do (:= broken l) (break))
      (= kind "req")
        (do (val args (refs (if (or (in "a" l) (in "ad" l)) (_resolve l arg-bases "e" "a" "ad" "ab" "ak") {})))
            ;; 問いの引数は名 → 値の表。記録が壊れて別の形なら、Entry に入れる前にここで名指して断る。
            (when (not (isinstance args dict))
              (raise (ValueError (.format "出来事 {} の引数が表でない: {}" e (type args)))))
            (val mode (if (is mode-of-type None) (get l "m") (mode-of-type (get l "ty") l)))
            ;; 旧い記録の行の引数を今の版の比べる形へ揃えて持つ(差分の元は揃える前の値)— 型ごとの関数を持たない汎用の正規化(#2579)。
            ;; 型の OpaqueJson の欄(形を書き手が決める JSON)は、今の codec が中の JSON の値で綴る。値が素の値だった旧い行
            ;; (tuple は $t・文字列でない鍵は $d)は JSON に運んだ値(list・文字列の鍵)に直して綴り直し、旧い ANY の綴りは今の綴りへ替える。
            ;; 欄が JSON でない値(ANY の $c)を持つ行はそのまま。今の形の行は同じ綴りに戻る。
            (val ty (get l "ty"))
            (when (not-in ty opaque-fields)
              (val cls (resolve-type ty))
              (setv (get opaque-fields ty)
                    (if (and (is-not cls None) (dataclasses.is-dataclass cls))
                        (frozenset (gfor f (dataclasses.fields cls) :if (or (is f.type OpaqueJson) (in OpaqueJson (get-args f.type))) f.name))
                        (frozenset))))
            (val fields (get opaque-fields ty))
            (val current (if fields
                             (dfor #(k v) (.items args)
                                   k (cond (not-in k fields) v
                                           (= v LEGACY-ANY) CURRENT-ANY
                                           (and (isinstance v dict) (in "$c" v)) v
                                           True (encode-value (json.loads (. (OpaqueJson.of (decode-value v)) text)))))
                             args))
            ;; 比べる形の文字列は、揃えた後の引数から 1 度だけ作る(鍵を並べるので、記録の行の鍵の順に依らない)。
            (setv (get entries e) (Entry e (get l "t") (get l "at") ty (canonical current) mode :subject (.get l "sj")))
            (.append (.setdefault queues (get l "t") []) e)
            (.append events #(e "req" (get l "t") e)))
      (= kind "ans")
        (do (val entry (.get entries (get l "s")))
            (when (is entry None)
              (raise (ValueError (.format "答え {} の問い {} が無い" e (get l "s")))))
            (setv entry.ans-e e entry.ans-at (.get l "at") entry.ok (get l "ok"))
            (if entry.ok
                ;; 答えの値はここで戻す(再生の handler は JSON を読まない — #2581)。差分の元(val-bases)は戻す前の JSON の値。
                ;; {"$w": n} は WatchedRef、他は decode-value で戻す。戻せない値(import できない型・読めない印・型の欄と合わない中身)は
                ;; 既定の値に倒さず、問いの番号と effect の型を名指して断る。
                (setv entry.value
                      (match (refs (_resolve l val-bases "e" "v" "vd" "vb" "vk"))
                        {"$w" watched} :if (isinstance watched int) (WatchedRef :entry watched)
                        raw (try (decode-value raw)
                                 (except [err [TypeError ValueError KeyError AttributeError]]
                                   (raise (ValueError (.format "記録の答え(問い {}・effect の型 {})を今の版で戻せない: {}: {}"
                                                               entry.e entry.type (. (type err) __name__) err))
                                          :from err)))))
                (setv entry.error (if (is (get l "err") None) None (OpaqueJson.of (get l "err")))))
            (.append events #(e "ans" entry.task (get l "s"))))
      (= kind "mut") (do (.append events #(e "mut" None l)) (.append muts l))
      (= kind "end") (do (setv (get ended (get l "t")) (get l "ok")) (.append events #(e "end" (get l "t") None)))
      True None))
  ;; 読みの間の索引(dict)を、再生が引く形へ: 問いは番号で引く tuple・task の問いの列は表・終わった task は名の集合。
  (Recording (RunHeader :format (get header "format") :service (.get header "service") :run (.get header "run")
                        :revision (.get header "revision") :program (.get header "program"))
             events
             (tuple (gfor i (range (+ (max entries :default -1) 1)) (.get entries i)))
             (table-of (tuple (gfor #(task numbers) (.items queues) (TableWrite task (tuple numbers)))))
             (frozenset ended)
             :broken broken :muts muts))


;; --- 突き合わせ -------------------------------------------------------------------------------

(defn #^ (get tuple #(str int list)) match-step [#^ Recording rec #^ tuple queue #^ int head #^ str type #^ str args-text #^ str mode #^ (| str None) subject #^ bool task-ended]
  "純粋: task の問いの列(queue・head = 次に見る位置)と、いま業務コードが出した問い(型・引数の比べる形 args-text・mode・対の鍵)から、
   何をするかを決める。答え = #(判定 位置 飛ばした問いの番号の列)。判定:
     \"strict\"   記録の問いと同じ(read / live)
     \"same\"     decision / output で記録と同じ
     \"changed\"  decision / output で同じ位置・同じ対の鍵・引数が違う
     \"extra\"    decision / output で記録に対が無い
     \"diverge\"  read / live で型か引数が違う・記録の終わった task がさらに問うた
     \"finish\"   記録がこの task について終わった(まだ動いていた task の記録の末尾)
   飛ばした問い = 記録に在って業務コードが出さなかった decision / output(missing)。"
  (setv skipped [] pos head)
  (while True
    (setv e (if (< pos (len queue)) (get queue pos) None))
    (when (is e None)
      (return (if (in mode LOOSE)
                  #("extra" pos skipped)
                  #((if task-ended "diverge" "finish") pos skipped))))
    (setv entry (get rec.entries e))
    (cond
      (and (in mode LOOSE) (in entry.mode LOOSE) (= entry.type type) (= entry.subject subject))
        (return #((if (= entry.args-text args-text) "same" "changed") pos skipped))
      (in entry.mode LOOSE)
        (do (.append skipped e) (+= pos 1))
      (in mode LOOSE)
        (return #("extra" pos skipped))
      (or (!= entry.type type) (!= entry.args-text args-text))
        (return #("diverge" pos skipped))
      True
        (return #("strict" pos skipped)))))


;; --- 結果 -------------------------------------------------------------------------------------

(defk diff-row [kind entry type subject task replayed-args [at None]]
  {:pre [(: kind str) (: entry (| Entry None)) (: type str) (: subject (| str None)) (: task str) (: replayed-args (| dict None)) (: at (| int None))] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation" :spells "json"}}
  "判断の違い 1 件。kind = changed / missing / extra。recorded = 記録の引数・replayed = 再生の引数・delta = 記録 → 再生の差分。"
  (val recorded (if (is entry None) None (recorded-args entry)))
  {"kind" kind "type" type "subject" subject "task" task
   "at" (if (is-not entry None) entry.at at) "event" (if (is entry None) None entry.e)
   "recorded" recorded "replayed" replayed-args
   "delta" (if (and (is-not recorded None) (is-not replayed-args None)) (delta-of recorded replayed-args) None)})

(defk summarize [#^ Recording rec #^ dict counts #^ list decisions #^ list outputs #^ (| dict None) divergence #^ str end #^ int consumed
                 #^ (| int None) [from-ms None] #^ (| int None) [to-ms None]]
  {:pre [(: rec Recording) (: counts dict) (: decisions list) (: outputs list) (: divergence (| dict None)) (: end str) (: consumed int) (: from-ms (| int None)) (: to-ms (| int None))] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation" :spells "json"}}
  "再生の結果を 1 つの dict にまとめる。from-ms / to-ms = 報告の範囲(判断の違いを記録の時刻で絞る。再生そのものは run の始まりから)。"
  ;; 範囲の内 = 時刻を持たないか、時刻が from-ms から to-ms の間(端を含む)。
  (val in-range (fn [row] (match (.get row "at")
                            None True
                            at (and (or (is from-ms None) (>= at from-ms)) (or (is to-ms None) (<= at to-ms))))))
  (val ds (lfor d decisions :if (in-range d) d))
  (val os (lfor o outputs :if (in-range o) o))
  ;; 対の鍵ごとのまとめ: 書きが記録の世界に着かないので、新しい版は同じ行を拍ごとに書き直そうとする(extra が繰り返す)。
  ;; 人が読むのは「どの行の判断が変わったか」なので、行ごとに種類の数・最初と最後の時刻・最初の違いを 1 件にまとめる。
  (val by-subject {})
  (for [d ds]
    (val subject-key #((get d "type") (get d "subject")))
    (when (not-in subject-key by-subject)
      (setv (get by-subject subject-key) {"type" (get d "type") "subject" (get d "subject") "counts" {} "firstAt" (get d "at")
                                          "lastAt" (get d "at") "first" d}))
    (val summary (get by-subject subject-key))
    (setv (get (get summary "counts") (get d "kind")) (+ 1 (.get (get summary "counts") (get d "kind") 0)))
    (setv (get summary "lastAt") (get d "at")))
  {"run" rec.header.run "service" rec.header.service
   "recordedRevision" rec.header.revision
   "events" (len rec.events) "consumed" consumed
   "matched" counts
   "decisionDiffs" ds
   "decisionDiffCounts" (dfor k ["changed" "missing" "extra"] k (len (lfor d ds :if (= (get d "kind") k) d)))
   "decisionDiffSubjects" (sorted (.values by-subject) :key (fn [s] #((or (get s "firstAt") 0) (str (get s "subject")))))
   "outputDiffCounts" (dfor k ["changed" "missing" "extra"] k (len (lfor o os :if (= (get o "kind") k) o)))
   "outputDiffSamples" (cut os 0 20)
   "divergence" divergence
   "broken" rec.broken
   "end" end
   "identical" (and (is divergence None) (not ds) (not os) (!= end "program-failed"))})
