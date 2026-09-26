;; 身元の名簿: principals.json を厳しく読み、Bearer の token を書き手の名へ引く(引けなければ Unauthorized)。
(require doeff-hy.macros [deftest])
(import json)
(import doeff [run])
(import doeff_records.principals [Principal Unauthorized decode-roster identify token-digest])


(defn roster-text [entries]
  "名簿の綴り(検の入力)。"
  (json.dumps {"version" 1 "principals" entries}))


(deftest test-a-roster-names-each-token-holder
  (setv roster (run (decode-roster (roster-text [{"name" "maker" "tokenSha256" (run (token-digest "tm"))}
                                                 {"name" "painter" "tokenSha256" (.upper (run (token-digest "tp")))}]))))
  (assert (= (run (identify roster "Bearer tm")) (Principal "maker")))
  (assert (= (run (identify roster "bearer   tp ")) (Principal "painter")))
  (for [header [None "" "Bearer" "Basic tm" "Bearer other"]]
    (assert (isinstance (run (identify roster header)) Unauthorized) (repr header))))


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
