;;; 資格の家 — 借りた codex の口座(auth.json の中身)を、その資格で起こす 1 つの process だけが読む CODEX_HOME に置き、process が
;;; 降りたら家ごと消す(handler の内側の器 — 上の層は家を知らない)。
;;;
;;; codex は口座を $CODEX_HOME/auth.json からだけ読む(ターンの資格の env が無い・provider の API key の env は上の層の決まりで禁じる)。
;;; 同じ機体で別の口座のターンが並んで走るので、元の CODEX_HOME の auth.json を書き換えず、process ごとに家を分ける。会話の記録
;;; (sessions の rollout)は元の置き場に書かせる — 家を消しても、次の process が同じ thread を thread/resume で続けられる。
;;;
;;; 家の形(本物の codex 0.162.1 で確かめた — 家を消した後に別の家から同じ thread を thread/resume でき、続きの上流の呼びが前のターンの
;;; 発言と答えを運んだ・2026-10-11):
;;;   <元の CODEX_HOME>/.credential-homes/<乱数>/    権限 0700
;;;       auth.json                        借りた資格の中身(権限 0600 — 作る時から。書き終わるまで他から読めない)
;;;       config.toml → <元>/config.toml     元に在る時だけ
;;;       sessions    → <元>/sessions        会話の記録の置き場(元に無ければ作る)
;;;   codex が家の中に作る物(log・tmp)は、家と一緒に消える。
;;;
;;; 状態の DB(state_5.sqlite ほか codex の sqlite — thread の一覧と rollout の置き場の索引)は家の外の元の CODEX_HOME に 1 つ持つ
;;; (起こす env の CODEX_SQLITE_HOME = 元 — 家を作らない process と同じ置き場)。空の DB の codex は起動の時に sessions の全部を読んで
;;; DB を埋める(state db backfill)ので、DB を家に置くと、起動のたびに記録の全部を読み直し、記録が育つと起動の待ちの上限を越える
;;; (10-19 に 5.0 GB を起動ごとに読み、Codex の会話の turn が起動できなくなった)。本物の codex 0.162.1 で
;;; 確かめた事(2026-10-19):
;;;   - DB が埋まっていれば、別の家から起こしても backfill をしない(6 本を同時に起こして、どれも約 2 秒で thread を開いた)。
;;;   - codex は DB の行に rollout の path を、家を通る形(<家>/sessions/…)で書く。家を消すとその path は無くなり、別の家からの
;;;     thread/resume が「no rollout found for thread id」で断られる。そこで家で起こすたびに、家を通る path を元の sessions の path へ
;;;     書き直す(同じ file — repoint-state-db)。行が無い thread は、codex が sessions の file を名で探して行を作り直す(元の path で書く)。
;;;     書き直しは sqlite への書き込みで、disk が混む間や codex の書き込みと重なる間は数十秒かかる(配備の後の最初の起動で 59 秒)。
;;;     handler の手で書くと turn-host の全部の会話が止まるので、handler は書き直しを手の外の thread で走らせ、待つのは続き
;;;     (thread/resume)の要求の前の、その会話の手だけにする(handler.hy の start-repoint)。
;;;   - DB が無い時に 3 本を同時に起こすと、1 本が「failed to initialize sqlite state runtime」で降りた。そこで DB が無い間は、
;;;     DB を作る起動を lock で 1 本ずつにする(state-db-claim — 在れば lock を取らない)。
;;; 手本 = 預かり所の deploy/custody/codex-home.sh(借りた auth.json を 0600 で置き、終わったら消す)。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "codex" :role "process"})
(import fcntl)
(import os)
(import shutil)
(import sqlite3)
(import uuid)
(import pathlib [Path])

