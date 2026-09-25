;;; effect の記録と再生(backtest)の型と純粋な判断。I/O をしない(記録の行を作る・読む・突き合わせる・結果をまとめる)。
;;;
;;; 記録 = 1 つの process(run)が出した effect の問いと答えの出来事の列。行は JSON で、欄 "k" が種類:
;;;
;;;   run    先頭の 1 行。形の版・service・run の名・worker・process の世代・版(revision / base)・設定・始まりの時刻
;;;   chunk  区切り(chunk-seconds ごと)の頭。区切りごとに差分の元を忘れるので、各区切りの最初の値は丸ごと(断面)
;;;   req    問い。e = 出来事の番号(run の中で 0 から 1 ずつ・問いの id を兼ねる)・t = task の名・at = 壁時計の ms・
;;;          ty = effect の型の名・a = 引数(大きければ ad = 差分・ab = 元の出来事の番号・ak = 差分の鍵)
;;;   ans    答え。s = 問いの e・ok = 真なら v(大きければ vd / vb / vk)、偽なら err(例外の符号)
;;;   call   (形の版 2)問いと答えを 1 行にまとめた物。問いの欄に ok・v / err・dt(答えの時刻 - 問いの時刻)を足し、答えの番号は e + 1
;;;   blob   (形の版 2)内容参照の中身。h = 内容の hash・v = 畳んだ節。a / v の中の {"$ref": h} をこの中身に戻す(effect_codec.resolve-refs)
;;;   mut    業務コードと handler が共有する可変の箱(Ask の答え)の中身が変わった。ref = その箱を渡した問いの e・v = 新しい中身
;;;   end    task が終わった(t・ok)
;;;   broken 記録の登録に無い型・記録の形にできない値に当たった。ここから先は記録していない(再生は記録の終わりとして扱う)
;;;
;;; 並行: 出来事の番号は問いと答えの両方に振る(scheduler が task を切り替える順 = 答えが返った順も再生で同じにするため)。
;;; task の名は親の名 + 「.」+ 親の中で何番目に Spawn したか(scheduler の番号に依らないので記録と再生で同じ)。根は "root"。
(import dataclasses [dataclass field])
(import doeff_cluster.effect_codec [READ LIVE DECISION OUTPUT LOOSE READABLE-FORMATS canonical apply-delta delta-of resolve-refs])

(setv ROOT "root")


(defclass ReplayFinished [Exception]
  "記録の終わりに着いた(再生の正常な終わり)。業務コードが捕まえても、次の effect でまた投げる。")

(defclass ReplayDiverged [Exception]
  "問いが記録と食い違った(分岐)。推測で答えを作らずに止める。")


;; --- 記録の読み ---------------------------------------------------------------------------------

