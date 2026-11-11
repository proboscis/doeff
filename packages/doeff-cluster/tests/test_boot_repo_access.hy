;; 起動の script の読み取りの鍵の表(WORKER_REPOS・設計 U5・U6・E13)の検。表は url に鍵を結ぶだけで url を断らない(表に無い url は
;; worker が鍵なしで clone する — test_env_prepare.hy の test-a-url-missing-from-the-key-table-is-cloned-without-a-key)。
;;
;; GitHub の deploy key は repo ごとなので、url ごとに別の鍵を ssh に選ばせる。boot.sh の ROLE=access が書く物を確かめる:
;;   - worker の鍵の表の JSON(url → 鍵の file・公開の repo は空)
;;   - git の insteadOf(url → その url だけの Host の別名)と、別名ごとの ssh の設定(本当の host・その鍵だけ)。どちらも専用の
;;     dir に書き、機体の ~/.gitconfig と ~/.ssh/config には書かない
;;   - 鍵の file が無ければ起動を止める(黙って鍵なしで進まない)
;; あわせて、worker の鍵の表の鍵(env_translation.git-environment)が worker の ssh の命令(ssh -F)を置き換えず足すことを確かめる。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import os)
(import subprocess)
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [EnvEntry])
(import doeff_core_effects [ProcessScript scripted-process-handler])
(import doeff_cluster._worker.protocol.env_translation [git-environment])

(val BOOT-SH (str (/ (. (Path __file__) (resolve) parent parent) "deploy" "boot.sh")))
(val PRIVATE-A "git@github.com:owner/private-a.git")
(val PRIVATE-B "ssh://git@github.com/owner/private-b.git")
(val PUBLIC "https://github.com/owner/public.git")


