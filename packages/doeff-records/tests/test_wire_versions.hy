;; 版の違う service と client の間の答えの綴り(wire.hy の頭の註): 答えの読み手は知らない鍵を読み捨て、書き手は鍵を足すだけにする。
;;
;; 凍らせた答えの見本 tests/wire-answers/<日>.json = その日の書き手が test_wire.hy の ANSWERS を綴った JSON。file は足すだけで、古い
;; file は直さない(1 版前の client が読む形の記録)。答えの形を変えた変更は、新しい日の file を足す(test-the-newest-frozen-answers-…)。
;;   - 今の読み手は、凍らせた全部の見本を読む(古い書き手 → 新しい読み手 — 新しい鍵を必須にしない)
;;   - 見本の object の全部に知らない鍵を足しても、同じ答えに読む(新しい書き手 → 1 版前の読み手 — 足した鍵で落ちない)
;;   - 今の書き手は、見本から読んだ答えを綴り直すと、見本の鍵を全部同じ綴りで持つ(書き手は鍵を消さない・綴りを変えない)
;; 出自: 2026-10-19 03:10〜03:33 UTC、上から読む一覧の次の頁の位置に鍵 afterValue を足した版の service を先に起こし、鍵を知らない
;; 1 版前の client が答えを WireMalformed で断って、その client の書きの要求が 23 分 500 で落ちた。
(require doeff-hy.macros [deftest defk <- val var])
(import json)
(import pathlib [Path])
(import doeff [run])
(import doeff_hy._json_value [JsonValue])
(import doeff_records.wire [ANSWER-KINDS encode-answer decode-answer])
(import tests.test_wire [ANSWERS])

(val FROZEN-DIR (/ (. (Path __file__) parent) "wire-answers"))
;; 使い手の JSON を運ぶ欄(行の値・書いた値・変更の値・出来事の本文)— 中の鍵は答えの綴りの鍵ではないので、足さず・比べ方も値の等しさ。
(val PAYLOAD-KEYS (frozenset ["value" "body"]))
(val UNKNOWN-KEY "futureKey")


(defk frozen-files []
  {:pre [] :post [(: % tuple)] :tags {:context "records" :role "judgment"}}
  "凍らせた答えの見本の file を日の順に並べるため(最後が今の形)。"
  (tuple (sorted (.glob FROZEN-DIR "*.json"))))


