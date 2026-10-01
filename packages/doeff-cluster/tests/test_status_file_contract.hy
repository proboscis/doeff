;;; worker の状態の file の答え手(status-file)の契約テスト — status-file は file system の effect を出すだけなので、本物(os-file-handler)
;;; と fake(memory-file-handler)の下で同じ deftest を通る。解釈器の組み立ては file_contract_handlers.hy。
;;;
;;;   * PublishStatus は、まだ無い親の dir を作って、状態の JSON(status-json の形・字下げ 1・ASCII に逃がさない)を path に置く
;;;   * 書き直しは中身を丸ごと置き換える(前の長い中身の尻尾を残さない)・dir には状態の file だけが残る(一時 file を残さない)
;;; 本物だけの性質(置き換えの原子性・mode)は os の rename と chmod の性質で、ここでは file の中身と在処だけを比べる。
(require doeff-hy.macros [deftest <- val])
(import json)
(import doeff [with_handlers])
(import doeff_core_effects.file_effects [ReadText ListDirectory])
(import doeff_cluster.handlers [CodeStore status-file status-json])
(import doeff_cluster.worker_model [JobStatus PublishStatus] doeff_cluster.shared.intent.job_model [JobPhase])
(import tests.file_contract_handlers [FilesRoot])

(val TIMINGS {"rev-1" 1.5})
(val ROWS #((JobStatus "svc/a" JobPhase.RUNNING "rev-1" "rev-1" 42 1 :detail "動いている" :instance "i-1" :placement 3)
            (JobStatus "svc/b" JobPhase.FINISHED "rev-1" None None 2)))


(deftest test-publish-writes-the-status-json-under-a-new-parent
  {:interpreters ["os-files" "memory-files"]}
  (<- root str (FilesRoot))
  (val path (+ root "/state/deeper/status.json"))
  (val codes (CodeStore "/repo" "/cache" None))
  (.update codes.timings TIMINGS)
  (<- (with_handlers [(status-file path codes)] (PublishStatus ROWS "注記")))
  (<- text str (ReadText path))
  (assert (= text (json.dumps (status-json ROWS "注記" TIMINGS) :ensure-ascii False :indent 1)) text)
  (assert (= (get (json.loads text) "jobs" 0 "detail") "動いている") text)
  (<- names tuple (ListDirectory (+ root "/state/deeper")))
  (assert (= (lfor e names e.name) ["status.json"]) names))


(deftest test-a-second-publish-replaces-the-whole-file
  {:interpreters ["os-files" "memory-files"]}
  (<- root str (FilesRoot))
  (val path (+ root "/state/status.json"))
  (val codes (CodeStore "/repo" "/cache" None))
  (<- (with_handlers [(status-file path codes)] (PublishStatus ROWS "長い注記 長い注記 長い注記")))
  (<- (with_handlers [(status-file path codes)] (PublishStatus #() "")))
  (<- text str (ReadText path))
  (assert (= (json.loads text) {"note" "" "codePrepareSeconds" {} "jobs" []}) text)
  (<- names tuple (ListDirectory (+ root "/state")))
  (assert (= (lfor e names e.name) ["status.json"]) names))
