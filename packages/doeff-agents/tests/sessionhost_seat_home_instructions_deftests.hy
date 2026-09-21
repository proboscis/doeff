;;; 直接束縛 deftest: 席の家へ運ぶ**共通の指示**(CLAUDE.md と skills)の連鎖。
;;;
;;; card acp:kanban-issue:ki-62aa1f4e9c9c(設計 = doeff docs/design/seat-home-common-instructions-AJ8C0B/)。
;;; 守る性質: **機体の参加の宣言が名指した正本が、無人席の家(CLAUDE_CONFIG_DIR)へ起動の拍ごとに届く**。
;;;
;;;   D1  据え付けは起動の拍(鋳造の拍ではない)・座は claude-pre-launch の 1 か所
;;;   D2  CLAUDE.md は**実体 file**(symlink / hard link にしない — 本体の user 層が落とす)
;;;   D3  skills は dir ごとの symlink・既に正しい先なら張り替えない(走っている席の見張りを起こさない)
;;;   D5  名指しが在って現物が無いのは**非致命**(参加も起動も断らない・名乗り 1 行 + 行の labels)
;;;   D6  二重読みは doeff が置く 3 つ目の settings の鍵(綴りの家 = impls/claude_code.hy の
;;;       CLAUDE-MD-EXCLUDES-SETTING)で落とす。値は席の $HOME から導く 1 本
;;;   D8  張り替えの動詞は substrate の FsEnsureSymlink(3 値)— 器は呼ぶだけ(盲検 A の反例)
;;;   D10 宣言していない path を器が触らない(盲検 B の反例 — 囮を置いて弁別する)
;;;   D11 運ぶ物の綴りは 1 つの名簿(policy.CARRIED-INSTRUCTION-SOURCES)— 検は**回る**・列挙しない
;;;
;;; fake substrate は sessionhost_launch_deftests の LaunchWorld を**そのまま**使う(第 2 の世界を
;;; 作らない)。生 IO ゼロ。
;;;
;;; ⚠ 射程外(報告済み): 降りた process の続き(headless.continue-headless-process = 4 つ目の腕)は
;;; claude-pre-launch を通らないので、据え付けは起こす拍だけ。家は session の生涯で残るので普段の
;;; 手番は影響を受けないが、正本の path が手番の途中で動いた日は次の**起こす**拍まで古い。

(require doeff-hy.macros [deftest defk deff <- defhandler])

(import json)
(import pytest)

(import doeff_agents.sessionhost.policy [
  CARRIED-INSTRUCTION-SOURCES
  CARRIED-SOURCE-DIR-LINK
  CARRIED-SOURCE-FILE-TEXT])
(import doeff_agents.sessionhost.acp.join [
  AGENTD-KEYS
  claude-settings-declaration-of
  declared-values-of
  instruction-sources-of])
(import doeff_agents.sessionhost.acp.effects [JoinDeclaration JOIN-SCHEMA])
(import doeff_agents.sessionhost.impls.claude_code [CLAUDE-MD-EXCLUDES-SETTING])

(import sessionhost_launch_deftests [LaunchWorld launch-params run-launch])


;; ---------------------------------------------------------------------------
;; 世界の組み立て(宣言 → env が据わった claude の席)
;; ---------------------------------------------------------------------------

(setv HOME "/home/agentd")
(setv MEMORY-SOURCE "/srv/common/claude/CLAUDE.md")
(setv SKILLS-SOURCE "/srv/common/agent/skills")
(setv MEMORY-TEXT "# 共通の条文\n- 日本語で答える\n")
(setv CONFIG-DIR "/x/claude")

;; 囮(D10・盲検 B): 宣言していない・器が「知っていそうな」path。宣言が無い拍にここへ手が
;; 伸びたら、それは綴りが code の中に第 2 の家を持っている証拠。
(setv DECOY-MEMORY f"{HOME}/dotfiles/claude/CLAUDE.md")
(setv DECOY-HOME-MEMORY f"{HOME}/.claude/CLAUDE.md")
(setv DECOY-SKILLS f"{HOME}/dotfiles/agent/skills")


