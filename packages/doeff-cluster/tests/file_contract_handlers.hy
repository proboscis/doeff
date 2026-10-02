;;; file の置き場の答え手(record-files・status-file)の契約テストの解釈器(composition root)— 同じ契約の Program を、file system の
;;; 答え手だけ替えて走らせる。record-files と status-file は自分で os を呼ばず、file system の effect を出すだけなので、差し替えるのは
;;; その答え手 1 つ:
;;;
;;;   os-files      本物: os-file-handler(走るたびに新しい一時 dir を置き場の根にし、走った後に消す)
;;;   memory-files  fake: memory-file-handler(根の dir だけが在る memory の置き場)
;;;
;;; 契約の Program は置き場の根を検の effect FilesRoot で読み、根からの相対で書く(本物と fake で根の path が違うため)。
;;; 使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])
(import os)
(import tempfile)
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.file_effects [MemoryFiles])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.memory_file [memory-file-handler])

(val OS-FILES "os-files")
(val MEMORY-FILES "memory-files")
(val MEMORY-ROOT "/contract")


(defclass [(dataclass :frozen True)] FilesRoot [EffectBase]
  "契約の置き場の根(在る dir の絶対 path)を読む検の effect。")


(defhandler root-answers [#^ str root]
  ;; 引数に残す理由: 根は解釈器ごとに違う値(本物 = 一時 dir・fake = memory の置き場の dir)。
  (FilesRoot []
    (resume root)))


(defk under-os-files [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "本物の file system の答え手の下で、新しい一時 dir を根にして program を走らせるため(走った後に一時 dir を消す)。"
  (val holder (tempfile.TemporaryDirectory))
  (var answer None)
  (try
    (<- ran (with_handlers [os-file-handler (root-answers (os.path.realpath holder.name))] program))
    (:= answer ran)
    (finally
      (.cleanup holder)))
  answer)


(defk under-memory-files [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "memory の置き場の答え手の下で、根の dir だけが在る置き場で program を走らせるため。"
  (<- answer (with_handlers [(state) (memory-file-handler (MemoryFiles :dirs #(MEMORY-ROOT))) (root-answers MEMORY-ROOT)] program))
  answer)


(val INTERPRETERS {OS-FILES under-os-files
                   MEMORY-FILES under-memory-files})
