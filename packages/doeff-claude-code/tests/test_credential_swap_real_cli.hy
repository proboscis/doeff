;; 本物の claude CLI(2.1.292 で確かめた)+ 偽の Messages API(tests/fake_messages_api.hy)で、借りた資格の差し替えが走っている CLI に
;; 効くことを確かめる検 — card ki-4ffdafa021e5。本物の API・本物の資格には触らない(ANTHROPIC_BASE_URL は 127.0.0.1 の
;; 偽の API・token は偽の文字列・CLI の設定 dir は検の tmp の下)。
;;
;; 印 e2e・slow(日次と着地の門の -m "not e2e" では走らない)。CLI が無い環境では skip する: CLI = env DOEFF_CLAUDE_CODE_CLI の path、
;; 無ければ PATH の claude。走らせ方(doeff の repo の根で):
;;   uv run pytest packages/doeff-claude-code/tests/test_credential_swap_real_cli.hy -m e2e
(require doeff-hy.macros [deftest defk <- val])
(import dataclasses [replace])
(import os)
(import os.path)
(import pathlib [Path])
(import shutil)
(import uuid)
(import doeff [with_handlers])
(import doeff_core_effects [slog-discard-handler])
(import doeff_time [sync-time-handler])
(import doeff_claude_code [ClaudeHome ClaudeSessionSpec FreshSession ResumeSession TurnInput LentCredential])
(import doeff_claude_code [Completed])
(import doeff_claude_code [ClaudeStartTurn ClaudeCloseSession ClaudeReplaceSessionCredential CredentialReplaced SessionClosed])
(import doeff_claude_code [ClaudeLiveProcess LiveProcess])
(import doeff_claude_code._clock [clock-of])
(import doeff_claude_code [ClaudeCodeHost claude-code-handler]
        doeff_claude_code._handler [wait-until])
(import tests.fake_messages_api [FakeMessagesApi])
(import tests.scenario_steps [read-to-end])

(val REAL-CLI (or (.get os.environ "DOEFF_CLAUDE_CODE_CLI") (shutil.which "claude")))
(val TURN-SECONDS 120.0)
(val FIRST-CALL-SECONDS 60.0)
;; 偽の token(上流へは出ない — 偽の API だけが受ける)。
(val TOKEN-A "fake-swap-token-a")
(val TOKEN-B "fake-swap-token-b")
(val TOKEN-C "fake-swap-token-c")


