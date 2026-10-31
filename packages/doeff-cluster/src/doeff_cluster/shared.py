# moved-submodule-shim: doeff_cluster.shared -> doeff_cluster._shared
# moved-submodule-shim-names doeff_cluster.shared.core.board_rules:
#   BOARD_MAX_TTL_SECONDS MODULE_TAGS board_allows board_ttl_refusal
# moved-submodule-shim-names doeff_cluster.shared.core.capabilities:
#   BodyInvalid CAPABILITY_PATTERN EnvVar HOST_PREFIX MODULE_TAGS POOL_PATTERN POOL_PREFIX
#   capabilities_of capability_refusal effect_needs_problem env_mapping env_vars_of environ_pairs
#   pool_refusal
# moved-submodule-shim-names doeff_cluster.shared.core.clock:
#   EPOCH MODULE_TAGS ONE_MS datetime_of_epoch_ms monotonic_ms_of now_epoch_ms
# moved-submodule-shim-names doeff_cluster.shared.core.declaring:
#   MODULE_TAGS RecordArgument RepoCheckout RuntimeEnvInvalid System checked_declaring_checkout
#   declaring_refusal foundation_needs_refusal
# moved-submodule-shim-names doeff_cluster.shared.core.detached_rules:
#   DETACHED_DEFAULT_LEASE_SECONDS DETACHED_DEFAULT_RETAIN_SECONDS DetachedSubmitAnswer EnvVar
#   GROUP_LIMIT_KEYS GroupLimit MODULE_TAGS SubmitDetached TASK_LIMIT_KEYS TASK_LIMIT_SCOPES
#   TASK_NOTIFY_MAX_LENGTH TaskLimit effect_needs_problem group_limit_of group_limit_refusal
#   group_limit_spec submit_detached_task task_limit_json task_limit_refusal task_limits_json
#   task_limits_of task_limits_refusal task_notify_json task_notify_refusal task_placing_json
#   task_priority_refusal
# moved-submodule-shim-names doeff_cluster.shared.core.due_policy:
#   DueAt DueNever DueNow MODULE_TAGS due_of_instants earlier_of earliest_due
# moved-submodule-shim-names doeff_cluster.shared.core.image_rules:
#   COPIED_ENV FORBIDDEN_ENV INSTALLERS Instruction MODULE_TAGS NPM_INSTALL PACKAGE_ROW PINNED_NPM
#   PYTHON_PACKAGE REASONS_TITLE REASON_LIBRARY REASON_TOOLS Violation apt_packages
#   declared_packages header_of image_contract_violations instructions_of npm_unpinned
# moved-submodule-shim-names doeff_cluster.shared.core.job_rules:
#   CONTENT_KEY JobSpec MODULE_TAGS content_refusal memory_refusal single_host_refusal spec_hash
#   spec_hash_matches
# moved-submodule-shim-names doeff_cluster.shared.core.launch_rules:
#   COORDINATOR_LAUNCH_FIELDS CoordinatorLaunch LaunchEnvMissing LaunchField LaunchFieldKind
#   MODULE_TAGS WORKER_LAUNCH_FIELDS WorkerLaunch coordinator_commit_env_name coordinator_launch_env
#   coordinator_launch_names coordinator_launch_of_env launch_env launch_values read_value
#   shown_value worker_commit_env_name worker_launch_env worker_launch_names worker_launch_of_env
# moved-submodule-shim-names doeff_cluster.shared.core.lease_rules:
#   BodyInvalid FENCE_MARGIN_MS LEASE_MAX_TTL_MS LEASE_OPS LeaseAnswer MODULE_TAGS SEMAPHORE_PREFIX
#   claim drop_holders fence_verdict holder_tokens_prefix holders_shifted lease_full_until
#   lease_holder lease_op lease_timing_refusal live_holders live_instances outside_holders release
#   renew semaphore_key semaphore_write_refusal
# moved-submodule-shim-names doeff_cluster.shared.core.native_wheel:
#   DOEFF_VM_PACKAGE DOEFF_VM_SOURCE ENV_KEY_LENGTH MODULE_TAGS NotReported UV_DROP UvVariable
#   WHEEL_CACHE_ENV WHEEL_REPORT_ENV current_platform reported_built uv_environment wheel_lock
#   wheel_out_dir wheels_root
# moved-submodule-shim-names doeff_cluster.shared.core.promise_wait:
#   promise_or_timeout
# moved-submodule-shim-names doeff_cluster.shared.core.readiness_report:
#   JsonField MODULE_TAGS REASON_KEPT_CHARS ROLE_ACTIVE ROLE_STANDBY ReadinessClaim
#   reported_readiness
# moved-submodule-shim-names doeff_cluster.shared.core.readiness_rules:
#   ENVIRON_NUMBER HANDOFF_TIMEOUT_SECONDS MODULE_TAGS READINESS_KEYS environ_number
#   handoff_timeout_ms positive_integer positive_number readiness_environ_refusal
#   readiness_from_environ readiness_refusal retired_lifetime_ms retired_limit_of
# moved-submodule-shim-names doeff_cluster.shared.core.remote_rules:
#   MODULE_TAGS RemoteJob TaskFailed VersionDiff diffs_text effect_needs_problem failed_from
#   program_sha remote_job version_diffs version_mismatch
# moved-submodule-shim-names doeff_cluster.shared.core.resend:
#   ClusterTiming IDEMPOTENT_DEADLINE_SECONDS MODULE_TAGS
# moved-submodule-shim-names doeff_cluster.shared.core.run_context_rules:
#   JobSpec MODULE_TAGS RunContext RuntimeEnv context_of_environ process_context_environ
#   runtime_env_of_context runtime_env_of_json spec_hash worker_context_environ
# moved-submodule-shim-names doeff_cluster.shared.core.runtime_env:
#   CheckoutRoot CheckoutState EnvVar FileSha256 InvalidKind LocalCheckout LocalPath MODULE_TAGS
#   ProjectOfCheckout PythonProject ReadCheckout RemoteRepo RepoCheckout RepoLocation RuntimeEnv
#   RuntimeEnvInvalid SenderSourceRoot ToolRequirement check_sender_source
#   checked_declaring_checkout checked_repo project_dir runtime_env_of_checkouts url_location
# moved-submodule-shim-names doeff_cluster.shared.core.runtime_env_rules:
#   ENV_KEY_LENGTH EnvFailure EnvFailureKind EnvVar InvalidKind LocalPath MODULE_TAGS NativeWheel
#   PythonProject RETRYABLE_KINDS RUNTIME_ENV_FORMAT RemoteRepo RepoCheckout RepoLocation RuntimeEnv
#   RuntimeEnvInvalid ToolRequirement child_environ_refusal env_failure env_key
#   hyx_runtime_env_XgreaterHthan_signXjson key_material location_material material_digest
#   repo_source_material root_split runtime_env_of_json url_location
# moved-submodule-shim-names doeff_cluster.shared.core.runtime_identity:
#   BytecodeCounts ENV_MARKER_FORMAT IdentityFailureKind MODULE_TAGS ModuleOrigin ProcessFacts
#   ReadRuntimeFacts RepoCommit RootMarker RuntimeEnv RuntimeFacts RuntimeIdentity
#   RuntimeIdentityMismatch check_runtime_identity decode_env decode_marker env_key judge_identity
#   marker_bytecode outside_origins project_dir runtime_env_of_json
# moved-submodule-shim-names doeff_cluster.shared.core.semaphore_handlers:
#   AwaitLeaseFree ClusterSemaphore CreateNamedSemaphore FENCE_PATIENCE_STEP_SECONDS HELD HeldLease
#   LOST LeaseAnswer LeaseLost LeaseOp LeaseStanding MODULE_TAGS STANDBY SemaphoreSession
#   WriteFenced acquire_lease claim_withdrawn cluster_semaphore fence_verdict holder_tokens_prefix
#   leases_fence named_semaphore_local now_epoch_ms release_lease renew_lease standby_divert
# moved-submodule-shim-names doeff_cluster.shared.core.service_back:
#   AwaitRunnersChange AwaitServiceReady MODULE_TAGS RunnersChange RunnersUnreachable
#   RunnersWatchMissing SERVICE_BACK_CHANGE_SECONDS SERVICE_BACK_STATE ServiceReady back_announced
#   promise_or_timeout service_back service_back_within stopped
# moved-submodule-shim-names doeff_cluster.shared.core.service_rules:
#   CallShape Job MODULE_TAGS RecordArgument System callables_in canonical_argument canonical_record
#   describe_identity environ_overlay_refusal foundation_needs_refusal function_reference
#   identity_of job_named
# moved-submodule-shim-names doeff_cluster.shared.core.supplied_spelling:
#   MODULE_TAGS OBJECT_SPELLING REF_SPELLING supplied_spellings
# moved-submodule-shim-names doeff_cluster.shared.core.timing_rules:
#   ClusterTiming MODULE_TAGS ReassignTooEarly SelfStopSpans scaled_timing self_stop_ms
#   timing_outlasts_the_self_stop
# moved-submodule-shim-names doeff_cluster.shared.core.warm_rules:
#   MODULE_TAGS RuntimeEnv WARM_KEY_LENGTH WarmAnswer WarmFailed WarmFailure WarmReady
#   WarmRuntimeEnv WarmState WarmUnreachable WarmWaitExpired effect_needs_problem env_key
#   hyx_warm_state_XgreaterHthan_signXjson warm_key warm_runtime_env warm_state_of_json
#   warm_wait_answer
# moved-submodule-shim-names doeff_cluster.shared.entry.boundary_recorder:
#   HostContract MODULE_TAGS RECORD_MODES RECORD_MODE_KEY RECORD_OTLP_KEY REPLAY_STATE_KEY
#   RecordingInstaller ReplayState RunContext boundary_recorder effect_replayer recording_handler
#   recording_header
# moved-submodule-shim-names doeff_cluster.shared.entry.client_foundation:
#   CoordinatorRoute DetachedSender IDEMPOTENT_DEADLINE_SECONDS RouteCell RouteOptions RuntimeEnv
#   coordinator_route_options detached_cluster now_epoch_ms process_versions route_of
#   with_detached_client
# moved-submodule-shim-names doeff_cluster.shared.entry.cluster_foundation:
#   CONNECT_SECONDS ClusterTiming CoordinatorRoute DetachedSender HOST_CONTRACT
#   IDEMPOTENT_DEADLINE_SECONDS PREFERRED_RECHECK_SECONDS RESEND_PAUSE_SECONDS RouteCell
#   RouteOptions RunContext RuntimeEnv SemaphoreSession ServiceReport TaskSender cluster_handlers
#   cluster_semaphore coordinator_route_options default_actor detached_cluster lease_holder
#   lease_holder_of metrics_http now_epoch_ms readiness_http remote_cluster route_of
#   runtime_env_of_context service_report_of shared_http warm_cluster with_cluster_handlers
# moved-submodule-shim-names doeff_cluster.shared.entry.declare:
#   CONFLICT_REREADS DECLARE_REPLY_SECONDS Declaration GroupLimit MODULE_TAGS RestartPlan
#   RestartRefused ServiceRead ServiceRestarted apply_declaration body_of declare_request
#   group_limit_write group_limit_written needed_programs reread_after_conflict restart_read
#   restart_service service_current service_read service_read_at service_written
#   service_written_rereading
# moved-submodule-shim-names doeff_cluster.shared.entry.deployed_cluster:
#   AwaitJobProcess AwaitReadiness Crash CrashCoordinator DeployedCannotAnswer DeployedCluster
#   JobProcessSeen JobProcessWaitExpired KillWorker MODULE_TAGS ReadinessOf ReadinessWaitExpired
#   Redeclare RestartRefused RestartService RuntimeEnv ServiceFailed ServiceReadiness
#   ServiceRestarted StopCoordinator StopWorker apply_declaration deployed_cluster
#   deployed_cluster_answers job_process_awaited readiness_awaited readiness_read restart_service
#   system_declaration this_process_versions
# moved-submodule-shim-names doeff_cluster.shared.entry.host_reader:
#   HOST_CONTRACT MODULE_TAGS context_from_env host_reader this_process_versions this_program_path
# moved-submodule-shim-names doeff_cluster.shared.entry.image_contract:
#   MODULE_TAGS dockerfile_violations image_contract_violations main
# moved-submodule-shim-names doeff_cluster.shared.entry.macros:
#   defsystem
# moved-submodule-shim-names doeff_cluster.shared.entry.replay_main:
#   MODULE_TAGS RECORD_MODE_KEY REPLAY_STATE_KEY Recording ReplayDiverged ReplayFinished ReplayState
#   main read_lines read_program read_recording replay replay_report replayed
# moved-submodule-shim-names doeff_cluster.shared.entry.run_context_env:
#   MODULE_TAGS RunContext context_from_env context_of_environ this_process_environ
# moved-submodule-shim-names doeff_cluster.shared.entry.service_build:
#   CallShape Declaration EnvVar GroupLimit Job MODULE_TAGS REPLICAS_VALUES RuntimeEnv System
#   UPDATE_FORMS capabilities_of content_refusal describe_identity encode_program
#   environ_overlay_refusal group_limit_refusal group_limit_spec
#   hyx_runtime_env_XgreaterHthan_signXjson identity_of job memory_refusal program_sha
#   readiness_environ_refusal readiness_from_environ readiness_refusal resolve resolve_value
#   single_host_refusal system_declaration system_of
# moved-submodule-shim-names doeff_cluster.shared.intent.checkout_model:
#   CheckoutRoot CheckoutState LocalCheckout MODULE_TAGS ProjectOfCheckout ReadCheckout
#   SenderSourceRoot
# moved-submodule-shim-names doeff_cluster.shared.intent.cluster_control:
#   AwaitJobProcess AwaitReadiness Crash CrashCoordinator FAILED_PHASES JobPhase JobProcessSeen
#   JobProcessWaitExpired KillWorker MODULE_TAGS ReadinessOf ReadinessWaitExpired Redeclare
#   RestartRefused RestartService ServiceFailed ServiceReadiness ServiceRestarted StopCoordinator
#   StopWorker System
# moved-submodule-shim-names doeff_cluster.shared.intent.detached_model:
#   AckDetachedOutbox AwaitDetached AwaitDetachedOutbox AwaitRunnersChange AwaitServiceReady
#   CancelDetached DETACHED_DEFAULT_LEASE_SECONDS DETACHED_DEFAULT_RETAIN_SECONDS DetachedAckAnswer
#   DetachedAwaited DetachedCancelled DetachedEnded DetachedEndedWire DetachedEnvUnavailable
#   DetachedFailed DetachedGroupWire DetachedHeldWire DetachedLimitWire DetachedLost DetachedOutbox
#   DetachedOutboxAcked DetachedOutboxAnswer DetachedOutboxWire DetachedOutcome DetachedPending
#   DetachedQueueAnswer DetachedQueueGroup DetachedQueueReason DetachedRefused DetachedSubmitAnswer
#   DetachedSubmitted DetachedSucceeded DetachedTaskRowWire DetachedTiming DetachedTimingAnswer
#   DetachedUnknown DetachedUnreachable DetachedUnrunnable DetachedVersionMismatch EnvVar GroupLimit
#   MODULE_TAGS OPEN_PHASES OUTBOX_ENDED OUTBOX_KINDS OUTBOX_QUEUED_OVERDUE QueueTaskRowWire
#   ReadDetachedQueue ReadDetachedTiming ReadRunners ReadServices ReleaseDetached RunnerFact
#   RunnersAnswer RunnersChange RunnersChangeAnswer RunnersUnreachable RunnersWatchMissing
#   ServiceFact ServiceListWire ServiceProcessWire ServiceReady ServiceRowSpecWire
#   ServiceRowStatusWire ServiceRowWire ServiceStatusWire ServiceViewWire ServicesAnswer
#   ServicesUnreachable StateQueueWire StateTasksWire SubmitDetached TASK_LIMIT_SCOPES TaskLimit
#   WARMING_PHASE
# moved-submodule-shim-names doeff_cluster.shared.intent.due_model:
#   DueAt DueNever DueNow MODULE_TAGS
# moved-submodule-shim-names doeff_cluster.shared.intent.env_marker_model:
#   BytecodeCounts ENV_MARKER ENV_MARKER_FORMAT FileSha256 MODULE_TAGS TreeCounts
# moved-submodule-shim-names doeff_cluster.shared.intent.job_call_model:
#   CallJob JobAnswer JobField JobRequest JobUnreachable MODULE_TAGS RecordMode RecordSpec
# moved-submodule-shim-names doeff_cluster.shared.intent.job_model:
#   JobPhase JobSpec MODULE_TAGS env_without_commits
# moved-submodule-shim-names doeff_cluster.shared.intent.job_reach_model:
#   JobNoWay JobNotReached JobReached JobRequest JobWay MODULE_TAGS ReachFailure ReachJob RecordMode
#   RecordSpec
# moved-submodule-shim-names doeff_cluster.shared.intent.kube_service_model:
#   EnsureKubeIngress EnsureKubeService KubeIngress KubeService KubeServiceOutcome KubeServicePort
#   KubeServiceRefused KubeServiceType MODULE_TAGS RecordMode RecordSpec
# moved-submodule-shim-names doeff_cluster.shared.intent.launch_model:
#   CoordinatorLaunch DesireCoordinator DesireWorker LaunchLineChange MODULE_TAGS StaticWorkerLaunch
#   WorkerLaunch
# moved-submodule-shim-names doeff_cluster.shared.intent.metrics_model:
#   MODULE_TAGS ReadProcessGauges RecordMode RecordSpec ReportMetrics Unexecuted
# moved-submodule-shim-names doeff_cluster.shared.intent.process_model:
#   AwaitProcessEnded MODULE_TAGS ProcessEnded ProcessWaitExpired
# moved-submodule-shim-names doeff_cluster.shared.intent.protocol:
#   BodyInvalid ClusterTiming CoordinatorStopRequested MODULE_TAGS NextRequests PROTOCOL_FORMAT
#   PlainText Reply Request ServiceReached
# moved-submodule-shim-names doeff_cluster.shared.intent.readiness_model:
#   HANDOFF_TIMEOUT_SECONDS JsonField MODULE_TAGS READINESS_KEYS REASON_KEPT_CHARS RETIREMENT_HOLD
#   RETIREMENT_HOLD_PERMITS ROLE_ACTIVE ROLE_STANDBY ReadinessClaim RecordMode RecordSpec
#   ReportReady Unexecuted
# moved-submodule-shim-names doeff_cluster.shared.intent.record_spec:
#   HandleKind MODULE_TAGS RecordMode RecordSpec Unexecuted
# moved-submodule-shim-names doeff_cluster.shared.intent.remote_model:
#   EnvUnavailable EnvVar MODULE_TAGS RemoteJob RemoteJobFailed TaskFailed TaskOutcome TaskSucceeded
#   UnsendableProgram VersionDiff VersionMismatch
# moved-submodule-shim-names doeff_cluster.shared.intent.run_context:
#   MODULE_TAGS RunContext
# moved-submodule-shim-names doeff_cluster.shared.intent.runtime_env_model:
#   CHILD_PROTOCOL COMMIT_PATTERN ENV_VAR_PATTERN EnvFailure EnvFailureKind EnvVar InvalidKind
#   LocalPath MODULE_TAGS NAME_PATTERN NativeWheel PATH_ENV_SUFFIXES PythonProject
#   RESERVED_ENV_NAMES RESERVED_ENV_PREFIXES RETRYABLE_KINDS RUNTIME_ENV_FORMAT RemoteRepo
#   RepoCheckout RepoLocation RuntimeEnv RuntimeEnvInvalid SECRET_ENV_SUFFIXES SHA256_PATTERN
#   SUPPORTED_CHILD_PROTOCOLS ToolRequirement WITHHELD_ENV_NAMES
# moved-submodule-shim-names doeff_cluster.shared.intent.runtime_identity_model:
#   IdentityFailureKind MODULE_TAGS ModuleOrigin ProcessFacts ReadRuntimeFacts RepoCommit RootMarker
#   RuntimeEnv RuntimeFacts RuntimeIdentity RuntimeIdentityMismatch
# moved-submodule-shim-names doeff_cluster.shared.intent.semaphore_model:
#   AwaitLeaseFree AwaitLeaseVacated ClusterSemaphore CreateNamedSemaphore FENCE_MARGIN_MS HELD
#   HandleKind HeldLease LEASE_MAX_TTL_MS LEASE_OPS LOST LeaseAnswer LeaseLost LeaseOp LeaseStanding
#   MODULE_TAGS RecordMode RecordSpec SEMAPHORE_PREFIX STANDBY WriteFenced
# moved-submodule-shim-names doeff_cluster.shared.intent.service_model:
#   CallShape Declaration GroupLimit Job MODULE_TAGS REPLICAS_VALUES RecordArgument System
#   UPDATE_FORMS
# moved-submodule-shim-names doeff_cluster.shared.intent.shared_model:
#   ANY AnyExpect MODULE_TAGS ReadShared RecordMode RecordSpec Unexecuted WriteShared
# moved-submodule-shim-names doeff_cluster.shared.intent.supplied_model:
#   MODULE_TAGS SuppliedKind SuppliedObjectRef SuppliedRef SuppliedValueUnavailable
#   WorkerFactMissing WorkerFactName
# moved-submodule-shim-names doeff_cluster.shared.intent.warm_model:
#   AwaitWarm MODULE_TAGS ReadWarmState RuntimeEnv WARM_KEY_LENGTH WarmAnswer WarmFailed WarmFailure
#   WarmReady WarmRuntimeEnv WarmState WarmUnreachable WarmWaitAnswer WarmWaitExpired
# moved-submodule-shim-names doeff_cluster.shared.protocol.board_requests:
#   MODULE_TAGS board_read_request board_write_request lease_request lease_vacate_answer
#   lease_vacate_request lease_wait_answer lease_wait_request
# moved-submodule-shim-names doeff_cluster.shared.protocol.checkout_reads:
#   CheckoutRoot CheckoutState FileSha256 MODULE_TAGS ReadCheckout SENDER_SOURCE_DIR
#   SenderSourceRoot checkout_reads checkout_root checkout_state_at file_sha256 git_output
# moved-submodule-shim-names doeff_cluster.shared.protocol.coordinator_reads:
#   FAILED_PHASES JobProcessSeen JobProcessWaitExpired MODULE_TAGS ReadinessWaitExpired
#   ServiceFailed ServiceReadiness WAIT_PROBE_SECONDS answer_body coordinator_commit_of_state
#   failure_of_body job_pids_of job_process_awaited readiness_awaited readiness_of_body
#   readiness_read readiness_wait_answer service_answer state_of
# moved-submodule-shim-names doeff_cluster.shared.protocol.coordinator_route:
#   CoordinatorRoute MODULE_TAGS ROUND_PAUSE_SECONDS RouteCell RouteOptions RouteRefused RouteTurn
#   RouteUnreachable RoutedReply answer_json now_epoch_ms resent_request round_pause_seconds
#   route_of route_order route_used routed_request write_accepted
# moved-submodule-shim-names doeff_cluster.shared.protocol.declaration_requests:
#   CONFLICT_REREADS Declaration GroupLimit MODULE_TAGS RESTART_MARK RestartPlan RestartRefused
#   ServiceRead body_of create_body group_limit_spec group_limit_write needed_programs
#   reread_after_conflict restart_read restarted_spec service_read spec_for_update update_body
# moved-submodule-shim-names doeff_cluster.shared.protocol.detached:
#   AckDetachedOutbox AwaitDetached AwaitDetachedOutbox AwaitProcessEnded AwaitRunnersChange
#   AwaitServiceReady AwaitWarm CancelDetached DetachedAckAnswer DetachedAwaited DetachedCancelled
#   DetachedEnded DetachedEnvUnavailable DetachedFailed DetachedGroupWire DetachedLimitWire
#   DetachedLost DetachedOutbox DetachedOutboxAcked DetachedOutboxAnswer DetachedOutboxWire
#   DetachedOutcome DetachedPending DetachedQueueAnswer DetachedQueueGroup DetachedQueueReason
#   DetachedRefused DetachedSender DetachedSubmitAnswer DetachedSubmitted DetachedSucceeded
#   DetachedTiming DetachedTimingAnswer DetachedUnknown DetachedUnreachable DetachedUnrunnable
#   DetachedVersionMismatch LIVE_JOB_PHASES MODULE_TAGS OPEN_PHASES OUTBOX_POLL_SECONDS
#   PROTOCOL_FORMAT ProcessEnded ProcessWaitExpired ProcessWatch REFUSED_STATUSES ReadDetachedQueue
#   ReadDetachedTiming ReadRunners ReadServices ReadWarmState ReleaseDetached RouteCell RouteOptions
#   RoutedReply RunnerFact RunnersChange RunnersChangeAnswer RunnersUnreachable RunnersWatchMissing
#   RuntimeEnv SERVER_ERROR ServiceFact ServiceListWire ServiceReady ServiceViewWire
#   ServicesUnreachable StateQueueWire StateTasksWire SubmitDetached TaskFailed TaskSucceeded
#   UNSTARTED_JOB_PHASES WARMING_PHASE WARM_UNREACHED_PAUSE_SECONDS WarmAnswer WarmFailed WarmReady
#   WarmRuntimeEnv WarmState WarmUnreachable WarmWaitExpired absent_warm_state answer_json
#   await_cluster await_process_cluster awaited_answer decode_outcome decoded_result
#   detached_cluster detached_flag detached_json detached_path detached_queue_of_state
#   detached_queue_read detached_refusal detached_submit_body detached_submitted
#   detached_timing_of_state detached_timing_read detached_view encode_program env_mapping
#   hyx_runtime_env_XgreaterHthan_signXjson now_epoch_ms outbox_ack_answer outbox_acked
#   outbox_answer outbox_awaited outbox_path outbox_query outbox_response outcome_from_task_outcome
#   outcome_of_view process_watch_step program_put queue_group_of resent_answer resent_request
#   routed_request runner_facts_of_view runners_change_of runners_changed runners_read
#   runners_unreachable service_facts_of_json service_facts_of_view service_readiness
#   service_ready_awaited service_ready_of services_read services_unreachable submit_unreachable
#   task_notify_json task_placing_json unknown_timing_confirmed warm_answer warm_awaited
#   warm_cluster warm_path warm_read warm_request_body warm_server_failure warm_state_of_json
#   warm_unconnected warm_wait_answer warm_written watch_query
# moved-submodule-shim-names doeff_cluster.shared.protocol.inbox:
#   CoordinatorStopRequested InboxQueue MODULE_TAGS NextRequests PlainText Reply ReplyTarget Request
#   ServiceReached StopSignal encoded_reply http_request http_requests raw_requests requests_of
#   stop_flag
# moved-submodule-shim-names doeff_cluster.shared.protocol.job_call_handlers:
#   CONTENT_TYPE CallJob Counterpart JobAnswer JobDestination JobField JobRequest JobUnreachable
#   MODULE_TAGS NO_CONTENT_TYPE PATH_SAFE job_answer_of job_call_handover job_call_sender job_target
#   sent_call
# moved-submodule-shim-names doeff_cluster.shared.protocol.job_reach_handlers:
#   Counterpart JobAnswer JobNoWay JobNotReached JobReached JobRequest JobUnreachable JobWay
#   MODULE_TAGS ReachDestination ReachFailure ReachJob handed_reach job_reach_handover
#   job_reach_sender job_target reach_answer_of reach_failure_of sent_reach way_environ
# moved-submodule-shim-names doeff_cluster.shared.protocol.kube_services:
#   APPLY_CONTENT_TYPE EnsureKubeIngress EnsureKubeService FIELD_MANAGER KUBE_API KubeIngress
#   KubeService KubeServiceOutcome KubeServiceRefused KubeServiceType MODULE_TAGS REFUSAL_BODY_LIMIT
#   REQUEST_TIMEOUT_SECONDS SERVICE_ACCOUNT_TOKEN_FILE kube_headers kube_services_api
#   outcome_of_answer refused service_account_text service_apply_text service_path status_field
#   text_field
# moved-submodule-shim-names doeff_cluster.shared.protocol.meter_report:
#   DEFAULT_REPORT_SECONDS MODULE_TAGS ReportMetrics report_meter_every report_meter_once
#   report_metrics_of with_meter_report
# moved-submodule-shim-names doeff_cluster.shared.protocol.metrics_handlers:
#   MODULE_TAGS ReportMetrics RouteCell RouteOptions ServiceReport metrics_http sent_report
# moved-submodule-shim-names doeff_cluster.shared.protocol.metrics_memory:
#   MODULE_TAGS ReportMetrics metrics_memory
# moved-submodule-shim-names doeff_cluster.shared.protocol.program_codec:
#   BYTES_OPCODES CLOUDPICKLE_DISPATCH GET_OPCODES MODULE_TAGS PUT_OPCODES RemoteJobFailed
#   StrictPickler TEXT_OPCODES TaskFailed TaskSucceeded UnsendableProgram canonical_pickle
#   decode_outcome decode_program encode_outcome encode_program memo_aliases memo_indices pickle_ops
#   portable_code portable_reduction portable_source_path source_module_name
# moved-submodule-shim-names doeff_cluster.shared.protocol.readiness_handlers:
#   MODULE_TAGS ReadinessClaim ReadinessLog ReportReady RouteCell RouteOptions ServiceReport
#   readiness_claims readiness_http reported_readiness sent_report
# moved-submodule-shim-names doeff_cluster.shared.protocol.remote:
#   EnvUnavailable MODULE_TAGS PROTOCOL_FORMAT RemoteJob RemoteJobFailed RouteCell RouteOptions
#   RoutedReply RuntimeEnv TaskFailed TaskSender TaskSucceeded answer_json decode_outcome
#   encode_program hyx_runtime_env_XgreaterHthan_signXjson outcome_of program_put program_sha
#   remote_cluster resent_request routed_request settled_value task_dropped task_submit_body
#   task_submitted task_view wait_outcome
# moved-submodule-shim-names doeff_cluster.shared.protocol.runtime_facts:
#   DECLARED_ENV ENV_MARKER KEY_ENV MODULE_TAGS ModuleOrigin ProcessFacts ReadRuntimeFacts env_value
#   given_runtime_facts marked_root marker_absent marker_text module_origin process_facts
#   process_runtime_facts
# moved-submodule-shim-names doeff_cluster.shared.protocol.service_report:
#   MODULE_TAGS RouteCell RouteOptions RoutedReply RunContext ServiceReport report_request
#   routed_request sent_report service_report_of
# moved-submodule-shim-names doeff_cluster.shared.protocol.shared_handlers:
#   ANY AwaitLeaseFree AwaitLeaseVacated LeaseAnswer LeaseOp MODULE_TAGS ReadShared RouteCell
#   RouteOptions RoutedReply WriteShared answer_json board_read_request board_write_request
#   lease_request lease_vacate_answer lease_vacate_request lease_wait_answer lease_wait_request
#   resent_request routed_request shared_http write_accepted
# moved-submodule-shim-names doeff_cluster.shared.protocol.supplied_values:
#   CopyPlace DIRECTORY_MODE FACT_ENVIRON_NAMES FILE_KEY FILE_MODE HELD_DIR_NAME HeldCopy HeldData
#   HeldWant HoldDone HoldTally KUBE_API MODULE_TAGS NoCopy OBJECT_SPELLING REFUSAL_BODY_LIMIT
#   REFUSED_STATUSES REF_SPELLING REQUEST_TIMEOUT_SECONDS SERVICE_ACCOUNT_CA_FILE
#   SERVICE_ACCOUNT_DIR SERVICE_ACCOUNT_TOKEN_FILE SuppliedKind SuppliedObjectRef SuppliedRef
#   SuppliedValueUnavailable WORKER_SPELLING WorkerFactMissing WorkerFactName copy_place copy_text
#   datetime_of_epoch_ms entries_of_answer held_bytes held_copy_entries held_copy_path held_entries
#   held_wants hold_one hold_supplied hyx_worker_spelledXquestion_markX kube_headers listed_names
#   now_epoch_ms object_answer object_path object_text private_dirs pruned_copies
#   service_account_text status_field supplied_directory supplied_object_ref_of supplied_ref_of
#   supplied_ref_text supplied_setting supplied_value text_field worker_fact written_entries
# moved-submodule-shim-names doeff_cluster.shared.protocol.task_result:
#   CoordinatorRoute MODULE_TAGS PROTOCOL_FORMAT RouteOptions RoutedReply delivered_task_result
#   now_epoch_ms route_failure route_of routed_request task_id_of_job task_result_request
"""Moved:``doeff_cluster.shared`` is now ``doeff_cluster._shared`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_cluster.shared…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import importlib.abc
import importlib.util
import sys
from collections.abc import Sequence
from importlib import import_module
from importlib.machinery import ModuleSpec
from types import CodeType, ModuleType

_OLD = "doeff_cluster.shared"
_NEW = "doeff_cluster._shared"


class _MovedSubmodules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Answers ``<old>.<sub>`` with the ``<new>.<sub>`` module itself (one module, not a second copy)."""

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if not fullname.startswith(_OLD + "."):
            return None
        moved = importlib.util.find_spec(self._target(fullname))
        if moved is None:
            return None
        # The origin is the moved file: python -m <old>.<sub> runs with __file__ and sys.argv[0] naming it (a __main__
        # without a file breaks inspect.getmodule, which Hy's compile and doeff-hy's bytecode guard call).
        return importlib.util.spec_from_loader(fullname, self, origin=moved.origin)

    def create_module(self, spec: ModuleSpec) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        sys.modules[module.__name__] = import_module(self._target(module.__name__))

    def get_code(self, fullname: str) -> CodeType | None:
        # python -m <old>.<sub>: run the moved module's own code as __main__ (a package's __main__, as runpy does). Not a
        # nested runpy.run_module: its outer run leaves a __main__ without a file in sys.modules while the moved Hy module
        # compiles, and inspect.getmodule raises TypeError on it.
        target = self._target(fullname)
        moved = importlib.util.find_spec(target)
        if moved is not None and moved.submodule_search_locations is not None:
            moved = importlib.util.find_spec(target + ".__main__")
        if moved is None or not isinstance(moved.loader, importlib.abc.InspectLoader):
            raise ImportError(f"no code to run for the moved module {target}", name=fullname)
        return moved.loader.get_code(moved.name)

    @staticmethod
    def _target(fullname: str) -> str:
        return _NEW + fullname[len(_OLD) :]


if not any(isinstance(finder, _MovedSubmodules) for finder in sys.meta_path):
    sys.meta_path.insert(0, _MovedSubmodules())

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_cluster._shared", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_cluster._shared")
