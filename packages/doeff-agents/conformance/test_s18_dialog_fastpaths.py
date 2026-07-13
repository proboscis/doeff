"""S18 R9 dialog fast-paths (contract README S18, tag P).

Observed oracle physics (this CORRECTS the original README row, which
listed S18 as mode M2 for all four dialogs):

  * codex-update / bypass-permissions / fullscreen-renderer / trust are
    detected and dismissed ONLY inside `wait_for_repl_idle` (main.rs:4138-4161) —
    the launch path that runs exclusively for M1 launches (no `command=`,
    main.rs:1791). They are unreachable in M2. They also run inside the
    launch blind window (before the session row exists), so the only
    observable is the key sequence landing on the fake's tty; the launch
    then proceeds to paste the prompt into the recovered REPL, which is
    itself proof the dialog was cleared.
  * managed-settings is the one dialog ALSO handled in the monitor loop
    (main.rs:3604), because it can appear after a turn has started. Its
    monitor-loop dismissal is exercised here in M2, mid-session.

Two tty facts drive the assert shapes (both learned here, both true of
the S5 arrow-key path too):
  * the pane's line discipline is canonical with ICRNL: a submit Enter
    (`\\r`) is translated to `\\n`, and a key line is delivered only once
    its terminating Enter arrives — so `Down Down Enter` reaches the fake
    as one line `\\x1b[B\\x1b[B\\n`. Awaiting the Down CSI bytes
    (`\\x1b[B`, confirmed as CSI not SS3 by S5) proves dismissal: a bare
    prompt paste never contains them.
  * a bare Enter is content-indistinguishable from the paste's own
    submit Enter, so the managed-settings dismissal is proven structurally
    instead: its branch SETS `observed_active_at` (main.rs:3608), and in a
    pane that shows only the managed dialog nothing else can — no idle
    glyph, no active marker, no ⏺ turn activity — so a non-null
    `observed_active_at` is unique to the fast-path having fired.

Dismissal key sequences transcribed from main.rs:3189-3228:
  codex-update -> Down×2 + Enter (frame highlights option 1; steps to
                  "3. Skip until next version" = 2)
  bypass       -> Down + Enter ("2. Yes, I accept")
  fullscreen   -> Down + Enter ("2. Not now")
  managed      -> Enter
  trust        -> Enter (default selection is "1. Yes, I trust this
                  folder"; NO Rust oracle counterpart — added 2026-07-07
                  after a live frame proved detect_dialog=None left the
                  launch hanging forever)
"""

from __future__ import annotations

import time

import pytest
from doeff_agents.agentd_client import AgentdClientError
from harness import RESULT_SCHEMA, AgentdHarness, session_exists_out_of_band

PROMPT = "Produce the conformance structured result."
PAYLOAD = {"summary": "dialog cleared", "ok": True}
DOWN = "\x1b[B"


def _matched_keys(scenario, expect: str, *, timeout_s: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for entry in scenario.journal():
            if (
                entry["event"] == "keys"
                and entry["expect"] == expect
                and entry["matched"]
            ):
                return True
        time.sleep(0.2)
    return False


def _run_launch_dialog_m1(
    harness,
    name: str,
    *,
    agent_type: str,
    dialog_frame: str,
    idle_frame: str,
    down_count: int,
    extra_env: dict[str, str],
) -> None:
    downs = DOWN * down_count
    scenario = harness.scenario(
        name,
        [
            {"render": dialog_frame},
            # the dismisser sends Down×N + Enter as one canonical line;
            # await the Down CSI bytes (Enter is implied and translated)
            {"await_keys": {"expect": downs, "timeout_s": 30}},
            # retire the dialog before rendering the REPL: wait_for_repl_idle
            # captures 60 lines and would otherwise re-dismiss a still-
            # visible dialog on this append-only pane
            {"scroll": 70},
            {"render": idle_frame},
            {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
        ],
    )
    scenario.launch_m1(
        agent_type=agent_type,
        prompt=PROMPT,
        expected_result={"payload_schema": RESULT_SCHEMA},
        extra_env=extra_env,
    )
    assert _matched_keys(scenario, downs), (
        f"dismissal keys never landed for {name}\n{harness.log_text()}"
    )
    # the launch cleared the dialog, reached the recovered REPL, and pasted
    # the prompt — the end-to-end proof the fast-path unblocked startup
    assert _matched_keys(scenario, PROMPT), (
        f"prompt never pasted after dialog dismissal for {name}\n{harness.log_text()}"
    )


def test_s18_codex_update_dialog_dismissed_at_launch(tmp_path) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    daemon_codex_home = tmp_path / "daemon-codex-home"
    with AgentdHarness(extra_env={"CODEX_HOME": str(daemon_codex_home)}) as harness:
        _run_launch_dialog_m1(
            harness,
            "s18-codex-update",
            agent_type="codex",
            dialog_frame="F-dialog-codex-update",
            idle_frame="F-idle-codex",
            down_count=2,
            extra_env={"CODEX_HOME": str(codex_home)},
        )


def test_s18_claude_bypass_dialog_accepted_at_launch(tmp_path) -> None:
    with AgentdHarness() as harness:
        _run_launch_dialog_m1(
            harness,
            "s18-bypass",
            agent_type="claude",
            dialog_frame="F-dialog-bypass",
            idle_frame="F-idle-claude",
            down_count=1,
            extra_env={"CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config")},
        )


def test_s18_claude_fullscreen_dialog_dismissed_at_launch(tmp_path) -> None:
    with AgentdHarness() as harness:
        _run_launch_dialog_m1(
            harness,
            "s18-fullscreen",
            agent_type="claude",
            dialog_frame="F-dialog-fullscreen",
            idle_frame="F-idle-claude",
            down_count=1,
            extra_env={"CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config")},
        )


