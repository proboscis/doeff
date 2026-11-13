;; 実行環境の掃除の残りの片づけ(env_store の sweep-leftovers → unused-mirrors)が、repo の bare mirror の置き場(分け合う dir の
;; mirrors/<url の digest16>.git)の持ち主として、root の宣言が指さず 7 日使われない mirror を消す形の検。
;;
;; 実例(2026-11-11 に全部の worker を読んだ): mirror を作るのは EnsureMirror の答え手だけで、root の掃除も残りの片づけも mirrors/ を
;; 見なかった。宣言から外した repo の mirror(177M・最後に動いたのは 9 日前)が、その repo の root が掃除で消えた後も worker の volume に
;; 残った。だから:
;;   - どの root の宣言も指さず、印(dir の時刻)が 7 日より古い mirror は、次の掃除の拍で消える(clone の書きかけ .git.tmp も)
;;   - 完成した root の宣言が指す mirror・新しい準備の要求の宣言が指す mirror・鍵の表の綴りで名付けた mirror(宣言は別の綴り)・印が
;;     新しい mirror・錠を取られている mirror(準備の clone・fetch の最中)・worker の作る名でない dir は残る
;;   - 鍵の表に無い事は消す訳にしない(公開の repo の mirror は鍵なしで正しく作られる)— 表に無い url の mirror も、宣言が指せば残る
;; 反例 = 直す前の形(残りの片づけが mirrors/ を見ない)は、指されない古い mirror が残って赤。
;; 土台: 本番の worker と同じ file の答え手(os-file-handler)と tmp の下の本物の dir。錠は検の殻が fcntl.flock で持つ(EnsureMirror の
;; AcquireLock と同じ file の錠 — 掃除の TryLock は待たずに LockBusy を受ける)。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import fcntl)
(import hashlib)
(import json)
(import os)
(import time)
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_core_effects [os-file-handler inline-compute-handler slog-handler reader state])
(import doeff_core_effects [subprocess-handler])
(import doeff_time [sync-time-handler])
(import doeff_cluster [env-translation]
        doeff_cluster._worker.protocol.env_translation [MIRROR-USED])
(import doeff_cluster._worker.intent.env_prepare_model [EnsureMirror MirrorReady])
(import doeff_cluster._worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster [ENV-MARKER])
(import doeff_cluster._worker.protocol.env_store [EnvSettings sweep-leftovers])
(import doeff_cluster._worker.core.env_upkeep [MIRROR-UNUSED-SECONDS unused-mirror-names])

;; 宣言の url(どれも検の中だけの名)。
(val POINTED-URL "https://example.com/owner/pointed.git")      ; 完成した root の宣言が指す
(val REQUESTED-URL "https://example.com/owner/requested.git")  ; 新しい準備の要求の宣言が指す
(val KEYED-URL "https://example.com/owner/keyed.git")          ; 宣言の綴り — mirror は鍵の表の綴りで名付く
(val KEYED-LISTED "ssh://git@example.com/owner/keyed.git")      ; 鍵の表の綴り
(val DROPPED-URL "ssh://git@example.com/owner/dropped.git")    ; 表から外した repo — どの宣言も指さない
(val FRESH-URL "https://example.com/owner/fresh.git")          ; どの宣言も指さないが、印が新しい
(val LOCKED-URL "https://example.com/owner/locked.git")        ; どの宣言も指さず古いが、錠を取られている
(val HALF-URL "https://example.com/owner/half.git")            ; clone の書きかけ(.git.tmp)が残った
(val ROOT-NAME "0123456789abcdef01234567")


