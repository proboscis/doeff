;;; 資格の家 — 借りた claude の口座の資格(LentCredential)を、その資格で起こす 1 つの process だけが読む設定 dir(CLAUDE_CONFIG_DIR)の
;;; .credentials.json に置き、走っている間も外から替えられるようにし、process が降りたら dir ごと消す(handler の内側の器 — 上の層は
;;; dir を知らない)。預かり所が更新した token を、走っている CLI へ配るための器。
;;;
;;; なぜ file で渡すか(CLI 2.1.292 の実測 — 127.0.0.1 の偽の API を ANTHROPIC_BASE_URL で向けた):
;;;   - 子の env の token(CLAUDE_CODE_OAUTH_TOKEN)は起動の後に外から替えられない。
;;;   - $CLAUDE_CONFIG_DIR/.credentials.json の資格は、同じ dir の別名に書いて rename で替えると、手番の間でも手番の途中でも次の API の
;;;     呼びから新しい token を使う。401 を受けると file を読み直して送り直す。
;;;   - refreshToken が null なら CLI は file を書き換えない。expiresAt が null でも過ぎていても、その token で呼ぶ(更新しに行かない)。
;;;   - scopes に user:inference が無いと「Not logged in」で降りる(CLI-SCOPES)。
;;;   - 資格の file の場所を設定 dir と別に指す env は無い。設定 dir は host の process に 1 つで全部の会話・口座の CLI が共有している
;;;     ので、process ごとの設定 dir を作り、共有の dir の中身へ symlink を張る。
;;;
;;; dir の形:
;;;   <共有の設定 dir>/.credential-homes/<乱数>/     権限 0700
;;;       .credentials.json         借りた資格(権限 0600 — 作る時から。書き終わるまで他から読めない)。替える時は同じ dir の別名に
;;;                                 0600 で書いて rename で一息に替える(CLI が読み掛けの file を見ない)
;;;       <名> → <共有>/<名>         共有の dir の直下の名ぜんぶ(.credentials.json と .credential-homes を除く)への symlink
;;;   共有に残すべき dir(KEPT-DIRS)は、共有の側に無ければ作ってから link する — process の dir に作られると dir と一緒に消える:
;;;       projects      会話の記録(transcript)。続き(--resume)・ClaudeSessionStatus・ClaudeExportSession は共有の dir から読む
;;;       session-env・file-history   持ち込み(LinkFromHome)が共有の dir から読む会話ごとの置き物
;;;   .claude.json(CLI の全体の設定と状態): 実測 2.1.292 では CLI は link を辿って共有の file を書き換え、link は link のまま残る
;;;     (rename で普通の file に替わらない)。だから共有に在れば link し、CLI の書いた物は共有に残る。共有に無ければ作らない — CLI が
;;;     process の dir に作り、dir と一緒に消える(中身は起動の回数・初回の刻・migration の印などで、手番の結果に効かない。共有に
;;;     作り置くと、まだ CLI が 1 度も起きていない家の形を変えてしまう)。
;;;   共有に無い名で CLI が process の dir に作る物(sessions = 走っている CLI の名簿・backups = .claude.json の写し・shell-snapshots など)
;;;   は、その process だけの物として dir と一緒に消える。
;;; 手本 = doeff-codex の credential_home.hy(process ごとの CODEX_HOME)。
(require doeff-hy.macros [defk <- val])
(import json)
(import os)
(import shutil)
(import uuid)
(import pathlib [Path])
(import doeff_claude_code._values [LentCredential])

