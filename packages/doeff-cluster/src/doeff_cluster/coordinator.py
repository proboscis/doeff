# moved-submodule-shim: doeff_cluster.coordinator -> doeff_cluster._coordinator
# moved-submodule-shim-names doeff_cluster.coordinator.core.api_policy:
#   ALIVE_MARK_MS BoardEntryView BoardRead BodyInvalid BodyMalformed BodyUnreadable COORDINATOR
#   ClusterNaming ClusterState ClusterTiming DeploymentSeen DeploymentUnreadable DueAt DueNever
#   DueNow ErrorReply Fault HandoffPhase LegacyJobs MODULE_TAGS OUTBOX_ENDED PlainText REACH_WAIT_MS
#   ROLLOUT_ACTOR Refused Reply Request RequestBody RolloutStatus RolloutTarget SEMAPHORE_PREFIX
#   STALL_SHIFTED_FIELDS STALL_UNSHIFTED_FIELDS ServiceBody Stall StateReply StepPace
#   TERMINAL_PHASES TargetView TaskDropped absorb_stopping absorb_task_result action_due
#   advance_drains alive board_write board_written cancel_detached cancel_drain clocks_shifted
#   create_needs_override create_resource delete_resource deployment_owners deployments_to_follow
#   detached_read detached_reply drains_view drift_status earliest_due events_view
#   format_version_refusal get_resource handoff_due heartbeat_reply holders_shifted lease_write
#   legacy_put_jobs lift_needs_overrides list_resources liveness_due loose_actor mark_alive
#   metrics_text needs_override_due note_liveness note_unplaced other_generation_boot outbox_ack
#   outbox_read placement_due plan_rollouts poll_task program_read program_write quiet_heartbeat
#   ready_instances reconcile record_action record_metrics record_readiness refuse
#   register_heartbeat release_detached remember_keep_marks request_drain require_actor respond
#   respond_board respond_legacy respond_observations respond_resources respond_stores respond_tasks
#   respond_workers resume_after_downtime rollout_step rollout_targets running_process scale_service
#   service_readiness service_stopped settle shift_clocks stall_of stamp stamp_alive state_view
#   submit_detached submit_task superseded_worker_view sweep_due sweep_programs target_key
#   target_view task_due tick tick_due unknown_request unreached_shifted update_resource valid_actor
#   warm_read warm_write watch_handoffs worker_view
# moved-submodule-shim-names doeff_cluster.coordinator.core.cluster_policy:
#   ACCEPTED_FORMATS ADOPTABLE_PHASES BOARD_MAX_BYTES BOARD_MAX_ROWS BOARD_MAX_VALUE_BYTES
#   BoardConflict BoardRefused BoardRow BoardTally BoardUsage BoardWrite BoardWritten BodyInvalid
#   ClusterJob ClusterState ClusterTiming DETACHED_TERMINAL DueAt DueNever DueNow ENV_RETRIES
#   EnvFailed EnvsReport ErrorReply GenerationOrder GroupHeld GroupLimit GroupLimitView
#   HANDOVER_SETTLED_PHASES HANDOVER_STALL_MS HandoffPhase HandoverBlocker HeartbeatBody
#   HeartbeatReply IMAGE_FOLLOW_KEYS JOB_ENTRY JobSpec KeepMark KnownExit LIVE_PHASES LeaseBody
#   MAX_EVENTS MODULE_TAGS NodeLabelsSeen OLD_RUN_KEYS PLACED_PHASES PROGRAM_GRACE_MS PROGRAM_SHA
#   Placement RETIRED_BOOTS_KEPT RETIREMENT_HOLD Request RuntimeEnvInvalid SEMAPHORE_PREFIX
#   ServiceView StateView StatusRow StatusView SupersededRun TASK_MAX_LEASE_SECONDS TASK_MAX_OPEN
#   TaskAccepted TaskBody TaskLimit TaskMissing TaskOffer TaskProgress TaskRecord TaskResultBody
#   TaskResultTaken TaskUnplacedKind UnplacedKind UnplacedView WarmOffer WorkerBack WorkerGone
#   WorkerInfo WorkerLoad WorkerReport WorkerView absorb_boot absorb_detached_report
#   absorb_env_failure absorb_superseded_heartbeat absorb_task_reports absorb_task_reports_into
#   absorb_task_result active_jobs adopt_running_detached adopted_over adopted_task alive
#   background_reserve_of board_allows board_capacity_refusal board_changes board_tally
#   board_tally_of board_ttl_refusal board_usage board_without_generation_holders board_write
#   board_written boot_at_of can_run_task can_take capabilities_of child_environ_refusal cold_starts
#   cold_starts_written component_versions_of content_refusal declared_job_from_json
#   declared_runtime_env derived_capabilities draining_workers drop_holders due_of_instants
#   durable_changed effective_needs elder_held eligible end_detached end_env_failed env_key
#   env_ready_on env_room_on environ_pairs environ_refusal foreground_reserve_of
#   forget_silent_workers format_version_refusal fresh_task_prefix generation_holder_prefixes
#   generation_order group_limit_views handover_blockers handover_pending handover_runs
#   handover_since handover_waiting heard_tasks heartbeat_reply held_placements hold_limits
#   holder_tokens_prefix holders_dropped hyx_held_taskXquestion_markX hyx_json_arrayXquestion_markX
#   hyx_json_objectXquestion_markX hyx_movableXquestion_markX hyx_text_mapXquestion_markX
#   identity_hash int_field job_from_json job_to_json jobs_for jobs_placed keep_mark_of keep_marked
#   known_exits_after lease_holder lease_op lease_write limit_holdings limit_slot limits_misfit
#   limits_undeclared_refusal live_instances liveness_deadline liveness_due liveness_moves
#   liveness_now load_of memory_misfit memory_misfits memory_refusal memory_room_of misfit_reason
#   missing_needs named_capabilities needs_named needs_refusal nodes_to_follow note_liveness
#   note_unplaced optional_text_field other_generation_boot place_jobs place_tasks placeable
#   placed_claims poll_task program_refs program_row_refusal program_versions promote_prepared
#   promote_prepared_into put_row raw_entry_refusal readiness_refusal reconcile register_heartbeat
#   released_keep_marks remember_keep_marks renew_detached renew_detached_into request_needs
#   required_field resource_version_of retired_after retired_lifetime_ms retired_limit_of
#   retired_with root_key_on rows_with_known_exits running_names runtime_env_of_json
#   runtime_env_refusal runtime_env_value_refusal same_boot scan_rows semaphore_key
#   semaphore_write_refusal service_rows settle_detached silent_names single_host_refusal
#   spec_of_declaration state_view status_row_to_json still_live_somewhere submit_task
#   superseded_boot superseded_reply superseded_runs_after supplied_for supplied_spellings
#   sweep_board sweep_drains sweep_due sweep_keep_marks sweep_warms task_body_refusal task_due
#   task_environ_refusal task_finished task_id task_lapse_at task_limits_of task_limits_refusal
#   task_misfit task_number task_priority_refusal task_room_of task_split task_unplaced_text
#   task_writes tasks_for tasks_placed tools_cover tools_satisfy unplaceable_phase unplaced_jobs
#   unplaced_kind unplaced_memory unplaced_text unplaced_view unplaced_views value_size
#   versions_note wait_lapse_at warms_for with_derived_capabilities worker_report written_value
# moved-submodule-shim-names doeff_cluster.coordinator.core.cluster_rules:
#   ACCEPTED_FORMATS BodyInvalid ComponentVersion MODULE_TAGS component_versions_of
#   format_version_refusal int_field required_field
# moved-submodule-shim-names doeff_cluster.coordinator.core.coordinator_invariants:
#   DrainWindow JobNeeds JobProcess OverCapacity PlacementSeen ProcessSpan ReserveTaken RevisionDrop
#   RevisionRead RunLimit ServiceVersionDrop ServiceVersionRead SettledSpan SpanOverlap
#   StoppedGeneration StrandedJob TaskCall TaskPlacementSeen WorkerAbility WorkerCapacity
#   WorkerExclusive WorkerGone WorkerProbe WorkerReserve acknowledged_values_survive
#   acknowledged_writes_survive alive_only_while_reachable declared_services_survive
#   exclusive_workers_take_only_their_jobs jobs_stay_out_of_the_task_reserve moves_to_a_live_worker
#   no_new_place_while_draining one_place_per_job placed_only_where_eligible
#   places_only_on_reachable ran_only_where_eligible revision_never_goes_back
#   running_within_capacity runs_within_their_limit service_versions_never_go_back
#   stopped_generation_gets_no_new_task tasks_answered_in_time
# moved-submodule-shim-names doeff_cluster.coordinator.core.detached_policy:
#   ClusterState ClusterTiming DETACHED_DEFAULT_LEASE_SECONDS DETACHED_DEFAULT_RETAIN_SECONDS
#   DETACHED_MAX_KEY_LENGTH DETACHED_MAX_LEASE_SECONDS DETACHED_MAX_RECORDS
#   DETACHED_MAX_RETAIN_SECONDS DETACHED_TERMINAL DetachedCancelled DetachedEnded DetachedOutbox
#   DetachedOutboxAcked DetachedProgress DetachedReleased DetachedSubmitted DetachedUnknown
#   DetachedWarming ErrorReply MODULE_TAGS OPEN_PHASES OUTBOX_ENDED OUTBOX_QUEUED_OVERDUE
#   OUTBOX_RETAIN_MS QUEUED_OVERDUE_SUFFIX Reply TASK_MAX_OPEN TaskBody TaskRecord WARMING_PHASE
#   cancel_detached detached_read detached_view end_detached ended_of environ_pairs
#   format_version_refusal key_refusal limits_undeclared_refusal needs_named outbox_ack outbox_noted
#   outbox_read overdue_of program_versions queued_noticed release_detached
#   runtime_env_value_refusal scan_rows seconds_refusal submit_detached task_body_refusal
#   task_by_key task_id task_limits_of task_limits_refusal task_notify_refusal task_priority_refusal
# moved-submodule-shim-names doeff_cluster.coordinator.core.drain_policy:
#   ClusterJob ClusterState ClusterTiming DRAIN_DEFAULT_TTL_SECONDS DRAIN_MAX_TTL_SECONDS Drain
#   DrainBody DrainPhase DrainProgress LIVE_PHASES MAX_EVENTS MODULE_TAGS Placement
#   STOPPING_DRAIN_ACTOR WorkerDrainView absorb_stopping advance_drains alive can_take cancel_drain
#   detached_rows drain_view draining_workers drains_view effective_needs eligible handover_pending
#   live_rows load_of move_target other_generation_boot refuse request_drain scan_rows
#   service_readiness superseded_leaving superseded_worker_view surge_holds worker_view
# moved-submodule-shim-names doeff_cluster.coordinator.core.failover_policy:
#   DownSpike FailoverWorker HOST_PREFIX JobTakers KeptApart MODULE_TAGS ResidentJob apart_refusals
#   failover_refusals hyx_per_machineXquestion_markX memory_refusal place_refusal placeable spike_of
#   takers_of
# moved-submodule-shim-names doeff_cluster.coordinator.core.failover_room:
#   ExtraProvides FailoverWorker MODULE_TAGS RoomReading RoomState WorkerInfo announced_provides
#   launch_provides room_file_workers room_of_text room_row_names
# moved-submodule-shim-names doeff_cluster.coordinator.core.handoff_policy:
#   ClusterJob ClusterState ClusterTiming HandoffPhase HandoffWatch JobSpec LIVE_PHASES MODULE_TAGS
#   carrier_rows carrier_stale_from declaration_fingerprint handoff_deadline handoff_due
#   handoff_timeout_ms job_to_json last_refusal new_generation_live next_watch readiness_due
#   retired_live service_readiness spec_hash spec_hash_matches watch_handoffs
# moved-submodule-shim-names doeff_cluster.coordinator.core.metrics_policy:
#   ClusterState ClusterTiming MAX_NAMES METRICS_STALE_MS MODULE_TAGS MetricsBody MetricsPayload
#   MetricsReport NAME_CHARS PLACED_PHASES ReportOrigin add_sample add_service_metrics board_usage
#   checked_metrics current_metrics current_report escape_label handover_of
#   hyx_metric_nameXquestion_markX hyx_numberXquestion_markX keep_report labels_text
#   last_metrics_age metrics_refusal metrics_report metrics_text number_text placed_senders
#   record_metrics refuse render_families report_origin running_process scan_rows service_readiness
#   unplaced_jobs
# moved-submodule-shim-names doeff_cluster.coordinator.core.program:
#   AnnotateDeployment BodyUnreadable ClusterNaming ClusterState ClusterTiming CoordinatorFault
#   CoordinatorStopRequested DeploymentUnreadable DueAt DueNever DueNow ErrorReply Fault
#   FollowDeployments FollowNodes HEARTBEAT_LAG_LOG HEARTBEAT_LAG_MS HeardBeat HeartbeatBody
#   HeartbeatLag KubeUnavailable LagSpan MODULE_TAGS NextRequests NodeLabelsUnreadable REACH_LOG
#   ROLLOUT_ACTOR ReadBody Reply Request STALL_LOG SaveState ScaleDeployment ServiceReached Stall
#   StepMarks StepPace WatchAnswer WatchRefusal WatchStep Watcher after_step announce_liveness
#   announced_aside clocks_shifted coordinator_step count_unsettled datetime_of_epoch_ms
#   deployment_observations deployments_to_follow fault_reply heartbeat_lag lap_ms liveness_moves
#   liveness_now mark_alive monotonic_ms_of next_wake node_observations nodes_to_follow
#   note_heartbeat_lags note_liveness now_epoch_ms outbox_noted plan_rollouts planned_wake_ms
#   queued_noticed reach_noted reach_resumed readable_body record_action release_watchers
#   request_reply respond rollout_tick run_coordinator scale_service settle_watch stall_of
#   stall_resumed stamp stamp_alive tick unreached_shifted wait_seconds watch_answer_body watch_of
#   with_derived_capabilities
# moved-submodule-shim-names doeff_cluster.coordinator.core.program_policy:
#   ClusterState ErrorReply PROGRAM_GRACE_MS PROGRAM_KEY PROGRAM_MAX_BYTES ProgramBody ProgramRow
#   ProgramStored program_read program_refs program_sha program_write sweep_programs
# moved-submodule-shim-names doeff_cluster.coordinator.core.resource_policy:
#   AUDIT_PER_KIND AuditEvent BLOCKER_DETAIL_CHARS BodyInvalid COORDINATOR ClusterJob ClusterState
#   ClusterTiming ErrorReply EventsView GroupLimit HANDOVER_STALL_MS HandoffPhase HandoverBlocker
#   JobPhase KINDS LEGACY_OWNER LegacyJobRow LiveProcess MIGRATION MODULE_TAGS MetricsBody
#   MetricsReport NEEDS_OVERRIDE_KEYS NEEDS_OVERRIDE_MAX_SECONDS NEEDS_OVERRIDE_REASON_CHARS
#   NeedsOverride NotReadyKind ObservedDeployment PROCESS_PHASES Placement REPORTS_KEPT
#   ReadinessBody ReadinessClaim ReadinessReport Refused RefusedJob ReportOrigin ResourceBody
#   ResourceList ResourceMeta ResourceView RolloutObserved RolloutRow RolloutStatus RolloutTarget
#   RowConflict ServiceBody ServiceObserved StatusRow TERMINAL_PHASES TaskObserved TaskRecord
#   UnplacedKind VersionState VersionVerdict WorkerInfo WorkerObserved WorkerReport active_jobs
#   active_rollouts_touching adopt_legacy alive blocker_json capabilities_of carrier_stale_from
#   changed_fields check_version checked_group_limit create_needs_override create_resource
#   current_report delete_resource dirty_keys effective_needs events_view get_resource
#   group_limit_of group_limit_refusal group_limit_row group_limit_spec handoff_timeout_ms
#   handover_blockers handover_of handover_since int_field job_status_row job_to_json keep_mark_of
#   keep_report key_of last_refusal legacy_put_jobs lift_needs_overrides list_resources
#   live_processes liveness_deadline moved_names needs_override_due needs_override_row
#   needs_override_spec needs_overrides_lifted needs_satisfiable not_ready_verdict not_ready_version
#   observed_of phase_named phase_version placeable placed_senders placement_rewrite_refusal
#   readiness_due readiness_refusal readiness_report ready_instance ready_instances record_readiness
#   refuse refused_row report_expired_from report_matches report_origin reported_readiness
#   require_actor resource_version_of resource_view rollout_row rollout_spec_to_json
#   rollout_status_to_json rollout_targets row_of running_process service_readiness service_row
#   service_rows service_spec service_stopped service_stopped_due short_value snapshot spec_hash
#   spec_hash_matches split_key stamp status_row_names still_live_somewhere target_key task_row
#   trim_audit unplaced_kind unplaced_memory unplaced_not_ready unplaced_text unplaced_view
#   unreported_until update_resource valid_actor validate_rollout_spec version_state warm_until
#   warms_for worker_row
# moved-submodule-shim-names doeff_cluster.coordinator.core.rollout_policy:
#   BodyInvalid DEFAULTS DeploymentSeen DeploymentUnreadable HISTORY_LIMIT MODULE_TAGS
#   RETRY_FIRST_MS RETRY_MAX_MS RolloutDrift RolloutHistory RolloutRow RolloutSpec RolloutStatus
#   RolloutStuck RolloutTarget STATUS_NAMES TERMINAL_PHASES TargetView action_due action_identity
#   action_retry_from deployment_owners drift_status ensure_replicas enter fail_after_from ms_past
#   ms_reached new_replicas observed_from ready_timeout_from retry_delay_ms rollback_late_from
#   rollout_phase_due rollout_spec_to_json rollout_status_from_json rollout_status_to_json
#   rollout_step rollout_targets scale shift_clocks spec_seconds stop_timeout_from target_key
#   target_to_json validate_rollout_spec validate_target
# moved-submodule-shim-names doeff_cluster.coordinator.core.wake_policy:
#   ClusterNaming ClusterState ClusterTiming CoordinatorUnsettled DueAt DueNever DueNow MODULE_TAGS
#   TERMINAL_PHASES UNSETTLED_STEP_LIMIT after_step all_waiting_unchanged count_unsettled
#   earliest_due lease_full_at next_wake planned_wake_ms readiness_due rollout_due rollout_phase_due
#   rollout_targets service_stopped_due tick_due wait_seconds watchers_due
# moved-submodule-shim-names doeff_cluster.coordinator.core.warm_policy:
#   BOARD_MAX_TTL_SECONDS ClusterState ClusterTiming ErrorReply MODULE_TAGS RuntimeEnvInvalid
#   WarmBody WarmEntry WarmFailure WarmState alive draining_workers needs_named placeable
#   root_key_on runtime_env_of_json tools_cover warm_key warm_read warm_view warm_write
# moved-submodule-shim-names doeff_cluster.coordinator.core.watch_policy:
#   ClusterState ClusterTiming HeartbeatReply Request WatchAnswer WatchRefusal WatchStep Watcher
#   all_waiting_unchanged heartbeat_reply hyx_lease_vacatedXquestion_markX lease_full_at
#   lease_full_until lease_row outside_holders query_revision query_seconds ready_instances
#   same_view semaphore_key settle_watch watch_answer_body watch_deadline watch_of worker_mark
# moved-submodule-shim-names doeff_cluster.coordinator.entry.handler_sets:
#   KubeMemory MODULE_TAGS MemoryFollows MemoryWalStore NOTICE_SOURCE RequestInbox RequestQueue
#   StopState WORKER_NOTICE_ROUTES WalStore coordinator_faults durable_states emulated_handlers
#   http_requests kube_memory memory_notices production_handlers queued_requests redis_notices
#   reply_bodies request_bodies stop_flag wal_store
# moved-submodule-shim-names doeff_cluster.coordinator.entry.main:
#   ACCEPTED_FORMATS COORDINATOR_LOG_ENV_NAMES ClusterState ClusterTiming DurableStore
#   KubeUnavailable LOG_END_SECONDS MODULE_TAGS RequestInbox SHIP_LIMITS StopState WalStore
#   adopt_legacy board_file_rows coordinator_commit_env_name coordinator_log_plan durable_checkpoint
#   durable_exists durable_load durable_persist fresh_task_prefix full_kv kube_api kube_available
#   kube_client kube_unavailable legacy_key_moves legacy_state load_state log_environment main
#   memory_notices naming_from_json now_epoch_ms process_lines production_handlers redis_notices
#   resume_after_downtime resume_writes run_coordinator running_commit state_from_json state_from_kv
#   state_on_start stop_on_signals with_running_commit
# moved-submodule-shim-names doeff_cluster.coordinator.intent.cluster_model:
#   ACCEPTED_FORMATS AuditEvent BoardConflict BoardEntryView BoardRead BoardRefused BoardRow
#   BoardTally BoardUsage BoardWritten ClusterJob ClusterNaming ClusterObservations ClusterState
#   ClusterTiming ComponentVersion CoordinatorFault DeploymentReading DeploymentSeen
#   DeploymentUnreadable DetachedCancelled DetachedEnded DetachedOutbox DetachedOutboxAcked
#   DetachedProgress DetachedReleased DetachedSubmitted DetachedUnknown DetachedWarming Drain
#   DrainPhase DrainProgress DurationRow ENDED_PHASES EnvFailed ErrorReply EventsView Fault
#   GenerationOrder GroupHeld GroupLimit GroupLimitView HandoffPhase HandoffWatch HandoverBlocker
#   HeartbeatReply JobSpec KeepMark KnownExit LegacyJobRow LegacyJobs LiveProcess MODULE_TAGS
#   MetricsReport NeedsOverride NodeLabelsSeen NodeLabelsUnreadable NotReadyKind ObservedDeployment
#   PLACED_PHASES Placement ProgramRow ProgramStored ReadinessReport RefusedJob ReportOrigin Request
#   ResourceList ResourceMeta ResourceView RolloutDrift RolloutHistory RolloutObserved RolloutRow
#   RolloutSpec RolloutStatus RolloutStuck RolloutTarget RowConflict SaveState ServiceBody
#   ServiceObserved ServiceView StateReply StateView StatusRow StatusView SupersededRun TargetView
#   TaskAccepted TaskDropped TaskLimit TaskMissing TaskObserved TaskOffer TaskProgress TaskRecord
#   TaskResultTaken TaskUnplacedKind UnplacedKind UnplacedView VersionState VersionVerdict WarmEntry
#   WarmOffer WatchAnswer WatchRefusal WatchStep Watcher WorkerDrainView WorkerInfo WorkerLoad
#   WorkerObserved WorkerReport WorkerView
# moved-submodule-shim-names doeff_cluster.coordinator.intent.due_model:
#   CoordinatorUnsettled MODULE_TAGS Stall StepPace
# moved-submodule-shim-names doeff_cluster.coordinator.intent.failover_model:
#   DownSpike ExtraProvides FailoverWorker JobTakers KeptApart MODULE_TAGS ResidentJob UPDATE_FORMS
#   WorkerInfo
# moved-submodule-shim-names doeff_cluster.coordinator.intent.kube_model:
#   AnnotateDeployment FollowDeployments FollowNodes KubeUnavailable MODULE_TAGS ScaleDeployment
# moved-submodule-shim-names doeff_cluster.coordinator.intent.request_bodies:
#   BoardWireBody BoardWrite BodyMalformed BodyUnreadable DrainBody DurationRow EnvFailedRow
#   EnvsReport HeartbeatBody LeaseBody LegacyJobsBody MODULE_TAGS MetricsBody MetricsPayload
#   ProbeRow ProgramBody ReadBody ReadinessBody RequestBody ResourceBody StatusRow TaskBody
#   TaskResultBody WarmBody
# moved-submodule-shim-names doeff_cluster.coordinator.intent.worker_notices:
#   MODULE_TAGS WorkerBack WorkerGone
# moved-submodule-shim-names doeff_cluster.coordinator.protocol.cluster_json:
#   ClusterNaming DetachedEnded ENDED_PHASES HandoffPhase HandoffWatch MODULE_TAGS OUTBOX_ENDED
#   POOL_PREFIX RETIRED_NAMING_FIELDS TaskRecord capabilities_of component_versions_of
#   ended_from_json ended_to_json environ_pairs handoff_watch_from_json naming_from_json
#   old_limits_reason old_task_row_reason stored_bool stored_int stored_items stored_limits
#   stored_optional_dict stored_optional_int stored_optional_str stored_str task_limits_json
#   task_limits_of task_limits_refusal task_record_from_json task_record_to_json
# moved-submodule-shim-names doeff_cluster.coordinator.protocol.durable_kv:
#   BOARD BoardRow ClusterState DRAIN Drain GROUP_LIMIT HANDOFF KEEP KeepMark LEGACY_PLACEMENT
#   MODULE_TAGS NEEDS_OVERRIDE OUTBOX PLACEMENT PROGRAM Placement SOURCE_GROUPS SUPERSEDED SURGE
#   SourceGroup Sources SupersededRun WARM WorkerInfo as_stored audit_event_from_json
#   audit_event_to_json board_changes board_entry board_tally_of built_pair component_versions_of
#   counter_json durable_delta durable_kv durable_sources ended_from_json ended_to_json full_kv
#   group_limit_from_json group_limit_spec handoff_watch_from_json job_from_json job_to_json
#   legacy_key_moves needs_override_from_json needs_override_spec program_row_from_json
#   program_row_to_json read_each read_service_rows resource_meta_from_json resource_meta_to_json
#   resume_writes rollout_row_from_json rollout_row_to_json same_parts state_from_kv table_group
#   table_sources table_sources_keyed task_record_from_json task_record_to_json value_size
#   warm_entry_from_json warm_entry_to_json worker_capabilities_of worker_generations_from_json
#   worker_generations_json worker_json
# moved-submodule-shim-names doeff_cluster.coordinator.protocol.faults:
#   CoordinatorFault MODULE_TAGS coordinator_faults
# moved-submodule-shim-names doeff_cluster.coordinator.protocol.kube:
#   AnnotateDeployment DeploymentReading DeploymentSeen DeploymentUnreadable FollowDeployments
#   FollowNodes KubeBodyRead KubeCalls KubeMemory KubeReadFailed KubeUnavailable MODULE_TAGS
#   MemoryFollows MemoryWatch MemoryWatching NextRequests NodeLabelsSeen NodeLabelsUnreadable
#   ScaleDeployment Watching deployment_observation_of deployment_reading deployment_view
#   deployment_writes follow_wait follows_change_at follows_changed follows_deployments
#   follows_nodes kube_api kube_memory kube_unavailable memory_relabel memory_settle
#   memory_watch_changes memory_watch_follow node_labels_observation_of node_labels_table
#   node_labels_view node_writes now_epoch_ms read_of watch_follow watch_mark watch_reporter
#   watch_take
# moved-submodule-shim-names doeff_cluster.coordinator.protocol.replies:
#   BoardConflict BoardRead BoardRefused BoardUsage BoardWritten DeploymentSeen DeploymentUnreadable
#   DetachedCancelled DetachedOutbox DetachedOutboxAcked DetachedProgress DetachedReleased
#   DetachedSubmitted DetachedUnknown DetachedWarming DrainPhase DrainProgress ErrorReply EventsView
#   HeartbeatReply JobSpec LeaseAnswer MODULE_TAGS PlainText ProgramRow ProgramStored
#   ReadinessReport Reply ResourceList ResourceView RolloutObserved RowConflict ServiceObserved
#   StateReply StateView TaskAccepted TaskDropped TaskMissing TaskObserved TaskOffer TaskProgress
#   TaskRecord TaskResultTaken UnplacedView VersionVerdict WarmState WorkerDrainView WorkerObserved
#   audit_event_to_json board_answer_json board_usage_json deployment_observation_json
#   detached_answer_json drain_progress_json ended_to_json error_reply_json group_limit_spec
#   heartbeat_reply_json hyx_warm_state_XgreaterHthan_signXjson job_to_json needs_override_spec
#   observed_json readiness_report_json reply_bodies reply_json resource_view_json row_conflict_json
#   spec_json spelled_each state_view_body state_view_json status_row_to_json task_answer_json
#   task_limits_json task_offer_json task_summary unplaced_view_json version_json
#   worker_drain_view_json
# moved-submodule-shim-names doeff_cluster.coordinator.protocol.request_bodies:
#   BoardWireBody BoardWrite BodyInvalid BodyMalformed BodyUnreadable ClusterState ClusterTiming
#   DrainBody HeartbeatBody LeaseBody LegacyJobRow LegacyJobs LegacyJobsBody MODULE_TAGS MetricsBody
#   ProgramBody ReadBody ReadinessBody Request RequestBody ResourceBody ServiceBody TaskBody
#   TaskResultBody WarmBody body_of body_type_of declared_job_from_json legacy_jobs_of legacy_row_of
#   reply_json request_bodies required_field respond responded service_body_of
# moved-submodule-shim-names doeff_cluster.coordinator.protocol.request_queue:
#   CoordinatorFault MODULE_TAGS NextRequests Reply Request RequestQueue ServiceReached await_answer
#   await_first_request enqueue_request now_epoch_ms nudge_takers promise_or_timeout queued_requests
#   take_requests
# moved-submodule-shim-names doeff_cluster.coordinator.protocol.state_json:
#   AuditEvent BoardRow ClusterState Drain GroupLimit KnownExit MODULE_TAGS NeedsOverride Placement
#   ProgramRow RefusedJob ResourceMeta RolloutRow WarmEntry WorkerInfo audit_event_from_json
#   audit_event_to_json board_rows_of board_tally_of boot_at_of capabilities_of
#   component_versions_of ended_from_json ended_to_json group_limit_from_json group_limit_of
#   group_limit_refusal group_limit_spec handoff_watch_from_json job_from_json job_to_json
#   named_capabilities needs_override_from_json needs_override_spec program_row_from_json
#   program_row_to_json read_each read_service_rows resource_meta_from_json resource_meta_to_json
#   rollout_row_from_json rollout_row_to_json rollout_spec_to_json rollout_status_from_json
#   rollout_status_to_json state_from_json state_to_json task_record_from_json task_record_to_json
#   validate_rollout_spec value_size warm_entry_from_json warm_entry_to_json worker_capabilities_of
#   worker_generations_from_json worker_generations_json
# moved-submodule-shim-names doeff_cluster.coordinator.protocol.store:
#   ByteLog DeltaStore DroppedTail DurableStore LogScan MODULE_TAGS MethodShape Persist SaveState
#   SnapshotRead apply_delta durable_checkpoint durable_delta durable_exists durable_load
#   durable_persist durable_states encode_line encode_snapshot memory_store read_snapshot scan_log
#   wal_append_line wal_check_place wal_drop_tail wal_exists wal_read_log_lines wal_read_snapshot
#   wal_store wal_write_snapshot writes_of
# moved-submodule-shim-names doeff_cluster.coordinator.protocol.wal_files:
#   FSYNC_REPORT_EVERY MODULE_TAGS MOVED_MARK SLOW_FSYNC_SECONDS SNAPSHOT_MODE log_lines_of
#   now_epoch_ms wal_append_line wal_check_place wal_drop_tail wal_exists wal_read_log_lines
#   wal_read_snapshot wal_write_snapshot
# moved-submodule-shim-names doeff_cluster.coordinator.protocol.wal_format:
#   BadLine DroppedTail GoodLine LineRead LogScan MODULE_TAGS SnapshotRead WalCorrupted apply_delta
#   canonical checksum encode_line encode_snapshot read_line_record read_snapshot scan_log sealed
#   writes_of
# moved-submodule-shim-names doeff_cluster.coordinator.protocol.worker_notices:
#   MODULE_TAGS MalformedNotice WORKER_NOTICE_CHANNEL WORKER_NOTICE_READS WORKER_NOTICE_ROUTES
#   WorkerBack WorkerGone back_of gone_of notice_channel notice_of notice_text
"""Moved:``doeff_cluster.coordinator`` is now ``doeff_cluster._coordinator`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_cluster.coordinator…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import importlib.abc
import importlib.util
import sys
from collections.abc import Sequence
from importlib import import_module
from importlib.machinery import ModuleSpec
from types import CodeType, ModuleType

_OLD = "doeff_cluster.coordinator"
_NEW = "doeff_cluster._coordinator"


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

    runpy.run_module("doeff_cluster._coordinator", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_cluster._coordinator")
