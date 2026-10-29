# moved-submodule-shim: doeff_agents.sessionhost -> doeff_agents._sessionhost
# moved-submodule-shim-names doeff_agents.sessionhost:
# moved-submodule-shim-names doeff_agents.sessionhost.adopt:
#   AdoptTargetNotFound SessionRow adopt_program clock_now iso_format mint_adopted_session_id
#   session_store_list_active session_store_record_event session_store_upsert tmux_has_session
# moved-submodule-shim-names doeff_agents.sessionhost.attachment:
#   ATTACHMENT_WIRE_BYTES ATTACHMENT_WIRE_DATA ATTACHMENT_WIRE_MIME ATTACHMENT_WIRE_NAME
#   ATTACHMENT_WIRE_SHA256 AttachmentRefused TurnAttachment TurnContent attachment_of_wire
#   attachment_wire
# moved-submodule-shim-names doeff_agents.sessionhost.drivers:
#   DRIVER_EXECUTABLE DriverListingItem driver_listing driver_path_in
# moved-submodule-shim-names doeff_agents.sessionhost.effects:
#   AWAITING_RESPONSE_TIMEOUT_SECONDS BuildLaunch BuildResume CONVERSATION_PROGRESS_MARGIN_SECONDS
#   CONVERSATION_QUIESCENCE_SECONDS ClassifyPane ClockNow DeliverMessage DiscoverConversation EnvGet
#   FS_ENSURE_SYMLINK_STATES FS_LINK_ARTIFACT_STATES FS_SYMLINK_LINKED FS_SYMLINK_OCCUPIED
#   FS_SYMLINK_REFUSED FS_SYMLINK_SAME_ENTITY FS_SYMLINK_SOURCE_MISSING FS_SYMLINK_TARGET_CONFLICT
#   FS_SYMLINK_UNCHANGED FsCanonicalPath FsComposeHomeView FsDirExists FsEnsureSymlink FsFileExists
#   FsFileMtime FsLinkArtifact FsListDir FsMakeDirs FsReadText FsRemoveFile FsSymlinkOutcome
#   FsWriteTextAtomic GitRun HydrateMemoryHome JudgeVerdict LogLine MonitorKnobs
#   PASTE_RESUBMIT_LIMIT PaneFrame PaneObservation PreLaunchSetup ProbeConversationActivity
#   ProcResult ProcRun READY_GATE_FRAME_LINES READY_GATE_FRAME_RETENTION READY_PROBE_CLEAR_KEY
#   READY_PROBE_TEXT REPL_IDLE_MAX_WAIT_SECONDS RESUME_ERR_IDENTITY_UNKNOWN
#   RESUME_ERR_KIND_NOT_SUPPORTED RESUME_ERR_ONE_LIVE_INCARNATION
#   RESUME_ERR_TRANSCRIPT_NOT_DISCOVERABLE RESUME_ERR_TRANSPLANT_REFUSED
#   RESUME_ERR_WORKDIR_NOT_FOUND ReadyGateVerdict SessionRow SessionStoreGet
#   SessionStoreKnownConversationIds SessionStoreListActive SessionStoreListCleanupPending
#   SessionStoreRecordEvent SessionStoreResultPayload SessionStoreUpsert TerminalCause TmuxCapture
#   TmuxHasSession TmuxKillSession TmuxNewSession TmuxPaneCurrentCommand TmuxSendKeys
#   TmuxSessionPaneIds TransplantConversation WireResultChannel build_launch build_resume
#   classify_pane clock_now deliver_message discover_conversation env_get fs_canonical_path
#   fs_compose_home_view fs_dir_exists fs_ensure_symlink fs_file_exists fs_file_mtime
#   fs_link_artifact fs_list_dir fs_make_dirs fs_read_text fs_remove_file fs_write_text_atomic
#   git_run hydrate_memory_home log_line pre_launch_setup probe_conversation_activity proc_run
#   session_store_get session_store_known_conversation_ids session_store_list_active
#   session_store_list_cleanup_pending session_store_record_event session_store_result_payload
#   session_store_upsert tmux_capture tmux_has_session tmux_kill_session tmux_new_session
#   tmux_pane_current_command tmux_send_keys tmux_session_pane_ids transplant_conversation
#   wire_result_channel
# moved-submodule-shim-names doeff_agents.sessionhost.host:
#   ACTIVE_STATUSES ATTACHMENTS_UNSUPPORTED_REASON AWAITING_RESPONSE_TIMEOUT_SECONDS
#   AWAIT_POLL_INTERVAL_SECONDS AWAIT_TERMINAL_STATUSES AdoptTargetNotFound
#   CONVERSATION_QUIESCENCE_SECONDS DEFAULT_AWAIT_TIMEOUT_SECONDS DEFAULT_HERDR_SOCKET
#   DEFAULT_MAX_RUNNING_SESSIONS DEFAULT_MONITOR_INTERVAL_MS DEFAULT_PROMPT_JUDGE_CMD
#   DEFAULT_PROMPT_STALL_SECONDS DEFAULT_PROMPT_UNBLOCK_LIMIT DEFAULT_RESULT_SOLICITATION_LIMIT
#   DEFAULT_STORE_WRITE_FAILURE_LIMIT DEFAULT_TURN_STALL_SECONDS ENV_BACKEND ENV_EXIT_WHEN_ORPHANED
#   ENV_HERDR_SOCKET ENV_PROMPT_JUDGE_CMD ENV_PROMPT_STALL_SECS ENV_PROMPT_UNBLOCK_ATTEMPTS
#   ENV_RESULT_SOLICITATIONS FLAG_ALLOW_METERED_BILLING FLAG_BACKEND FLAG_DB FLAG_HERDR_SOCKET
#   FLAG_MAX_RUNNING FLAG_MONITOR_INTERVAL_MS FLAG_PROMPT_JUDGE_CMD FLAG_PROMPT_STALL_SECS
#   FLAG_PROMPT_UNBLOCK_ATTEMPTS FLAG_RESULT_SOLICITATIONS FLAG_SOCKET FLAG_TMUX HELP_FLAGS
#   HISTORY_PRUNE_BATCH_ROWS HISTORY_PRUNE_INTERVAL_SECONDS HISTORY_RETENTION_DAYS_DEFAULT
#   HostConfig JsonValue LAUNCH_TIMEOUT_SECONDS LEASE_TTL_SECONDS MAX_AWAIT_TIMEOUT_SECONDS
#   MIN_AWAIT_TIMEOUT_SECONDS MonitorKnobs ORPHAN_EXIT_BACKSTOP_SECONDS ORPHAN_POLL_INTERVAL_SECONDS
#   PASTE_RESUBMIT_LIMIT REPL_IDLE_MAX_WAIT_SECONDS RPC_ERR_ADOPT_TARGET_NOT_FOUND
#   RPC_ERR_ALREADY_TERMINAL RPC_ERR_AWAIT_TIMEOUT RPC_ERR_NO_SUCH_SESSION RPC_ERR_RESULT_REJECTED
#   ResumeRejected RpcHostError SERVE_ENV_ONLY_SPECS SERVE_FLAG_SPECS
#   STALE_OBSERVATION_THRESHOLD_SECONDS SessionRow StoreActor TURN_CARRIED_KEYS TURN_COUNTERS
#   TURN_HOLDER_AGENT TurnAttachment USAGE_INDENT USAGE_WIDTH actor_prune_history admit_context_file
#   admit_expected_result admit_workspace_seed adopt_program arg_at attachment_of_wire
#   augment_wire_snapshot await_result_blocking backend_alive_program bind_listener
#   binding_kind_advertisement build_await_response build_launch_program_params build_monitor_knobs
#   build_resume_program_params cancel_program capture_program carry_charter_fields cause_if_absent
#   claude_code_impl cleanup_program clock_now codex_impl db_acquire_lease db_clear_awaiting_latches
#   db_count_active db_heartbeat_once db_late_result_accept db_mark_cleaned db_read_lease
#   db_record_command db_record_event db_release_lease db_report_result_guarded_update
#   db_session_by_conversation db_session_get db_session_list db_turn_stamp db_vacuum_if_bloated
#   default_db_path default_socket_path dispatch_line dispatch_method effective_turn_stall_seconds
#   env_positive_i64 env_u32 err_response handle_stream heartbeat_loop herdr_substrate
#   history_retention_cutoff_iso home_dir is_run_to_completion is_terminal_status iso_format
#   late_result_cleanup_program launch_session main make_cause monitor_cycle monitor_loop
#   normalize_prompt_judge_cmd ok_response orphan_watch_loop page_int_param params_object parse_args
#   prepare_socket_path prune_history_tick readiness_of real_substrate reap_launched_sessions
#   record_command report_result_op require_session_row required_arg required_str_param
#   resume_session run_hosted run_worker_tick schema_admission_error send_program serve
#   serve_usage_text session_store_get session_store_record_event session_store_upsert
#   snapshot_to_wire_dict sqlite_session_store store_write_failure_limit substrate_present_program
#   tail_chars tmux_capture tmux_has_session tmux_kill_session tmux_send_keys tmux_session_pane_ids
#   turn_attachments_of turn_close_holder turn_stalled validate_against_schema wire_snapshot
#   wire_with_stalled xdg_state_home
# moved-submodule-shim-names doeff_agents.sessionhost.impls:
# moved-submodule-shim-names doeff_agents.sessionhost.impls.channel:
#   REPORT_RESULT_MCP_SERVER result_channel_spec
# moved-submodule-shim-names doeff_agents.sessionhost.impls.claude_code:
#   AUTOCOMPACT_ARG AUTOCOMPACT_AUTO AUTOCOMPACT_DEFAULT_TOKENS AUTOCOMPACT_MAX_TOKENS
#   AUTOCOMPACT_MIN_TOKENS AUTOCOMPACT_PARAM_KEY BILLING_METERED BILLING_SUBSCRIPTION BuildLaunch
#   BuildResume CARRIED_INSTRUCTION_SOURCES_PARAM CARRIED_ITEM_HOME_NAME CARRIED_ITEM_KEY
#   CARRIED_ITEM_KIND CARRIED_ITEM_PATH CARRIED_ITEM_TEXT CARRIED_SEAT_HOME_PARAM
#   CARRIED_SOURCE_DIR_LINK CARRIED_SOURCE_FILE_TEXT CLAUDE_AUTO_MEMORY_DIR_SETTING
#   CLAUDE_DEFAULT_HOME_DIR CLAUDE_DISABLE_ALL_HOOKS_SETTING CLAUDE_MD_EXCLUDES_SETTING
#   CLAUDE_MEMORY_BASE_FILE CLAUDE_MEMORY_FILES_KEY CLAUDE_MEMORY_FILE_SUFFIX
#   CLAUDE_MEMORY_INDEX_FILE CLAUDE_MEMORY_RETIRED_FILES_KEY CLAUDE_REBUILT_TRANSCRIPT_KEY
#   CLAUDE_REBUILT_TRANSCRIPT_SESSION_KEY CLAUDE_REBUILT_TRANSCRIPT_TEXT_KEY
#   CLAUDE_SETTINGS_API_KEY_HELPER CLAUDE_SETTINGS_FILE CLAUDE_SETTINGS_OWNED_KEYS
#   CLAUDE_SETTINGS_VERTEX_ENV CLAUDE_SETTINGS_VERTEX_PROJECT_ENV ClassifyPane DRIVER_EXECUTABLE
#   DeliverMessage DiscoverConversation FAST_JEV_KEY_FILE_ENV FS_SYMLINK_REFUSED
#   FS_SYMLINK_SOURCE_MISSING HOME_READING_ABSENT HOME_READING_MALFORMED HydrateMemoryHome
#   OTEL_ENDPOINT_ENV OTEL_HOST_ENV OTEL_TENANT_ENV PreLaunchSetup ProbeConversationActivity
#   REPORT_RESULT_MCP_SERVER RESUME_ERR_TRANSCRIPT_NOT_DISCOVERABLE RESUME_ERR_TRANSPLANT_REFUSED
#   TransplantConversation WireResultChannel autocompact_arg_pair_ok binding_billing_class
#   build_claude_argv build_claude_resume_argv classify_output claude_autocompact_args
#   claude_autocompact_value claude_baseline_kept claude_code_impl claude_conversation_activity
#   claude_discover_conversation claude_home_metered_reading claude_home_reading_label
#   claude_hydrate_memory_home claude_install_instruction_sources claude_install_rebuilt_transcript
#   claude_pre_launch claude_transplant_conversation env_get fast_jev_compaction_enabled
#   fast_jev_home_settings fast_jev_install_command fast_jev_installed_version
#   fast_jev_plugin_json_path fast_jev_plugin_outdated fast_jev_state_dir fast_jev_update_command
#   fs_canonical_path fs_ensure_symlink fs_file_exists fs_file_mtime fs_link_artifact fs_list_dir
#   fs_make_dirs fs_read_text fs_remove_file fs_write_text_atomic install_fast_jev_plugin
#   install_otel_telemetry log_line otel_home_settings preseed_claude_trust proc_run
#   resolve_claude_config_dir result_channel_spec tmux_send_keys
# moved-submodule-shim-names doeff_agents.sessionhost.impls.codex:
#   BILLING_METERED BILLING_SUBSCRIPTION BuildLaunch BuildResume CODEX_AUTH_API_KEY_FIELD
#   ClassifyPane DRIVER_EXECUTABLE DeliverMessage DiscoverConversation FS_SYMLINK_REFUSED
#   FS_SYMLINK_SOURCE_MISSING HOME_READING_ABSENT HOME_READING_MALFORMED HydrateMemoryHome
#   PreLaunchSetup ProbeConversationActivity REPORT_RESULT_MCP_SERVER
#   RESUME_ERR_TRANSCRIPT_NOT_DISCOVERABLE RESUME_ERR_TRANSPLANT_REFUSED TransplantConversation
#   WireResultChannel binding_billing_class build_codex_argv build_codex_resume_argv classify_output
#   codex_auth_gate codex_conversation_activity codex_discover_conversation
#   codex_home_metered_reading codex_impl codex_pre_launch codex_transplant_conversation
#   codex_view_root env_get fs_canonical_path fs_compose_home_view fs_file_exists fs_file_mtime
#   fs_link_artifact fs_list_dir fs_make_dirs fs_read_text fs_write_text_atomic result_channel_spec
#   tmux_send_keys toml_quoted upsert_codex_trust_toml
# moved-submodule-shim-names doeff_agents.sessionhost.impls.fast_jev:
#   FAST_JEV_CACHE_TTL_MINUTES FAST_JEV_KEY_FILE_ENV FAST_JEV_MARKETPLACE_NAME
#   FAST_JEV_MARKETPLACE_URL FAST_JEV_PLUGIN_DEFAULT_TTL_MINUTES FAST_JEV_PLUGIN_ID
#   FAST_JEV_PLUGIN_JSON_SUFFIX FAST_JEV_PLUGIN_VERSION FAST_JEV_STATE_DIR_SUFFIX
#   FAST_JEV_WARM_MARGIN_MS fast_jev_cache_surely_warm fast_jev_compaction_enabled
#   fast_jev_home_settings fast_jev_install_command fast_jev_installed_version
#   fast_jev_plugin_json_path fast_jev_plugin_options fast_jev_plugin_outdated
#   fast_jev_session_state_path fast_jev_state_dir fast_jev_update_command
# moved-submodule-shim-names doeff_agents.sessionhost.impls.markers:
#   API_LIMIT_ERROR_STATUS API_LIMIT_EXHAUSTED_FAMILY_RE API_LIMIT_MODEL_FAMILY_WORDS
#   API_LIMIT_MONTHS API_LIMIT_ORG_CAP_FAMILY_RE API_LIMIT_POSSESSIVE_WORDS_RE
#   API_LIMIT_REASON_RATE_LIMITED API_LIMIT_RESETS_RE API_LIMIT_SCOPE_ACCOUNT API_LIMIT_SCOPE_MODEL
#   API_LIMIT_SCOPE_UNKNOWN ApiLimitReading COMPOSER_TAIL_LINES CONVERSATION_NOT_FOUND_MARKERS
#   DIALOG_CONFIRM_PHRASES DIALOG_OPTION_RE FRAME_BORDER_CHARS FRAME_BORDER_RUN
#   PROVIDER_ACCESS_REVOKED_FAMILY_RE PROVIDER_CONTEXT_EXHAUSTED_FAMILY_RE
#   PROVIDER_FAILURE_CLASS_ACCESS_REVOKED PROVIDER_FAILURE_CLASS_CONTEXT_EXHAUSTED
#   PROVIDER_FAILURE_CLASS_REAUTH PROVIDER_FAILURE_CLASS_TRANSPORT PROVIDER_REAUTH_FAMILY_RE
#   PROVIDER_TRANSPORT_FAILURE_FAMILY_RE PaneObservation UNSUBMITTED_CHIP_MARKERS
#   api_limit_names_a_model api_limit_reading_of api_limit_resets_at classify_output
#   codex_update_dismiss_keys codex_update_selected_option composer_region detect_dialog
#   has_active_marker has_agent_frame has_api_limit_marker has_claude_bypass_dialog
#   has_claude_fullscreen_dialog has_claude_managed_dialog has_claude_trust_dialog
#   has_codex_active_marker has_codex_update_dialog has_conversation_not_found_marker
#   has_failure_marker has_frame_border has_idle_prompt has_live_claude_spinner has_queued_messages
#   has_turn_activity has_unsubmitted_paste has_waiting_marker is_api_limit_refusal
#   is_claude_composer_border is_composer_clear is_dialog_shaped is_input_loop_wired
#   is_starting_mcp_servers provider_failure_class startup_finished tail_lower
# moved-submodule-shim-names doeff_agents.sessionhost.impls.otel_telemetry:
#   OTEL_ENDPOINT_ENV OTEL_HOST_ENV OTEL_MANAGED_KEYS OTEL_TENANT_ENV OTEL_TENANT_PERSONAL otel_env
#   otel_home_settings
# moved-submodule-shim-names doeff_agents.sessionhost.launch:
#   BILLING_METERED BINDING_OWNED_ENV_KEYS CARRIED_INSTRUCTION_SOURCES
#   CARRIED_INSTRUCTION_SOURCES_PARAM CARRIED_ITEM_HOME_NAME CARRIED_ITEM_KEY CARRIED_ITEM_KIND
#   CARRIED_ITEM_PATH CARRIED_ITEM_TEXT CARRIED_SEAT_HOME_PARAM CARRIED_SOURCE_DIR_LINK
#   CARRIED_SOURCE_FILE_TEXT DIALOG_REDRAW_SECONDS FS_SYMLINK_LINKED FS_SYMLINK_REFUSED
#   FS_SYMLINK_SAME_ENTITY FS_SYMLINK_SOURCE_MISSING INTERACTIVE_AGENT_TYPES LIFECYCLES
#   LIFECYCLE_INTERACTIVE LIFECYCLE_MULTI_TURN LIFECYCLE_RUN_TO_COMPLETION PaneFrame PaneObservation
#   READY_GATE_FRAME_LINES READY_GATE_FRAME_RETENTION READY_PROBE_CLEAR_KEY READY_PROBE_TEXT
#   REPL_IDLE_MAX_WAIT_SECONDS REPL_IDLE_POLL_SECONDS RESULT_PROTOCOL_INSTRUCTION
#   RESUME_ERR_IDENTITY_UNKNOWN RESUME_ERR_KIND_NOT_SUPPORTED RESUME_ERR_ONE_LIVE_INCARNATION
#   RESUME_ERR_WORKDIR_NOT_FOUND ReadyGateVerdict ResumeRejected SessionRow admit_launch
#   binding_admission_error binding_billing_class build_launch build_resume carry_charter_fields
#   classify_pane claude_instruction_sources claude_settings_declaration clock_now
#   command_mentions_codex counts_toward_launch_capacity deliver_message env_get find_branch_holder
#   format_evidence_frames fs_dir_exists fs_link_artifact fs_make_dirs fs_read_text
#   fs_write_text_atomic git_run host_active_workdirs hyx_path_inside_rootXquestion_markX iso_format
#   launch_conversation_plan_of launch_not_ready_category launch_not_ready_class
#   launch_not_ready_reason launch_session launch_spawn_env link_workspace_sibling_deps log_line
#   make_cause materialize_workspace_seed overlay_env_offenders overlay_without_turn_auth
#   params_with_instruction_sources parse_worktree_list_porcelain pre_launch_setup
#   prepare_launch_workspace release_stale_branch_holder resume_launch_params_of resume_session
#   retain_frame seconds_since session_env_admission_error session_hooks_mode session_store_get
#   session_store_list_active session_store_record_event session_store_upsert shell_join shell_quote
#   strip_incarnation_suffix tmux_capture tmux_has_session tmux_kill_session tmux_new_session
#   tmux_send_keys transplant_conversation wait_for_launch_ready wire_result_channel
#   workspace_seed_owner_marker_root
# moved-submodule-shim-names doeff_agents.sessionhost.policy:
#   ACTIVE_STATUSES ALLOWED_UNBLOCK_KEY_NAMES AUTOCOMPACT_PARAM_KEY BILLING_METERED
#   BILLING_SUBSCRIPTION BINDING_KIND_AGENT_TYPE BINDING_KIND_API_VERSION BINDING_KIND_BILLING
#   BINDING_KIND_FORKABLE BINDING_KIND_RESUMABLE BINDING_KIND_SHAPES BINDING_OWNED_ENV_KEYS
#   CARRIED_INSTRUCTION_SOURCES CARRIED_INSTRUCTION_SOURCES_PARAM CARRIED_ITEM_HOME_NAME
#   CARRIED_ITEM_KEY CARRIED_ITEM_KIND CARRIED_ITEM_PATH CARRIED_ITEM_TEXT CARRIED_SEAT_HOME_PARAM
#   CARRIED_SOURCE_DIR_LINK CARRIED_SOURCE_FILE_TEXT CARRIED_SOURCE_KINDS CHARTER_CARRIED_KEYS
#   CLAUDE_SETTINGS_API_KEY_HELPER CLAUDE_SETTINGS_METERED_ENV_KEYS CLAUDE_SETTINGS_VERTEX_ENV
#   CLAUDE_SETTINGS_VERTEX_PROJECT_ENV CODEX_AUTH_API_KEY_FIELD CarriedSource HOME_READING_ABSENT
#   HOME_READING_MALFORMED HOME_READING_READ IDLE_SHELL_COMMANDS JudgeVerdict LAUNCH_FLAG_KEYS
#   LAUNCH_NOT_READY_CLASSES LAUNCH_READY_GATE_REASON_PREFIX METERED_CREDENTIAL_ENV_ALIASES
#   MonitorKnobs PROMPT_JUDGE_MAX_KEYS PROVIDER_AUTH_ENV_KEYS PROVIDER_FAILURE_TERMINAL_CATEGORY
#   PROVIDER_ROUTING_ENV_KEYS PaneObservation ProcResult RESULT_SOLICITATION_MESSAGE
#   SEAT_ATTRIBUTED_DEFAULT_CATEGORIES SEAT_ENV_CREDENTIAL_SHAPED_SEGMENTS
#   SEAT_ENV_CREDENTIAL_SHAPED_WORDS SESSIONHOST_TIMEOUT_KINDS SPAWN_INHERITED_ENV_KEYS
#   SPAWN_INHERITED_ENV_PREFIXES SessionRow TERMINAL_CAUSE_CATEGORIES TERMINAL_CAUSE_RETRYABLE
#   TERMINAL_STATUSES TURN_AUTH_ENV_KEYS TURN_CARRIED_KEYS TerminalCause action_terminal_cause
#   binding_admission_error binding_billing_class binding_kind_advertisement
#   binding_kind_shape_label carry_charter_fields carry_keys carry_launch_flags cause_if_absent
#   classify_pane claude_home_metered_reading cleanup_terminal_session_once clock_now
#   codex_home_metered_reading counts_toward_launch_capacity deliver_message discover_conversation
#   env_offenders_against event_type_for_status failed_output_cause finalize format_evidence_frames
#   hyx_declared_nonemptyXquestion_markX hyx_spawn_env_inheritedXquestion_markX
#   inheritable_spawn_env is_allowed_unblock_key is_multi_turn is_run_to_completion
#   is_terminal_status iso_format launch_conversation_plan_of launch_not_ready_category
#   launch_not_ready_class launch_not_ready_reason make_cause metered_credential_env_offenders
#   monitor_cycle monitor_session_once observed_status_from_markers overlay_env_offenders
#   overlay_without_turn_auth parse_iso parse_judge_verdict policy_normalized_env_key
#   probe_conversation_activity proc_run prompt_judge_instructions provider_auth_env_offenders
#   reap_exempt run_judge seat_env_credential_shaped_offenders seconds_since send_unblock_keys
#   session_env_admission_error session_store_known_conversation_ids session_store_list_active
#   session_store_list_cleanup_pending session_store_record_event session_store_result_payload
#   session_store_upsert sessionhost_timeout_kind tail_chars tail_lower tmux_capture
#   tmux_has_session tmux_kill_session tmux_pane_current_command tmux_send_keys
#   tmux_session_pane_ids turn_stalled
# moved-submodule-shim-names doeff_agents.sessionhost.result_mcp:
# moved-submodule-shim-names doeff_agents.sessionhost.schema:
#   JsonValue schema_admission_error validate_against_schema
# moved-submodule-shim-names doeff_agents.sessionhost.store:
#   ACTIVE_STATUSES BODY_CLOSED_TRANSACTION ColumnOverlay ENSURE_COLUMNS HISTORY_PRUNE_BATCH_ROWS
#   LEASE_NAME LEASE_TTL_SECONDS OVERLAY_RULES SCHEMA_BATCH SNAPSHOT_SELECT SQLITE_BUSY_TIMEOUT_MS
#   STORED_COLUMNS SessionRow SessionStoreGet SessionStoreKnownConversationIds
#   SessionStoreListActive SessionStoreListCleanupPending SessionStoreRecordEvent
#   SessionStoreResultPayload SessionStoreUpsert StoreActor StoreWriteHealth TERMINAL_STATUSES
#   TerminalCause UPSERT_SNAPSHOT_SQL VACUUM_FREELIST_FLOOR_PAGES VACUUM_FREELIST_RATIO
#   actor_prune_history changed_policy_patch column_overlay db_acquire_lease db_acquire_lease_body
#   db_clear_awaiting_latches db_count_active db_current_result_payload db_heartbeat_lease_body
#   db_heartbeat_once db_immediate_transaction db_journal_seq db_known_conversation_ids
#   db_late_result_accept db_mark_cleaned db_merge_policy_row db_migrate db_prune_history
#   db_read_lease db_record_command db_record_event db_release_lease db_release_lease_body
#   db_report_result_guarded_update db_session_by_conversation db_session_get db_session_list
#   db_session_list_cleanup_pending db_upsert_lease db_upsert_snapshot db_vacuum_if_bloated
#   ensure_column event_payload list_query_matches merged_snapshot next_health now_iso open_conn
#   overlaid_values parse_iso policy_row_patch reactivation_refusal snapshot_from_db_row
#   snapshot_from_policy_row snapshot_to_policy_row snapshot_to_wire_dict sqlite_session_store
#   storage_failure_name stored_values terminal_cause_from_dict terminal_cause_to_dict
#   upsert_snapshot_statement
# moved-submodule-shim-names doeff_agents.sessionhost.store_health:
#   DEFAULT_STORE_WRITE_FAILURE_LIMIT Readiness STORAGE_FAILURE_CODES StoreWriteHealth next_health
#   readiness_of storage_failure_name
# moved-submodule-shim-names doeff_agents.sessionhost.store_memory:
#   ACTIVE_STATUSES MemorySessionRows ReadMemorySessionRows SessionRow SessionStoreGet
#   SessionStoreKnownConversationIds SessionStoreListActive SessionStoreListCleanupPending
#   SessionStoreRecordEvent SessionStoreResultPayload SessionStoreUpsert StoredEvent
#   TERMINAL_STATUSES active_rows cleanup_pending_rows event_payload known_conversation_ids
#   memory_session_store merged_snapshot overlaid_values reactivation_refusal snapshot_from_db_row
#   snapshot_to_policy_row snapshots_of stored_row stored_rows stored_snapshot stored_values
#   with_event with_row
# moved-submodule-shim-names doeff_agents.sessionhost.substrate:
#   CONFIRM_INITIAL_SECONDS CONFIRM_MAX_RETRIES CONFIRM_RETRY_SECONDS ClockNow EnvGet
#   FORBIDDEN_AGENT_ENV_KEYS FS_SYMLINK_LINKED FS_SYMLINK_OCCUPIED FS_SYMLINK_REFUSED
#   FS_SYMLINK_SAME_ENTITY FS_SYMLINK_SOURCE_MISSING FS_SYMLINK_TARGET_CONFLICT FS_SYMLINK_UNCHANGED
#   FsCanonicalPath FsComposeHomeView FsDirExists FsEnsureSymlink FsFileExists FsFileMtime
#   FsLinkArtifact FsListDir FsMakeDirs FsReadText FsRemoveFile FsSymlinkOutcome FsWriteTextAtomic
#   GitRun LogLine PASTE_SETTLE_SECONDS PROC_RUN_TIMEOUT_SECONDS PROVIDER_AUTH_ENV_KEYS ProcResult
#   ProcRun SHELL_PROMPT_SUPPRESSING_ENV SessionRow TmuxCapture TmuxHasSession TmuxKillSession
#   TmuxNewSession TmuxPaneCurrentCommand TmuxSendKeys TmuxSessionPaneIds compact_prompt_text
#   compose_home_view compose_home_view_name ensure_no_forbidden_agent_env ensure_symlink_outcome
#   env_offenders_against hyx_unsubmitted_paste_inputXquestion_markX literal_prompt_fragments
#   normalize_prompt_text normalized_env_key policy_normalized_env_key real_substrate refusal_of
#   run_tmux tmux_capture_io tmux_paste_literal_io tmux_send_enter_io tmux_send_keys_io
# moved-submodule-shim-names doeff_agents.sessionhost.substrate_herdr:
#   ANSI_ESCAPE_RE CONFIRM_INITIAL_SECONDS CONFIRM_MAX_RETRIES CONFIRM_RETRY_SECONDS
#   DEFAULT_HERDR_SOCKET HERDR_KEY_NAME_MAP HerdrApiError HerdrContractError PASTE_SETTLE_SECONDS
#   REQUEST_LINE_BYTE_LIMIT SHELL_PROMPT_SUPPRESSING_ENV TmuxCapture TmuxHasSession TmuxKillSession
#   TmuxNewSession TmuxPaneCurrentCommand TmuxSendKeys TmuxSessionPaneIds chunked_send_texts
#   ensure_no_forbidden_agent_env herdr_call herdr_capture_io herdr_external_agent_pane_id_io
#   herdr_foreground_command herdr_key_name herdr_kill_session_io herdr_label_holders
#   herdr_label_workspace_ids_io herdr_new_session_io herdr_new_session_verdict
#   herdr_pane_current_command_io herdr_registry_agent_pane_id herdr_request_line herdr_send_key_io
#   herdr_send_keys_io herdr_send_text_io herdr_session_pane_ids_io herdr_substrate
#   herdr_workspace_order_key hyx_unsubmitted_paste_inputXquestion_markX normalize_ansi_read
# moved-submodule-shim-names doeff_agents.sessionhost.turn:
#   ACTIVE_STATUSES CONVERSATION_ID_EXPR TURN_HOLDER_AGENT TURN_HOLDER_DEFAULT_CLOSE
#   db_resolve_turn_target db_turn_stamp now_iso turn_close_holder
"""Moved: ``doeff_agents.sessionhost`` is now ``doeff_agents._sessionhost`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_agents.sessionhost…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import importlib.abc
import importlib.util
import sys
from collections.abc import Sequence
from importlib import import_module
from importlib.machinery import ModuleSpec
from types import CodeType, ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_agents._sessionhost import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

_OLD = "doeff_agents.sessionhost"
_NEW = "doeff_agents._sessionhost"


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

    runpy.run_module("doeff_agents._sessionhost", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_agents._sessionhost")