def test_s18_claude_trust_dialog_accepted_at_launch(tmp_path) -> None:
    """The workspace-trust gate ("Is this a project you created or one you
    trust?") appears whenever claude starts in a cwd whose project entry
    lacks hasTrustDialogAccepted — including when the pre-seed is bypassed
    or the CLI decides to re-ask. Undismissed it blocks startup forever:
    wait_for_repl_idle never sees an idle prompt, degrades at its cap, and
    the launch pastes the prompt into the dialog. Its default selection is
    option 1 ("Yes, I trust this folder"), so the dismisser is a bare Enter
    (same intent as the S12 pre-seed). A bare Enter has no unique bytes, so
    ordering is the proof: the scenario only advances past the dialog once
    "\\n" lands while the dialog is on screen, and the prompt paste can only
    match after that barrier."""
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s18-trust",
            [
                {"render": "F-dialog-trust"},
                # the dismisser's Enter (`\r`, ICRNL -> `\n`) is the first
                # input to reach the pane: nothing is pasted while the
                # dialog is showing
                {"await_keys": {"expect": "\n", "timeout_s": 30}},
                # retire the dialog before rendering the REPL (append-only
                # pane; wait_for_repl_idle would re-dismiss a visible one)
                {"scroll": 70},
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
            ],
        )
        scenario.launch_m1(
            agent_type="claude",
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
            extra_env={"CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config")},
        )
        assert _matched_keys(scenario, "\n"), (
            f"trust dismissal Enter never landed\n{harness.log_text()}"
        )
        assert _matched_keys(scenario, PROMPT), (
            f"prompt never pasted after trust dismissal\n{harness.log_text()}"
        )


def test_s18_unknown_dialog_fails_launch_closed(tmp_path) -> None:
    """A startup dialog OUTSIDE the R9 set must fail the launch with a
    clear error instead of degrading silently (2026-07-07 contract
    revision; the frozen oracle pasted the prompt into whatever was on
    screen after the repl-idle budget, which turned the trust-dialog gap
    into a silent forever-hang). Contract: the prompt is never delivered,
    the RPC returns a typed error naming the blocked startup and carrying
    the screen tail, and the created mux session is cleaned up.

    DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS compresses the 120s repl-idle
    budget for the test (same env-only knob pattern as the S19 watchdogs).
    """
    with AgentdHarness(
        extra_env={"DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS": "3"}
    ) as harness:
        scenario = harness.scenario(
            "s18-unknown",
            [
                # the unknown dialog just sits there — nothing dismisses it
                {"render": "F-dialog-unknown"},
            ],
        )
        with pytest.raises(AgentdClientError) as excinfo:
            scenario.launch_m1(
                agent_type="claude",
                prompt=PROMPT,
                expected_result={"payload_schema": RESULT_SCHEMA},
                extra_env={"CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config")},
            )
        message = str(excinfo.value)
        assert "did not become ready" in message, message
        # the error carries the screen evidence (what blocked startup)
        assert "share anonymous usage data" in message.lower(), message

        # no session row was registered (the launch failed before persist)
        assert harness.client.get_session(scenario.session_id) is None

        # the created mux session was cleaned up, not leaked
        assert not session_exists_out_of_band(scenario.session_id), (
            f"failed launch leaked its mux session: {scenario.session_id}"
        )


def test_s18_managed_settings_dialog_dismissed_by_monitor_m2() -> None:
    with AgentdHarness(extra_serve_args=["--prompt-judge-cmd", ""]) as harness:
        scenario = harness.scenario(
            "s18-managed",
            [
                {"render": "F-dialog-managed"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                # the monitor's managed-settings dismisser sends a bare
                # Enter every tick the dialog is on screen (ICRNL -> \n);
                # the paste's own Enter was consumed with the prompt line
                {"await_keys": {"expect": "\n", "timeout_s": 30}},
                {"scroll": 110},
                {"render": "F-turn-activity-claude"},
                {"report_result": {"payload": PAYLOAD}},
                {"render": "F-idle-claude"},
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=40.0)
        assert outcome.result == PAYLOAD, (
            f"{outcome!r}\n{harness.log_text()}"
        )

        # load-bearing proof the managed fast-path fired: observed_active_at
        # is set, which in a managed-dialog-only pane can ONLY come from the
        # managed branch (no idle/active/turn-activity marker present)
        row = harness.session_row(scenario.session_id)
        assert row["status"] == "done", (
            f"status={row['status']} err={row['last_validation_error']}\n"
            + harness.log_text()
        )
        assert row["observed_active_at"] is not None, (
            "managed-settings fast-path never ran (observed_active_at NULL)\n"
            + harness.log_text()
        )

        # corroboration: the dismissal Enter reached the fake, and it was
        # NOT the unsubmitted-paste guard re-sending (that records its own
        # event and never fired here)
        assert _matched_keys(scenario, "\n", timeout_s=1.0), scenario.journal()
        types = [e["event_type"] for e in harness.events(scenario.session_id)]
        assert "session_unsubmitted_paste_resubmitted" not in types, types