(val CREDENTIAL-HOMES-DIR ".credential-homes")
(val CREDENTIALS-FILE ".credentials.json")
;; 共有の dir に残すべき dir(頭の註)。
(val KEPT-DIRS #("projects" "session-env" "file-history"))
;; process の dir へ link しない名: 資格(process ごとに置く)と、資格の家の置き場そのもの(link すると dir の中に自分が見える)。
(val UNLINKED-NAMES #{CREDENTIALS-FILE CREDENTIAL-HOMES-DIR})
;; CLI が資格を推論に使うのに要る scope(無いと「Not logged in」— 実測 2.1.292)。env の token で起こした CLI が自分に付ける scope と同じ。
(val CLI-SCOPES #("user:inference"))


(defk credential-document [#^ LentCredential credential]
  {:pre [(: credential LentCredential)] :post [(: % str)] :tags {:context "claude-code" :role "foundation"}}
  "借りた資格を、CLI が読む .credentials.json の中身(JSON の文字列)に綴るため(写像を作るのは JSON の境界のここだけ)。refreshToken は
   null(更新は貸し手だけが行う — null なら CLI は file を書き換えない)・expiresAt は epoch ミリ秒(期限を知らなければ null)・知らない
   契約の種類と階級は null。"
  (json.dumps {"claudeAiOauth" {"accessToken" credential.access-token
                                "refreshToken" None
                                "expiresAt" (if (is credential.expires-at None) None (int (* credential.expires-at 1000)))
                                "scopes" (list CLI-SCOPES)
                                "subscriptionType" credential.subscription-type
                                "rateLimitTier" credential.rate-limit-tier}}
              :separators #("," ":")))

(defk write-new-file [#^ Path path #^ str text]
  {:pre [(: path Path) (: text str)] :post [(: % None)] :tags {:context "claude-code" :role "foundation"}}
  "まだ無い path に、作る時から権限 0600 の file として text を書くため(既に在れば FileExistsError — 他人の file を上書きしない)。"
  (val descriptor (os.open path (| os.O-WRONLY os.O-CREAT os.O-EXCL) 0o600))
  (with [stream (os.fdopen descriptor "w" :encoding "utf-8")]
    (.write stream text))
  None)

(defk open-credential-home [#^ str base #^ LentCredential credential]
  {:pre [(: base str) (: credential LentCredential)] :post [(: % str)] :tags {:context "claude-code" :role "process"}}
  "借りた資格で起こす process だけの設定 dir を共有の設定 dir(base)の下に作り、その path を返すため(頭の註の形)。途中で失敗したら
   作り掛けの dir を消してから例外を上げる(半端な dir を残さない)。"
  (val root (Path base))
  (val homes (/ root CREDENTIAL-HOMES-DIR))
  (.mkdir homes :mode 0o700 :parents True :exist-ok True)
  (<- document (credential-document credential))
  (val home (/ homes (. (uuid.uuid4) hex)))
  (.mkdir home :mode 0o700)
  (try
    (<- (write-new-file (/ home CREDENTIALS-FILE) document))
    (for [name KEPT-DIRS]
      (.mkdir (/ root name) :parents True :exist-ok True))
    (for [entry (sorted (os.scandir root) :key (fn [entry] entry.name))
          :if (not-in entry.name UNLINKED-NAMES)]
      (.symlink-to (/ home entry.name) (/ root entry.name) :target-is-directory (.is-dir entry)))
    (except [BaseException]
      (shutil.rmtree home)
      (raise)))
  (str home))

(defk replace-credential [#^ str home #^ LentCredential credential]
  {:pre [(: home str) (: credential LentCredential)] :post [(: % None)] :tags {:context "claude-code" :role "process"}}
  "走っている process の設定 dir(home)の資格を新しい資格へ一息に替えるため: 同じ dir の別名に権限 0600 で書いてから rename で
   .credentials.json に替える(CLI は書き掛けの file を読まない — 次の API の呼びから新しい token を使う)。書き掛けの別名は、失敗したら
   消す。"
  (<- document (credential-document credential))
  (val temporary (/ (Path home) (.format "{}.{}.tmp" CREDENTIALS-FILE (. (uuid.uuid4) hex))))
  (try
    (<- (write-new-file temporary document))
    (os.replace temporary (/ (Path home) CREDENTIALS-FILE))
    (except [BaseException]
      (.unlink temporary :missing-ok True)
      (raise)))
  None)

(defk close-credential-home [home]
  {:pre [(: home (| str None))] :post [(: % None)] :tags {:context "claude-code" :role "process"}}
  "資格の家を中身ごと消すため(link は辿らずに外す — 共有の dir の中身は残る)。家が無い・None なら何もしない(冪等 — process の終わりの
   callback と起動の失敗の後始末の両方が消しに来る)。"
  (when (is-not home None)
    (try
      (shutil.rmtree home)
      (except [FileNotFoundError]
        None)))
  None)
