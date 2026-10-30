# moved-submodule-shim: doeff_cluster.foundation -> doeff_cluster._foundation
# moved-submodule-shim-names doeff_cluster.foundation.bytecode_pool:
#   MODULE_TAGS PYC_HEAD_BYTES STORE_MODULE_NAME bake bake_one current_pyc main mark_progress
#   place_pyc prepare_path store_module_at write_pyc
# moved-submodule-shim-names doeff_cluster.foundation.child_environ:
#   MODULE_TAGS replace_environ
# moved-submodule-shim-names doeff_cluster.foundation.coordinator_http:
#   CONNECT_SECONDS MODULE_TAGS PREFERRED_RECHECK_SECONDS RESEND_PAUSE_SECONDS default_actor
# moved-submodule-shim-names doeff_cluster.foundation.coordinator_inbox:
#   AcceptingServer InboxBusy InboxWaiting InboxWake LIVE_STALL_SECONDS MODULE_TAGS
#   READY_STALL_SECONDS RawRequest ReplySlot RequestInbox StopState WAKE json_reply probe_verdict
#   stop_on_signals
# moved-submodule-shim-names doeff_cluster.foundation.foundation_check:
#   FoundationClosure MODULE_TAGS UnreadDeclaration foundation_closure hyx_closedXquestion_markX
# moved-submodule-shim-names doeff_cluster.foundation.host_contract:
#   HOST_CONTRACT HostContract SIM_PASSABLE environ_table_reader os_environ_reader this_program_path
# moved-submodule-shim-names doeff_cluster.foundation.in_cluster_api:
#   CA_MISSING CLUSTER_API ClusterCaMissing MODULE_TAGS SERVICE_ACCOUNT_CA_PATH cluster_ca_answer
#   effective_port hyx_readable_portXquestion_markX hyx_same_originXquestion_markX
#   in_cluster_api_connected in_cluster_api_routed routed_answer
# moved-submodule-shim-names doeff_cluster.foundation.kube_client:
#   API_URL KubeClient KubeSettings MODULE_TAGS SA_DIR WATCH_READ_MARGIN_SECONDS WatchNext
#   WatchPlace annotate_deployment annotate_patch deployment_url deployments_url follow_deployment
#   follow_node_object keepalive_options kube_available kube_call kube_client kube_headers list_once
#   new_pool nodes_url scale_deployment scale_patch start_watch stop_watch tell_body tell_error
#   tell_unexpected version_of watch_once watch_params watch_place watch_query watch_run
# moved-submodule-shim-names doeff_cluster.foundation.lifeline:
#   LifelineSettings MODULE_TAGS PostAnswer event_pause lifeline_run posted start_lifeline
# moved-submodule-shim-names doeff_cluster.foundation.notice_pipe:
#   MODULE_TAGS NoticeBox NoticePipe NoticeWaiter next_notice notice_pipe_of open_notice_box
#   own_pipe read_notice_lines
# moved-submodule-shim-names doeff_cluster.foundation.otlp_post:
#   MODULE_TAGS OtlpPoster endpoint_usable
# moved-submodule-shim-names doeff_cluster.foundation.process_environ:
#   MODULE_TAGS NamedValue child_environ named_environ
# moved-submodule-shim-names doeff_cluster.foundation.process_versions:
#   MODULE_TAGS RUNTIME_ENV_KEY_VAR SourceUnidentified clock_ticks process_versions
#   this_process_environ this_process_versions
# moved-submodule-shim-names doeff_cluster.foundation.record_codec:
#   BLOB_MEMORY_MAX BlobMemory DECISION DELTA_MIN_CHARS DIVERGE DataclassValue EffectCodec
#   FORMAT_VERSION FROZEN_MARKS HandleTable INTERN_MIN_CHARS JsonValue LIVE LOOSE MODULE_TAGS
#   MOVED_MODULES MOVED_TYPES MalformedRecordSpec OUTPUT READ READABLE_FORMATS RecordedError
#   ReplayHandle ReplaySemaphore RestoredValue SPEC_ATTRIBUTE SPEC_FIELDS UnencodableValue
#   UnrecordableEffect apply_delta args_of can_record canonical codec_of content_hash decode_error
#   decode_value delta_of encode_error encode_value handle_for intern_json mode_of register
#   registered_types resolve_refs resolve_type short_hash subject_of type_name
# moved-submodule-shim-names doeff_cluster.foundation.record_handlers:
#   BLOB_MEMORY_MAX BlobMemory BufferedSink DECISION DIVERGE EffectCodec EffectLog Entry
#   FORMAT_VERSION HandleTable HttpSink INTERN_MIN_CHARS LIVE LOOSE LogCursor MODULE_TAGS MemorySink
#   OUTPUT OtlpSink READ RECORD_MODES RECORD_MODE_KEY RECORD_OTLP_KEY REPLAY_STATE_KEY ROOT
#   RecordSink Recording RecordingInstaller ReplayDiverged ReplayFinished ReplayState SYSTEM_WALL_MS
#   SinkBatch SinkPost T TaskTap UnencodableValue UnrecordableEffect WatchedRef args_of canonical
#   close_log codec_of consume_at_turn decode_error decode_value deliver_recorded diff_row
#   effect_recorder effect_replayer encode_error encode_value entry_of expected_of intern_json
#   log_break log_break_lost log_check_watched log_emit log_refresh_watched log_release_held
#   log_request log_roll log_settle log_task_ended match_step mode_of open_log otlp_logs_post
#   park_until post_records queue_of recorded_args recorded_run recording_handler replay_driver
#   replay_report replay_task_ended rewrap run_name send_records sink_post spawn_program
#   stall_divergence store_append_post subject_of summarize task_body task_marker wake
# moved-submodule-shim-names doeff_cluster.foundation.record_inbox:
#   MAX_BODY_BYTES MODULE_TAGS RECORD_REPLY_SECONDS RawRequest RecordInbox ReplySlot RequestInbox
#   json_reply
# moved-submodule-shim-names doeff_cluster.foundation.record_log:
#   CURRENT_ANY DECISION Entry JsonValue LEGACY_ANY LIVE LOOSE MODULE_TAGS OUTPUT READ
#   READABLE_FORMATS ROOT Recording ReplayDiverged ReplayFinished RestoredAnswer RestoredValue
#   RunHeader WatchedRef apply_delta canonical decode_value delta_of diff_row encode_value entry_of
#   match_step queue_of read_recording recorded_args resolve_refs resolve_type summarize
# moved-submodule-shim-names doeff_cluster.foundation.wal_store:
#   FSYNC_WINDOW MAX_LOG_BYTES MODULE_TAGS WalStore
"""Moved:``doeff_cluster.foundation`` is now ``doeff_cluster._foundation`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_cluster.foundation…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import importlib.abc
import importlib.util
import sys
from collections.abc import Sequence
from importlib import import_module
from importlib.machinery import ModuleSpec
from types import CodeType, ModuleType

_OLD = "doeff_cluster.foundation"
_NEW = "doeff_cluster._foundation"


class _MovedSubmodules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Answers ``<old>.<sub>`` with the ``<new>.<sub>`` module itself (one module, not a second copy)."""

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if not fullname.startswith(_OLD + ".") or importlib.util.find_spec(self._target(fullname)) is None:
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec: ModuleSpec) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        sys.modules[module.__name__] = import_module(self._target(module.__name__))

    def get_code(self, fullname: str) -> CodeType:
        # python -m <old>.<sub>: run the moved module as __main__.
        target = self._target(fullname)
        source = f"import runpy\nrunpy.run_module({target!r}, run_name='__main__', alter_sys=True)\n"
        return compile(source, f"<moved {fullname}>", "exec")

    @staticmethod
    def _target(fullname: str) -> str:
        return _NEW + fullname[len(_OLD) :]


if not any(isinstance(finder, _MovedSubmodules) for finder in sys.meta_path):
    sys.meta_path.insert(0, _MovedSubmodules())

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_cluster._foundation", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_cluster._foundation")
