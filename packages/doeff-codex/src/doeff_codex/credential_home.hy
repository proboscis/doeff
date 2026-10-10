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
;;;   codex が家の中に作る物(状態の sqlite・log・tmp)は、家と一緒に消える。
;;; 手本 = 預かり所の deploy/custody/codex-home.sh(借りた auth.json を 0600 で置き、終わったら消す)。
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "codex" :role "process"})
(import os)
(import shutil)
(import uuid)
(import pathlib [Path])

(val CREDENTIAL-HOMES-DIR ".credential-homes")
(val AUTH-FILE "auth.json")
(val LINKED-FILES #("config.toml"))
(val SESSIONS-DIR "sessions")


(defk open-credential-home [#^ str base #^ str auth-json]
  {:pre [(: base str) (: auth-json str)] :post [(: % str)] :tags {:context "codex" :role "process"}}
  "借りた資格の家を元の CODEX_HOME(base)の下に作り、その path を返すため(頭の註の形)。auth.json は作る時から権限 0600 で書く。"
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
