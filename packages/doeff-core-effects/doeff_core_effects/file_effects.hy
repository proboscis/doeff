;;; 汎用の file system の effect(agora-redesign #802 便 1・消費者 = #796 日次の全体検証・#795 webapp の組み立て)。業務の語を持たない土台の語彙で、
;;; HttpRequest(http_effects.hy)と同じ段。答え手は仕組みごとに差し替える:
;;;   os-file-handler      本物の file system(os_file.hy)
;;;   memory-file-handler  I/O なし — session の値に持つ file の置き場(memory_file.hy)。本物と同じ所で断る(親の dir が無い・file と dir の
;;;                        取り違え・中身の在る dir への rename)。symlink も持つ(MakeSymlink — agora-redesign #4036)。
;;;
;;; 失敗は値(FileFailed — path と理由)で答える(例外にしない — 呼び手が型で読む)。成功の答えは effect ごと:
;;;   StatPath       path の種類・実の path・大きさ・mtime。答え = PathStat(無い path は kind MISSING — 失敗ではない)。follow-symlinks = False は
;;;                  symlink を辿らずに kind SYMLINK で答える(os.lstat — 先が壊れていても)
;;;   ReadText / ReadBytes    file の中身(text は UTF-8・読めない byte は置き換え)。答え = str / bytes。ReadBytes の offset は読み始める
;;;                  byte の位置(既定 0 — 追記される file を前に読んだ所から先だけ読む読み手のため・agora-redesign #3977。file の終わりより先は
;;;                  空の bytes)・limit は offset から先の limit byte だけを読む(None = 終わりまで — 大きな file の頭の 1 行だけが要る読み手のため)
;;;   WriteText / WriteBytes  file を書く。replace = True は別名に書いてから置き換える(書きかけを読ませない)。mode は書いた後に与える。答え = None
;;;   AppendText     file の末尾に足す(無ければ作る)。答え = None
;;;   (書きの 3 つの sync = True は、答える前に中身を disk へ落とす(fsync — replace では置き換える前)。返事を済ませた中身が機体の停止で
;;;    消えては困る書き手のため。memory の置き場には落とす先が無いので、答えは sync に依らない)
;;;   MakeDirectory  dir を親ごと作る(在ってもよい)。mode は新しく作った最後の dir に与える。答え = None
;;;   ListDirectory  dir の直下。答え = DirEntry の tuple(名の順)
;;;   WalkTree       dir の下の全部(再帰)。答え = DirEntry の tuple(name = dir からの相対 path・/ 区切り・並べた順)
;;;   CopyFile       file 1 つを写す(写し先は上書き)。答え = None
;;;   CompilePythonSources  木の source(#(相対 path module 名) の列)を import が検める方式(PEP 552 の checked hash)の .pyc に焼いて
;;;                  __pycache__ へ置く(本物は jobs 個の process で並列・roots = 焼く間の import の根 — #2463)。答え = 焼けなかった物の
;;;                  SourceNotCompiled の tuple
;;;   LinkFile       file 1 つにもう 1 つの名を付ける(ハードリンク — 写し先が在れば断る・別の file system へは断る・#2462)。答え = None
;;;   CopyTree       dir の中身を target の下へ重ねて写す(target は在ってよい・同じ名は上書き・symlink は symlink のまま)。答え = None
;;;   MakeSymlink    path に target を指す symlink を作る(os.symlink と同じ — path が在れば断る・target は相対なら link の在る dir から読む・
;;;                  先が無くても作る)。答え = None。dir の中身を別の木へ 1 手で付け替える書き手のため(別名の link を作って RenamePath で
;;;                  置き換える — agora-redesign #4036)
;;;   RenamePath     path の名を変える(os.replace と同じ — 写し先の file は置き換え・中身の在る dir へは断る・symlink は辿らずに link 自身を
;;;                  動かし、置き換える)。答え = None
;;;   RemoveTree     file か dir を中身ごと消す(無ければ断る・symlink は link だけを消す)。答え = None
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


(defrecord SourceNotCompiled
  "焼けなかった source 1 つ(path = 木の中の相対 path・reason = 理由 — 例外の型と文)。import の時に同じ誤りが出るので、焼きの失敗にはしない。"
  (#^ str path)
  (#^ str reason))


(defrecord LockHeld
  "取った錠(path = 錠の file・token = 放す時に渡す手札)。"
  (#^ str path)
  (#^ int token))


(defclass [(dataclass :frozen True)] StatPath [(get EffectBase (| PathStat FileFailed))]
  "path の様子(頭の註)。follow-symlinks = False は symlink を辿らない(壊れた先の symlink も kind SYMLINK)。"
  (#^ str path)
  (setv #^ bool follow-symlinks True))


(defclass [(dataclass :frozen True)] ReadText [(get EffectBase (| str FileFailed))]
  "text を読む(頭の註)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] ReadBytes [(get EffectBase (| bytes FileFailed))]
  "bytes を読む(頭の註)。offset = 読み始める byte の位置(0 以上)・limit = offset から先の limit byte だけ(None = 終わりまで)。"
  (#^ str path)
  (setv #^ (| int None) limit None)
  (setv #^ int offset 0)
  (defn #^ None __post-init__ [self]  ; defk にできない: dataclass が作る時に呼ぶ検め(Program を返すと実行されない)
    (when (or (not (isinstance self.offset int)) (isinstance self.offset bool) (< self.offset 0))
      (raise (ValueError f"ReadBytes.offset must be an int >= 0, got {self.offset !r}")))))


(defclass [(dataclass :frozen True)] WriteText [(get EffectBase (| FileFailed None))]
  "text を書く(頭の註)。"
  (#^ str path)
  (#^ str text)
  (setv #^ (| int None) mode None)
  (setv #^ bool replace False)
  (setv #^ bool sync False))


(defclass [(dataclass :frozen True)] WriteBytes [(get EffectBase (| FileFailed None))]
  "bytes を書く(頭の註)。"
  (#^ str path)
  (#^ bytes content)
  (setv #^ (| int None) mode None)
  (setv #^ bool replace False)
  (setv #^ bool sync False))


(defclass [(dataclass :frozen True)] AppendText [(get EffectBase (| FileFailed None))]
  "text を末尾に足す(頭の註)。"
  (#^ str path)
  (#^ str text)
  (setv #^ bool sync False))


(defclass [(dataclass :frozen True)] MakeDirectory [(get EffectBase (| FileFailed None))]
  "dir を親ごと作る(頭の註)。"
  (#^ str path)
  (setv #^ (| int None) mode None))


(defclass [(dataclass :frozen True)] ListDirectory [(get EffectBase (| (get tuple #(DirEntry ...)) FileFailed))]
  "dir の直下を並べる(頭の註)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] WalkTree [(get EffectBase (| (get tuple #(DirEntry ...)) FileFailed))]
  "dir の下の全部を並べる(頭の註)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] CopyFile [(get EffectBase (| FileFailed None))]
  "file の中身を写す(頭の註)。"
  (#^ str source)
  (#^ str target))


(defclass [(dataclass :frozen True)] CompilePythonSources [(get EffectBase (get tuple #(SourceNotCompiled ...)))]
  "木の source を .pyc に焼く(頭の註)。"
  (#^ str tree)
  (#^ (get tuple #((get tuple #(str str)) ...)) items)
  (setv #^ int jobs 1)
  (setv #^ (get tuple #(str ...)) roots #(".")))


(defclass [(dataclass :frozen True)] LinkFile [(get EffectBase (| FileFailed None))]
  "file 1 つにもう 1 つの名を付ける(頭の註)。"
  (#^ str source)
  (#^ str target))


(defclass [(dataclass :frozen True)] CopyTree [(get EffectBase (| FileFailed None))]
  "dir の中身を重ねて写す(頭の註)。"
  (#^ str source)
  (#^ str target))


(defclass [(dataclass :frozen True)] MakeSymlink [(get EffectBase (| FileFailed None))]
  "path に target を指す symlink を作る(頭の註)。"
  (#^ str path)
  (#^ str target))


(defclass [(dataclass :frozen True)] RenamePath [(get EffectBase (| FileFailed None))]
  "path の名を変える(頭の註)。"
  (#^ str source)
  (#^ str target))


(defclass [(dataclass :frozen True)] RemoveTree [(get EffectBase (| FileFailed None))]
  "file か dir を中身ごと消す(頭の註)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] AcquireLock [(get EffectBase (| LockHeld FileFailed))]
  "錠を排他で取る(頭の註)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] ReleaseLock [(get EffectBase (| FileFailed None))]
  "取った錠を放す(頭の註)。"
  (#^ LockHeld held))


(defclass [(dataclass :frozen True)] ReadDiskFree [(get EffectBase (| int FileFailed))]
  "path を含む file system の空きを読む(頭の註)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] ReadDiskUsage [(get EffectBase (| DiskUsage FileFailed))]
  "path を含む file system の総量と空きを読む(頭の註)。"
  (#^ str path))


(defclass [(dataclass :frozen True)] MeasureTree [(get EffectBase (| int FileFailed))]
  "dir の下の file の大きさの合計を測る(頭の註)。"
  (#^ str path))


(defrecord MemoryFile
  "memory の置き場の file 1 つ(path = 絶対 path・content = 中身の bytes・mode = 与えた mode か None)。"
  (#^ str path)
  (#^ bytes content)
  (setv #^ (| int None) mode None))


(defrecord MemoryLink
  "memory の置き場の symlink 1 つ(path = link の絶対 path・target = 書いたままの先 — 相対なら link の在る dir から読む)。"
  (#^ str path)
  (#^ str target))


(defrecord MemoryFiles
  "memory の置き場の中身(files = file の列・dirs = dir の絶対 path の列 — 根 / は暗に在る・links = symlink の列・locks = 取られている錠の
   path・free = ReadDiskFree に答える空きの byte・total = ReadDiskUsage に答える総量の byte)。"
  (setv #^ (get tuple #(MemoryFile ...)) files #())
  (setv #^ (get tuple #(str ...)) dirs #())
  (setv #^ (get tuple #(MemoryLink ...)) links #())
  (setv #^ (get tuple #(str ...)) locks #())
  (setv #^ int free (** 2 40))
  (setv #^ int total (** 2 41)))


(defk file-done [request]
  {:tp [A] :pre [(: request (of EffectBase (| A FileFailed)))] :post [(: % A)] :tags {:context "file-system" :role "foundation"}}
  "file system の effect を 1 つ出し、断り(FileFailed)は OSError で上げて成功の答えだけを返すため(失敗したら続けられない書き手・
   読み手が、os の呼び出しを直に書いていた時と同じ例外の型で落ちる)。"
  (<- answer request)
  (when (isinstance answer FileFailed)
    (raise (OSError (.format "{}: {}" answer.path answer.detail))))
  answer)


(defclass [(dataclass :frozen True)] ReadMemoryFiles [(get EffectBase MemoryFiles)]
  "memory の置き場の今の中身(MemoryFiles)を読む(頭の註)。")