(defk run-access [home keys repos]
  {:pre [(: home Path) (: keys Path) (: repos str)] :post [(: % subprocess.CompletedProcess)]}
  "ROLE=access で boot.sh を起こす(家と鍵の dir を検の tmp に向ける)— 鍵の表から書かれる設定を外から読むため。"
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


(deftest test-a-keyed-repo-is-read-with-its-key-in-every-spelling [tmp-path]
  ;; 反例(doeff が非公開の repo へ移った後): 表の綴りが ssh でも、uv の git の依存と WORKER_DOEFF_URL は https(.git の有無)で書かれ、
  ;; 鍵なしの https で「could not read Username」と断られる。鍵つきの行の repo は、どの綴りでも同じ鍵の別名へ書き換わる。
  (val home (/ tmp-path "home"))
  (val keys (/ tmp-path "keys"))
  (.mkdir home)
  (.mkdir keys)
  (for [name #("key-a" "known_hosts")] (.write-text (/ keys name) "x\n"))
  (<- done subprocess.CompletedProcess (run-access home keys (.format "{}=key-a {}=" PRIVATE-A PUBLIC)))
  (assert (= done.returncode 0) done.stderr)
  (val access (. (Path (.strip done.stdout)) parent))
  (for [spelling ["https://github.com/owner/private-a.git" "https://github.com/owner/private-a"
                  "ssh://git@github.com/owner/private-a.git" "git@github.com:owner/private-a"]]
    (<- seen str (seen-by access ["git" "ls-remote" "--get-url" spelling]))
    (assert (= (.strip seen) "ssh://git@doeff-repo-1/owner/private-a.git") #(spelling seen)))
  (<- url-p str (seen-by access ["git" "ls-remote" "--get-url" PUBLIC]))
  (assert (= (.strip url-p) PUBLIC) url-p))


(val FAKE-SSH (+ "#!/bin/sh\n"
                 "# 検の ssh: git が向けた host(引数の頭の旗の後の 1 つ)を記し、検の repo を upload-pack で渡す。\n"
                 "while [ $# -gt 0 ]; do\n"
                 "  case $1 in -F|-o|-i|-p) shift 2 ;; -*) shift ;; *) break ;; esac\n"
                 "done\n"
                 "echo \"$1\" >>\"$FAKE_SSH_LOG\"\n"
                 "exec git upload-pack \"$FAKE_SSH_REPO\"\n"))


(defk git-in [cwd #* args]
  {:pre [(: cwd Path) (: args tuple)] :post [(: % str)]}
  "tmp の repo で git を 1 回撃つ(検の doeff の材料を作るため)。"
  (. (subprocess.run ["git" "-C" (str cwd) #* args] :capture-output True :text True :check True) stdout))


(defk private-doeff [tmp]
  {:pre [(: tmp Path)] :post [(: % str)]}
  "root の起動の script(自分の名乗りと git の向けを出して終わる)を持つ commit 1 つの repo を tmp/src に作る。答え = その sha。"
  (val src (/ tmp "src"))
  (val deploy (/ src "packages" "doeff-cluster" "deploy"))
  (.mkdir deploy :parents True)
  (.write-text (/ deploy "boot.sh") "echo \"root の script: [${GIT_CONFIG_GLOBAL:-}]\"\n")
  (<- (git-in src "init" "-q"))
  (<- (git-in src "add" "."))
  (<- (git-in src "-c" "user.email=t@t" "-c" "user.name=t" "commit" "-q" "-m" "x"))
  (<- head str (git-in src "rev-parse" "HEAD"))
  (.strip head))


(deftest test-the-doeff-fetch-reads-a-private-repo-through-the-key-table [tmp-path]
  ;; 反例(2026-11-11 — doeff が非公開の repo へ移った): image の起動の script は置き場に無い commit を鍵の表を組む前に鍵なしの https で
  ;; 取り寄せ、版を上げた worker が起きられない。表が在れば取り寄せは表を通り、https の url のまま鍵の別名(検の ssh)で読む。表の向けは
  ;; 取り寄せの中だけに置き、引き継いだ root の script へ継がない。
  (<- sha str (private-doeff tmp-path))
  (val keys (/ tmp-path "keys"))
  (val bin (/ tmp-path "bin"))
  (val log (/ tmp-path "ssh.log"))
  (.mkdir keys)
  (.mkdir bin)
  (for [name #("key-a" "known_hosts")] (.write-text (/ keys name) "x\n"))
  (.write-text (/ bin "ssh") FAKE-SSH)
  (.chmod (/ bin "ssh") 0o755)
  (val done (subprocess.run ["sh" BOOT-SH]
                            :env {"PATH" (+ (str bin) ":/usr/bin:/bin") "HOME" (str tmp-path) "ROLE" "records"
                                  "WORK_DIR" (str (/ tmp-path "work")) "WORKER_DOEFF_COMMIT" sha
                                  "WORKER_DOEFF_URL" "https://github.com/owner/private-a.git"
                                  "WORKER_REPOS" (.format "{}=key-a" PRIVATE-A) "WORKER_REPO_KEYS_DIR" (str keys)
                                  "GIT_TERMINAL_PROMPT" "0" "FAKE_SSH_LOG" (str log) "FAKE_SSH_REPO" (str (/ tmp-path "src"))}
                            :capture-output True :text True :timeout 60))
  (assert (= done.returncode 0) (+ done.stdout done.stderr))
  (assert (in "root の script: []" done.stdout) (+ done.stdout done.stderr))
  (assert (in "git@doeff-repo-1" (.read-text log)) (.read-text log)))


(deftest test-a-failed-doeff-fetch-says-why [tmp-path]
  ;; 取り寄せの失敗を黙らず、理由の 1 行を出して止まる(前は失敗を || true で黙らせ、後の rev-parse の素の失敗だけが残った)。
  (val done (subprocess.run ["sh" BOOT-SH]
                            :env {"PATH" "/usr/bin:/bin" "HOME" (str tmp-path) "ROLE" "records"
                                  "WORK_DIR" (str (/ tmp-path "work")) "WORKER_DOEFF_COMMIT" (* "a" 40)
                                  "WORKER_DOEFF_URL" (str (/ tmp-path "missing.git")) "GIT_TERMINAL_PROMPT" "0"}
                            :capture-output True :text True :timeout 60))
  (assert (!= done.returncode 0) done.stdout)
  (assert (in "取り寄せられない" done.stderr) done.stderr)
  (assert (in "置き場" done.stderr) done.stderr))


(deftest test-a-missing-key-stops-the-boot [tmp-path]
  (val home (/ tmp-path "home"))
  (val keys (/ tmp-path "keys"))
  (.mkdir home)
  (.mkdir keys)
  (<- done subprocess.CompletedProcess (run-access home keys (.format "{}=key-a" PRIVATE-A)))
  (assert (!= done.returncode 0))
  (assert (in "鍵" done.stderr) done.stderr))


(deftest test-a-refused-rebuild-keeps-the-previous-table [tmp-path]
  ;; worker は準備の前ごとに ROLE=access で表を組み直す。組み直しが断られても(鍵の file が無い)、置き場の
  ;; 3 つの file は前の値のまま残り、書きかけの別名の file も残らない — 走っている worker の git・ssh・準備の process は前の表で読める。
  (val home (/ tmp-path "home"))
  (val keys (/ tmp-path "keys"))
  (.mkdir home)
  (.mkdir keys)
  (for [name #("key-b" "known_hosts")] (.write-text (/ keys name) "x\n"))
  (<- first subprocess.CompletedProcess (run-access home keys (.format "{}=key-b {}=" PRIVATE-B PUBLIC)))
  (assert (= first.returncode 0) first.stderr)
  (val access (. (Path (.strip first.stdout)) parent))
  (val before (dfor name #("repo-keys.json" "gitconfig" "ssh_config") name (.read-text (/ access name) :encoding "utf-8")))
  (assert (in "doeff-repo-1" (get before "gitconfig")) before)
  (<- refused subprocess.CompletedProcess (run-access home keys (.format "{}=key-a {}=key-b {}=" PRIVATE-A PRIVATE-B PUBLIC)))
  (assert (!= refused.returncode 0) refused.stdout)
  (assert (in "鍵" refused.stderr) refused.stderr)
  (val after (dfor name #("repo-keys.json" "gitconfig" "ssh_config") name (.read-text (/ access name) :encoding "utf-8")))
  (assert (= after before) after)
  (assert (= (sorted (gfor p (.iterdir access) p.name)) ["gitconfig" "repo-keys.json" "ssh_config"]) (list (.iterdir access))))


(defk added-by [worker-env]
  {:pre [(: worker-env tuple)] :post [(: % dict)]}
  "worker の環境が worker-env の時に、git の子へ足す変数を名 → 値で読むため(土台 = 台本の子 process の答え手 — 親の環境は worker-env)。"
  (<- entries tuple (with-handlers [(state) (scripted-process-handler (ProcessScript :commands #() :env worker-env))]
                                   (git-environment "/keys/key-a")))
  (dfor e entries e.name e.value))


(deftest test-the-repo-key-extends-the-worker-ssh-command
  ;; 起動の script が書いた ssh -F を保ったまま鍵を足す(置き換えると Host の別名が解けない)。
  (<- env dict (added-by #((EnvEntry :name "GIT_SSH_COMMAND" :value "ssh -F /x/ssh_config"))))
  (assert (.startswith (get env "GIT_SSH_COMMAND") "ssh -F /x/ssh_config -i /keys/key-a ") env)
  (assert (= (get env "GIT_TERMINAL_PROMPT") "0") env)
  ;; 命令が無い機体は今までどおり ssh から。足すのは 2 つだけ(親の環境は RunProcess の env-mode EXTEND が継ぐ)。
  (<- plain dict (added-by #()))
  (assert (.startswith (get plain "GIT_SSH_COMMAND") "ssh -i /keys/key-a ") plain)
  (assert (= (sorted plain) ["GIT_SSH_COMMAND" "GIT_TERMINAL_PROMPT"]) plain))
