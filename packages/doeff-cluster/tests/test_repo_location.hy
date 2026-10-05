;; repo の url の正体(runtime_env_rules の url-location)の読み分けの検(#3693)。
;;
;;   - 手元の path(/・./・../ で始まる・相対の path・file://)は LocalPath で綴りのまま — git の url_is_local_not_ssh と同じ読み分け
;;     (`:` を含まない・最初の `/` が最初の `:` より前・file:// で始まる)。scp の形の `:` の後ろの owner/name(`/` を含む)を
;;     相対の path と取り違えない。
;;   - 網の url(scp の形・https・ssh://)は RemoteRepo。`.git` の有無・末尾の `/`・利用者・port・host の大文字は同じ正体に畳む。
;;   - 反例: owner・name・host のどれかが違えば別の正体。
(require doeff-hy.macros [deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_cluster.shared.core.runtime_env_rules [url-location])
(import doeff_cluster.shared.intent.runtime_env_model [LocalPath RemoteRepo RepoLocation])

(val LOCAL-URLS #("/srv/git/app.git" "./app.git" "../remotes/app.git" "file:///srv/git/app.git" "app.git" "repos/app.git"
                  "/srv/git/with:colon.git"))
(val NETWORK-URLS #("git@github.com:owner/app.git" "https://github.com/owner/app.git" "ssh://git@github.com/owner/app.git"))


(deftest test-local-paths-and-network-urls-are-told-apart
  ;; 手元の path は LocalPath(綴りのまま)、網の url は RemoteRepo。
  (for [url LOCAL-URLS]
    (<- location RepoLocation (url-location url))
    (assert (= location (LocalPath :path url)) #(url location)))
  (for [url (+ NETWORK-URLS #("git@host.example:app.git" "https://example.invalid/app.git"))]
    (<- remote RepoLocation (url-location url))
    (assert (isinstance remote RemoteRepo) #(url remote))))


(deftest test-network-spellings-of-one-repo-share-a-location
  ;; https・scp の形・ssh://(port つきも)・`.git` の有無・末尾の `/`・host の大文字は、同じ正体 github.com / o / lib。
  (val lib (RemoteRepo :host "github.com" :owner "o" :name "lib"))
  (for [spelling ["https://github.com/o/lib.git" "https://github.com/o/lib" "https://github.com/o/lib/" "git@github.com:o/lib.git"
                  "git@github.com:o/lib" "ssh://git@github.com/o/lib.git" "ssh://git@github.com:22/o/lib.git"
                  "https://GitHub.com/o/lib.git"]]
    (<- location RepoLocation (url-location spelling))
    (assert (= location lib) #(spelling location)))
  ;; owner の無い scp の形は空の owner。
  (<- bare RepoLocation (url-location "git@host.example:app.git"))
  (assert (= bare (RemoteRepo :host "host.example" :owner "" :name "app")) bare)
  ;; 反例: 別の owner・別の host・別の名は別の正体。
  (for [other ["git@github.com:p/lib.git" "https://gitlab.com/o/lib.git" "git@github.com:o/lib2.git"]]
    (<- other-location RepoLocation (url-location other))
    (assert (!= other-location lib) #(other other-location))))


(deftest test-local-paths-match-only-by-spelling
  ;; 手元の path どうしは綴りの一致だけ(`.git` の有無を畳まない)— 網の url と同じ正体にもならない。
  (<- with-git RepoLocation (url-location "file:///remotes/app.git"))
  (<- without-git RepoLocation (url-location "file:///remotes/app"))
  (assert (!= with-git without-git) #(with-git without-git))
  (<- remote RepoLocation (url-location "https://remotes/app.git"))
  (assert (!= with-git remote) #(with-git remote)))
