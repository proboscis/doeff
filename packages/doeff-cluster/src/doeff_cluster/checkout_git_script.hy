;;; 送り手の手元の checkout の読み(runtime_env の翻訳の handler checkout-reads が出す git の問い)に答える、git の台本(2026-09-27)。
;;; doeff の scripted-process-handler に渡す ScriptedCommand 1 つで、子 process を起こさずに checkout の世界(GitCheckout の列)から答える。
;;; 業務を知らない: 答えるのは checkout-reads が出す 5 つの問いの形だけで、他の形は git と同じく exit 129(使い方の誤り)で断る。
;;;
;;;   git -C <path> rev-parse HEAD                                   head
;;;   git -C <path> rev-parse --show-toplevel                        path を含む checkout の根(path か、その下か、members に在る dir)
;;;   git -C <path> remote get-url <名>                              remotes の URL(無い名は exit 2)
;;;   git -C <path> status --porcelain --untracked-files=no          dirty なら変更の行 1 つ・でなければ空
;;;   git -C <path> branch -r --contains <sha> --list <型>            sha が head なら pushed のうち型(fnmatch)に合う branch の行
;;; checkout でない path は exit 128(fatal: not a git repository — 本物の git と同じ)。
;;;
;;;   (scripted-process-handler (ProcessScript :commands #((git-command #((GitCheckout :path "/src/app" :head sha …))))))
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import fnmatch)
(import functools [partial])
(import doeff_core_effects.process_effects [ProcessOutcome RunProcess])
(import doeff_core_effects.scripted_process [ScriptedCommand])

;; 本物の git と同じ exit(checkout でない = 128・使い方の誤り = 129・無い remote = 2)。
(val NOT-A-REPOSITORY 128)
(val BAD-USAGE 129)
(val NO-SUCH-REMOTE 2)


(defrecord GitRemote
  "checkout の remote 1 つ(名と URL)。"
  (#^ str name)
  (#^ str url))


(defrecord GitCheckout
  "台本の git が知る checkout 1 つ。path = 根・head = HEAD の sha・remotes = remote の列・dirty = commit していない変更がある・
   pushed = head を含む remote の branch(`origin/main` の形)・members = 根の外に書くがこの checkout に属する dir(送り手の source の dir
   SENDER-SOURCE-DIR のように、模擬の checkout の path と本物の置き場が違う dir)。"
  (#^ str path)
  (#^ str head)
  (setv #^ (get tuple #(GitRemote ...)) remotes #())
  (setv #^ bool dirty False)
  (setv #^ (get tuple #(str ...)) pushed #())
  (setv #^ (get tuple #(str ...)) members #()))


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


(defk git-answer [checkouts commands request]
  {:pre [(: checkouts tuple) (: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)]}
  "checkout の読みの git の問い 1 つに checkout の世界から答えるため(頭の註の 5 形)。"
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
        #("remote" "get-url" name) (if (in name remote-urls)
                                       (answered (get remote-urls name))
                                       (refused NO-SUCH-REMOTE (.format "error: No such remote '{}'" name)))
        #("status" "--porcelain" "--untracked-files=no") (answered (if found.dirty " M changed" ""))
        #("branch" "-r" "--contains" sha "--list" pattern)
        (answered (if (= sha found.head)
                      (.join "\n" (gfor b found.pushed :if (fnmatch.fnmatchcase b pattern) (+ "  " b)))
                      ""))
        _ (refused BAD-USAGE (.format "usage: 台本の git が知らない問い: {}" (.join " " argv)))))
  answer)


(defn #^ ScriptedCommand git-command [#^ tuple checkouts]  ; defk にできない: handler の列(ProcessScript)を組む時に Program の外で呼ぶ
  "checkout の世界(GitCheckout の tuple)に答える git の台本の命令を作るため(scripted-process-handler の ProcessScript に並べる)。"
  (ScriptedCommand :name "git" :run (partial git-answer checkouts)))
