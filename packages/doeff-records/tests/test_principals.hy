;; 書き手の名は呼び手の X-Records-Writer の名乗りだけで決まる(writer-of — 無ければ ANONYMOUS・#3008)。
;; 名簿の読み(decode-roster・identify)は service の経路から外れ、名簿を自前で読む呼び手の系のために残してある物の検(次の変更で消す)。
(require doeff-hy.macros [deftest])
(import json)
(import doeff [run])
(import doeff_records.principals [ANONYMOUS Principal decode-roster identify token-digest writer-of])


(defn roster-text [entries]
  "名簿の綴り(検の入力)。"
  (json.dumps {"version" 1 "principals" entries}))


(deftest test-a-roster-names-each-token-holder
  (setv roster (run (decode-roster (roster-text [{"name" "maker" "tokenSha256" (run (token-digest "tm"))}
                                                 {"name" "painter" "tokenSha256" (.upper (run (token-digest "tp")))}]))))
  (assert (= (run (identify roster "Bearer tm")) (Principal "maker")))
  (assert (= (run (identify roster "bearer   tp ")) (Principal "painter")))
  (for [header [None "" "Bearer" "Basic tm" "Bearer other"]]
    (assert (= (run (identify roster header)) (Principal ANONYMOUS)) (repr header))))


(deftest test-a-declared-writer-name-is-the-writer
  (assert (= (run (writer-of "painter")) (Principal "painter")))
  (assert (= (run (writer-of "  painter ")) (Principal "painter"))))


(deftest test-no-declared-name-is-anonymous
  (assert (= (run (writer-of None)) (Principal ANONYMOUS)))
  (assert (= (run (writer-of "  ")) (Principal ANONYMOUS))))


(deftest test-a-malformed-roster-is-refused-at-startup
  (setv digest (run (token-digest "t")))
  (for [text [(json.dumps [])
              (json.dumps {"version" 2 "principals" []})
              (json.dumps {"version" 1 "principals" [] "extra" 1})
              (roster-text [{"name" "a" "tokenSha256" digest "token" "t"}])
              (roster-text [{"name" "" "tokenSha256" digest}])
              (roster-text [{"name" "a:b" "tokenSha256" digest}])
              (roster-text [{"name" "a" "tokenSha256" "zz"}])
              (roster-text [{"name" "a" "tokenSha256" digest} {"name" "a" "tokenSha256" (run (token-digest "u"))}])
              (roster-text [{"name" "a" "tokenSha256" digest} {"name" "b" "tokenSha256" digest}])]]
    (try
      (run (decode-roster text))
      (assert False (.format "形の違う名簿を読んだ: {}" text))
      (except [ValueError] None))))
