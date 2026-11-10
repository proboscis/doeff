;;; 値の ConfigMap の写しの書き(doeff_cluster._shared.protocol.workspace_volumes の settings-held)の検。
;;;
;;;   写しの dir を作れない  作業の根の下の写しの dir の名に file が在って dir を作れない時は、TypeError で落ちずに warning の 1 行に残して進み、
;;;                          写しの file を書かない(前は dir の作りの失敗の値 FileFailed を、後の <- の枝でそのまま yield して落ちた)
;;;   書ける                 dir を作れる時は、写しの file に読んだ刻と字を書く
(require doeff-hy.macros [deftest defk defhandler <- val])
(import datetime [datetime timezone])
(import json)
(import doeff [with_handlers])
(import doeff_core_effects [Listen MemoryFile MemoryFiles ReadMemoryFiles SlogEffect listen-handler slog-discard-handler state])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [EnvEntry ReadEnvironment])
(import doeff_time [sim-time-handler])
(import doeff_cluster._shared.intent.workspace_volume_model [WorkspaceVolumeSite])
(import doeff_cluster._shared.protocol.workspace_volumes [settings-held])

(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(val SITE (WorkspaceVolumeSite :longhorn-namespace "longhorn-system" :settings-namespace "agent-worker" :settings-name "workspace-volume"
                               :mount-dir "/run/workspace-mount" :mountinfo "/proc/self/mountinfo"
                               :attach-seconds 5.0 :detach-seconds 5.0 :mount-seconds 5.0 :backup-seconds 5.0 :restore-seconds 5.0
                               :poll-seconds 0.5 :recheck-seconds 0.5))
(val TEXT "{\"class\": \"workspace\", \"sizeBytes\": 1073741824}")
(val HELD-AT (datetime 2026 11 10 16 0 0 :tzinfo timezone.utc))
(val HELD-DIR "/work/workspace-volume-held")
(val COPY (+ HELD-DIR "/agent-worker/workspace-volume.json"))


(defhandler worker-environ [#^ dict environ]
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  ;; 引数に残す理由: worker の環境変数は筋書きごとの外の世界の形。作業の根の事実の読み(ReadEnvironment)に、表に在る名だけを答える。
  (ReadEnvironment [names]
    (resume (tuple (gfor name names :if (in name environ) (EnvEntry :name name :value (get environ name)))))))


(defk held-and-read []
  {:pre [] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "program"}}
  "写しを 1 回書き、その後の file の置き場を得るため(置き場は同じ答え手の中で読む)。"
  (<- (settings-held SITE TEXT))
  (<- store MemoryFiles (ReadMemoryFiles))
  store)


(defk held-under [files]
  {:pre [(: files MemoryFiles)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "作業の根 /work の worker の file の置き場 files の上で写しを 1 回書き、その後の置き場と出した log の行を得るため。"
  (<- heard tuple (with_handlers [slog-discard-handler (state) (sim-time-handler :start-time HELD-AT) (worker-environ {"WORK_DIR" "/work"})
                                  (memory-file-handler files) listen-handler]
                    (Listen (held-and-read) :types #(SlogEffect))))
  heard)


(deftest test-a-copy-whose-dir-cannot-be-made-is-logged-and-not-written
  ;; 写しの dir の名 HELD-DIR に file が在るので、private-dirs が dir を作れない(FileFailed)。
  (val blocked (MemoryFiles :files #((MemoryFile :path HELD-DIR :content b"not a dir")) :dirs #("/work")))
  (<- heard tuple (held-under blocked))
  (val store (get heard 0))
  (val lines (get heard 1))
  (assert (= (lfor f store.files f.path) [HELD-DIR]) store.files)
  (val warnings (lfor line lines :if (= line.msg "値の ConfigMap の写しを書けない") line))
  (assert (= (len warnings) 1) lines)
  (assert (= (get (. (get warnings 0) kwargs) "level") "warning") warnings))


(deftest test-a-copy-is-written-with-the-time-it-was-read
  (<- heard tuple (held-under (MemoryFiles :files #() :dirs #("/work"))))
  (val store (get heard 0))
  (val lines (get heard 1))
  (val written (lfor f store.files :if (= f.path COPY) f))
  (assert (= (len written) 1) store.files)
  (val held (json.loads (.decode (. (get written 0) content) "utf-8")))
  (assert (= (get held "text") TEXT) held)
  (assert (= (get held "readAtMs") (int (* (.timestamp HELD-AT) 1000))) held)
  (assert (= (lfor line lines :if (= line.msg "値の ConfigMap の写しを書けない") line) []) lines))