(defk name-of [url]
  {:pre [(: url str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "url の mirror の名の頭を、EnsureMirror と同じ式(sha256 の頭 16 桁)で求めるため。"
  (cut (.hexdigest (hashlib.sha256 (.encode url "utf-8"))) 0 16))


(defk placed-mirror [place name at]
  {:pre [(: place Path) (: name str) (: at float)] :post [(: % Path)] :tags {:context "doeff-cluster-test" :role "program"}}
  "mirror の置き場に bare mirror の形の dir を 1 つ置き、dir の時刻(使った印)を at にして path を返すため。"
  (val path (/ place name))
  (.mkdir (/ path "objects") :parents True)
  (.write-text (/ path "HEAD") "ref: refs/heads/main\n")
  (os.utime path #(at at))
  path)


(deftest test-the-sweep-removes-mirrors-no-root-declares-and-unused-for-seven-days [tmp-path]
  (val state-dir (/ tmp-path "state"))
  (val mirrors (/ state-dir "mirrors"))
  (val locks (/ state-dir "locks"))
  (.mkdir locks :parents True)
  (val now-s (time.time))
  (val old (- now-s MIRROR-UNUSED-SECONDS 3600))
  ;; 完成した root(宣言が POINTED-URL と KEYED-URL を指す)。
  (val root (/ state-dir "roots" ROOT-NAME))
  (.mkdir root :parents True)
  (.write-text (/ root ENV-MARKER) (json.dumps {"env" {"repos" [{"name" "pointed" "url" POINTED-URL "commit" (* "a" 40)}
                                                                {"name" "keyed" "url" KEYED-URL "commit" (* "b" 40)}]}}))
  ;; 準備中の root の要求(この世代の env-requests — 完成マーカーはまだ無い)。
  (val requests (/ state-dir "env-requests"))
  (.mkdir requests :parents True)
  (.write-text (/ requests "env-feedfacefeedfacefeedface.json")
               (json.dumps {"env" {"repos" [{"name" "requested" "url" REQUESTED-URL "commit" (* "c" 40)}]} "key" "feedfacefeedfacefeedface"}))
  ;; 鍵の表(KEYED-URL の repo を別の綴りで持つ — EnsureMirror はその綴りで mirror を名付ける)。
  (val keys (/ tmp-path "repo-keys.json"))
  (.write-text keys (json.dumps {KEYED-LISTED "/keys/keyed"}))
  (val pointed (placed-mirror mirrors (+ (name-of POINTED-URL) ".git") old))
  (val requested (placed-mirror mirrors (+ (name-of REQUESTED-URL) ".git") old))
  (val keyed (placed-mirror mirrors (+ (name-of KEYED-LISTED) ".git") old))
  (val dropped (placed-mirror mirrors (+ (name-of DROPPED-URL) ".git") old))
  (val fresh (placed-mirror mirrors (+ (name-of FRESH-URL) ".git") (- now-s 60)))
  (val locked (placed-mirror mirrors (+ (name-of LOCKED-URL) ".git") old))
  (val half (placed-mirror mirrors (+ (name-of HALF-URL) ".git.tmp") old))
  (val foreign (placed-mirror mirrors "hand-made.git" old))
  (val settings (EnvSettings :state (str state-dir) :uv-cache (str (/ tmp-path "uv-cache")) :hy-command "hy" :platform "test"
                             :code-prepare PREPARE-TOOL :roots-cap-bytes 0 :repo-keys (str keys)))
  ;; 錠を取られている mirror: 検の殻が EnsureMirror と同じ錠の file を持つ間に掃除する。
  (val fd (os.open (str (/ locks (+ "mirror-" (name-of LOCKED-URL)))) (| os.O-RDWR os.O-CREAT) 0o644))
  (fcntl.flock fd fcntl.LOCK-EX)
  (try
    (<- removed tuple (with-handlers [slog-handler os-file-handler inline-compute-handler] (sweep-leftovers settings (int (* now-s 1000)))))
    (finally (fcntl.flock fd fcntl.LOCK-UN) (os.close fd)))
  (assert (= (sorted removed) (sorted [(str dropped) (str half)])) removed)
  (assert (not (.exists dropped)) "どの宣言も指さない 7 日使われない mirror が残った")
  (assert (not (.exists half)) "clone の書きかけが残った")
  (for [kept [pointed requested keyed fresh locked foreign]]
    (assert (.exists kept) (.format "残るはずの mirror を消した: {}" kept.name)))
  ;; 錠が放された後の次の掃除の拍では、錠を取られていた古い mirror も消える。
  (<- again tuple (with-handlers [slog-handler os-file-handler inline-compute-handler] (sweep-leftovers settings (int (* now-s 1000)))))
  (assert (= again #((str locked))) again)
  (assert (not (.exists locked)) "錠が放された後も古い mirror が残った"))


(deftest test-the-sweep-keeps-every-mirror-when-the-key-table-is-unreadable [tmp-path]
  ;; 鍵の表を読めない時は、宣言の url から mirror の名(鍵の表の綴りの名)を決められないので、どの mirror も消さない。
  (val state-dir (/ tmp-path "state"))
  (val now-s (time.time))
  (val dropped (placed-mirror (/ state-dir "mirrors") (+ (name-of DROPPED-URL) ".git") (- now-s MIRROR-UNUSED-SECONDS 3600)))
  (val settings (EnvSettings :state (str state-dir) :uv-cache (str (/ tmp-path "uv-cache")) :hy-command "hy" :platform "test"
                             :code-prepare PREPARE-TOOL :roots-cap-bytes 0 :repo-keys (str (/ tmp-path "missing.json"))))
  (<- removed tuple (with-handlers [slog-handler os-file-handler inline-compute-handler] (sweep-leftovers settings (int (* now-s 1000)))))
  (assert (= removed #()) removed)
  (assert (.exists dropped) "鍵の表を読めないのに mirror を消した"))


(deftest test-the-mirror-judgment-names-only-worker-made-unpointed-idle-mirrors []
  ;; 選びの判断だけ: worker の作る名で、指されず、7 日より長く時刻の進んでいない物だけを名の順に選ぶ。
  (val now-ms (* 100 MIRROR-UNUSED-SECONDS 1000))
  (val old (- now-ms (* 1000 MIRROR-UNUSED-SECONDS) 1))
  (val edge (- now-ms (* 1000 MIRROR-UNUSED-SECONDS)))
  (<- chosen tuple (unused-mirror-names #(#("bbbbbbbbbbbbbbbb.git" old) #("aaaaaaaaaaaaaaaa.git.tmp" old) #("cccccccccccccccc.git" old)
                                          #("dddddddddddddddd.git" edge) #("hand-made.git" old) #("eeeeeeeeeeeeeeee.git.old" old))
                                        (frozenset #("cccccccccccccccc")) now-ms))
  (assert (= chosen #("aaaaaaaaaaaaaaaa.git.tmp" "bbbbbbbbbbbbbbbb.git")) chosen))


(deftest test-ensuring-a-present-mirror-advances-its-used-mark [tmp-path]
  ;; 使った印: 在る mirror を EnsureMirror で使う(clone も fetch もしない)だけでも、url の錠の中で印の file を置き換え、mirror の dir の時刻が
  ;; 進む — 進まないと、使っている mirror を掃除が 7 日で消す。
  (val state-dir (/ tmp-path "state"))
  (val url "https://example.com/owner/used.git")
  (val old (- (time.time) MIRROR-UNUSED-SECONDS 3600))
  (val mirror (placed-mirror (/ state-dir "mirrors") (+ (name-of url) ".git") old))
  (val settings {"runtime-env.state" (str state-dir) "runtime-env.shared" (str state-dir) "runtime-env.uv-cache" (str (/ tmp-path "uv-cache"))
                 "runtime-env.repo-keys" {} "runtime-env.code-prepare" "" "runtime-env.uv" "uv" "runtime-env.progress" ""
                 "runtime-env.notes" "/dev/null"})
  (<- ready MirrorReady (with-handlers [(state) (sync-time-handler) (reader settings) subprocess-handler os-file-handler env-translation]
                                       (EnsureMirror url)))
  (assert (= ready.path (str mirror)) ready)
  (assert (.exists (/ mirror MIRROR-USED)) "使った印の file が無い")
  (assert (> (. (.stat mirror) st-mtime) (+ old 3600)) "在る mirror を使っても dir の時刻が進まない"))