(defn claude-params [#** overrides]
  (setv params {"agent_type" "claude"
                "binding" {"kind" "claude-code" "config_dir" CONFIG-DIR}})
  (.update params overrides)
  (launch-params #** params))


(defn seat-world [[declare True] [memory-present True] [skills-present True]
                  [memory-source MEMORY-SOURCE] [skills-source SKILLS-SOURCE]]
  "宣言(env)と正本の在否を据えた世界。囮は**常に**disk に置く(D10 の弁別力)。"
  (setv world (LaunchWorld))
  (setv (get world.env "HOME") HOME)
  (setv world.capture-script ["❯ {composer}"])
  ;; 囮は宣言の有無に依らず在る(「無いから届かない」だけの検にしない)。
  (setv (get world.fs DECOY-MEMORY) "# 囮(宣言していない正本)\n")
  (setv (get world.fs DECOY-HOME-MEMORY) "# 囮(有人 profile の家)\n")
  (setv (get world.listings DECOY-SKILLS) ["decoy-skill"])
  (when declare
    (setv (get world.env "DOEFF_AGENTD_CLAUDE_MEMORY_FILE") memory-source)
    (setv (get world.env "DOEFF_AGENTD_CLAUDE_SKILLS_DIR") skills-source))
  (if memory-present
      (setv (get world.fs memory-source) MEMORY-TEXT)
      (.pop world.fs memory-source None))
  (if skills-present
      (setv (get world.listings skills-source) ["browser-ops" "herdr-comms"])
      (.add world.missing-dirs skills-source))
  world)


(defn touched-strings [world]
  "世界の効果の痕跡に現れた文字列の全部(= 器が触った path の上界)。"
  (setv out [])
  (for [entry world.trace]
    (for [item entry]
      (when (isinstance item str)
        (.append out item))))
  out)


(defn launch-command [world]
  (get (get world.sent-keys 0) 1))


(defn settings-json [cmd]
  "起動 command の --settings に**現に出た** JSON(導出点の戻りではなく argv の出口を見る)。"
  (setv marker "--settings ")
  (when (not-in marker cmd)
    (return None))
  (setv rest (cut cmd (+ (.index cmd marker) (len marker)) None))
  ;; shell-join は JSON を ' で括る(空白を含まないので裸の日もある)。
  (setv body (if (.startswith rest "'")
                 (cut rest 1 (.index rest "'" 1))
                 (get (.split rest " ") 0)))
  (json.loads (.replace body "'\\''" "'")))


;; ---------------------------------------------------------------------------
;; (a) 宣言 → env → 起動の拍 → 家の中の実体 file
;; ---------------------------------------------------------------------------

(deftest test-declared-memory-lands-in-the-home-as-a-real-file
  ;; ⚑ 受入 1 / 2(D2): 家の CLAUDE.md は**実体 file**で、中身は正本と一致する。
  ;; 世界では「実体 file」= fs(書いた物)に在り links(張った物)に**無い**こと。
  ;; 実 substrate の側(symlink でない・nlink 1)は sessionhost_substrate_deftests と、
  ;; 本体の契約は dotfiles check_native_claude_home_contract.py が持つ。
  (setv world (seat-world))
  (<- row (run-launch world (claude-params)))
  (setv home-memory f"{CONFIG-DIR}/CLAUDE.md")
  (assert (in home-memory world.fs) (list (.keys world.fs)))
  (assert (= (get world.fs home-memory) MEMORY-TEXT))
  (assert (not-in home-memory world.links) world.links)
  ;; 正本は**読んだ**だけ(家の外へ書かない)。
  (assert (= (get world.fs MEMORY-SOURCE) MEMORY-TEXT)))


;; ---------------------------------------------------------------------------
;; (b) skills は正本を指す symlink
;; ---------------------------------------------------------------------------

(deftest test-declared-skills-land-in-the-home-as-a-symlink
  ;; ⚑ 受入 3(D3): 家の skills は正本の dir を指す symlink 1 本(中身を写さない)。
  (setv world (seat-world))
  (<- row (run-launch world (claude-params)))
  (setv home-skills f"{CONFIG-DIR}/skills")
  (assert (= (.get world.links home-skills) SKILLS-SOURCE) world.links)
  (assert (not-in home-skills world.fs) (list (.keys world.fs)))
  ;; 1 本ずつ写していない(dir ごと 1 本 — 中の file 名は家に現れない)。
  (assert (not-in f"{home-skills}/browser-ops" world.links) world.links))


;; ---------------------------------------------------------------------------
;; (c) 2 度目の起動は張り替えない
;; ---------------------------------------------------------------------------

(deftest test-second-launch-leaves-the-skills-symlink-untouched
  ;; ⚑ D3 / D8: 既に正しい先を指していれば張り替えない(走っている席の見張りを起こさない)。
  ;; 名乗りは**動詞が返した 3 値そのもの**(D1b)。
  (setv world (seat-world))
  (<- _ (run-launch world (claude-params)))
  (setv first-log (list world.log-lines))
  (<- _ (run-launch world (claude-params :session_id "s2" :session_name "doeff-s2")))
  (setv second-log (cut world.log-lines (len first-log) None))
  (assert (= (.get world.links f"{CONFIG-DIR}/skills") SKILLS-SOURCE))
  (assert (any (gfor line first-log (and (in "claude_skills_dir" line) (in "linked" line))))
          first-log)
  (assert (any (gfor line second-log (and (in "claude_skills_dir" line) (in "unchanged" line))))
          second-log)
  (assert (not (any (gfor line second-log (and (in "claude_skills_dir" line) (in "outcome=linked" line)))))
          second-log))


;; ---------------------------------------------------------------------------
;; (d) 名指しが在って現物が無い日も席は起きる
;; ---------------------------------------------------------------------------

(deftest test-an-absent-source-still-opens-the-seat-and-names-itself
  ;; ⚑ 受入 7(D5): 劣化の日(宿の checkout が追いつく前)に席を止めない。
  ;; 断りの代わりに**起動の拍ごとの名乗り 1 行**を出す。
  (setv world (seat-world :memory-present False :skills-present False))
  (<- row (run-launch world (claude-params)))
  (setv kinds (lfor t world.trace (get t 0)))
  (assert (in "new-session" kinds) kinds)
  (assert (.startswith (launch-command world) "claude --dangerously-skip-permissions"))
  (assert (any (gfor line world.log-lines (in "seat-memory-file-absent" line))) world.log-lines)
  (assert (any (gfor line world.log-lines (in "seat-skills-dir-absent" line))) world.log-lines)
  ;; 家には 1 byte も置かない(在ると嘘の条文が載る)。
  (assert (not-in f"{CONFIG-DIR}/CLAUDE.md" world.fs) (list (.keys world.fs)))
  (assert (not-in f"{CONFIG-DIR}/skills" world.links) world.links))


;; ---------------------------------------------------------------------------
;; (e) 名指しの無い機体は今日どおり
;; ---------------------------------------------------------------------------

(deftest test-a-host-that-declares-nothing-keeps-todays-home-and-argv
  ;; ⚑ 受入 8: 宣言しない機体の家と argv は 1 byte も変わらない。
  (setv world (seat-world :declare False))
  (<- row (run-launch world (claude-params)))
  (setv cmd (launch-command world))
  (assert (not-in CLAUDE-MD-EXCLUDES-SETTING cmd) cmd)
  (assert (not-in f"{CONFIG-DIR}/CLAUDE.md" world.fs) (list (.keys world.fs)))
  (assert (not-in f"{CONFIG-DIR}/skills" world.links) world.links)
  ;; 今日の argv(hook の無効化だけ)。
  (assert (= (settings-json cmd) {"disableAllHooks" True}) cmd))


;; ---------------------------------------------------------------------------
;; (f) argv の出口に二重読みの落としが 1 本ちょうど
;; ---------------------------------------------------------------------------

(deftest test-argv-carries-exactly-one-claude-md-exclude
  ;; ⚑ 受入 6(D6): 席に固有の二重読み(祖先の歩きで拾う有人 profile の家の記憶)を
  ;; doeff が置く 3 つ目の鍵で落とす。値は席の $HOME から**導く** 1 本ちょうど。
  ;; 導出点の戻りではなく **`--settings` に現に出た JSON**を見る(autocompact-arg-pair-ok と同じ向き)。
  (setv world (seat-world))
  (<- row (run-launch world (claude-params)))
  (setv cmd (launch-command world))
  (setv settings (settings-json cmd))
  (assert (= (get settings CLAUDE-MD-EXCLUDES-SETTING) [DECOY-HOME-MEMORY]) settings)
  (assert (= (.count cmd DECOY-HOME-MEMORY) 1) cmd)
  ;; $HOME を名乗らない宿では鍵を置かない(導けないものを発明しない)。
  (setv homeless (seat-world))
  (.pop homeless.env "HOME" None)
  (<- _ (run-launch homeless (claude-params)))
  (setv homeless-settings (settings-json (launch-command homeless)))
  (assert (not-in CLAUDE-MD-EXCLUDES-SETTING homeless-settings) homeless-settings)
  ;; 家への据え付けは $HOME に依らない(宣言が名指した正本だけで決まる)。
  (assert (in f"{CONFIG-DIR}/CLAUDE.md" homeless.fs) (list (.keys homeless.fs))))


;; ---------------------------------------------------------------------------
;; (g) 宣言 file が doeff の鍵を持てば参加が断る
;; ---------------------------------------------------------------------------

(deftest test-a-seat-settings-file-may-not-spell-the-doeff-exclude-key
  ;; ⚑ D6: 落としの鍵(CLAUDE-MD-EXCLUDES-SETTING)は doeff が合流点で置く。席の settings file がこれを持つと、
  ;; argv の合流で衝突して**二重読みの落としが黙って消える**(か hook が死ぬ)。
  ;; 参加の門 (c) が断る = CLAUDE-SETTINGS-OWNED-KEYS にこの鍵が入っていること。
  (setv raised None)
  (try
    (<- _ (claude-settings-declaration-of
            (json.dumps {CLAUDE-MD-EXCLUDES-SETTING ["/home/someone/.claude/CLAUDE.md"]})
            "inherit"))
    (except [e ValueError] (setv raised e)))
  (assert (is-not raised None))
  (assert (in CLAUDE-MD-EXCLUDES-SETTING (str raised)) (str raised)))


;; ---------------------------------------------------------------------------
;; (h) 【D10・盲検 B】宣言が無い機体へは 1 byte も届かない(囮つき)
;; ---------------------------------------------------------------------------

(deftest test-without-a-declaration-no-decoy-reaches-the-seat
  ;; ⚑ 受入 13 前半(盲検 B): 「宣言が無ければ届かない」を、**囮を disk に置いた上で**撃つ。
  ;; 囮が無いと「無いから届かない」だけが証明され、code の中に第 2 の綴りの家が在っても緑になる。
  (setv world (seat-world :declare False))
  (<- row (run-launch world (claude-params)))
  (setv touched (touched-strings world))
  (for [decoy [DECOY-MEMORY DECOY-HOME-MEMORY DECOY-SKILLS]]
    (assert (not-in decoy touched) #(decoy touched)))
  (assert (not-in f"{CONFIG-DIR}/CLAUDE.md" world.fs) (list (.keys world.fs)))
  (assert (not-in f"{CONFIG-DIR}/skills" world.links) world.links))


;; ---------------------------------------------------------------------------
;; (i) 【D10・盲検 B】劣化の日に、宣言外の path を器が触らない
;; ---------------------------------------------------------------------------

(deftest test-on-a-degraded-day-the-vessel-touches-no-undeclared-path
  ;; ⚑ 受入 13 後半(盲検 B): 条件は**宣言は在る・名指された現物は無い**(= 劣化の日)。
  ;; ⚠ 平常日に置くと候補の列が宣言の path で短絡するので違反が通る(設計の counterexamples.md
  ;; 「自分の検に弁別力が無かった」)。だから劣化の日に置く — ここで囮へ手が伸びるなら、
  ;; それは「宣言が名指した物が無い日に別の綴りへ落ちる」実装(= 盲検 B の違反)。
  (setv world (seat-world :memory-present False :skills-present False))
  (<- row (run-launch world (claude-params)))
  (setv touched (touched-strings world))
  (for [decoy [DECOY-MEMORY DECOY-HOME-MEMORY DECOY-SKILLS]]
    (assert (not-in decoy touched) #(decoy touched)))
  ;; 名指した path は**現に**読みに行っている(この検自身の弁別力の担保 — 何も触らない
  ;; 実装でも上の assert は通るので、触った証拠を 1 つ要求する)。
  (assert (in MEMORY-SOURCE touched) touched)
  (assert (in SKILLS-SOURCE touched) touched))


;; ---------------------------------------------------------------------------
;; (j) 【D8】正本の path が変わった日に家の symlink が張り替わる
;; ---------------------------------------------------------------------------

(deftest test-when-the-source-moves-the-home-symlink-follows
  ;; ⚑ 受入 11(盲検 A の反例): FsLinkArtifact は据わっている物を絶対に置き換えない
  ;; ("target-conflict" を返して終わる)ので、それで組むと家の skills が**古い先を指したまま**
  ;; になる。張り替えは substrate の 1 動詞(FsEnsureSymlink)の責務。
  (setv world (seat-world))
  (<- _ (run-launch world (claude-params)))
  (assert (= (.get world.links f"{CONFIG-DIR}/skills") SKILLS-SOURCE) world.links)
  ;; 正本が動いた日(宣言の値が変わり、次の席が起きる)。
  (setv moved "/srv/common-v2/agent/skills")
  (setv (get world.env "DOEFF_AGENTD_CLAUDE_SKILLS_DIR") moved)
  (setv (get world.listings moved) ["browser-ops"])
  (setv before (len world.log-lines))
  (<- _ (run-launch world (claude-params :session_id "s2" :session_name "doeff-s2")))
  (assert (= (.get world.links f"{CONFIG-DIR}/skills") moved) world.links)
  (setv after (cut world.log-lines before None))
  (assert (any (gfor line after (and (in "claude_skills_dir" line) (in "linked" line)))) after))


;; ---------------------------------------------------------------------------
;; (k) 【D11】母集団は名簿から導く(列挙しない)
;; ---------------------------------------------------------------------------

(deftest test-every-carried-source-travels-from-the-declaration-to-the-home
  ;; ⚑ 受入 15(D11): 名簿を**回る**。1 種足して行き先(join の鍵・env・運び方・家の名)を
  ;; 宣言しない足し方はここで赤くなる。種ごとの枝をこの検に書かない。
  (assert CARRIED-INSTRUCTION-SOURCES)
  (for [source CARRIED-INSTRUCTION-SOURCES]
    ;; 宣言の鍵は join が許す鍵に在る(名簿から導いていれば自動で在る)。
    (assert (in source.key AGENTD-KEYS) #(source.key AGENTD-KEYS))
    ;; その 1 種だけを名指した世界を組み、家に現物が現れることを撃つ。
    (setv world (LaunchWorld))
    (setv (get world.env "HOME") HOME)
    (setv world.capture-script ["❯ {composer}"])
    (setv origin f"/srv/only/{source.key}")
    (setv (get world.env source.env) origin)
    (cond
      (= source.kind CARRIED-SOURCE-FILE-TEXT) (setv (get world.fs origin) MEMORY-TEXT)
      (= source.kind CARRIED-SOURCE-DIR-LINK) (setv (get world.listings origin) ["a-skill"])
      True (assert False f"名簿の運び方が閉語彙の外: {source.kind}"))
    (<- _ (run-launch world (claude-params)))
    (setv landed f"{CONFIG-DIR}/{source.home-name}")
    (cond
      (= source.kind CARRIED-SOURCE-FILE-TEXT)
        (do (assert (in landed world.fs) #(source.key (list (.keys world.fs))))
            (assert (= (get world.fs landed) MEMORY-TEXT)))
      (= source.kind CARRIED-SOURCE-DIR-LINK)
        (assert (= (.get world.links landed) origin) #(source.key world.links)))
    ;; 名乗りは鍵つきで出る(どの種が届いたかが log で分かる)。
    (assert (any (gfor line world.log-lines (in source.key line))) #(source.key world.log-lines))))


(deftest test-a-declaration-key-outside-the-roster-is-refused
  ;; ⚑ D11 / fail-closed: 名簿に無い鍵は参加が断る(旧い agentd が宣言された宛先を黙って落とさない)。
  ;; 逆向きに、名簿の鍵は**全部**通る。
  (setv known (dfor source CARRIED-INSTRUCTION-SOURCES source.key "/srv/x"))
  (setv (get known "server") "https://acp.example")
  (setv (get known "token_file") "/run/token")
  (<- values (declared-values-of (JoinDeclaration
                                   :tables {"schema" JOIN-SCHEMA "agentd" known}
                                   :sha256 None)))
  (for [source CARRIED-INSTRUCTION-SOURCES]
    (assert (in source.key (get values "agentd")) #(source.key values)))
  (setv raised None)
  (try
    (<- _ (declared-values-of (JoinDeclaration
                                :tables {"schema" JOIN-SCHEMA
                                         "agentd" {"server" "https://acp.example"
                                                   "claude_memory_dir" "/srv/x"}}
                                :sha256 None)))
    (except [e ValueError] (setv raised e)))
  (assert (is-not raised None))
  (assert (in "claude_memory_dir" (str raised)) (str raised)))


(deftest test-the-declared-spelling-must-be-absolute-or-tilde
  ;; ⚑ D4 / D11: 名指しの形の門は 1 点(join.instruction-sources-of)— cwd 相対は断る
  ;; (どの cwd で読むかを黙って決めない)。名簿を回る 1 本で、種ごとの枝を持たない。
  (for [source CARRIED-INSTRUCTION-SOURCES]
    (setv raised None)
    (try
      (<- _ (instruction-sources-of {source.key "relative/path"}))
      (except [e ValueError] (setv raised e)))
    (assert (is-not raised None) source.key)
    (assert (in source.key (str raised)) (str raised))
    ;; `~/…` は通る(展開は composition root)。
    (<- pairs (instruction-sources-of {source.key "~/dotfiles/x"}))
    (assert (in #(source.key "~/dotfiles/x") pairs) pairs)))
