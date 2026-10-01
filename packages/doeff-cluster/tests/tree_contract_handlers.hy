;;; コードの木の答え手(code_prepare の木の effect — ScanTree・LinkPycs・ImportClosure・CompileSources・WriteMarker・Note)の契約テストの
;;; 解釈器(composition root)— 同じ契約の Program を、木の答え手だけ替えて走らせる。
;;;
;;;   os-files-tree      本物: 言い換え tree-files(木の effect を file system の effect へ出し直す)+ os-file-handler。木の置き場は走るたびに
;;;                      新しい一時 dir(契約が木を用意する file system の effect も同じ一時 dir の上で答える)
;;;   memory-files-tree  fake: 同じ tree-files + memory-file-handler(根の dir だけが在る置き場)
;;;
;;; 木の言い換えの Note(slog)は捨てる(行き先の stderr は契約の外)。契約の Program は置き場の根を FilesRoot(file_contract_handlers.hy)で読み、根からの相対で書く。焼きの経過の秒(GetMonotonic)は
;;; 両方とも模擬の時計(sim-time-handler)が答える。使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk <- val var])
(import os)
(import tempfile)
(import doeff [Program with_handlers])
(import doeff_core_effects.handlers [state slog-discard-handler])
(import doeff_core_effects.file_effects [MemoryFiles])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.worker.protocol.tree_files [tree-files])
(import tests.file_contract_handlers [root-answers MEMORY-ROOT])

(val OS-FILES-TREE "os-files-tree")
(val MEMORY-FILES-TREE "memory-files-tree")


(defk under-os-files [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "木の言い換えと本物の file system の答え手の下で、新しい一時 dir を根にして program を走らせるため(走った後に一時 dir を消す)。"
  (val holder (tempfile.TemporaryDirectory))
  (var answer None)
  (try
    (<- ran (with_handlers [os-file-handler (root-answers (os.path.realpath holder.name)) (sim-time-handler :clock (SimClock)) slog-discard-handler tree-files]
                           program))
    (:= answer ran)
    (finally
      (.cleanup holder)))
  answer)


(defk under-memory-files [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "木の言い換えと memory の置き場の下で program を走らせるため。"
  (<- answer (with_handlers [(state) (memory-file-handler (MemoryFiles :dirs #(MEMORY-ROOT))) (root-answers MEMORY-ROOT)
                             (sim-time-handler :clock (SimClock)) slog-discard-handler tree-files]
                            program))
  answer)


(val INTERPRETERS {OS-FILES-TREE under-os-files
                   MEMORY-FILES-TREE under-memory-files})
