;;; 汎用の file system の effect(file_effects.hy)を、外側の置き場の 1 つの dir(root)の下へ移して撃ち直す答え手 rooted-file-handler
;;; (agora-redesign #823 項目 1)。container の volume の mount と同じ: 内側から見た / は外側の root。置き場は持たない — 答えるのは外側の
;;; 答え手(memory-file-handler か os-file-handler)で、この handler は path の付け替えだけをする(業務を知らない)。
;;;
;;; 使い道: 1 つの memory の置き場を、node ごと・pod ごとの file system に分けて見せる(模擬の世界で、管理者の手と pod の読みが同じ置き場に
;;; 触る)。内側の path は絶対 path だけを受け、`..` は内側の / で止まる(root の外へ出ない)。
;;;
;;; 付け替え:
;;;   要求   path・source・target を root + 内側の path へ(ReleaseLock は錠の path を)。
;;;   答え   PathStat の real-path・LockHeld の path・FileFailed の path と理由の文の中の path を内側の path へ戻す(内側に root を見せない)。
;;;          DirEntry の name は dir からの名か相対 path なので変えない。
;;; path を持たない effect(ReadMemoryFiles など)は触らずに外側へ通す。
(require doeff-hy.macros [defhandler defk <- val])
(import posixpath)
(import dataclasses [replace])
(import doeff [EffectBase])
(import doeff_core_effects.file_effects [FileFailed PathStat LockHeld StatPath ReadText ReadBytes WriteText WriteBytes AppendText
                                         MakeDirectory ListDirectory WalkTree CopyFile CopyTree RenamePath RemoveTree AcquireLock
                                         ReleaseLock ReadDiskFree DiskUsage ReadDiskUsage MeasureTree LinkFile
                                         CompilePythonSources])

;; path 1 つを持つ effect と、写し元と写し先の 2 つを持つ effect。
(val PATH-EFFECTS #(StatPath ReadText ReadBytes WriteText WriteBytes AppendText MakeDirectory ListDirectory WalkTree RemoveTree
                    AcquireLock ReadDiskFree ReadDiskUsage MeasureTree))
(val MOVE-EFFECTS #(CopyFile LinkFile CopyTree RenamePath))
;; file の effect の答えの型の和(失敗・様子・錠・総量と空き・中身・一覧・空きと大きさの byte・答えの無い書き)。
(val ANSWER (| FileFailed PathStat LockHeld DiskUsage str bytes tuple int None))


(defk outer-path [root path]
  {:pre [(: root str) (: path str)] :post [(: % str)]}
  "内側の絶対 path を外側の path(root の下)へ移すため。`..` は内側の / で止まる(normpath が根の上へ出ない)。"
  (when (not (and (.startswith root "/") (= root (posixpath.normpath root))))
    (raise (ValueError (.format "rooted-file-handler の root は正規の絶対 path: {!r}" root))))
  (when (not (.startswith path "/"))
    (raise (ValueError (.format "rooted-file-handler は絶対 path だけを受ける: {!r}" path))))
  (val inner (posixpath.normpath path))
  (cond (= inner "/") root
        (= root "/") inner
        True (+ root inner)))


(defk inner-path [root path]
  {:pre [(: root str) (: path str)] :post [(: % str)]}
  "外側の path を内側の path へ戻すため(root の外の path はそのまま — 外側の答え手が root の外を答えることは無い)。"
  (cond (= root "/") path
        (= path root) "/"
        (.startswith path (+ root "/")) (cut path (len root) None)
        True path))


(defk inner-detail [root detail]
  {:pre [(: root str) (: detail str)] :post [(: % str)]}
  "断りの文の中の外側の path を内側の path へ戻すため(文は OSError の形 — path は repr で入る)。"
  (if (= root "/")
      detail
      (.replace (.replace detail (+ root "/") "/") (repr root) (repr "/"))))


(defk inner-answer [root answer]
  {:pre [(: root str) (: answer ANSWER)] :post [(: % ANSWER)]}
  "外側の答えを内側から見た形へ戻すため(path を持たない答えはそのまま)。"
  (match answer
    (FileFailed :path path :detail detail)
    (do (<- inner str (inner-path root path))
        (<- told str (inner-detail root detail))
        (FileFailed :path inner :detail told))
    ;; 欄 real-path は match の keyword の綴りに mangle されないので、型だけで当てて属性で読む。
    (PathStat)
    (do (<- inner str (inner-path root answer.real-path))
        (replace answer :real-path inner))
    (LockHeld :path held)
    (do (<- inner str (inner-path root held))
        (replace answer :path inner))
    _ answer))


(defhandler rooted-file-handler [#^ str root]
  ;; 引数に残す理由: root は筋書きの世界の形(どの dir を / に見せるか)で、同じ組の中で pod ごとに違う値。
  ;; 節の順: 型で当てる節を先に、:when つきの EffectBase の節を最後に置く(:when が偽の EffectBase の節は、後ろの節を試さずに外側へ通す)。
  (CopyFile [source target]
    (<- answer (moved root effect))
    (resume answer))
  (LinkFile [source target]
    (<- answer (moved root effect))
    (resume answer))
  (CompilePythonSources [tree items jobs roots]
    ;; 答えは木の中の相対 path なので、木の path だけを外側へ写す。
    (<- outer str (outer-path root tree))
    (<- answer (CompilePythonSources outer items jobs roots))
    (resume answer))
  (CopyTree [source target]
    (<- answer (moved root effect))
    (resume answer))
  (RenamePath [source target]
    (<- answer (moved root effect))
    (resume answer))
  (ReleaseLock [held]
    (<- path str (outer-path root held.path))
    (<- answer (ReleaseLock (replace held :path path)))
    (resume answer))
  (EffectBase [] :when (isinstance effect PATH-EFFECTS)
    (<- path str (outer-path root effect.path))
    (<- answer (replace effect :path path))
    (<- inner ANSWER (inner-answer root answer))
    (resume inner)))


(defk moved [root request]
  {:pre [(: root str) (: request MOVE-EFFECTS)] :post [(: % ANSWER)]}
  "写し元と写し先の 2 つの path を持つ effect を root の下へ移して撃ち直すため(答えは内側の path へ戻す)。"
  (<- source str (outer-path root request.source))
  (<- target str (outer-path root request.target))
  (<- answer (replace request :source source :target target))
  (<- inner ANSWER (inner-answer root answer))
  inner)
