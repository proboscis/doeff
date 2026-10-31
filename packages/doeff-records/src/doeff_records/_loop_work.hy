;;; 記録の service の scheduler の thread の仕事の分け(ADR-DOE-RECORDS-002)— 要求 1 つのために scheduler の thread が回す計算の上限を、
;;; 答えの型と読みの形で決める 1 点。
;;;
;;; 記録の service の process は 1 つの run・1 つの scheduler で動き、受けの loop・要求の task・書きの門・変化の待ちの全部が 1 本の
;;; scheduler の thread の上で回る(_http_server.hy の頭の註)。その thread が 1 要求の計算で塞がる間、後から届いた要求は受けの列で待つ。
;;;   持つ    要求の受け(待ち受けの出来事・本文の読みの命令)・順の制御(task の立てと待ち・書きの門・錠・変化の待ち・取り消し)・計器・
;;;           答えの送りの命令と、大きさが要求そのものか 1 行で決まる計算(要求の本文の読み解き・1 行と 1 出来事の読み解きと綴り・書きの
;;;           答えの綴り)
;;;   持たない 大きさが置き場に在る行の数で決まる計算 = 置き場の束の読み(一覧の頁・変更の列・出来事の列 — 長さは上限 limit までの、置き場に
;;;           在る行の数で決まる)の行の読み解き(置き場の payload の JSON → 凍らせた値)と、束の読みの答え(BATCH-READ-ANSWERS)の綴り
;;;           (凍らせた値 → JSON の文字列 → UTF-8 の byte 列)。どちらも純粋な program を Compute で出し(off-loop)、composition root が
;;;           置く Compute の答え手が回す(本番の土台の口 _main.hy の records-connected と検の殻 _http_server.hy の start-records-server =
;;;           thread の pool — free-threaded の Python では scheduler の thread と並んで走る・台本の検と模擬 = inline-compute-handler)
;;; 行を 1 つも運ばない束(空の頁・静かな変更の列)は綴る行が無く計算が定数なので、その場で綴る(thread の受け渡しの上乗せを払わない)。
;;;
;;;   BATCH-READ-ANSWERS   束の読みの答えの型(Page・Changes・Events)— 綴りを scheduler の thread の外で回す
;;;   ON-LOOP-ANSWERS      ほかの wire の答えの型 — 大きさが要求の本文か 1 行の上限で決まり、その場で綴る
;;;   off-loop             純粋な program を Compute で scheduler の thread の外で回し、答えの値を返す(落ちた program の例外はそのまま上げる)
;;;   off-loop-answer?     答え 1 つの綴りを scheduler の thread の外で回すか(束の読みの答えで、行を 1 つ以上運ぶ)
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "records" :role "foundation"})
(import doeff [DoExpr])
(import doeff_core_effects [Compute Computed ComputeFailed])
(import doeff_records._values [Row Missing Page Written Conflict Refused NotIndexed Reset Changes Appended Events WrittenRows RowsConflict
                              RowsRefused StreamEnd StreamEmpty EventsMoved EventsQuiet Event EventAbsent EventRetired AppendedEvents
                              Count WrittenAndAppended AppendRefused BatchRead])
(import doeff_records._wire [WireAnswer])

;; 束の読みの答え(頭の註の「持たない」)と、ほかの wire の答え(頭の註の「持つ」)。2 つで wire の答えの型(_wire.hy の WireAnswer)を
;; ちょうど 1 度ずつ覆う — 答えの型を足す変更は、どちらかに入れないと ADR-DOE-RECORDS-002 の検が赤になる。
;; Count(行の数 1 つ)・WrittenAndAppended と AppendRefused(書きの束の答え — 大きさは要求の本文で決まる)はその場で綴る。
;; BatchRead(読みの束 read-batch の答え)も今はその場で綴る: 大きさは束の中の一覧の limit の和(LIST-LIMIT-MAX まで)で縛られるが、
;; 頭の註の「持つ」の上限(要求の本文か 1 行)には入らない。scheduler の thread の外へ出すかは、この表の外の変更で決める。
(val BATCH-READ-ANSWERS #(Page Changes Events))
(val ON-LOOP-ANSWERS #(Row Missing Written Conflict Refused NotIndexed Reset Appended WrittenRows RowsConflict RowsRefused StreamEnd
                       StreamEmpty EventsMoved EventsQuiet Event EventAbsent EventRetired AppendedEvents
                       Count WrittenAndAppended AppendRefused BatchRead))


(defk off-loop [program]
  {:pre [(: program DoExpr)] :post [(: % "program の答え")] :tags {:context "records" :role "foundation"}}
  "純粋な program(effect を出さない — 行の読み解き・答えの綴り)を Compute で scheduler の thread の外で回し、答えの値を返すため(頭の註)。
   program が落ちた時は、その場で回した時と同じく例外のまま上げる(要求の task が 500 internal で答える — _http_server.hy の answer-arrival)。"
  (<- outcome (| Computed ComputeFailed) (Compute program))
  (match outcome
    (Computed :value value) value
    (ComputeFailed :error error) (raise error)))


(defk off-loop-answer? [answer]
  {:pre [(: answer WireAnswer)] :post [(: % bool)] :tags {:context "records" :role "foundation"}}
  "答え 1 つの綴りを scheduler の thread の外で回すかを決めるため: 束の読みの答え(BATCH-READ-ANSWERS)で、行・変更・出来事を 1 つ以上
   運ぶ答えだけが外(空の束は綴る行が無い — 頭の註)。"
  (match answer
    (Page :rows rows) (> (len rows) 0)
    (Changes :items items) (> (len items) 0)
    (Events :items items) (> (len items) 0)
    _ False))
