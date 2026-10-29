;; 大きな transcript の差し替え(rotation.hy — card ki-d96c2ca8a5a1)の検: 切る位置の探し方・置き直しと前の file の残し方・
;; 差し替えない場合と、本番の handler の続き(替え玉の CLI が init の前に transcript の大きさに比例して待つ)が起動の期限を越えずに
;; 始まること。
(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import json)
(import os)
(import os.path)
(import pathlib [Path])
(import sys)
(import uuid)
(import doeff [with_handlers])
(import doeff_core_effects [slog-discard-handler])
(import doeff_time [sync-time-handler])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec ResumeSession TurnInput])
(import doeff_claude_code.lines [BackendLost Completed Failed Interrupted])
(import doeff_claude_code.effects [ClaudeCloseSession ClaudeStartTurn LaunchFailed SessionClosed TurnStarted])
(import doeff_claude_code.argv [transcript-path])
(import doeff_claude_code.clock [clock-of])
(import doeff_claude_code.handler [ClaudeCodeHost claude-code-handler])
(import doeff_claude_code.rotation [ARCHIVE-SUFFIX TranscriptKept TranscriptRotated TranscriptUnrotatable rotate-transcript
                                    rotation-cut])
(import tests.interpreters [STUB-PATH child-env])
(import tests.scenario_rules [reply-prompt])
(import tests.scenario_steps [read-to-end])

(val NOW-MS 1791385479741)
(val MIB 1048576)


