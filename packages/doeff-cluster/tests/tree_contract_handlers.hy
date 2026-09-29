;;; コードの木の答え手(code_prepare の木の effect — ScanTree・LinkPycs・ImportClosure・CompileSources・WriteMarker・Note)の契約テストの
;;; 解釈器(composition root)— 同じ契約の Program を、木の答え手だけ替えて走らせる。
;;;
;;;   local-tree  本物: local-tree(os・hardlink・焼き)。木の置き場は走るたびに新しい一時 dir。契約が木を用意する file system の effect は
;;;               同じ一時 dir の上の os-file-handler が答える
;;;   files-tree  fake: files-tree(木の effect を file system の effect へ出し直す)+ memory-file-handler(根の dir だけが在る置き場)
;;;
;;; 契約の Program は置き場の根を FilesRoot(file_contract_handlers.hy)で読み、根からの相対で書く。焼きの経過の秒(GetMonotonic)は
;;; 両方とも模擬の時計(sim-time-handler)が答える。使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk <- val var])
(import os)
(import tempfile)
(import doeff [Program with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.file_effects [MemoryFiles])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.code_prepare [local-tree files-tree])
(import tests.file_contract_handlers [root-answers MEMORY-ROOT])

(val LOCAL-TREE "local-tree")
(val FILES-TREE "files-tree")


(defk under-local-tree [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本物の木の答え手の下で、新しい一時 dir を根にして program を走らせるため(走った後に一時 dir を消す)。"
  (val holder (tempfile.TemporaryDirectory))
  (var answer None)
  (try
    (<- ran (with_handlers [os-file-handler (root-answers (os.path.realpath holder.name)) (sim-time-handler :clock (SimClock)) (local-tree)]
                           program))
    (:= answer ran)
    (finally
      (.cleanup holder)))
  answer)


(defk under-files-tree [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "fake の木の答え手(file system の effect の上)と memory の置き場の下で program を走らせるため。"
  (<- answer (with_handlers [(state) (memory-file-handler (MemoryFiles :dirs #(MEMORY-ROOT))) (root-answers MEMORY-ROOT)
                             (sim-time-handler :clock (SimClock)) files-tree]
                            program))
  answer)


(val INTERPRETERS {LOCAL-TREE under-local-tree
                   FILES-TREE under-files-tree})