(defk frozen-samples []
  {:pre [] :post [(: % tuple)] :tags {:context "records" :role "judgment"}}
  "凍らせた全部の見本を (file の名, 操作, 答えの JSON) の組で読むため。"
  (var samples #())
  (<- files tuple (frozen-files))
  (for [path files]
    (for [sample (json.loads (.read-text path :encoding "utf-8"))]
      (:= samples (+ samples #(#(path.name (get sample "operation") (get sample "answer")))))))
  samples)


(defk with-unknown-keys [value]
  {:pre [(: value JsonValue)] :post [(: % JsonValue)] :tags {:context "records" :role "judgment"}}
  "答えの綴りの object の全部に、今の読み手の知らない鍵を足すため(新しい版の書き手が足した鍵の代わり — 使い手の JSON の欄の中は触らない)。"
  (cond
    (isinstance value dict)
      (do (var grown {UNKNOWN-KEY {"kind" "later" "n" 1}})
          (for [#(name part) (.items value)]
            (if (in name PAYLOAD-KEYS)
                (:= grown (| grown {name part}))
                (do (<- inner (with-unknown-keys part))
                    (:= grown (| grown {name inner})))))
          grown)
    (isinstance value list)
      (do (var items [])
          (for [item value]
            (<- inner (with-unknown-keys item))
            (:= items (+ items [inner])))
          items)
    True value))


(defk dropped-keys [written frozen where]
  {:pre [(: written JsonValue) (: frozen JsonValue) (: where str)] :post [(: % tuple)] :tags {:context "records" :role "judgment"}}
  "今の書き手の綴り written が、凍らせた見本 frozen の鍵を全部同じ綴りで持つかを照らすため(答え = 消えた・綴りや値の違う所の列)。"
  (cond
    (and (isinstance frozen dict) (isinstance written dict))
      (do (var problems #())
          (for [#(name part) (.items frozen)]
            (cond
              (not-in name written) (:= problems (+ problems #((+ where "." name " が消えた"))))
              (in name PAYLOAD-KEYS)
                (when (!= (get written name) part) (:= problems (+ problems #((+ where "." name " の値が違う")))))
              True (do (<- inner (dropped-keys (get written name) part (+ where "." name)))
                       (:= problems (+ problems inner)))))
          problems)
    (and (isinstance frozen list) (isinstance written list) (= (len frozen) (len written)))
      (do (var item-problems #())
          (for [#(index part) (enumerate frozen)]
            (<- inner (dropped-keys (get written index) part (.format "{}[{}]" where index)))
            (:= item-problems (+ item-problems inner)))
          item-problems)
    (= frozen written) #()
    True #((.format "{} が {!r} から {!r} に変わった" where frozen written))))


(deftest test-the-reader-of-today-reads-every-frozen-answer
  ;; 古い書き手 → 新しい読み手: 読み手が新しい鍵を必須にすると、先に起きた client が古い service の答えを読めない。
  (val samples (run (frozen-samples)))
  (assert samples "凍らせた答えの見本が無い")
  (for [#(name operation answer) samples]
    (run (decode-answer operation answer))))


(deftest test-an-answer-with-keys-unknown-to-the-reader-reads-as-the-same-answer
  ;; 新しい書き手 → 1 版前の読み手: 答えの object のどこに鍵が足されても、読み手は読み捨てて同じ答えにする(知らない kind は別 —
  ;; test_wire.hy の test-malformed-json-is-refused-not-defaulted)。
  (for [#(name operation answer) (run (frozen-samples))]
    (val grown (run (with-unknown-keys answer)))
    (assert (= (run (decode-answer operation grown)) (run (decode-answer operation answer)))
            (.format "{} の {} の答えに知らない鍵を足すと読みが変わる: {!r}" name operation grown))))


(deftest test-the-writer-of-today-only-adds-keys-to-every-frozen-answer
  ;; 書き手は鍵を足すだけ: 鍵を消す・綴りを変える版の service を起こすと、1 版前の client は足りない鍵で答えを断る。
  (for [#(name operation answer) (run (frozen-samples))]
    (val written (json.loads (json.dumps (run (encode-answer (run (decode-answer operation answer)))))))
    (val problems (run (dropped-keys written answer operation)))
    (assert (not problems) (.format "{} の見本を今の書き手が綴り直すと鍵が足りない: {}" name (list problems)))))


(deftest test-the-newest-frozen-answers-are-the-shape-of-today
  ;; 最も新しい見本 = 今の書き手が ANSWERS を綴った JSON。答えの形を変えた(ANSWERS に鍵の在る形を足した)変更は、ここで赤になる —
  ;; 新しい日の file を足し(中身 = ANSWERS の組ごとの {"operation" 操作 "answer" encode-answer の JSON} の列 — 鍵の順・字下げ 1)、
  ;; 古い file は直さない。
  (val files (run (frozen-files)))
  (val newest (json.loads (.read-text (get files -1) :encoding "utf-8")))
  (val today (lfor #(operation answer) ANSWERS {"operation" operation "answer" (run (encode-answer answer))}))
  (assert (= (json.loads (json.dumps today)) newest)
          (.format "答えの形が {} と違う — tests/wire-answers に新しい日の file を足す(古い file は直さない)" (. (get files -1) name)))
  ;; 見本は答えの全部の kind を 1 回以上持つ。
  (assert (= (sfor sample newest (get sample "answer" "kind")) (sfor kinds (.values ANSWER-KINDS) kind kinds kind))))