(defk record-line [#^ str uuid #^ dict fields]
  {:pre [(: uuid str) (: fields dict)] :post [(: % str)] :tags {:context "claude-code" :role "program"}}
  "検の transcript の 1 行(実物と同じ順の欄 — parentUuid・type・uuid)を作るため。"
  (+ (json.dumps (| {"parentUuid" None "type" "user" "uuid" uuid} fields) :separators #("," ":") :ensure-ascii False) "\n"))

(defk boundary-line [#^ str uuid #^ list kept #^ list all-kept #^ (| str None) head]
  {:pre [(: uuid str) (: kept list) (: all-kept list) (: head (| str None))] :post [(: % str)]
   :tags {:context "claude-code" :role "program"}}
  "compact_boundary の行(実物 2.1.292 と同じ欄の名 — preservedMessages の uuids / allUuids・preservedSegment の headUuid)を作るため。
   kept が空なら残す行の記しの無い境。"
  (val meta (if kept
                {"trigger" "auto" "preservedSegment" {"headUuid" head "anchorUuid" "anchor" "tailUuid" (get kept -1)}
                 "preservedMessages" {"anchorUuid" "anchor" "uuids" kept "allUuids" all-kept}}
                {"trigger" "auto"}))
  (+ (json.dumps {"parentUuid" None "logicalParentUuid" "x" "type" "system" "subtype" "compact_boundary"
                  "compactMetadata" meta "uuid" uuid}
                 :separators #("," ":"))
     "\n"))

(defk padding [#^ int count]
  {:pre [(: count int)] :post [(: % str)] :tags {:context "claude-code" :role "program"}}
  "境の前の古い行(差し替えで落ちる行)を count 本作るため(record-line と同じ形)。"
  (.join "" (gfor n (range count)
                  (+ (json.dumps {"parentUuid" None "type" "user" "uuid" (.format "old-{}" n) "text" (* "x" 200)} :separators #("," ":"))
                     "\n"))))

(defk written [#^ Path path #^ str text]
  {:pre [(: path Path) (: text str)] :post [(: % str)] :tags {:context "claude-code" :role "program"}}
  "検の transcript を書くため。答え = path の文字列。"
  (.mkdir path.parent :parents True :exist-ok True)
  (.write-text path text :encoding "utf-8")
  (str path))

(defk archives-of [#^ str path]
  {:pre [(: path str)] :post [(: % list)] :tags {:context "claude-code" :role "program"}}
  "置き直しが残した前の file の名前を数えるため。"
  (sorted (gfor name (os.listdir (os.path.dirname path)) :if (.startswith name (+ (os.path.basename path) ARCHIVE-SUFFIX)) name)))


(deftest test-the-rotation-keeps-the-lines-the-last-boundary-preserves [tmp-path]
  ;; 境の行の前に、境が残すと記した行(p1・p2 — 間に残さない行 x を挟む)が在る。置き直した file は p1 の行から始まり、境と境の後の行を
  ;; 持つ。前の file は .before-rotation-<刻> の名前で同じ中身のまま残る(.jsonl で終わらない)。allUuids の行の無い uuid(ghost)は探す
  ;; 足しにするだけで、無くても切れる。
  (<- old (padding 50))
  (<- p1 (record-line "p1" {"text" "preserved head"}))
  (<- x (record-line "x" {"text" "dropped between"}))
  (<- p2 (record-line "p2" {"parentUuid" "p1" "text" "preserved tail"}))
  (<- boundary (boundary-line "b1" ["p1" "p2"] ["p1" "ghost" "p2"] "p1"))
  (<- after (record-line "a1" {"parentUuid" "b1" "text" "summary"}))
  (val whole (+ old p1 x p2 boundary after))
  (<- path (written (/ tmp-path "s.jsonl") whole))
  (<- outcome (rotate-transcript path 1000 NOW-MS))
  (val kept (+ p1 x p2 boundary after))
  (assert (= outcome (TranscriptRotated :before (len (.encode whole)) :after (len (.encode kept))
                                        :archive (.format "{}{}{}" path ARCHIVE-SUFFIX NOW-MS)))
          (repr outcome))
  (assert (= (.read-text (Path path) :encoding "utf-8") kept))
  (assert (= (.read-text (Path outcome.archive) :encoding "utf-8") whole))
  (assert (not (.endswith outcome.archive ".jsonl")))
  (assert (not (os.path.exists (+ path ".doeff-rotate-tmp")))))


(deftest test-the-cut-is-the-last-boundary-and-its-own-uuid-line [tmp-path]
  ;; 境が 2 つあれば最後の境で切る。残す行 p1 の uuid を parentUuid の欄や文字列の中(逃がした引用符)に持つ行を、p1 の行と取り違えない。
  ;; 残す行の記しの無い境は、境の行の頭で切る。
  (<- first-boundary (boundary-line "b0" [] [] None))
  (<- decoy (record-line "d" {"text" "{\"uuid\":\"p1\"}" "parentUuid" "p1"}))
  (<- p1 (record-line "p1" {"text" "head"}))
  (<- boundary (boundary-line "b1" ["p1"] ["p1"] "p1"))
  (<- after (record-line "a1" {"text" "after"}))
  (val data (.encode (+ first-boundary decoy p1 boundary after) "utf-8"))
  (<- cut-at (rotation-cut data))
  (assert (= (cut data cut-at None) (.encode (+ p1 boundary after) "utf-8")))
  (<- plain (boundary-line "b2" [] [] None))
  (val bare (.encode (+ p1 plain after) "utf-8"))
  (<- bare-cut (rotation-cut bare))
  (assert (= (cut bare bare-cut None) (.encode (+ plain after) "utf-8"))))


(deftest test-a-boundary-far-from-the-end-is-found-by-widening [tmp-path]
  ;; 境の後が 1 MiB を越える(最初の幅に境が無い)transcript も、幅を広げて同じ位置で切る。
  (<- old (padding 50))
  (<- boundary (boundary-line "b1" [] [] None))
  (<- after (padding 7000))
  (assert (> (len after) MIB))
  (<- path (written (/ tmp-path "s.jsonl") (+ old boundary after)))
  (<- outcome (rotate-transcript path 1000 NOW-MS))
  (assert (isinstance outcome TranscriptRotated) (repr outcome))
  (assert (= (.read-text (Path path) :encoding "utf-8") (+ boundary after))))


(deftest test-small-missing-linked-and-boundaryless-transcripts-stay-as-they-are [tmp-path]
  ;; 上限の内・無い・symlink の transcript は置き直さない(Kept)。上限を越えても境が無い・境が残すと記した行が無い transcript も
  ;; 置き直さない(Unrotatable — 探した幅を名乗る)。どれも前の file を残さず、中身を変えない。
  (<- old (padding 50))
  (<- boundary (boundary-line "b1" [] [] None))
  (<- small (written (/ tmp-path "small.jsonl") (+ old boundary)))
  (<- kept (rotate-transcript small (* 10 MIB) NOW-MS))
  (assert (= kept (TranscriptKept :size (os.path.getsize small))) (repr kept))
  (<- missing (rotate-transcript (str (/ tmp-path "missing.jsonl")) 1000 NOW-MS))
  (assert (= missing (TranscriptKept :size 0)) (repr missing))
  (val linked (/ tmp-path "linked.jsonl"))
  (.symlink-to linked small)
  (<- link-kept (rotate-transcript (str linked) 1000 NOW-MS))
  (assert (= link-kept (TranscriptKept :size 0)) (repr link-kept))
  (<- bare (written (/ tmp-path "bare.jsonl") old))
  (<- unrotatable (rotate-transcript bare 1000 NOW-MS))
  (assert (= unrotatable (TranscriptUnrotatable :size (os.path.getsize bare) :searched (os.path.getsize bare))) (repr unrotatable))
  (<- lost-boundary (boundary-line "b2" ["gone"] ["gone"] "gone"))
  (<- lost (written (/ tmp-path "lost.jsonl") (+ old lost-boundary)))
  (<- lost-outcome (rotate-transcript lost 1000 NOW-MS))
  (assert (isinstance lost-outcome TranscriptUnrotatable) (repr lost-outcome))
  (assert (= (.read-text (Path lost) :encoding "utf-8") (+ old lost-boundary)))
  (for [path [small bare lost]]
    (<- archives (archives-of path))
    (assert (= archives []) (repr archives))))


;; --- 本番の handler の続き(替え玉の CLI)-----------------------------------------------------------------------------

;; 替え玉の CLI を撃つ命令と、init の前の待ち(transcript 1 MiB あたりの秒 — 実物の全部の読みを模す・stub_cli/claude.hy の頭の註)。
(val STUB-COMMAND #(sys.executable "-m" "hy" STUB-PATH))
(val READ-SECONDS-PER-MIB "10")
(val LAUNCH-TIMEOUT 5.0)

(defk slow-read-spec [#^ Path tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % ClaudeSessionSpec)] :tags {:context "claude-code" :role "program"}}
  "替え玉の CLI が --resume の init の前に transcript の大きさに比例して待つ会話の宣言を作るため。"
  (val work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (ClaudeSessionSpec :home (ClaudeHome (str (/ tmp-path "home")) (| (child-env "") {"STUB_CLI_READ_SECONDS_PER_MIB" READ-SECONDS-PER-MIB}))
                     :cwd (str work) :settings {"disableAllHooks" True}))

(defk big-transcript [#^ ClaudeSessionSpec spec #^ str sid #^ bool with-boundary]
  {:pre [(: spec ClaudeSessionSpec) (: sid str) (: with-boundary bool)] :post [(: % str)] :tags {:context "claude-code" :role "program"}}
  "2 MiB を越える会話の transcript を置くため(境の在る形は、境と境の後の短い行で終わる)。答え = transcript の path。"
  (<- old (padding 9000))
  (<- boundary (boundary-line "b1" [] [] None))
  (<- after (record-line "a1" {"text" "earlier"}))
  (val path (Path (transcript-path spec.home.config-dir (os.path.realpath spec.cwd) sid)))
  (<- written-path (written path (if with-boundary (+ old boundary after) old)))
  (assert (> (os.path.getsize written-path) (* 2 MIB)))
  written-path)

(defk resume-once [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sid str)] :post [(: % (| Completed Failed Interrupted BackendLost LaunchFailed))] :tags {:context "claude-code" :role "program"}}
  "会話の続きを 1 手番起こし、始まれば最後まで読んで閉じるため。答え = 手番の終わりか LaunchFailed。"
  (<- started (ClaudeStartTurn (ResumeSession sid) spec (TurnInput (reply-prompt "AGAIN") "r1")))
  (when (isinstance started LaunchFailed)
    (return started))
  (assert (isinstance started TurnStarted) (repr started))
  (<- done (read-to-end started.turn 30.0))
  (<- closed (ClaudeCloseSession sid "test"))
  (assert (isinstance closed SessionClosed) (repr closed))
  done.end)

(defrecord Resumed
  "大きな transcript の続きを 1 手番起こした結末: end = 手番の終わりか LaunchFailed・size = 起こした後の transcript の大きさ・
   path = transcript の path。"
  (#^ (| Completed Failed Interrupted BackendLost LaunchFailed) end)
  (#^ int size)
  (#^ str path))

(defk resumed-with [#^ int rotate-above #^ Path tmp-path #^ bool with-boundary]
  {:pre [(: rotate-above int) (: tmp-path Path) (: with-boundary bool)] :post [(: % Resumed)] :tags {:context "claude-code" :role "program"}}
  "上限 rotate-above の host で大きな transcript の続きを起こし、結末を読むため。"
  (<- spec (slow-read-spec tmp-path))
  (val sid (str (uuid.uuid4)))
  (<- path (big-transcript spec sid with-boundary))
  (val host (ClaudeCodeHost STUB-COMMAND (clock-of (sync-time-handler)) 8 7200.0 :launch-timeout LAUNCH-TIMEOUT
                            :rotate-above-bytes rotate-above))
  (<- end (with_handlers [(sync-time-handler) slog-discard-handler (claude-code-handler host)] (resume-once host spec sid)))
  (Resumed :end end :size (os.path.getsize path) :path path))


(deftest test-a-big-transcript-is-shortened-and-the-resume-starts-within-the-launch-timeout [tmp-path]
  ;; 2 MiB を越える transcript を、替え玉の CLI は init の前に 20 秒かけて読む(起動の期限 5 秒を越える)。上限 1 MiB の host は境の後
  ;; だけに置き直してから起こすので、手番は始まって最後まで走る(前の file は残る)。
  (<- result (resumed-with MIB tmp-path True))
  (val end result.end)
  (val size result.size)
  (val path result.path)
  (assert (isinstance end Completed) (repr end))
  (assert (< size MIB) size)
  (<- archives (archives-of path))
  (assert (= (len archives) 1) (repr archives)))


(deftest test-without-the-rotation-the-same-resume-misses-the-launch-timeout [tmp-path]
  ;; 対照: 置き直さない host(上限 1 GiB)では、同じ transcript の続きは init の前に起動の期限を越えて LaunchFailed になる — 上の検が
  ;; 守る失敗(card ki-d96c2ca8a5a1 の 206 MB の会話の形)。
  (<- result (resumed-with (* 1024 MIB) tmp-path True))
  (val end result.end)
  (val size result.size)
  (val path result.path)
  (assert (isinstance end LaunchFailed) (repr end))
  (assert (in "no init line within" end.stderr-tail) end.stderr-tail)
  (assert (> size (* 2 MIB)) size))


(deftest test-a-big-transcript-without-a-boundary-fails-with-the-reason [tmp-path]
  ;; 境の無い大きな transcript は縮められない。起動が期限を越えた失敗の訳に、縮められなかった訳(大きさと探した幅)が載る。
  (<- result (resumed-with MIB tmp-path False))
  (val end result.end)
  (val size result.size)
  (val path result.path)
  (assert (isinstance end LaunchFailed) (repr end))
  (assert (in "no usable compact boundary" end.stderr-tail) end.stderr-tail)
  (assert (in (str size) end.stderr-tail) end.stderr-tail)
  (<- archives (archives-of path))
  (assert (= archives []) (repr archives)))