(defk lent [#^ str token]
  {:pre [(: token str)] :post [(: % LentCredential)] :tags {:context "claude-code" :role "program"}}
  "検の借りた資格(偽の token・1 時間先の期限の代わりに遠い先の期限・契約の種類 max)を作るため。"
  (LentCredential :access-token token :expires-at 4102444800.0 :subscription-type "max"))

(defk swap-spec [#^ Path tmp-path #^ FakeMessagesApi api]
  {:pre [(: tmp-path Path) (: api FakeMessagesApi)] :post [(: % ClaudeSessionSpec)] :tags {:context "claude-code" :role "program"}}
  "本物の CLI を偽の API へ向ける会話の宣言を作るため: env は PATH と検の HOME と偽の API の向け先だけ(この process の env の資格を
   継がせない)・設定 dir は検の tmp の下・資格は TOKEN-A。"
  (val work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (.mkdir (/ tmp-path "user-home") :parents True :exist-ok True)
  (.mkdir (/ tmp-path "shared") :parents True :exist-ok True)
  (<- first-lent (lent TOKEN-A))
  (ClaudeSessionSpec :home (ClaudeHome (str (/ tmp-path "shared"))
                                       {"PATH" (.get os.environ "PATH" "/usr/bin:/bin")
                                        "HOME" (str (/ tmp-path "user-home"))
                                        "ANTHROPIC_BASE_URL" api.url
                                        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC" "1"})
                     :cwd (str work) :model "claude-sonnet-4-5" :settings {"disableAllHooks" True}
                     :credential first-lent))

(defk swap-mid-turn-then-refresh-between-turns [#^ ClaudeCodeHost host #^ FakeMessagesApi api #^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: host ClaudeCodeHost) (: api FakeMessagesApi) (: spec ClaudeSessionSpec) (: sid str)] :post [(: % str)]
   :tags {:context "claude-code" :role "program"}}
  "1 手番目の本体の最初の呼びを偽の API に待たせている間に、資格を TOKEN-B へ替えて TOKEN-A を取り消し、手番を最後まで読む。続けて
   資格だけを TOKEN-C に替えた宣言で 2 手番目を走らせ(process を使い回す)、閉じる。答え = 資格の家の path。"
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput "Read the note file." "swap-1")))
  (assert (.wait api.first-main FIRST-CALL-SECONDS) "CLI の本体の最初の呼びが偽の API に届かない")
  (<- second-lent (lent TOKEN-B))
  (<- replaced (ClaudeReplaceSessionCredential sid second-lent))
  (assert (= replaced (CredentialReplaced :session-id sid)) replaced)
  (.revoke api TOKEN-A)
  (.set api.release)
  (<- one (read-to-end started.turn TURN-SECONDS))
  (assert (isinstance one.end Completed) (repr one.end))
  (<- third-lent (lent TOKEN-C))
  (<- again (ClaudeStartTurn (ResumeSession sid) (replace spec :credential third-lent) (TurnInput "Say done." "swap-2")))
  (<- two (read-to-end again.turn TURN-SECONDS))
  (assert (isinstance two.end Completed) (repr two.end))
  (<- view (ClaudeLiveProcess sid))
  (assert (= view (LiveProcess :launches 1)) view)
  (val runtime (.runtime host sid))
  (val home runtime.binding.credential-home)
  (<- closed (ClaudeCloseSession sid "test"))
  (assert (isinstance closed SessionClosed) (repr closed))
  (<- gone (wait-until (fn [bell] (.hang runtime.doorbell bell)) (fn [] (not (os.path.exists home))) 10.0))
  (assert gone home)
  home)


(deftest test-the-real-cli-uses-a-replaced-credential-from-its-next-call [tmp-path]
  {:marks ["e2e" "slow"]
   :skip-if (is REAL-CLI None)
   :skip-reason "本物の claude CLI が無い(env DOEFF_CLAUDE_CODE_CLI か PATH の claude)"}
  ;; 手番の途中で資格を替える(ClaudeReplaceSessionCredential): 替えた後の次の API の呼びから新しい token を使い、前の token を取り消して
  ;; も手番は成功する。手番の間で資格だけを替えた宣言の次の手番は process を起こし直さず(起こした数 1)、その手番の資格で呼ぶ。
  ;; process が降りたら資格の家は消える。
  (val note (/ tmp-path "work" "note.txt"))
  (.mkdir note.parent :parents True :exist-ok True)
  (.write-text note "the note\n" :encoding "utf-8")
  (val api (.start (FakeMessagesApi (str note))))
  (try
    (<- spec (swap-spec tmp-path api))
    (val host (ClaudeCodeHost #(REAL-CLI) (clock-of (sync-time-handler)) 8 0.0 :launch-timeout 120.0))
    (<- home (with_handlers [(sync-time-handler) slog-discard-handler (claude-code-handler host)]
                            (swap-mid-turn-then-refresh-between-turns host api spec (str (uuid.uuid4)))))
    (assert (not (os.path.exists home)))
    (val calls (.seen api))
    (val main (lfor call calls :if call.main call))
    ;; 本体の最初の呼びは TOKEN-A(替える前)、その後の呼びはどれも TOKEN-A を使わない。1 手番目の残りは TOKEN-B、2 手番目は TOKEN-C。
    (assert (= (. (get main 0) token) TOKEN-A) calls)
    (assert (all (gfor call (cut calls (+ (.index calls (get main 0)) 1) None) (!= call.token TOKEN-A))) calls)
    (assert (in TOKEN-B (lfor call main call.token)) calls)
    (assert (= (. (get main -1) token) TOKEN-C) calls)
    (assert (all (gfor call calls (= call.status 200))) calls)
    (finally
      (.close api))))
