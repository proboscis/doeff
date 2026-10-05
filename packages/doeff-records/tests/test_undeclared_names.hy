;; UndeclaredTable の欄 tables・streams(宣言に無いと分かった名)の検。使い手(無くても仕事を続けられる表の断りだけを畳む呼び手)は
;; どの表の断りかを欄で照らし、文の綴りを読まない。http の口を通した形は test_http_service.hy の 2 本。
;;   (a) 宣言の引き(memory の置き場と service が使う RecordsSchema.table・stream)は名を欄に入れる。
;;   (b) 404 の理由の綴り: service が組む理由から client が戻す欄は、宣言に無い名だけ(宣言に在る名・名の前方が同じ別の名は入らない)。
;;       欄を足す前の service の理由(「宣言に無い表: ['x']」)からも同じ欄が戻る。知らない route の断りは欄が空。
;;   (c) 文だけで作った旧い形は欄が空(使い手は欄が空なら畳まない)。写し(pickle)を通しても欄が残る。
(require doeff-hy.macros [deftest <- var])
(import pickle)
(import doeff_hy.frozen [FrozenMap])
(import doeff_records.values [FieldDecl TableDecl StreamDecl RecordsSchema UndeclaredTable])
(import doeff_records.effects [ReadRow PutRows RowWrite AppendEvent WatchChanges])
(import doeff_records.values [ExpectAny WatchCursor])
(import doeff_records.wire [undeclared-reason undeclared-refusal])

(setv SCHEMA (RecordsSchema :tables (FrozenMap {"parts" (TableDecl :name "parts" :key-fields #("id")
                                                                   :fields #((FieldDecl :name "id")))})
                            :streams (FrozenMap {"journal" (StreamDecl "journal")})))


(deftest test-a-the-schema-lookup-names-what-is-undeclared
  (var names [])
  (try (SCHEMA.table "nothing") (except [refused UndeclaredTable] (:= names (+ names [#(refused.tables refused.streams)]))))
  (try (SCHEMA.stream "nowhere") (except [refused UndeclaredTable] (:= names (+ names [#(refused.tables refused.streams)]))))
  (assert (= names [#(#("nothing") #()) #(#() #("nowhere"))]) names))


(deftest test-b-the-refusal-reason-carries-back-only-the-undeclared-names
  (setv declared-tables (tuple SCHEMA.tables) declared-streams (tuple SCHEMA.streams))
  ;; 1 つの表の読み・束の書き(宣言に在る表と無い表)・待ち(前方が同じ別の名を含む)・追記の列。
  (for [#(ask tables streams) [#((ReadRow "nothing" #("k")) #("nothing") #())
                               #((PutRows #((RowWrite "parts" #("p1") {} (ExpectAny)) (RowWrite "nothing" #("x") {} (ExpectAny)))) #("nothing") #())
                               #((WatchChanges #("part" "parts") (WatchCursor 1 0)) #("part") #())
                               #((AppendEvent "nowhere" "k1" {}) #() #("nowhere"))]]
    (<- reason (undeclared-reason ask declared-tables declared-streams))
    (<- refused (undeclared-refusal ask reason))
    (assert (= #(refused.tables refused.streams) #(tables streams)) #(ask reason refused.tables refused.streams))
    (assert (= (str refused) reason) refused))
  ;; 宣言に在る名だけの要求は断らない。
  (<- none (undeclared-reason (ReadRow "parts" #("p1")) declared-tables declared-streams))
  (assert (is none None) none))


(deftest test-b-an-older-service-reason-and-an-unknown-route-are-read-as-before
  ;; 欄を足す前の service の理由の綴り(本番の記録の service の 2026-10-02 の log の文)からも同じ欄が戻る。
  (<- older (undeclared-refusal (ReadRow "pane-observation" #("zeus")) "宣言に無い表: ['pane-observation']"))
  (assert (= older.tables #("pane-observation")) older)
  ;; 知らない route の断り(名を載せない)は欄が空 — 使い手はどの表の断りかを決めない。
  (<- route (undeclared-refusal (ReadRow "parts" #("p1")) "知らない route: POST /v1/records/read-row"))
  (assert (= #(route.tables route.streams) #(#() #())) route))


(deftest test-c-the-text-only-form-has-no-names-and-a-copy-keeps-them
  (setv older (UndeclaredTable "宣言に無い表: 'parts'"))
  (assert (= #(older.tables older.streams (str older)) #(#() #() "宣言に無い表: 'parts'")) older)
  (setv named (UndeclaredTable "宣言に無い表: ['nothing']" :tables #("nothing"))
        copied (pickle.loads (pickle.dumps named)))
  (assert (and (isinstance copied UndeclaredTable) (= #(copied.tables copied.streams (str copied)) #(#("nothing") #() (str named))))
          copied))
