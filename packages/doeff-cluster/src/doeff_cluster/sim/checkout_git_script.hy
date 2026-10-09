;;; 送り手の手元の checkout の読み(runtime_env の翻訳の handler checkout-reads が出す git の問い)に答える、git の台本(2026-09-27)。
;;; doeff の scripted-process-handler に渡す ScriptedCommand 1 つで、子 process を起こさずに checkout の世界(GitCheckout の列)から答える。
;;; 業務を知らない: 答えるのは checkout の読みの 7 つの問いの形だけ(checkout-reads の 5 形・remote の URL の書き換える前の読み 1 形と、名指しの rev を commit へ解く 1 形)
;;; で、他の形は git と同じく exit 129(使い方の誤り)で断る。
;;;
;;;   git -C <path> rev-parse HEAD                                   head
;;;   git -C <path> rev-parse --verify --quiet <rev>^{commit}         HEAD・head の sha・revs の名/sha → その sha。知らない rev は exit 1・出力なし
;;;   git -C <path> rev-parse --show-toplevel                        path を含む checkout の根(path か、その下か、members に在る dir)
;;;   git -C <path> remote get-url <名>                              remotes の URL に rewrites(git の url.<base>.insteadOf)を当てた値
;;;                                                                  (無い名は exit 2 — 本物の get-url も insteadOf で書き換えて答える)
;;;   git -C <path> config --get remote.<名>.url                     remotes の URL そのまま(書き換えない・無い名は exit 1 で出力なし)
;;;   git -C <path> status --porcelain --untracked-files=no          dirty なら変更の行 1 つ・でなければ空
;;;   git -C <path> branch -r --contains <sha> --list <型>            sha が head なら pushed・revs の sha ならその rev の pushed のうち、
;;;                                                                  型(fnmatch)に合う branch の行
;;; checkout でない path は exit 128(fatal: not a git repository — 本物の git と同じ)。
;;;
;;;   (scripted-process-handler (ProcessScript :commands #((git-command #((GitCheckout :path "/src/app" :head sha …))))))
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import fnmatch)
(import functools [partial])
(import doeff_core_effects.process_effects [ProcessOutcome RunProcess])
(import doeff_core_effects.scripted_process [ScriptedCommand])

;; 本物の git と同じ exit(checkout でない = 128・使い方の誤り = 129・無い remote = 2・--verify で解けない rev = 1)。
(val NOT-A-REPOSITORY 128)
(val BAD-USAGE 129)
(val NO-SUCH-REMOTE 2)
(val UNKNOWN-REV 1)
;; config --get で名が無い時の exit(本物の git config と同じ)。
(val NO-SUCH-KEY 1)
;; rev-parse --verify で commit を求める接尾。
(val COMMIT-PEEL "^{commit}")


(defrecord GitRemote
  "checkout の remote 1 つ(名と URL)。"
  (#^ str name)
  (#^ str url))


(defrecord GitUrlRewrite
  "送り手の機体の git の設定 url.<base>.insteadOf <instead-of> 1 つ(remote の URL が instead-of で始まれば、その頭を base に替えて使う —
   会話の Pod の ~/.gitconfig が GitHub の URL を機体だけの ssh の別名へ書き換える形・card acp:kanban-issue:ki-1ada4f0c8344)。"
  (#^ str base)
  (#^ str instead-of))


(defrecord GitRev
  "checkout が知る名指しの rev 1 つ(branch・tag の名 → commit の sha)と、その commit を含む remote の branch(`origin/main` の形)。"
  (#^ str name)
  (#^ str sha)
  (setv #^ (get tuple #(str ...)) pushed #()))


(defrecord GitCheckout
  "台本の git が知る checkout 1 つ。path = 根・head = HEAD の sha・remotes = remote の列・dirty = commit していない変更がある・
   pushed = head を含む remote の branch(`origin/main` の形)・members = 根の外に書くがこの checkout に属する dir(送り手の source の dir
   SENDER-SOURCE-DIR のように、模擬の checkout の path と本物の置き場が違う dir)・revs = 名指しの rev(GitRev の列)・rewrites = 機体の git の
   URL の書き換え(GitUrlRewrite の列 — remote get-url だけが当てる)。"
  (#^ str path)
  (#^ str head)
  (setv #^ (get tuple #(GitRemote ...)) remotes #())
  (setv #^ bool dirty False)
  (setv #^ (get tuple #(str ...)) pushed #())
  (setv #^ (get tuple #(str ...)) members #())
  (setv #^ (get tuple #(GitRev ...)) revs #())
  (setv #^ (get tuple #(GitUrlRewrite ...)) rewrites #()))


(defk answered [stdout]
  {:pre [(: stdout str)] :post [(: % ProcessOutcome)]}
  "成功の答えを作るため(git の出力は末尾に改行)。"
  (ProcessOutcome :exit-code 0 :stdout (if stdout (+ stdout "\n") "") :stderr ""))


(defk refused [code message]
  {:pre [(: code int) (: message str)] :post [(: % ProcessOutcome)]}
  "git が断った答えを作るため。"
  (ProcessOutcome :exit-code code :stdout "" :stderr (+ message "\n")))


(defk checkout-of [checkouts path]
  {:pre [(: checkouts tuple) (: path str)] :post [(: % (| GitCheckout None))]}
  "path を含む checkout を引くため(根そのもの・根の下・members の dir)。"
  (next (gfor c checkouts
              :if (or (= path c.path) (.startswith path (+ c.path "/")) (in path c.members))
              c)
        None))


(defk commit-of [checkout rev]
  {:pre [(: checkout GitCheckout) (: rev str)] :post [(: % (| str None))]}
  "名指しの rev を checkout の世界で commit の sha へ解くため(HEAD・head の sha・revs の名か sha。知らなければ None)。"
  (if (in rev #("HEAD" checkout.head))
      checkout.head
      (next (gfor r checkout.revs :if (in rev #(r.name r.sha)) r.sha) None)))


(defk pushed-of [checkout sha]
  {:pre [(: checkout GitCheckout) (: sha str)] :post [(: % tuple)]}
  "その commit を含む remote の branch を引くため(head なら checkout の pushed・revs の sha ならその rev の pushed・他は空)。"
  (if (= sha checkout.head)
      checkout.pushed
      (next (gfor r checkout.revs :if (= sha r.sha) r.pushed) #())))


(defk verified [checkout peeled]
  {:pre [(: checkout GitCheckout) (: peeled str)] :post [(: % ProcessOutcome)]}
  "rev-parse --verify --quiet <rev>[^{commit}] に答えるため(解けた sha・解けなければ exit 1 で出力なし — 本物の --quiet と同じ)。"
  (<- sha (| str None) (commit-of checkout (.removesuffix peeled COMMIT-PEEL)))
  (if (is-not sha None)
      (do (<- found ProcessOutcome (answered sha)) found)
      (ProcessOutcome :exit-code UNKNOWN-REV :stdout "" :stderr "")))


(defk rewritten-url [checkout url]
  {:pre [(: checkout GitCheckout) (: url str)] :post [(: % str)]}
  "remote get-url の答えを作るため: URL の頭に合う書き換えのうち、合う頭がいちばん長い 1 つを当てる(本物の insteadOf と同じ — 合わなければ
   URL のまま)。"
  (val matching (sorted (gfor r checkout.rewrites :if (.startswith url r.instead-of) r) :key (fn [r] (len r.instead-of)) :reverse True))
  (if matching
      (+ (. (get matching 0) base) (cut url (len (. (get matching 0) instead-of)) None))
      url))


(defk containing [checkout sha pattern]
  {:pre [(: checkout GitCheckout) (: sha str) (: pattern str)] :post [(: % ProcessOutcome)]}
  "branch -r --contains <sha> --list <型> に答えるため(その commit を含む remote の branch のうち型に合う行)。"
  (<- pushed tuple (pushed-of checkout sha))
  (<- lines ProcessOutcome (answered (.join "\n" (gfor b pushed :if (fnmatch.fnmatchcase b pattern) (+ "  " b)))))
  lines)


(defk remote-url-key [key]
  {:pre [(: key str)] :post [(: % (| str None))]}
  "config --get の鍵 remote.<名>.url から remote の名を取り出すため(他の鍵は None — 台本が答える config の鍵はこの形だけ)。"
  (if (and (.startswith key "remote.") (.endswith key ".url"))
      (cut key (len "remote.") (- (len ".url")))
      None))


(defk git-answer [checkouts commands request]
  {:pre [(: checkouts tuple) (: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)]}
  "checkout の読みの git の問い 1 つに checkout の世界から答えるため(頭の註の 6 形)。"
  (val argv request.argv)
  (when (or (< (len argv) 4) (!= (get argv 1) "-C"))
    (<- usage ProcessOutcome (refused BAD-USAGE (.format "usage: 台本の git は -C <path> の形だけ: {}" (.join " " argv))))
    (return usage))
  (<- found (| GitCheckout None) (checkout-of checkouts (get argv 2)))
  (when (is found None)
    (<- outside ProcessOutcome (refused NOT-A-REPOSITORY "fatal: not a git repository (or any of the parent directories): .git"))
    (return outside))
  (val remote-urls (dfor r found.remotes r.name r.url))
  (<- answer ProcessOutcome
      (match (tuple (cut argv 3 None))
        #("rev-parse" "HEAD") (answered found.head)
        #("rev-parse" "--show-toplevel") (answered found.path)
        #("rev-parse" "--verify" "--quiet" peeled) (verified found peeled)
        #("remote" "get-url" name) (if (in name remote-urls)
                                       (do (<- url str (rewritten-url found (get remote-urls name)))
                                           (answered url))
                                       (refused NO-SUCH-REMOTE (.format "error: No such remote '{}'" name)))
        #("config" "--get" key) (match (remote-url-key key)
                                  (| None "") (refused NO-SUCH-KEY "")
                                  name (if (in name remote-urls)
                                           (answered (get remote-urls name))
                                           (refused NO-SUCH-KEY "")))
        #("status" "--porcelain" "--untracked-files=no") (answered (if found.dirty " M changed" ""))
        #("branch" "-r" "--contains" sha "--list" pattern)
        (containing found sha pattern)
        _ (refused BAD-USAGE (.format "usage: 台本の git が知らない問い: {}" (.join " " argv)))))
  answer)


(defn #^ ScriptedCommand git-command [#^ tuple checkouts]  ; defk にできない: handler の列(ProcessScript)を組む時に Program の外で呼ぶ
  "checkout の世界(GitCheckout の tuple)に答える git の台本の命令を作るため(scripted-process-handler の ProcessScript に並べる)。"
  (ScriptedCommand :name "git" :run (partial git-answer checkouts)))