(val CREDENTIAL-HOMES-DIR ".credential-homes")
(val AUTH-FILE "auth.json")
(val LINKED-FILES #("config.toml"))
(val SESSIONS-DIR "sessions")
;; codex が状態の DB の置き場を読む env(codex 0.162.1 — 無ければ CODEX_HOME)と、その置き場の thread の索引の DB の名(版を上げて
;; 名が替わったら、ここを替えて録り直す)。
(val SQLITE-HOME-ENV "CODEX_SQLITE_HOME")
(val STATE-DB "state_5.sqlite")
;; DB が無い間、DB を作る起動を 1 本ずつにする lock の file(元の CODEX_HOME の直下 — 状態の DB の隣)。
(val STATE-DB-LOCK ".state-db.lock")
;; 書き直しが、起動中の codex の書き込みと重なった時に待つ上限(秒)。
(val STATE-DB-BUSY-SECONDS 30.0)


(defk open-credential-home [#^ str base #^ str auth-json]
  {:pre [(: base str) (: auth-json str)] :post [(: % str)] :tags {:context "codex" :role "process"}}
  "借りた資格の家を元の CODEX_HOME(base)の下に作り、その path を返すため(頭の註の形)。auth.json は作る時から権限 0600 で書く。
   状態の DB は触らない(DB の path の書き直しは、起こす手が手の外の thread で走らせる — 頭の註)。"
  (val root (Path base))
  (val homes (/ root CREDENTIAL-HOMES-DIR))
  (.mkdir homes :mode 0o700 :parents True :exist-ok True)
  (val home (/ homes (. (uuid.uuid4) hex)))
  (.mkdir home :mode 0o700)
  (val descriptor (os.open (/ home AUTH-FILE) (| os.O-WRONLY os.O-CREAT os.O-EXCL) 0o600))
  (with [stream (os.fdopen descriptor "w" :encoding "utf-8")]
    (.write stream auth-json))
  (for [name LINKED-FILES :if (.exists (/ root name))]
    (.symlink-to (/ home name) (/ root name)))
  (.mkdir (/ root SESSIONS-DIR) :parents True :exist-ok True)
  (.symlink-to (/ home SESSIONS-DIR) (/ root SESSIONS-DIR) :target-is-directory True)
  (str home))


(defk credential-env [#^ dict env #^ str base #^ str home]
  {:pre [(: env dict) (: base str) (: home str)] :post [(: % dict)] :tags {:context "codex" :role "process"}}
  "資格の家で起こす process の env を作るため: CODEX_HOME を家へ、状態の DB の置き場(CODEX_SQLITE_HOME)を元の CODEX_HOME へ向ける。"
  (| (dict env) {"CODEX_HOME" home SQLITE-HOME-ENV base}))


(defk repoint-state-db [#^ str base]
  {:pre [(: base str)] :post [(: % int)] :tags {:context "codex" :role "process"}}
  "元の CODEX_HOME(base)の状態の DB の、資格の家を通る rollout の path(<base>/.credential-homes/<家>/sessions/…)を、元の
   sessions の path(<base>/sessions/…)へ書き直すため(頭の註 — 消えた家の path で thread/resume が断られない)。起動中の codex の
   家を通る path を書き直しても同じ file を指す。DB が無ければ何もしない。答え = 書き直した行の数。"
  (val db (/ (Path base) STATE-DB))
  (when (not (.exists db))
    (return 0))
  (val prefix (+ (str (/ (Path base) CREDENTIAL-HOMES-DIR)) "/"))
  (val connection (sqlite3.connect (str db) :timeout STATE-DB-BUSY-SECONDS))
  (try
    (with [connection]
      (val rows (.fetchall (.execute connection "SELECT id, rollout_path FROM threads WHERE substr(rollout_path, 1, ?) = ?"
                                     #((len prefix) prefix))))
      ;; <家>/sessions/… の家の名を外した残り(sessions/…)を元の CODEX_HOME の下へ。
      (val moved (lfor [thread-id path] rows
                       #((str (/ (Path base) (get (.split (cut path (len prefix) None) "/" 1) 1))) thread-id)))
      (.executemany connection "UPDATE threads SET rollout_path = ? WHERE id = ?" moved)
      (len moved))
    (finally
      (.close connection))))


(defk state-db-claim [#^ str base #^ bool block]
  {:pre [(: base str) (: block bool)] :post [(: % (| int bool))] :tags {:context "codex" :role "process"}}
  "状態の DB がまだ無い時、DB を作る起動の lock を取るため(頭の註 — DB が無い時に同時に起こすと 1 本が降りる)。block = 取れるまで
   待つか(待つのは handler の待ちの thread だけ)。答え = DB が在る(lock は要らない)なら True・lock を取れたらその file descriptor
   (起動の初期化が済んだら state-db-release で返す)・待たずに取れなければ False。lock を取った後に DB が在れば、lock を返して True。"
  (val root (Path base))
  (when (.exists (/ root STATE-DB))
    (return True))
  (val descriptor (os.open (/ root STATE-DB-LOCK) (| os.O-RDWR os.O-CREAT) 0o600))
  (try
    (fcntl.flock descriptor (if block fcntl.LOCK-EX (| fcntl.LOCK-EX fcntl.LOCK-NB)))
    (except [BlockingIOError]
      (os.close descriptor)
      (return False)))
  (when (.exists (/ root STATE-DB))
    (<- (state-db-release descriptor))
    (return True))
  descriptor)


(defk state-db-release [claim]
  {:pre [(: claim (| int bool None))] :post [(: % None)] :tags {:context "codex" :role "process"}}
  "state-db-claim で取った lock を返すため(lock を取っていない答え True・False・None なら何もしない)。"
  (when (and (isinstance claim int) (not (isinstance claim bool)))
    (fcntl.flock claim fcntl.LOCK-UN)
    (os.close claim))
  None)


(defk close-credential-home [home]
  {:pre [(: home (| str None))] :post [(: % None)] :tags {:context "codex" :role "process"}}
  "資格の家を中身ごと消すため(link は辿らずに外す — 元の config.toml と会話の記録は残る)。家が無ければ何もしない(冪等 — process の
   終わりの callback と降ろす手順の両方が消しに来る)。"
  (when (is-not home None)
    (try
      (shutil.rmtree home)
      (except [FileNotFoundError]
        None)))
  None)
