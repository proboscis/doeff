;; 資格の家(credential_home.hy)の形の検 — card ki-4ffdafa021e5。共有の設定 dir の直下の名へ symlink を張った process
;; だけの設定 dir に、借りた資格を .credentials.json(0600)で置き、替える時は rename で一息に替え、消すのは冪等。token は偽の文字列。
(require doeff-hy.macros [deftest defk <- val])
(import json)
(import os)
(import os.path)
(import stat)
(import pathlib [Path])
(import pytest)
(import doeff_claude_code [LentCredential])
(import doeff_claude_code._credential_home [open-credential-home replace-credential close-credential-home CREDENTIAL-HOMES-DIR
                                           KEPT-DIRS])


(defk shared-config-dir [#^ Path tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % Path)] :tags {:context "claude-code" :role "program"}}
  "検の共有の設定 dir を作るため: settings.json・.claude.json・家の資格(.credentials.json — process の家へ link してはいけない)・
   sessions の dir と、別の場所を指す projects の link(本番の家の形)を置く。"
  (val shared (/ tmp-path "shared"))
  (.mkdir shared)
  (.write-text (/ shared "settings.json") "{}" :encoding "utf-8")
  (.write-text (/ shared ".claude.json") "{\"numStartups\": 1}" :encoding "utf-8")
  (.write-text (/ shared ".credentials.json") "{\"claudeAiOauth\": {\"accessToken\": \"fake-home-token\"}}" :encoding "utf-8")
  (.mkdir (/ shared "sessions"))
  (val projects (/ tmp-path "elsewhere-projects"))
  (.mkdir projects)
  (.symlink-to (/ shared "projects") projects :target-is-directory True)
  shared)

(defk mode-of [#^ Path path]
  {:pre [(: path Path)] :post [(: % int)] :tags {:context "claude-code" :role "program"}}
  "権限の bit を読むため(link は辿らない)。"
  (stat.S-IMODE (. (os.lstat path) st-mode)))

(defk token-in [#^ Path home]
  {:pre [(: home Path)] :post [(: % str)] :tags {:context "claude-code" :role "program"}}
  "家の .credentials.json の access token を読むため(JSON の境界)。"
  (get (json.loads (.read-text (/ home ".credentials.json") :encoding "utf-8")) "claudeAiOauth" "accessToken"))


(deftest test-the-credential-home-links-the-shared-dir-and-holds-only-the-lent-credential [tmp-path]
  ;; 家は共有の dir の .credential-homes の下に 0700 で作る。共有の直下の名は link(家の資格の .credentials.json と .credential-homes
  ;; は除く)・共有に残すべき dir(projects・session-env・file-history)は共有の側に作ってから link・資格は 0600 の普通の file。
  (<- shared (shared-config-dir tmp-path))
  (val lent (LentCredential :access-token "fake-lent-token-1" :expires-at 1800000000.5 :subscription-type "max"))
  (<- opened (open-credential-home (str shared) lent))
  (val home (Path opened))
  (assert (= home.parent (/ shared CREDENTIAL-HOMES-DIR)) home)
  (<- home-mode (mode-of home))
  (assert (= home-mode 0o700) (oct home-mode))
  (val credentials (/ home ".credentials.json"))
  (assert (not (.is-symlink credentials)) "資格が共有の家の資格への link になった")
  (<- file-mode (mode-of credentials))
  (assert (= file-mode 0o600) (oct file-mode))
  (assert (= (json.loads (.read-text credentials :encoding "utf-8"))
             {"claudeAiOauth" {"accessToken" "fake-lent-token-1" "refreshToken" None "expiresAt" 1800000000500
                               "scopes" ["user:inference"] "subscriptionType" "max" "rateLimitTier" None}}))
  (for [name ["settings.json" ".claude.json" "sessions" "projects" #* KEPT-DIRS]]
    (assert (.is-symlink (/ home name)) name)
    (assert (= (os.readlink (/ home name)) (str (/ shared name))) name)
    (assert (.exists (/ shared name)) name))
  (assert (not (.exists (/ home CREDENTIAL-HOMES-DIR))) "資格の家の置き場が家の中に見える")
  ;; 共有の家の資格は触らない。
  (assert (in "fake-home-token" (.read-text (/ shared ".credentials.json") :encoding "utf-8")))
  ;; 替える: 同じ file が新しい token になり(0600)、書き掛けの別名は残らない。期限を知らない資格は expiresAt が null。
  (<- (replace-credential opened (LentCredential :access-token "fake-lent-token-2" :expires-at None)))
  (<- token (token-in home))
  (assert (= token "fake-lent-token-2") token)
  (assert (is (get (json.loads (.read-text credentials :encoding "utf-8")) "claudeAiOauth" "expiresAt") None))
  (<- replaced-mode (mode-of credentials))
  (assert (= replaced-mode 0o600) (oct replaced-mode))
  (assert (= (sorted (lfor entry (os.listdir home) :if (.startswith entry ".credentials.json") entry)) [".credentials.json"]))
  ;; 消す: link は辿らない(共有の中身は残る)・2 度目も None も何もしない。
  (<- (close-credential-home opened))
  (assert (not (.exists home)))
  (assert (.exists (/ shared "settings.json")))
  (assert (.is-dir (/ tmp-path "elsewhere-projects")))
  (<- (close-credential-home opened))
  (<- (close-credential-home None)))


(deftest test-a-credential-home-that-cannot-be-built-is-not-left-behind [tmp-path]
  ;; 作り掛けで失敗した家は残さない: 共有に残すべき dir の名が普通の file として在れば作れずに例外を上げ、.credential-homes は空。
  (<- shared (shared-config-dir tmp-path))
  (.write-text (/ shared "session-env") "not a dir" :encoding "utf-8")
  (with [(pytest.raises FileExistsError)]
    (<- (open-credential-home (str shared) (LentCredential :access-token "fake-lent-token-x" :expires-at None))))
  (assert (= (os.listdir (/ shared CREDENTIAL-HOMES-DIR)) [])))


(deftest test-the-lent-credential-never-shows-its-token
  ;; token は repr にも断りの文にも出ない(例外・log に写らない — #665)。1 行でない・空の token と、数でない期限は作れない。
  (val lent (LentCredential :access-token "fake-secret-token" :expires-at 1.0 :subscription-type "pro" :rate-limit-tier "t"))
  (assert (not-in "fake-secret-token" (repr lent)) (repr lent))
  (for [bad ["" "fake\nsecret" "fake\rsecret" "fake\x00secret"]]
    (with [info (pytest.raises ValueError)]
      (LentCredential :access-token bad :expires-at None))
    (assert (not-in "secret" (str info.value)) (str info.value)))
  (for [bad-expiry [True "1"]]
    (with [(pytest.raises TypeError)]
      (LentCredential :access-token "fake-secret-token" :expires-at bad-expiry)))
  (for [bad-fact ["" "a\nb"]]
    (with [(pytest.raises ValueError)]
      (LentCredential :access-token "fake-secret-token" :expires-at None :subscription-type bad-fact))))