(defclass [(dataclass)] Entry []
  "問い 1 つ(req)と、その答え(ans)。ans-e が None = 記録が終わった時にまだ答えが返っていなかった。"
  (#^ int e)
  (#^ str task)
  (#^ int at)
  (#^ str type)
  (#^ dict args)
  (#^ str mode)
  (setv #^ object subject None)
  (setv #^ object ans-e None)
  (setv #^ object ans-at None)
  (setv #^ bool ok True)
  (setv #^ object value None)
  (setv #^ object error None))


(defclass [(dataclass)] Recording []
  "読んだ記録。events = 出来事の番号の昇順の #(番号 種類 task の名 付帯)。entries = 問いの番号 → Entry。
   queues = task の名 → その task の問いの番号の列(問いの順)。ended = 記録の中で終わった task の名 → ok。"
  (#^ dict header)
  (#^ list events)
  (#^ dict entries)
  (#^ dict queues)
  (#^ dict ended)
  (setv #^ object broken None)
  (setv #^ list muts (field :default-factory list)))


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


(defn #^ list _expand-calls [#^ list lines]
  "call の行(問いと答えの 1 行)を req と ans の 2 行に戻す。他の行はそのまま。"
  (setv out [])
  (for [l lines]
    (if (!= (.get l "k") "call")
        (.append out l)
        (do (setv req (dfor #(k v) (.items l) :if (not-in k #("ok" "v" "err" "dt")) k v))
            (setv (get req "k") "req")
            (.append out req)
            (.append out (| {"k" "ans" "e" (+ (get l "e") 1) "s" (get l "e") "at" (+ (get l "at") (.get l "dt" 0)) "ok" (get l "ok")}
                            (if (get l "ok") {"v" (.get l "v")} {"err" (get l "err")}))))))
  out)


(defn #^ Recording read-recording [#^ list lines [until-ms None] [mode-of-type None]]
  "記録の行(dict の列・順不同でよい)→ Recording。until-ms = この時刻より後の出来事を捨てる(範囲の終わり)。
   mode-of-type = 型の名 → 登録の mode(再生の側の effect_codec から渡す。無ければ行の mode 欄)。"
  (setv header None broken None events [] entries {} queues {} ended {} arg-bases {} val-bases {} muts [] blobs {} memo {})
  (setv ordered (sorted (_expand-calls (lfor l lines :if (in "e" l) l)) :key (fn [l] (get l "e"))))
  (for [l lines]
    (setv kind (.get l "k"))
    (cond
      (= kind "run") (setv header l)
      ;; 内容参照の中身は順に依らない(同じ h は同じ中身)ので先に全部集める。
      (= kind "blob") (setv (get blobs (get l "h")) (get l "v"))))
  (when (is header None)
    (raise (ValueError "記録に run の行が無い")))
  (when (not-in (.get header "format") READABLE-FORMATS)
    (raise (ValueError (.format "記録の形の版が違う: {}(読めるのは {})" (.get header "format") READABLE-FORMATS))))
  (defn #^ object refs [#^ object v] (if blobs (resolve-refs v blobs memo) v))
  (setv seen {})
  (for [l ordered]
    (setv e (get l "e") kind (get l "k"))
    ;; 置き場へ送り直した行は同じ中身で 2 度在りうる(届いたか分からずに送り直した)。同じなら 1 つにし、違えば壊れた記録。
    (setv body (canonical (dfor #(key v) (.items l) :if (not-in key #("_chunk")) key v)))
    (when (in e seen)
      (if (= (get seen e) body)
          (continue)
          (raise (ValueError (.format "出来事の番号 {} に違う中身が 2 つある" e)))))
    (setv (get seen e) body)
    (when (and (is-not until-ms None) (> (.get l "at" 0) until-ms))
      (break))
    (cond
      (= kind "broken") (do (setv broken l) (break))
      (= kind "req")
        (do (setv args (refs (if (or (in "a" l) (in "ad" l)) (_resolve l arg-bases "e" "a" "ad" "ab" "ak") {})))
            (setv mode (if (is mode-of-type None) (get l "m") (mode-of-type (get l "ty") l)))
            (setv (get entries e) (Entry e (get l "t") (get l "at") (get l "ty") args mode :subject (.get l "sj")))
            (.append (.setdefault queues (get l "t") []) e)
            (.append events #(e "req" (get l "t") e)))
      (= kind "ans")
        (do (setv entry (.get entries (get l "s")))
            (when (is entry None)
              (raise (ValueError (.format "答え {} の問い {} が無い" e (get l "s")))))
            (setv entry.ans-e e entry.ans-at (.get l "at") entry.ok (get l "ok"))
            (if entry.ok
                (setv entry.value (refs (_resolve l val-bases "e" "v" "vd" "vb" "vk")))
                (setv entry.error (get l "err")))
            (.append events #(e "ans" entry.task (get l "s"))))
      (= kind "mut") (do (.append events #(e "mut" None l)) (.append muts l))
      (= kind "end") (do (setv (get ended (get l "t")) (get l "ok")) (.append events #(e "end" (get l "t") None)))
      True None))
  (Recording header events entries queues ended :broken broken :muts muts))


;; --- 突き合わせ -------------------------------------------------------------------------------

(defn match-step [#^ Recording rec #^ list queue #^ int head #^ str type #^ dict args #^ str mode subject #^ bool task-ended]
  "純粋: task の問いの列(queue・head = 次に見る位置)と、いま業務コードが出した問い(型・引数・mode・対の鍵)から、
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
        (return #((if (= (canonical entry.args) (canonical args)) "same" "changed") pos skipped))
      (in entry.mode LOOSE)
        (do (.append skipped e) (+= pos 1))
      (in mode LOOSE)
        (return #("extra" pos skipped))
      (or (!= entry.type type) (!= (canonical entry.args) (canonical args)))
        (return #("diverge" pos skipped))
      True
        (return #("strict" pos skipped)))))


;; --- 結果 -------------------------------------------------------------------------------------

(defn #^ dict diff-row [#^ str kind entry #^ str type subject #^ str task replayed-args [at None]]
  "判断の違い 1 件。kind = changed / missing / extra。recorded = 記録の引数・replayed = 再生の引数・delta = 記録 → 再生の差分。"
  (setv recorded (if (is entry None) None entry.args))
  {"kind" kind "type" type "subject" subject "task" task
   "at" (if (is-not entry None) entry.at at) "event" (if (is entry None) None entry.e)
   "recorded" recorded "replayed" replayed-args
   "delta" (if (and (is-not recorded None) (is-not replayed-args None)) (delta-of recorded replayed-args) None)})

(defn #^ dict summarize [#^ Recording rec #^ dict counts #^ list decisions #^ list outputs divergence #^ str end #^ int consumed
                         [from-ms None] [to-ms None]]
  "再生の結果を 1 つの dict にまとめる。from-ms / to-ms = 報告の範囲(判断の違いを記録の時刻で絞る。再生そのものは run の始まりから)。"
  (defn in-range [row]
    (setv at (.get row "at"))
    (or (is at None)
        (and (or (is from-ms None) (>= at from-ms)) (or (is to-ms None) (<= at to-ms)))))
  (setv ds (lfor d decisions :if (in-range d) d) os (lfor o outputs :if (in-range o) o))
  ;; 対の鍵ごとのまとめ: 書きが記録の世界に着かないので、新しい版は同じ行を拍ごとに書き直そうとする(extra が繰り返す)。
  ;; 人が読むのは「どの行の判断が変わったか」なので、行ごとに種類の数・最初と最後の時刻・最初の違いを 1 件にまとめる。
  (setv by-subject {})
  (for [d ds]
    (setv k #((get d "type") (get d "subject")))
    (when (not-in k by-subject)
      (setv (get by-subject k) {"type" (get d "type") "subject" (get d "subject") "counts" {} "firstAt" (get d "at")
                                "lastAt" (get d "at") "first" d}))
    (setv s (get by-subject k))
    (setv (get (get s "counts") (get d "kind")) (+ 1 (.get (get s "counts") (get d "kind") 0)))
    (setv (get s "lastAt") (get d "at")))
  {"run" (.get rec.header "run") "service" (.get rec.header "service")
   "recordedRevision" (.get rec.header "revision") "recordedBase" (.get rec.header "base")
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
