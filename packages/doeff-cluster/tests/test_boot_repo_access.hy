;; 起動の script の読み取りの許可表(WORKER_REPOS・設計 U5・U6・E13)の検。
;;
;; GitHub の deploy key は repo ごとなので、url ごとに別の鍵を ssh に選ばせる。boot.sh の ROLE=access が書く物を確かめる:
;;   - worker の許可表の JSON(url → 鍵の file・公開の repo は空)
;;   - git の insteadOf(url → その url だけの Host の別名)と、別名ごとの ssh の設定(本当の host・その鍵だけ)。どちらも専用の
;;     dir に書き、機体の ~/.gitconfig と ~/.ssh/config には書かない
;;   - 鍵の file が無ければ起動を止める(黙って鍵なしで進まない)
;; あわせて、worker の許可表の鍵(env_handlers.git-environment)が worker の ssh の命令(ssh -F)を置き換えず足すことを確かめる。
(require doeff-hy.macros [deftest defk <- val var])
(import json)
(import os)
(import subprocess)
(import pathlib [Path])
(import unittest.mock [patch])
(import doeff_cluster.env_handlers [git-environment])

(val BOOT-SH (str (/ (. (Path __file__) (resolve) parent parent) "deploy" "boot.sh")))
(val PRIVATE-A "git@github.com:owner/private-a.git")
(val PRIVATE-B "ssh://git@github.com/owner/private-b.git")
(val PUBLIC "https://github.com/owner/public.git")


(defk run-access [home keys repos]
  {:pre [(: home Path) (: keys Path) (: repos str)] :post [(: % subprocess.CompletedProcess)]}
  "ROLE=access で boot.sh を起こす(家と鍵の dir を検の tmp に向ける)— 許可表から書かれる設定を外から読むため。"
  (subprocess.run ["sh" BOOT-SH]
                  :env {"PATH" (os.environ.get "PATH" "") "HOME" (str home) "ROLE" "access"
                        "WORKER_REPOS" repos "WORKER_REPO_KEYS_DIR" (str keys)}
                  :capture-output True :text True))


(defk seen-by [access command]
  {:pre [(: access Path) (: command list)] :post [(: % str)]}
  "書かれた設定の下で git / ssh が解く答え(worker と同じく GIT_CONFIG_GLOBAL で向ける)。"
  (. (subprocess.run command :env {"PATH" (os.environ.get "PATH" "") "GIT_CONFIG_GLOBAL" (str (/ access "gitconfig"))}
                     :capture-output True :text True :check True) stdout))


(deftest test-each-url-reads-with-its-own-key [tmp-path]
  (val home (/ tmp-path "home"))
  (val keys (/ tmp-path "keys"))
  (.mkdir home)
  (.mkdir keys)
  (for [name #("key-a" "key-b" "known_hosts")] (.write-text (/ keys name) "x\n"))
  (<- done subprocess.CompletedProcess (run-access home keys (.format "{}=key-a {}=key-b {}=" PRIVATE-A PRIVATE-B PUBLIC)))
  (assert (= done.returncode 0) done.stderr)
  (val table-path (Path (.strip done.stdout)))
  (val access table-path.parent)
  (val table (json.loads (.read-text table-path :encoding "utf-8")))
  (assert (= table {PRIVATE-A (str (/ keys "key-a")) PRIVATE-B (str (/ keys "key-b")) PUBLIC ""}) table)
  ;; 機体の設定には書かない。
  (assert (not (.exists (/ home ".gitconfig"))))
  (assert (not (.exists (/ home ".ssh"))))
  ;; git は url ごとに別の Host の別名へ書き換え、公開の repo はそのまま。
  (<- url-a str (seen-by access ["git" "ls-remote" "--get-url" PRIVATE-A]))
  (<- url-b str (seen-by access ["git" "ls-remote" "--get-url" PRIVATE-B]))
  (<- url-p str (seen-by access ["git" "ls-remote" "--get-url" PUBLIC]))
  (assert (= (.strip url-a) "ssh://git@doeff-repo-1/owner/private-a.git") url-a)
  (assert (= (.strip url-b) "ssh://git@doeff-repo-2/owner/private-b.git") url-b)
  (assert (= (.strip url-p) PUBLIC) url-p)
  ;; ssh は別名ごとに本当の host とその鍵だけを使う。
  (<- ssh-b str (seen-by access ["ssh" "-F" (str (/ access "ssh_config")) "-G" "doeff-repo-2"]))
  (val lines (set (.splitlines ssh-b)))
  (assert (in "hostname github.com" lines) ssh-b)
  (assert (in (.format "identityfile {}" (/ keys "key-b")) lines) ssh-b)
  (assert (not-in (.format "identityfile {}" (/ keys "key-a")) lines) ssh-b)
  (assert (in "identitiesonly yes" lines) ssh-b))


(deftest test-a-missing-key-stops-the-boot [tmp-path]
  (val home (/ tmp-path "home"))
  (val keys (/ tmp-path "keys"))
  (.mkdir home)
  (.mkdir keys)
  (<- done subprocess.CompletedProcess (run-access home keys (.format "{}=key-a" PRIVATE-A)))
  (assert (!= done.returncode 0))
  (assert (in "鍵" done.stderr) done.stderr))


(deftest test-the-allowlist-key-extends-the-worker-ssh-command
  ;; 起動の script が書いた ssh -F を保ったまま鍵を足す(置き換えると Host の別名が解けない)。
  (with [_ (patch.dict os.environ {"GIT_SSH_COMMAND" "ssh -F /x/ssh_config"})]
    (<- env dict (git-environment "/keys/key-a")))
  (assert (.startswith (get env "GIT_SSH_COMMAND") "ssh -F /x/ssh_config -i /keys/key-a ") env)
  ;; 命令が無い機体は今までどおり ssh から。
  (with [_ (patch.dict os.environ {} :clear False)]
    (.pop os.environ "GIT_SSH_COMMAND" None)
    (<- plain dict (git-environment "/keys/key-a")))
  (assert (.startswith (get plain "GIT_SSH_COMMAND") "ssh -i /keys/key-a ") plain))
