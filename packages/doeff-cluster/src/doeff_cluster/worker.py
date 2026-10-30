# moved-submodule-shim: doeff_cluster.worker -> doeff_cluster._worker
# moved-submodule-shim-names doeff_cluster.worker.core.account_token:
#   MODULE_TAGS READ_EVERY_MS STALE_AFTER_MS TokenClaims TokenState token_claims token_read_due
#   token_state
# moved-submodule-shim-names doeff_cluster.worker.core.bake_plan:
#   BAKE_JOBS_CEILING BakeAnswer BakeItem BakeSummary BakeTree IMPORTS_TAG MODULE_TAGS ModuleIndex
#   TreeArgs TreeOutcome bake_answer bake_argv bake_input bake_order closure_scopes closure_step
#   cpu_limit_of imported_modules imported_names imports_entry imports_key_parts imports_of_entry
#   module_index module_name module_place scoped_sources total_line tree_arguments tree_count
#   tree_failures tree_line trees_import_path
# moved-submodule-shim-names doeff_cluster.worker.core.beat_policy:
#   BEAT_LEASE_DIVISOR BEAT_TASK_LEASE_DIVISOR ClusterTiming DEFAULT_LEASE_MS
#   LIFELINE_PERIOD_SECONDS LIFELINE_SLACK_RATIO LIFELINE_STALL_LIMIT_MS MODULE_TAGS
#   WAKE_HOLD_SECONDS WATCH_RETRY_SECONDS WatchKind WatchReading beat_interval_ms heartbeat_due
#   lifeline_due reply_revision watch_reading
# moved-submodule-shim-names doeff_cluster.worker.core.boot_timing:
#   BOOT_EXEC_VAR BOOT_STARTED_VAR BootMarks BootStamp MODULE_TAGS ProcessStartedMs boot_line
#   boot_stamps env_ms process_start_ms read_boot_marks stamps_out_of_order
# moved-submodule-shim-names doeff_cluster.worker.core.code_plan:
#   DEFAULT_IMPORT_ROOTS MARKER MARKER_FORMAT MODULE_TAGS SOURCE_SUFFIXES cache_rel carry_pairs
#   compilable compile_plan imported_names marker_content marker_problem missing_pycs module_name
#   source_of_pyc tree_problem
# moved-submodule-shim-names doeff_cluster.worker.core.code_prepare:
#   CompileSources ImportClosure LinkPycs MODULE_TAGS Note SOURCE_SUFFIXES ScanTree WriteMarker
#   carry_pairs closure_of compile_plan imported_names marker_content marker_text module_name
#   prepare_tree tree_listing tree_problem
# moved-submodule-shim-names doeff_cluster.worker.core.code_rules:
#   CodeLayout MARKER MARKER_FORMAT MODULE_TAGS prepare_script
# moved-submodule-shim-names doeff_cluster.worker.core.drain_client:
#   AskDrain CoordinatorCall DRAIN_DEADLINE_SECONDS DRAIN_INTERVAL_SECONDS DRAIN_TTL_MARGIN_SECONDS
#   MODULE_TAGS await_drained drain_outcome ready_of worker_path worker_ready
# moved-submodule-shim-names doeff_cluster.worker.core.env_prepare:
#   BYTECODE_STAGE BytecodeReport BytecodeTree CHILD_PROTOCOL CompileTrees DiskFree
#   ENV_MARKER_FORMAT EnsureMirror EnsureNativeWheel EnvFailure EnvFailureKind EnvMarker EnvReady
#   FetchCommit FetchState FileSha256 InstallWheels MODULE_TAGS MOUNT_ESCAPE MaterializeTree
#   MirrorReady PrepareNote PrepareRequest PrepareState ProbeImports ProbeReport ReadCgroupMemory
#   ReadEditableRoots ReadHyVersion ReadVolume RepoCheckout RepoMirror RuntimeEnv STAGES
#   SUPPORTED_CHILD_PROTOCOLS Stage StagePart StageStarted StageTime SyncProject SyncReport
#   TreePlaced TreeSource VolumeKind WheelOrigin WheelReady WriteEnvMarker WriteImportRoots
#   absolute_roots build_memory_of built_count bytecode_outcome bytecode_roots bytecode_trees
#   editable_repo_roots env_failure hyx_env_marker_XgreaterHthan_signXjson
#   hyx_runtime_env_XgreaterHthan_signXjson mount_unescaped prepare_env project_dir repo_in
#   repo_roots repo_source_material root_split stage_bytecode stage_disk stage_lock stage_mirrors
#   stage_native stage_probe stage_roots stage_sync stage_trees stage_wheels timing_line tree_source
#   volume_of_mountinfo wheel_out_dir
# moved-submodule-shim-names doeff_cluster.worker.core.env_rules:
#   ANSWER_HEAD_CHARS EnvFailure EnvFailureKind MODULE_TAGS PrepareLimits ReadyAnswer
#   STALL_TAIL_CHARS answer_of_text declared_project launch_order lowered_priority_argv
#   overdue_failure prepare_argv prepare_outcome prepare_request root_project
# moved-submodule-shim-names doeff_cluster.worker.core.env_upkeep:
#   CODE_STORE_UNUSED_SECONDS DEFAULT_BUILD_MEMORY_BYTES DEFAULT_ROOT_BYTES EnvFailure
#   EnvFailureKind KEEP_PER_PROJECT MEMORY_ROOM_RATIO MODULE_TAGS MemoryUnread MemoryUse
#   NEAR_MARGIN_BYTES NEAR_RETURN_BYTES PrepareLimits RootInfo RootsTally SWEEP_EVERY_MS
#   SWEEP_TARGET_RATIO WHEEL_UNUSED_SECONDS WarmRoom build_memory_estimate env_capacity kept_bytes
#   latest_of_project memory_use_of prepare_overdue recent_per_project reclaimable_bytes
#   root_estimate roots_bytes sweep_candidates sweep_choice sweep_due sweep_target sweep_wanted
#   warm_refusal
# moved-submodule-shim-names doeff_cluster.worker.core.heartbeat_rules:
#   CutOff DesiredJobs DesiredUnreadable ENV_KEY_PREFIX JobPhase JobStatus MODULE_TAGS WarmEnv
#   desired_after_silence desired_when_unreachable env_key finished_task_id keep_marks_held
#   kept_when_cut_off runtime_env_of_json silent_ms warm_env_of_row
# moved-submodule-shim-names doeff_cluster.worker.core.invariants:
#   DescendantLife DescendantOutlivedTheStop ENV_KEY_PREFIX ReapJob RetirementSeen SignalJob
#   StartJob WarmChildMark WorldView handoff_keeps_a_ready_writer retiring_process_hears_first
#   stopped_job_leaves_no_descendant warm_child_state_leaves_running_tasks
#   warm_fork_only_before_any_vm warm_fork_uses_its_own_root
# moved-submodule-shim-names doeff_cluster.worker.core.job_cgroup:
#   CGROUP_ROOT MODULE_TAGS PLACE_SCRIPT job_cgroup_procs placed_argv
# moved-submodule-shim-names doeff_cluster.worker.core.launch:
#   CHILD_ENV_ALLOWED CHILD_ENV_PREFIXES CodeLayout JobLaunch JobSpec MODULE_TAGS WITHHELD_ENV_NAMES
#   child_environment env_project_dir job_launch process_context_environ program_file
#   program_file_text project_hy project_venv shim_argv spec_program_file spec_program_name
#   uv_run_environ
# moved-submodule-shim-names doeff_cluster.worker.core.memory_rules:
#   ContainerMemory DOMINANT_SHARE Gathering JobRoot JobSteady JobUse KillUnit MIB MODULE_TAGS
#   MemoryLevel MemoryLimits NodeMemory ProcTree TASK_PREFIX UnitKind cgroup_bytes_of gathering_of
#   hyx_beyond_steadyXquestion_markX hyx_task_rootXquestion_markX meminfo_kib memory_level mib_of
#   node_memory_of pressure_avg10 proc_tree_of row_of spared_steady steady_sizes swarm_need swarm_of
#   units_of_job victim_of
# moved-submodule-shim-names doeff_cluster.worker.core.places:
#   GENERATION_MARK_LENGTH HEX_DIGITS MODULE_TAGS WorkerPlaces warm_dir_of worker_places
# moved-submodule-shim-names doeff_cluster.worker.core.pod_label_rules:
#   JOB_LABEL_PREFIX JOB_LABEL_VALUE JobLabels JobSpec LABEL_NAME LabelChange MODULE_TAGS PodLabel
#   RESYNC_EVERY_MS RETRY_FIRST_MS RETRY_LIMIT_MS job_label_key job_labels_patch job_labels_wanted
#   labels_line labels_sync_due retry_after_ms
# moved-submodule-shim-names doeff_cluster.worker.core.policy:
#   Action CodeState CodeView DeclarationRead ENV_KEY_PREFIX EnvFailure EnvFailureKind ExitNote
#   ForgetProbes ForgetWarmChild HandoffAbandoned JobHold JobNotStarted JobPhase JobRecord JobSpec
#   JobStatus JobStop MODULE_TAGS NotYetRead NoticeJob Outcome PrepareCode PrepareEnv ProbeEntry
#   ProbeState ProbeStatus ProbeView ProcessView RETIRED_MARK RETRYING_HOLDS ReapJob ReleaseLeases
#   RetireJob Retired SignalJob SpecChanged StartHold StartJob StartStep StartWarmChild StopReason
#   StopStage StopWarmChild SweepEnvs Undeclared WarmChildView WarmLaunch WarmStart WorkerPolicy
#   WorldView backoff_from backoff_ms code_key code_of declared_jobs declared_warm desired_of
#   elder_ready elder_waits exit_notes exit_signal forget_probe_actions fork_candidate fork_refusals
#   forks_from_warm_child handoff_actions held_records hyx_kept_when_cut_offXquestion_markX
#   hyx_ran_stablyXquestion_markX hyx_recreatingXquestion_markX in_backoff job_names job_status
#   kept_when_cut_off launch_of mark_refusal memory_given_up noted_holds notice_actions phase_of
#   pinned_env_keys plan plan_job prepare_action prepare_actions prepare_hold probe_actions
#   probe_failure probe_hold probe_in_flight probe_of probe_status probed_job probing_detail
#   process_of ready_followups ready_path ready_tree_step record_after records_after
#   refused_warm_child replace_step retired_actions retired_at_of retired_limit_for retired_name
#   retired_of retired_room_full service_warm_keys spec_hash start_holds start_step statuses
#   stop_actions sweep_actions wanted_notice warm_actions warm_child_actions warm_child_of
#   warm_child_ready warm_child_refused warm_child_step warm_key_of warm_launch warm_mark_clean
#   warm_refusal_failure warm_start warm_stop_step warm_wait_detail
# moved-submodule-shim-names doeff_cluster.worker.core.probe_rules:
#   CodeLayout JobLaunch JobSpec MODULE_TAGS PROBE_DETAIL_CHARS PROBE_MARK PROBE_PROGRAM
#   PROBE_SECONDS ProbeSettle ProbeSettled ProbeStep child_environment env_project_dir probe_command
#   probe_launches probe_reason probe_results probe_settle probe_step probe_targets probe_verdict
#   project_hy uv_run_environ
# moved-submodule-shim-names doeff_cluster.worker.core.program:
#   ACTIONS_TO_PUBLISH AwaitNextTick CutOff DeclarationRead DesiredJobs DesiredUnreadable DueAt
#   DueNever DueNow EnvReport FOLLOWUP_ROUNDS GapMark HEARTBEAT_GAP_LEASE_RATIO HEARTBEAT_GAP_LOG
#   HeartbeatGap JOB_EXIT_LOG JobNotStarted JobPhase MODULE_TAGS ObserveWorld PublishStatus
#   ReadDesired START_HOLD_LOG StartHold SweepEnvs TICK_LAG_LOG TICK_LAG_MS TickLag TickMarks
#   TickSpan UNSETTLED_TICK_LIMIT Undeclared WAKE_DRIVEN_ACTIONS WakeSet WorkerPolicy WorkerState
#   WorkerStopping WorkerUnsettled WorkerWakes WorldView count_unsettled_ticks datetime_of_epoch_ms
#   declared_jobs declared_warm exit_notes fired_not_started heartbeat_gap held_records
#   monotonic_ms_of next_tick_due noted_holds now_epoch_ms plan plan_due ready_followups
#   records_after run_worker silent_ms start_holds statuses sweep_actions tick_lag wakes_with
#   worker_tick
# moved-submodule-shim-names doeff_cluster.worker.core.room_rules:
#   HOST_PREFIX MODULE_TAGS READ_EVERY_MS ROOM_FIELDS RoomReading RoomState TEMPLATE_PROVIDES
#   WorkerRoom announced_provides capabilities_of launch_provides pool_refusal room_line
#   room_of_text room_read_due room_warm_services_of row_pools
# moved-submodule-shim-names doeff_cluster.worker.core.shim_timing:
#   MODULE_TAGS ShimOutlastsTheKill ShimSpans WorkerPolicy shim_deadline_ms
#   shim_ends_before_the_kill shim_spans
# moved-submodule-shim-names doeff_cluster.worker.core.warm_rules:
#   EnvFailure EnvFailureKind JobSpec MODULE_TAGS RuntimeEnv WARM_CHILD_FLAGS WarmChildFlags
#   WarmChildMark WarmChildView WarmLaunch WarmMarkUnreadable WarmPlace WarmStart WorkerPolicy
#   WorldView code_key ended_before_ready ended_refusing fork_candidate forks_from_warm_child
#   mark_refusal project_dir refusals_after runtime_env_of_json service_warm_keys warm_child_argv
#   warm_child_of warm_child_ready warm_child_refused warm_dir_of warm_key_of warm_launch
#   warm_mark_clean warm_place warm_refusal_failure warm_start
# moved-submodule-shim-names doeff_cluster.worker.core.worker_due:
#   CodeState DueAt DueNever DueNow MODULE_TAGS PrepareLimits ProbeState RootsTally SWEEP_EVERY_MS
#   StopStage WakeSet WorkerPolicy WorldView backoff_from backoff_ms beat_due due_after
#   due_of_instants earliest_due fence_due plan_due prepare_stop_due sweep_interval_due wakes_with
#   warm_child_refused
# moved-submodule-shim-names doeff_cluster.worker.core.worker_rules:
#   CodeView ENV_KEY_PREFIX JobSpec MODULE_TAGS OLD_SERVICE_FLAGS RETIRED_MARK code_key probe_args
#   probe_refusal probed_job ready_path retired_name
# moved-submodule-shim-names doeff_cluster.worker.entry.boot_wheel:
#   BootWheel BootWheelFailed HOW_BUILT HOW_STORED HOW_UNREPORTED MODULE_TAGS built_or_stored
#   child_environ ensured_wheel main report_text wheel_in
# moved-submodule-shim-names doeff_cluster.worker.entry.code_prepare:
#   BAKE_PLAN BAKE_PLAN_MODULE BakeSources CODE_STORE CODE_STORE_MODULE DiscardStoreEntry
#   MODULE_TAGS MarkProgress Note POOL_TOOL READ_THREADS ReadSources ReadStoreEntries ScanTree
#   WORKER_CODE WriteMarker WriteStoreEntry bake_trees closure_of_trees closure_reads compile_plan
#   imports_entry_path main marker_content module_at pool_tool_baker prepare_trees progress_marks
#   read_all read_entry_or_none read_source store_entries store_imports stored_imports tree_files
#   tree_problem usable_cpus
# moved-submodule-shim-names doeff_cluster.worker.entry.drain_main:
#   CALL_SECONDS CONNECT_SECONDS ClusterTiming DRAIN_DEADLINE_SECONDS DRAIN_INTERVAL_SECONDS
#   IDEMPOTENT_DEADLINE_SECONDS MODULE_TAGS PREFERRED_RECHECK_SECONDS PROBE_CALL_SECONDS
#   RESEND_PAUSE_SECONDS RouteCell RouteOptions await_drained coordinator_calls leaving_mark_written
#   main now_epoch_ms read_boot route_of worker_ready
# moved-submodule-shim-names doeff_cluster.worker.entry.env_tool:
#   MODULE_TAGS PrepareRequest answer_json env_translation json_file main prepared_once
#   prepared_to_file request_of_json requested_ms
# moved-submodule-shim-names doeff_cluster.worker.entry.job_entry:
#   MODULE_TAGS RemoteJobFailed RunContext TaskFailed TaskSucceeded VersionMismatch context_from_env
#   decode_program decoded_program diffs_text encode_outcome failed_from main parsed_program_row
#   program_row read_program run_probe run_service run_task runtime_env_of_context task_outcome
#   this_process_versions version_diffs
# moved-submodule-shim-names doeff_cluster.worker.entry.line_stamp:
#   Attribute Batch CHUNK_BYTES COORDINATOR_LOG_ENV_NAMES COORDINATOR_PROCESS_JOB_PREFIX CutLines
#   DRAIN_LIMIT_SECONDS Dropped FEED_LIMITS FeedLimits FileFeed JOB_LOG_ATTRIBUTES_VAR
#   JOB_LOG_ENV_NAMES JOB_LOG_OTLP_VAR JOB_LOG_SERVICE JobLogOff JobLogPlan JobLogRefused
#   JobLogShipper JobLogTarget LOGS_PATH MODULE_TAGS NoShipping OpenLine OutputLines
#   POD_NAMESPACE_VAR POD_NAME_VAR Poster ProcessLines QueuedLine RawLines SHIP_LIMITS ShipCounts
#   ShipLimits Shipping Stamped StampedLines StampedStreams Streams WORKER_ATTEMPT_VAR
#   WORKER_INSTANCE_VAR WORKER_JOB_VAR WORKER_LOG_ENV_NAMES WORKER_NAME_VAR WORKER_PROCESS_ATTEMPT
#   WORKER_PROCESS_JOB_PREFIX WriterEnds admitted any_value batch_size coordinator_log_plan
#   cut_lines endpoint_usable extended extra_attributes job_log_plan logs_body process_lines
#   process_log_plan pumped read_some real_poster record_json same_file shipping_of stamp_of stamped
#   stamped_lines stamped_streams worker_log_plan written
# moved-submodule-shim-names doeff_cluster.worker.entry.main:
#   BOOT_EXEC_VAR BOOT_FILE_VAR BOOT_STARTED_VAR CGROUP_DIR_VAR CGROUP_ROOT CONNECT_SECONDS
#   ClusterTiming CodeLayout CodeSettings EnvSettings HOST_CONTRACT HostSettings
#   IDEMPOTENT_DEADLINE_SECONDS JOB_CGROUP_VAR KUBE_API LIFELINE_PERIOD_SECONDS LifelineSettings
#   LinkState MODULE_TAGS MemoryCell MemoryWatchSettings POD_UID_VAR PREFERRED_RECHECK_SECONDS
#   PREPARE_TOOL PodLabelCell PodLabelSettings ProbeSettings RESEND_PAUSE_SECONDS ReassignTooEarly
#   RoomReading RoomState RouteCell RouteOptions SERVICE_ACCOUNT_CA_PATH SERVICE_ACCOUNT_DIR
#   SERVICE_ACCOUNT_TOKEN_FILE SERVICE_CGROUP_VAR SelfStopSpans ShimOutlastsTheKill ShimSpans
#   SuppliedHoldCell SuppliedHoldSettings WARM_SERVICES_VAR WORKER_LOG_ENV_NAMES WarmSettings
#   WorkerPolicy WorkerRoom WorkerState boot_file_written boot_name boot_room boot_warm_services
#   capabilities_of cgroup_dir_environ clock_ticks code_host coordinator_link current_platform
#   env_host event_pause in_cluster_api_routed job_cgroup_placed job_cgroup_placer job_cgroup_procs
#   lease_release leaving_observer lifeline_settled lifeline_turn local_host machine_environment
#   main memory_observer memory_watch_loop monotonic_ms_of no_wakes now_epoch_ms parse_labels
#   pass_env_names passed_environment pod_job_labels pod_label_parts probe_host process_clock
#   process_host process_lines production_handlers read_boot_marks room_of_text
#   room_warm_services_of route_of run_worker shim_ends_before_the_kill shim_spans start_lifeline
#   status_file supplied_hold_loop this_process_versions tick_pauses timing_checked
#   timing_outlasts_the_self_stop warm_host warm_services_of worker_commit_env_name
#   worker_context_environ worker_log_plan worker_on worker_on_watching_memory worker_places
#   worker_watching_memory
# moved-submodule-shim-names doeff_cluster.worker.entry.result_delivery:
#   CONNECT_SECONDS ClusterTiming IDEMPOTENT_DEADLINE_SECONDS MODULE_TAGS PREFERRED_RECHECK_SECONDS
#   RESEND_PAUSE_SECONDS RouteOptions RunContext default_actor deliver_task_result
#   delivered_task_result
# moved-submodule-shim-names doeff_cluster.worker.entry.retirement_notices:
#   AwaitRetirement HOST_CONTRACT HandoffAbandoned MODULE_TAGS Retired next_notice open_notice_box
#   pipe_retirement_notices retirement_of_word stop_reason_word
# moved-submodule-shim-names doeff_cluster.worker.entry.shim:
#   Adopting Adoption Harvest JOB_LOG_ENV_NAMES MODULE_TAGS NotAdopting NoticeRelay NoticeVariable
#   OutputLines PR_SET_CHILD_SUBREAPER PopenSpawner RawLines SWEEP_IDLE_SECONDS SWEEP_PAUSE_SECONDS
#   ShimFlags StopClock awaited become_subreaper child_environ children_of drained exit_status
#   group_killed harvested job_log_plan killed main named_environ notice_variable open_relay
#   parent_of relayed settled shim_code shim_flags signal_wakeups stamped_lines swept watch_parent
#   worker_gone
# moved-submodule-shim-names doeff_cluster.worker.entry.warm_child:
#   CGROUP_REFUSED EntrySpawner JOB_ENTRY MODULE_TAGS NoticeRelay REQUEST_LIMIT_BYTES
#   REQUEST_TIMEOUT_SECONDS Received SESSION_LED START_REFUSED_CODE answered become_subreaper
#   drained entry_main exit_status fitted forked handled job_log_plan main module_named
#   notice_variable open_relay placed_in reaped received_request refused_start replace_environ
#   request_of run_entry run_forked served shim_code stamped_lines start_ticks thread_count
#   write_replacing
# moved-submodule-shim-names doeff_cluster.worker.intent.code_model:
#   CompileSources DEFAULT_IMPORT_ROOTS ImportClosure LinkPycs MODULE_TAGS Note ScanTree WriteMarker
# moved-submodule-shim-names doeff_cluster.worker.intent.drain_model:
#   AskDrain CoordinatorCall MODULE_TAGS
# moved-submodule-shim-names doeff_cluster.worker.intent.env_prepare_model:
#   BytecodeCounts BytecodeReport BytecodeTree CompileTrees DiskFree EnsureMirror EnsureNativeWheel
#   EnvFailure EnvMarker EnvReady FetchCommit FetchState InstallWheels KnownRoot MODULE_TAGS
#   MaterializeTree MirrorReady PrepareNote PrepareRequest PrepareState ProbeImports ProbeReport
#   ROOTS_PTH ReadCgroupMemory ReadEditableRoots ReadHyVersion ReadVolume RepoMirror RuntimeEnv
#   StagePart StageStarted StageTime SyncProject SyncReport TREE_COPY TREE_EXPAND TreePlaced
#   TreeProblem TreeSource VolumeKind WheelOrigin WheelReady WriteEnvMarker WriteImportRoots
# moved-submodule-shim-names doeff_cluster.worker.intent.retirement_model:
#   AwaitRetirement HandoffAbandoned MODULE_TAGS Retired Retirement
# moved-submodule-shim-names doeff_cluster.worker.intent.worker_model:
#   Action AwaitNextTick BootMarks CodeLayout CodeState CodeView CutOff DeclarationRead DesiredJobs
#   DesiredUnreadable DueAt DueNever DueNow EnvDisk EnvFailure EnvReport ForgetProbes
#   ForgetWarmChild HandoffAbandoned HeartbeatSent JobNotStarted JobPhase JobRecord JobSpec
#   JobStarted JobStatus JobStop MODULE_TAGS NotYetRead NoticeJob ObserveWorld Outcome PrepareCode
#   PrepareEnv ProbeEntry ProbeState ProbeStatus ProbeView ProcessStartedMs ProcessView
#   PublishStatus ReadDesired ReapJob ReleaseLeases RetireJob Retired SignalJob SpecChanged
#   StartHold StartJob StartWarmChild StopProgress StopReason StopStage StopWarmChild SweepEnvs
#   TickMarks Undeclared WakeSet WarmChildMark WarmChildView WarmEnv WarmLaunch WarmMarkUnreadable
#   WorkerPolicy WorkerState WorkerStopping WorkerUnsettled WorkerWakes WorldView
# moved-submodule-shim-names doeff_cluster.worker.protocol.code_store:
#   CodeLayout CodeSettings CodeState CodeTimings CodeView DueNever MARKER MODULE_TAGS ObserveCode
#   PREPARE_TOOL PrepareCode TreeCheck WakeSet WorkerWakes cache_checks code_host marker_problem
#   now_epoch_ms prepare_script tree_check tree_listing wakes_with
# moved-submodule-shim-names doeff_cluster.worker.protocol.coordinator_link:
#   BeatCell BootMarks ClusterTiming CoordinatorRoute DeclaredReply DesiredJobs DesiredUnreadable
#   DueAt DueNever DueNow HeartbeatSent LIFELINE_SENT_KEPT LifeCell LinkState MODULE_TAGS
#   PublishStatus RESEND_AFTER_MS ReadDesired RoomReading RoomState RouteCell RouteOptions
#   RoutedReply TokenClaims TokenState WAKE_HOLD_SECONDS WATCH_RETRY_SECONDS WakeSet WatchCell
#   WatchKind WatchReading WorkerRoom WorkerWakes accepted_supplied accepted_tasks accepted_warm
#   answer_json armed_bell beat beat_beside_loop beat_due beat_interval_ms bell_rung boot_line
#   coordinator_link current_platform datetime_of_epoch_ms declared_job_specs declared_reply_of_json
#   desired_after_silence desired_when_unreachable earliest_due env_heartbeat_part fence_due
#   fetched_program fetched_programs finished_task_id heard_silence heartbeat_body heartbeat_due
#   hyx_watchingXquestion_markX keep_marks_held lifeline_due lifeline_settled lifeline_told
#   lifeline_turn link_silence monotonic_ms_of now_epoch_ms polled program_file_text program_sha
#   progress_noted ready_file_written reply_revision room_line room_noted room_of_text room_read_due
#   routed_request send_settled sent_beat silent_ms spec_program_name status_report status_rows
#   task_specs token_claims token_line token_noted token_read_due token_state told_once wakes_with
#   warm_env_of_row watch_loop watch_once watch_params watch_reading watch_told_once with_bell
#   woken_held
# moved-submodule-shim-names doeff_cluster.worker.protocol.declared:
#   DeclaredReply DeclaredReplyMalformed ENV_KEY_PREFIX EnvPlacement JOB_ENTRY JobSpec MODULE_TAGS
#   RuntimeEnv current_platform declared_job_spec declared_job_specs declared_reply_of_json env_key
#   env_placement environ_pairs runtime_env_of_json task_spec task_specs
# moved-submodule-shim-names doeff_cluster.worker.protocol.drain_requests:
#   AskDrain CoordinatorCall MODULE_TAGS RouteCell RouteOptions RoutedReply call_answer
#   coordinator_calls drain_request routed_request worker_path
# moved-submodule-shim-names doeff_cluster.worker.protocol.env_store:
#   CODE_STORE_UNUSED_SECONDS CodeState CodeView DueAt DueNever DueNow ENV_KEY_PREFIX ENV_MARKER
#   ENV_TOOL EnvDisk EnvFailure EnvReport EnvSettings MEMORY_UNMEASURED_LOG MODULE_TAGS
#   MeasuredRoots MemoryUnread MemoryUse ObserveEnvDisk ObserveEnvs PINS_DIR PINS_UNWRITTEN_LOG
#   PRUNE_EVERY_MS PendingEnv PinRecord PrepareEnv PrepareLimits PruneState REPO_ACCESS_TIMEOUT
#   ROOT_NAME_PATTERN ReadyAnswer RemovedRoots RootInfo RootsTally SWEEP_DONE_LOG SWEEP_LOG
#   SWEPT_MARK SweepEnvs SweepMeasuring SweepRemoving WARM_REFUSED_LOG WHEEL_UNUSED_SECONDS
#   WaitingEnv WakeSet WarmAdmission WarmRoom WorkerWakes answer_of_text build_memory_estimate
#   declared_project disk_free earliest_due env_capacity env_host env_report env_root kept_bytes
#   known_roots launch_order launch_prepare launch_waiting measuring_roots memory_use_of modified_ms
#   now_epoch_ms observe_envs others_pins overdue_failure own_pin_file pin_of_text pin_records
#   pins_dir prepare_argv prepare_outcome prepare_overdue prepare_request prepare_stop_due
#   progressed_ms pruned_after read_answer read_marker read_memory read_text_or_empty ready_keys
#   ready_views reclaimable_bytes refreshed_repo_access removing_leftovers root_dirs root_estimate
#   root_infos root_project roots_bytes roots_dir set_aside start_measuring start_removing
#   sweep_answer sweep_candidates sweep_choice sweep_due sweep_interval_due sweep_leftovers
#   sweep_wanted unused_store_entries unused_store_names wakes_with warm_admission warm_refusal
#   wheels_root wrote_pins
# moved-submodule-shim-names doeff_cluster.worker.protocol.env_translation:
#   BytecodeCounts BytecodeReport BytecodeTree CGROUP_DIR CGROUP_MEMORY_EVENTS CHILD_PROTOCOL_PLACES
#   COMPILED_PATTERN CommandResult CompileTrees CopyRefused DETAIL_CHARS DiskFree ENV_MARKER
#   EnsureMirror EnsureNativeWheel EnvFailure EnvFailureKind EnvReady FILE_ANSWER FetchCommit
#   FetchState FileSha256 HY_DIST_PATTERN InstallWheels KILLED_CODE KnownRoot LOCK_STALE_PATTERN
#   MODULE_TAGS MOUNT_TABLE MaterializeTree MirrorReady NETWORK_PATTERN NO_TREE_LINE NotReported
#   OOM_KILL_PATTERN PATHS_PER_COMMAND PREPARED_PATTERN PROBE_PROGRAM PYTHON_PATTERN PathChange
#   PrepareNote PrepareRequest ProbeImports ProbeReport ROOTS_PTH ReadCgroupMemory ReadEditableRoots
#   ReadHyVersion ReadVolume RepoLocation RuntimeEnv StageStarted SyncProject SyncReport TREE_COPY
#   TREE_EXPAND TREE_PATTERN TREE_STAGE TreeCounts TreeDelta TreePlaced TreeProblem TreeSource
#   UNLINKED_MODES UV_DROP VolumeKind WATCH_SECONDS WHEEL_REPORT_ENV WheelOrigin WheelReady
#   WriteEnvMarker WriteImportRoots answer_json archive_into asked_repo_keys asked_text
#   compile_answer compile_argv copied_tree digest16 editable_dirs env_translation expanded_tree
#   fetch_commit file_sha256 files_under finished_root git git_environment hy_dist_version
#   hyx_env_marker_XgreaterHthan_signXjson interpreter_of kind_at link_unchanged listed_url locked
#   materialize memory_killed_of mirror_of nul_words oom_kills_now origin_of_report outcome_result
#   place_paths prepare_env prepared_once prepared_under_lock remove_if_present reported_built
#   reported_counts request_of_json run_watched runtime_env_of_json settled site_packages
#   sync_failure tail_of touch_progress tracked_entries tree_delta url_location uv_command
#   uv_environment uv_variables volume_of_mountinfo wheel_in wheel_lock wheel_of write_replacing
#   written_bytes
# moved-submodule-shim-names doeff_cluster.worker.protocol.heartbeat:
#   CodeState CodeView ENV_KEY_PREFIX JobStatus MODULE_TAGS PROTOCOL_FORMAT announced_provides
#   env_heartbeat_part env_report finished_task_id heartbeat_body status_report status_row
#   status_rows_json
# moved-submodule-shim-names doeff_cluster.worker.protocol.job_cgroup:
#   MODULE_TAGS job_cgroup_placer placed_argv
# moved-submodule-shim-names doeff_cluster.worker.protocol.lease_release:
#   CoordinatorRoute MODULE_TAGS ReleaseLeases RouteCell RouteOptions RoutedReply SEMAPHORE_PREFIX
#   board_rows drop_holders holder_tokens_prefix lease_holder lease_release released_by_board
#   released_leases routed_request
# moved-submodule-shim-names doeff_cluster.worker.protocol.leaving_mark:
#   LEAVING_SUFFIX MODULE_TAGS leaving_mark_read leaving_mark_written leaving_path
# moved-submodule-shim-names doeff_cluster.worker.protocol.leaving_observer:
#   MODULE_TAGS ObserveLeaving leaving_mark_read leaving_observer
# moved-submodule-shim-names doeff_cluster.worker.protocol.memory_observer:
#   MODULE_TAGS MemoryCell MemoryLevel ObserveMemoryKills ObserveMemoryTight memory_observer
# moved-submodule-shim-names doeff_cluster.worker.protocol.memory_watch:
#   ContainerMemory JOB_PEAK_LOG JobMax JobPeak JobSteady JobUse KillUnit LEVEL_UNITS
#   MEMORY_KILL_LOG MEMORY_LEVEL_LOG MEMORY_NO_VICTIM_LOG MEMORY_SWARM_KILL_LOG MEMORY_UNREAD_LOG
#   MEMORY_WINDOW_LOG MODULE_TAGS MemoryCell MemoryKill MemoryLevel MemoryLimits MemoryReading
#   MemoryUnreadable MemoryWatchSettings NodeMemory ObserveJobRoots WindowMarks cgroup_bytes_of
#   datetime_of_epoch_ms guarded memory_level memory_reading memory_watch_loop memory_watch_step
#   merged_maxima mib_or_none node_memory_of noted_peaks noted_window now_epoch_ms raised_peak
#   spared_steady steady_sizes swarm_killed swarm_of text_or_none unit_signalled units_text
#   victim_of
# moved-submodule-shim-names doeff_cluster.worker.protocol.observations:
#   CodeTimings MODULE_TAGS ObserveCode ObserveEnvDisk ObserveEnvs ObserveJobRoots ObserveLeaving
#   ObserveMemoryKills ObserveMemoryTight ObserveProbes ObserveProcesses ObserveWarmChildren
# moved-submodule-shim-names doeff_cluster.worker.protocol.pod_labels:
#   DesiredJobs DesiredUnreadable JobLabels LabelChange MODULE_TAGS OwnPod PodLabel PodLabelCell
#   PodLabelSettings PodListWire PodMetaWire PodWire REFUSAL_BODY_LIMIT REQUEST_TIMEOUT_SECONDS
#   ReadDesired account_text job_labels_patch job_labels_wanted labels_line labels_noted
#   labels_sync_due labels_synced now_epoch_ms outcome_noted own_pod_of patch_body pod_job_labels
#   refusal_of retry_after_ms
# moved-submodule-shim-names doeff_cluster.worker.protocol.probes:
#   CHILD_ENV_ALLOWED CHILD_ENV_PREFIXES CodeLayout DueNever ForgetProbes JobLaunch JobSpec
#   MODULE_TAGS ObserveProbes PROBE_SECONDS ProbeEntry ProbeOutcome ProbeRun ProbeSettings
#   ProbeSettle ProbeState ProbeStep ProbeView ShimSpans WakeSet WorkerWakes finish_probe
#   launch_probe now_epoch_ms probe_command probe_host probe_launches probe_reason probe_refusal
#   probe_results probe_settle probe_step probe_targets shim_argv shim_deadline_ms spec_hash
#   wakes_with
# moved-submodule-shim-names doeff_cluster.worker.protocol.process_clock:
#   MODULE_TAGS ProcessStartedMs now_epoch_ms process_clock process_start_ms
# moved-submodule-shim-names doeff_cluster.worker.protocol.process_host:
#   CHILD_ENV_ALLOWED CHILD_ENV_PREFIXES CodeLayout CutOff DueNever ForkedFrom HandoffAbandoned
#   HostSettings JOB_NOT_STARTED_LOG JobLaunch JobNotStarted JobRoot JobSpec JobStarted MODULE_TAGS
#   NoticeJob ObserveJobRoots ObserveProcesses ProcessView RETIREMENT_NOTICES ReapJob RetireJob
#   Retired SET_ASIDE_CLEARED_LOG SET_ASIDE_DIR SET_ASIDE_FAILED_LOG STOP_TIMING_LOG SetAside
#   SetAsideClearing ShimSpans SignalJob SpecChanged StartJob Started StopAsked StopMoment
#   StopReason StopStage Undeclared WakeSet WarmPlace WorkerStopping WorkerWakes clearing_progressed
#   clearing_running clearing_set_aside job_launch job_log_stem job_work_dir noted_stop now_epoch_ms
#   placed_argv process_host process_instance retirement_line retirement_of_word set_aside_place
#   set_aside_work_dir shim_argv shim_deadline_ms silent_ms_of spec_program_file start_job
#   stop_reason_word tell_retirement wakes_with warm_place work_dir_set_aside
# moved-submodule-shim-names doeff_cluster.worker.protocol.status_file:
#   CodeTimings MODULE_TAGS PublishStatus STATUS_FILE_MODE status_file status_json status_rows_json
#   write_status_file
# moved-submodule-shim-names doeff_cluster.worker.protocol.supplied_hold:
#   HoldTally LinkState MODULE_TAGS SuppliedHoldCell SuppliedHoldSettings hold_supplied now_epoch_ms
#   supplied_hold_loop supplied_hold_step
# moved-submodule-shim-names doeff_cluster.worker.protocol.tick_pauses:
#   AwaitNextTick DueAt DueNever DueNow MODULE_TAGS OtherWake WakeSet WorkerPolicy await_wakes
#   child_ended first_wake monotonic_ms_of now_epoch_ms silent_ms tick_pauses woke_by_bell
# moved-submodule-shim-names doeff_cluster.worker.protocol.tree_files:
#   CompileSources ImportClosure LinkPycs MARKER MODULE_TAGS Note ScanTree WriteMarker closure_of
#   link_pycs marker_text source_texts tree_file_rels tree_files tree_listing
# moved-submodule-shim-names doeff_cluster.worker.protocol.warm_host:
#   CHILD_ENV_ALLOWED CHILD_ENV_PREFIXES DueNever ForgetWarmChild MODULE_TAGS ObserveWarmChildren
#   StartWarmChild StopProgress StopStage StopWarmChild WARM_CHILD_FLAGS WakeSet WarmChildMark
#   WarmChildView WarmLaunch WarmMarkUnreadable WarmPlace WarmSettings WorkerWakes log_tail
#   now_epoch_ms observed_warm_child ready_mark_of refusals_after start_warm_child wakes_with
#   warm_child_argv warm_host warm_log_path warm_place
# moved-submodule-shim-names doeff_cluster.worker.protocol.worker_wakes:
#   DueNever MODULE_TAGS WakeSet WorkerWakes no_wakes
# moved-submodule-shim-names doeff_cluster.worker.protocol.world:
#   EnvDisk MODULE_TAGS ObserveCode ObserveEnvDisk ObserveEnvs ObserveLeaving ObserveMemoryKills
#   ObserveMemoryTight ObserveProbes ObserveProcesses ObserveWarmChildren ObserveWorld WorldView
#   local_host
"""Moved:``doeff_cluster.worker`` is now ``doeff_cluster._worker`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_cluster.worker…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import importlib.abc
import importlib.util
import sys
from collections.abc import Sequence
from importlib import import_module
from importlib.machinery import ModuleSpec
from types import CodeType, ModuleType

_OLD = "doeff_cluster.worker"
_NEW = "doeff_cluster._worker"


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

    runpy.run_module("doeff_cluster._worker", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_cluster._worker")
