;;; 汎用の file system の effect(agora-redesign #802 便 1・消費者 = #796 日次の全体検証・#795 webapp の組み立て)。業務の語を持たない土台の語彙で、
;;; HttpRequest(http_effects.hy)と同じ段。答え手は仕組みごとに差し替える:
;;;   os-file-handler      本物の file system(os_file.hy)
;;;   memory-file-handler  I/O なし — session の値に持つ file の置き場(memory_file.hy)。本物と同じ所で断る(親の dir が無い・file と dir の
;;;                        取り違え・中身の在る dir への rename)。symlink は持たない。
;;;
;;; 失敗は値(FileFailed — path と理由)で答える(例外にしない — 呼び手が型で読む)。成功の答えは effect ごと:
;;;   StatPath       path の種類・実の path・大きさ・mtime。答え = PathStat(無い path は kind MISSING — 失敗ではない)。follow-symlinks = False は
;;;                  symlink を辿らずに kind SYMLINK で答える(os.lstat — 先が壊れていても)
;;;   ReadText / ReadBytes    file の中身(text は UTF-8・読めない byte は置き換え)。答え = str / bytes。ReadBytes の limit は先頭の limit byte
;;;                  だけを読む(None = 全部 — 大きな file の頭の 1 行だけが要る読み手のため)
;;;   WriteText / WriteBytes  file を書く。replace = True は別名に書いてから置き換える(書きかけを読ませない)。mode は書いた後に与える。答え = None
;;;   AppendText     file の末尾に足す(無ければ作る)。答え = None
;;;   (書きの 3 つの sync = True は、答える前に中身を disk へ落とす(fsync — replace では置き換える前)。返事を済ませた中身が機体の停止で
;;;    消えては困る書き手のため。memory の置き場には落とす先が無いので、答えは sync に依らない)
;;;   MakeDirectory  dir を親ごと作る(在ってもよい)。mode は新しく作った最後の dir に与える。答え = None
;;;   ListDirectory  dir の直下。答え = DirEntry の tuple(名の順)
;;;   WalkTree       dir の下の全部(再帰)。答え = DirEntry の tuple(name = dir からの相対 path・/ 区切り・並べた順)
;;;   CopyFile       file 1 つを写す(写し先は上書き)。答え = None
;;;   LinkFile       file 1 つにもう 1 つの名を付ける(ハードリンク — 写し先が在れば断る・別の file system へは断る・#2462)。答え = None
;;;   CopyTree       dir の中身を target の下へ重ねて写す(target は在ってよい・同じ名は上書き・symlink は symlink のまま)。答え = None
;;;   RenamePath     path の名を変える(os.replace と同じ — 写し先の file は置き換え・中身の在る dir へは断る)。答え = None
;;;   RemoveTree     file か dir を中身ごと消す(無ければ断る)。答え = None
;;;   AcquireLock    錠の file を排他で取る(取れるまで待つ)。答え = LockHeld
;;;   ReleaseLock    取った錠を放す。答え = None
;;;   ReadDiskFree   path を含む file system の空き(byte・無い path は在る親で測る — agora-redesign #831)。答え = int
;;;   ReadDiskUsage  path を含む file system の総量と空き(byte・無い path は在る親で測る — #2504)。答え = DiskUsage
;;;   MeasureTree    dir の下の file の大きさの合計(byte・symlink は辿らずリンク自身の大きさ・hardlink は重ねて数える — #2504)。答え = int
;;;
;;; memory の置き場の語彙(本物の file system には無い): MemoryFile / MemoryFiles = 置き場の初めの形と今の中身・ReadMemoryFiles = 今の中身を
;;; 読む effect(検と筋書きが置き場を覗くため — memory-file-handler だけが答える)。
(require doeff-hy.macros [defk <- val])
(require doeff-hy.record [defrecord defenum])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff [EffectBase])


(defenum PathKind FILE DIRECTORY SYMLINK MISSING OTHER)


(defrecord FileFailed
  "file system の操作が断られた・失敗した。path = 対象・detail = 理由(OSError の文 か、memory の置き場の同じ意味の文)。"
  (#^ str path)
  (#^ str detail))


(defrecord PathStat
  "path の様子。kind = 種類(symlink は辿った先・無ければ MISSING)・real-path = 辿った後の絶対 path・size = byte 数・modified = mtime(epoch 秒)。"
  (#^ PathKind kind)
  (#^ str real-path)
  (#^ int size)
  (#^ float modified))


(defrecord DirEntry
  "dir の中の 1 つ。name = 名(WalkTree では dir からの相対 path)・kind = 種類(symlink は辿らずに SYMLINK)。"
  (#^ str name)
  (#^ PathKind kind))


(defrecord DiskUsage
  "file system の総量と空き(byte — ReadDiskUsage の答え)。"
  (#^ int total)
  (#^ int free))


(defrecord LockHeld
  "取った錠(path = 錠の file・token = 放す時に渡す手札)。"
  (#^ str path)
  (#^ int token))


(defclass [(dataclass :frozen True)] StatPath [EffectBase]
  "path の様子(頭の註)。follow-symlinks = False は symlink を辿らない(壊れた先の symlink も kind SYMLINK)。"
  (#^ str path)
  (setv #^ bool follow-symlinks True))


(defclass [(dataclass :frozen True)] ReadText [EffectBase]
  "text を読む(頭の註)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] ReadBytes [EffectBase]
  "bytes を読む(頭の註)。limit = 先頭の limit byte だけ(None = 全部)。"
  (#^ str path)
  (setv #^ (| int None) limit None))


(defclass [(dataclass :frozen True)] WriteText [EffectBase]
  "text を書く(頭の註)。"
  (#^ str path)
  (#^ str text)
  (setv #^ (| int None) mode None)
  (setv #^ bool replace False)
  (setv #^ bool sync False))


(defclass [(dataclass :frozen True)] WriteBytes [EffectBase]
  "bytes を書く(頭の註)。"
  (#^ str path)
  (#^ bytes content)
  (setv #^ (| int None) mode None)
  (setv #^ bool replace False)
  (setv #^ bool sync False))


(defclass [(dataclass :frozen True)] AppendText [EffectBase]
  "text を末尾に足す(頭の註)。"
  (#^ str path)
  (#^ str text)
  (setv #^ bool sync False))


(defclass [(dataclass :frozen True)] MakeDirectory [EffectBase]
  "dir を親ごと作る(頭の註)。"
  (#^ str path)
  (setv #^ (| int None) mode None))


(defclass [(dataclass :frozen True)] ListDirectory [EffectBase]
  "dir の直下を並べる(頭の註)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] WalkTree [EffectBase]
  "dir の下の全部を並べる(頭の註)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] CopyFile [EffectBase]
  "file の中身を写す(頭の註)。"
  (#^ str source)
  (#^ str target))


(defclass [(dataclass :frozen True)] LinkFile [EffectBase]
  "file 1 つにもう 1 つの名を付ける(頭の註)。"
  (#^ str source)
  (#^ str target))


(defclass [(dataclass :frozen True)] CopyTree [EffectBase]
  "dir の中身を重ねて写す(頭の註)。"
  (#^ str source)
  (#^ str target))


(defclass [(dataclass :frozen True)] RenamePath [EffectBase]
  "path の名を変える(頭の註)。"
  (#^ str source)
  (#^ str target))


(defclass [(dataclass :frozen True)] RemoveTree [EffectBase]
  "file か dir を中身ごと消す(頭の註)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] AcquireLock [EffectBase]
  "錠を排他で取る(頭の註)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] ReleaseLock [EffectBase]
  "取った錠を放す(頭の註)。"
  (#^ LockHeld held))


(defclass [(dataclass :frozen True)] ReadDiskFree [EffectBase]
  "path を含む file system の空きを読む(頭の註)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] ReadDiskUsage [EffectBase]
  "path を含む file system の総量と空きを読む(頭の註)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] MeasureTree [EffectBase]
  "dir の下の file の大きさの合計を測る(頭の註)。"
  (#^ str path))


(defrecord MemoryFile
  "memory の置き場の file 1 つ(path = 絶対 path・content = 中身の bytes・mode = 与えた mode か None)。"
  (#^ str path)
  (#^ bytes content)
  (setv #^ (| int None) mode None))


(defrecord MemoryFiles
  "memory の置き場の中身(files = file の列・dirs = dir の絶対 path の列 — 根 / は暗に在る・locks = 取られている錠の path・free = ReadDiskFree
   に答える空きの byte・total = ReadDiskUsage に答える総量の byte)。"
  (setv #^ (get tuple #(MemoryFile ...)) files #())
  (setv #^ (get tuple #(str ...)) dirs #())
  (setv #^ (get tuple #(str ...)) locks #())
  (setv #^ int free (** 2 40))
  (setv #^ int total (** 2 41)))


(defk file-done [request]
  {:pre [(: request EffectBase)] :post [(: % (| PathStat LockHeld DiskUsage str bytes tuple int None))] :tags {:context "file-system" :role "foundation"}}
  "file system の effect を 1 つ出し、断り(FileFailed)は OSError で上げて成功の答えだけを返すため(失敗したら続けられない書き手・
   読み手が、os の呼び出しを直に書いていた時と同じ例外の型で落ちる)。"
  (<- answer request)
  (when (isinstance answer FileFailed)
    (raise (OSError (.format "{}: {}" answer.path answer.detail))))
  answer)


(defclass [(dataclass :frozen True)] ReadMemoryFiles [EffectBase]
  "memory の置き場の今の中身(MemoryFiles)を読む(頭の註)。")
