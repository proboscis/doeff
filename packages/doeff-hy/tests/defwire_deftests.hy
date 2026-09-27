;;; defwire(doeff_hy/record.hy)と解き手(doeff_hy/wire.hy)の検(agora-redesign #840)。
;;;
;;; 確かめること: 解けた値・欄の名の写し(:camel / :snake / :kebab / 明示の辞書)・知らない欄の断りと読み捨て・:check の失敗・
;;; 型違いの Malformed(文字列の数・真偽の数・列挙の外の綴り)・凍らせた JSON と JSON の文字列からの解き・dump と parse の往復・
;;; JSON Schema・タグ・展開の時に断る形。
;;; 公開は test_defwire.py(包み直さずそのまま公開する — ADR-DOE-HY-002)。

(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defwire defenum])
(import dataclasses [dataclass FrozenInstanceError])
(import enum [StrEnum])
(import hy)
(import pytest)
(import doeff_hy.declarations [DefinitionTags])
(import doeff_hy.frozen [freeze-json])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_hy.wire [parse parse-json dump dump-json json-schema Malformed MalformedField WireShape])


(defenum LandState QUEUED LANDED)

(defwire Note
  "注記 1 つ"
  {:names :camel}
  (#^ str note-text))

(defwire LandingRow
  "取り込みの台帳の 1 行(記録の service が返す JSON)"
  {:tags {:context "land-notice" :role "type"}
   :names :camel
   :unknown :reject
   :check [(.startswith lane-id "L")]}
  (#^ str lane-id)
  (#^ LandState state)
  (setv #^ (| int None) landed-at None)
  (setv #^ float ratio 0.0)
  (setv #^ (get tuple #(Note ...)) notes #()))

(defwire SnakeRow {:names :snake} (#^ str lane-id))
(defwire KebabRow {:names :kebab} (#^ str lane-id))
(defwire ExplicitRow {:names {lane-id "LANE" size "n"}} (#^ str lane-id) (#^ int size))
(defwire LenientRow {:names :camel :unknown :ignore} (#^ str lane-id))


(defk refused [source]
  {:pre [(: source str)] :post [(: % (| str None))] :tags {:context "wire" :role "judgment"}}
  "source を展開して、断った時の文言を返す(断らなければ None)。"
  (try
    (hy.eval (hy.read-many (+ "(require doeff-hy.record [defwire])\n(import dataclasses [dataclass])\n" source))
             :module (hy.I.types.ModuleType "probe"))
    None
    (except [e Exception]
      (str e))))


(deftest test-parse-gives-the-typed-value
  {:tags {:context "wire" :role "judgment"}}
  (<- row (parse LandingRow {"laneId" "L1" "state" "queued" "landedAt" 3 "ratio" 1 "notes" [{"noteText" "x"}]}))
  (assert (= row (LandingRow :lane-id "L1" :state LandState.QUEUED :landed-at 3 :ratio 1.0 :notes #((Note :note-text "x")))))
  ;; 既定値の在る欄は省ける・値は凍っている。
  (<- short (parse LandingRow {"laneId" "L2" "state" "landed"}))
  (assert (= #(short.landed-at short.notes) #(None #())))
  (with [(pytest.raises FrozenInstanceError)]
    (setattr short "state" LandState.QUEUED)))


(deftest test-field-names-follow-names
  {:tags {:context "wire" :role "judgment"}}
  (assert (= LandingRow.__doeff_wire__.names {"lane_id" "laneId" "state" "state" "landed_at" "landedAt" "ratio" "ratio" "notes" "notes"}))
  (<- snake (parse SnakeRow {"lane_id" "L1"}))
  (<- kebab (parse KebabRow {"lane-id" "L1"}))
  (<- explicit (parse ExplicitRow {"LANE" "L1" "n" 2}))
  (assert (= #(snake.lane-id kebab.lane-id explicit.lane-id explicit.size) #("L1" "L1" "L1" 2)))
  ;; Python の欄の名では受けない(wire の名だけ)。
  (<- by-python-name (parse LandingRow {"lane_id" "L1" "state" "queued"}))
  (assert (isinstance by-python-name Malformed))
  (assert (in "laneId" (lfor f by-python-name.fields f.field))))


(deftest test-unknown-fields-are-refused-or-ignored
  {:tags {:context "wire" :role "judgment"}}
  (<- refused-row (parse LandingRow {"laneId" "L1" "state" "queued" "extra" 1}))
  (assert (= refused-row (Malformed :wire-type "LandingRow"
                                    :fields #((MalformedField :field "extra" :reason "Unexpected keyword argument [unexpected_keyword_argument]")))))
  (<- lenient (parse LenientRow {"laneId" "L1" "extra" 1}))
  (assert (= lenient (LenientRow :lane-id "L1"))))


(deftest test-a-failed-check-is-malformed
  {:tags {:context "wire" :role "judgment"}}
  (<- answer (parse LandingRow {"laneId" "X1" "state" "queued"}))
  (assert (isinstance answer Malformed))
  (assert (= (. (get answer.fields 0) field) ""))
  (assert (in "LandingRow の欄 lane-id が検め (.startswith lane-id \"L\") で落ちた" (. (get answer.fields 0) reason))))


(deftest test-type-mismatches-are-malformed-with-the-field-named
  {:tags {:context "wire" :role "judgment"}}
  ;; 文字列を数にしない・真偽を数にしない・列挙の外の綴り・入れ子の欄の型 — 合わない所を全部名指す。
  (<- answer (parse LandingRow {"laneId" "L1" "state" "nope" "landedAt" "3" "ratio" True "notes" [{"noteText" 1}]}))
  (assert (= (lfor f answer.fields f.field) ["state" "landedAt" "ratio" "notes.0.noteText"]))
  (<- not-an-object (parse LandingRow [1 2]))
  (assert (= (lfor f not-an-object.fields f.field) [""])))


(deftest test-frozen-json-and-json-text-parse-the-same
  {:tags {:context "wire" :role "judgment"}}
  (val raw {"laneId" "L1" "state" "landed" "notes" [{"noteText" "y"}]})
  (<- from-value (parse LandingRow raw))
  (<- from-frozen (parse LandingRow (freeze-json raw)))
  (<- from-text (parse-json LandingRow "{\"laneId\": \"L1\", \"state\": \"landed\", \"notes\": [{\"noteText\": \"y\"}]}"))
  (<- from-bytes (parse-json LandingRow b"{\"laneId\": \"L1\", \"state\": \"landed\", \"notes\": [{\"noteText\": \"y\"}]}"))
  (assert (= from-value from-frozen from-text from-bytes))
  (<- broken (parse-json LandingRow "{bad"))
  (assert (= (. (get broken.fields 0) field) ""))
  (assert (in "json_invalid" (. (get broken.fields 0) reason))))


(deftest test-dump-and-parse-round-trip
  {:tags {:context "wire" :role "judgment"}}
  (val row (LandingRow :lane-id "L1" :state LandState.LANDED :landed-at 7 :notes #((Note :note-text "z"))))
  (<- raw (dump row))
  ;; 既定値と同じ欄(ratio 0.0)は書かない — 読みは既定値で埋めるので往復する。
  (assert (= raw {"laneId" "L1" "state" "landed" "landedAt" 7 "notes" [{"noteText" "z"}]}))
  (<- back (parse LandingRow raw))
  (assert (= back row))
  (<- text (dump-json row))
  (<- back-from-text (parse-json LandingRow text))
  (assert (= back-from-text row)))


(defwire Declared
  "入れ子の任意の欄(null でなく欄の無いことで表す契約の形)"
  {:names :camel}
  (setv #^ (| str None) model None)
  (setv #^ (| str None) work-dir None))

(defwire Preference
  "入れ子の型の欄と、既定値の無い None を許す欄を持つ行"
  {:names :camel}
  (#^ Declared agent)
  (#^ (| str None) note)
  (setv #^ (| str None) notify-view None))


(deftest test-dump-omits-fields-at-their-default-even-when-nested
  {:tags {:context "wire" :role "judgment"}}
  (val row (Preference :agent (Declared :model "m") :note None))
  ;; 既定値 None の欄は null を書かず欄ごと無い(入れ子の型の欄も)・既定値の無い欄は None でも null で書く。
  (<- raw (dump row))
  (assert (= raw {"agent" {"model" "m"} "note" None}))
  (<- text (dump-json row))
  (assert (= text "{\"agent\":{\"model\":\"m\"},\"note\":null}"))
  (<- back (parse Preference raw))
  (assert (= back row)))


(deftest test-json-schema-uses-wire-names
  {:tags {:context "wire" :role "judgment"}}
  (<- schema (json-schema LandingRow))
  (assert (= (sorted (get schema "properties")) ["landedAt" "laneId" "notes" "ratio" "state"]))
  (assert (= (get schema "required") ["laneId" "state"]))
  (assert (is (get schema "additionalProperties") False))
  (assert (= (get schema "description") "取り込みの台帳の 1 行(記録の service が返す JSON)")))


(deftest test-tags-and-shape-are-readable-on-the-type
  {:tags {:context "wire" :role "judgment"}}
  (<- _ (parse Note {"noteText" "x"}))
  (assert (= LandingRow.__doeff_tags__ (DefinitionTags :context "land-notice" :role "type")))
  (assert (= LandingRow.__doeff_checks__ #("(.startswith lane-id \"L\")")))
  (assert (isinstance LandingRow.__doeff_wire__ WireShape))
  (assert (= LandingRow.__doeff_wire__.unknown "reject"))
  (assert (= LenientRow.__doeff_wire__.unknown "ignore")))


(deftest test-malformed-headers-are-refused-at-expansion
  {:tags {:context "wire" :role "judgment"}}
  (<- no-header (refused "(defwire A #^ str a)"))
  (assert (in "頭の辞書" no-header))
  (<- no-names (refused "(defwire A {:unknown :reject} #^ str a)"))
  (assert (in ":names が要る" no-names))
  (<- bad-names (refused "(defwire A {:names :pascal} #^ str a)"))
  (assert (in ":names は" bad-names))
  (<- bad-unknown (refused "(defwire A {:names :camel :unknown :drop} #^ str a)"))
  (assert (in ":unknown は" bad-unknown))
  (<- unknown-key (refused "(defwire A {:names :camel :doc \"x\"} #^ str a)"))
  (assert (in "受けない" unknown-key))
  (<- missing (refused "(defwire A {:names {a \"A\"}} #^ str a #^ str b)"))
  (assert (in "欄が足りない: b" missing))
  (<- extra (refused "(defwire A {:names {a \"A\" c \"C\"}} #^ str a)"))
  (assert (in "知らない欄: c" extra))
  (<- clash (refused "(defwire A {:names :camel} #^ str lane-id #^ str laneId)"))
  (assert (in "同じ wire の名" clash))
  (<- unnamable (refused "(defwire A {:names :camel} #^ str ok?)"))
  (assert (in "wire の名を作れない" unnamable))
  (<- fine (refused "(defwire A {:names :camel} #^ str a)"))
  (assert (is fine None)))


(defwire ToolCall
  "形を呼び手が決める任意の JSON の欄(OpaqueJson)を持つ行"
  {:names :camel :unknown :reject}
  (#^ str tool-name)
  (#^ OpaqueJson input)
  (setv #^ (| OpaqueJson None) output None))

(defwire ToolArgs
  "OpaqueJson の中を、形を知る読み手が解く型"
  {:names :camel :unknown :ignore}
  (#^ str path))


(deftest test-opaque-json-is-carried-unread-and-parsed-by-a-reader-that-knows-the-shape
  {:tags {:context "wire" :role "judgment"}}
  ;; defwire の欄に書いた OpaqueJson は任意の JSON を包み、dump は元の JSON の値へ戻す(往復する)。
  (val raw {"toolName" "Read" "input" {"path" "/a" "n" [1 {"x" None}]} "output" "ok"})
  (<- call (parse ToolCall raw))
  (assert (= call.input (OpaqueJson.of {"path" "/a" "n" [1 {"x" None}]})))
  (assert (= call.input.text "{\"path\":\"/a\",\"n\":[1,{\"x\":null}]}"))
  (assert (= call.input.encoded-size (len (.encode call.input.text "utf-8"))))
  (<- back (dump call))
  (assert (= back raw))
  ;; 形を知る読み手は parse で中を型へ解く — 形が違えば Malformed。
  (<- args (parse ToolArgs call.input))
  (assert (= args (ToolArgs :path "/a")))
  (<- wrong (parse ToolArgs call.output))
  (assert (isinstance wrong Malformed))
  ;; 等しさは最小の直列化の文字列の等しさ・凍らせた JSON からも同じ値・読めない文字列は断る。
  (assert (= (OpaqueJson.of (freeze-json {"a" [1 2]})) (OpaqueJson.from-text "{ \"a\" : [1, 2] }")))
  (with [(pytest.raises ValueError)]
    (OpaqueJson "{bad"))
  (with [(pytest.raises FrozenInstanceError)]
    (setattr call.input "text" "1")))
