"""S21: conversation resume / fork over the daemon+socket gate
(ADR-DOE-AGENTS-006; contract rows in README「カバレッジ行列」).

Obligations:
  (a) kill → session.resume preserves the conversation context (the revived
      fake CLI journals the inherited transcript containing gen-1's marker)
  (b) fork = a NEW conversation with recorded lineage, independent of the
      parent (works while the parent row is terminal or alive)
  (c) identity-unknown rows reject resume with a typed error
  (d) a live incarnation of the same conversation rejects resume
      (one-live-incarnation-per-conversation)
  (e) generation integrity: the source row stays terminal/untouched; the
      new incarnation carries generation+1 and lineage

Both kind lanes run M1 (PATH shadowing) so the daemon's REAL argv builders
(--session-id / --resume / --fork-session / codex resume|fork) and the
monitor's discovery arm are exercised end to end.
"""

import json
import time
from typing import Any

import pytest
from doeff_agents.agentd_client import AgentdClientError
from harness import AgentdHarness


IDLE_FRAME = {"claude": "F-idle-claude", "codex": "F-idle-codex"}


def _fresh_script(kind: str, marker: str) -> list[dict[str, Any]]:
    return [
        {"render": IDLE_FRAME[kind]},
        {"await_keys": {"expect": "start the task", "timeout_s": 30}},
        {"transcript_note": marker},
    ]


def _revived_script(kind: str) -> list[dict[str, Any]]:
    return [{"render": IDLE_FRAME[kind]}]


def _journal_conversations(scenario) -> list[dict[str, Any]]:
    return [e for e in scenario.journal() if e.get("event") == "conversation"]


def _wait_wire_conversation(harness, session_id: str, timeout_s: float = 15.0):
    """Poll session.get until the monitor's discovery arm has filled the
    conversation (codex fresh / both kinds' forks are CLI-minted)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        wire = harness.client.request("session.get", {"session_id": session_id})
        if wire and wire.get("conversation"):
            return wire
        time.sleep(0.2)
    raise AssertionError(
        f"conversation was not discovered for {session_id}\n{harness.log_text()}"
    )


def _wait_transcript_note(scenario, marker: str, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if any(
            e.get("event") == "transcript_note" and e.get("text") == marker
            for e in scenario.journal()
        ):
            return
        time.sleep(0.2)
    raise AssertionError(f"transcript_note {marker!r} never journaled")


@pytest.mark.parametrize("kind", ["claude", "codex"])
def test_s21_resume_and_fork(kind: str) -> None:
    marker = f"S21-MARKER-{kind.upper()}-G1"
    with AgentdHarness() as harness:
        scenario = harness.scenario(f"s21-{kind}", _fresh_script(kind, marker))
        auth_key = "CLAUDE_CONFIG_DIR" if kind == "claude" else "CODEX_HOME"
        auth_dir = harness.runtime_dir / f"auth-{kind}"
        auth_dir.mkdir(parents=True, exist_ok=True)
        scenario.launch_m1(
            agent_type=kind,
            prompt="start the task",
            extra_env={auth_key: str(auth_dir)},
            resume_script=_revived_script(kind),
        )
        sid = scenario.session_id

        # identity capture: claude is minted at launch (--session-id) and
        # visible immediately; codex is discovered post-boot by the monitor.
        wire = _wait_wire_conversation(harness, sid)
        conv_id = wire["conversation"]["session_id"]
        assert wire["generation"] == 1
        launched = _journal_conversations(scenario)
        assert launched and launched[0]["mode"] == "fresh"
        assert launched[0]["conversation_id"] == conv_id

        # gen-1 wrote its marker into the transcript, then dies.
        _wait_transcript_note(scenario, marker)
        harness.client.cancel_session(sid)

        # (d)-precheck: resume of a session whose conversation has NO live
        # incarnation succeeds …
        revived = harness.client.resume_session(sid)
        new_sid = revived["session_id"]
        assert new_sid == f"{sid}~g2"
        assert revived["generation"] == 2
        assert revived["conversation"]["session_id"] == conv_id
        assert revived["resumed_from_session_id"] == sid
        assert "forked_from_session_id" not in revived
        harness._sessions.append(new_sid)

        # (a) context preservation: the revived incarnation inherited the
        # transcript containing gen-1's marker.
        deadline = time.monotonic() + 15.0
        revived_events: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            revived_events = [
                e for e in _journal_conversations(scenario) if e["mode"] == "resume"
            ]
            if revived_events:
                break
            time.sleep(0.2)
        assert revived_events, f"revived agent never started\n{harness.log_text()}"
        assert revived_events[0]["conversation_id"] == conv_id
        assert marker in revived_events[0]["inherited"]

        # (d) one-live-incarnation: while ~g2 is alive, resuming the same
        # conversation is rejected with the law's name.
        with pytest.raises(AgentdClientError, match="one-live-incarnation"):
            harness.client.resume_session(sid)

        # (b) fork: parent conversation forks into a NEW conversation with
        # lineage, independent of the parent's liveness (~g2 is still alive).
        forked = harness.client.fork_session(sid)
        fork_sid = forked["session_id"]
        assert fork_sid == f"{sid}~fork1"
        assert forked["generation"] == 1
        assert forked["forked_from_session_id"] == sid
        harness._sessions.append(fork_sid)

        # the fork's CLI-minted identity is discovered and differs from the
        # parent conversation; its transcript inherited gen-1's marker.
        fork_wire = _wait_wire_conversation(harness, fork_sid)
        assert fork_wire["conversation"]["session_id"] != conv_id
        fork_events = [
            e for e in _journal_conversations(scenario) if e["mode"] == "fork"
        ]
        assert fork_events and fork_events[0]["parent"] == conv_id
        assert marker in fork_events[0]["inherited"]

        # (e) generation integrity: the source row is untouched — terminal,
        # generation 1, original conversation. The terminal LABEL is racy by
        # frozen physics: session.cancel writes "stopped" while an in-flight
        # monitor tick that saw the pane die writes "exited" — status is
        # deliberately last-write-wins (only terminal_cause_json /
        # result_payload_json are COALESCE-protected, store.hy upsert). The
        # invariant is terminal-ness, never a specific label.
        source_row = harness.session_row(sid)
        assert source_row["status"] in ("stopped", "cancelled", "exited")
        assert source_row["generation"] == 1
        assert (
            json.loads(source_row["conversation_json"])["session_id"] == conv_id
        )


def test_s21_identity_unknown_rejects_resume() -> None:
    # (c) an M2 command-override launch never captures a conversation
    # (the override argv bypasses the kind builders), so resume must fail
    # with the typed identity-unknown error and leave no new session behind.
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s21-unknown", [{"render": "F-idle-claude"}]
        )
        auth_dir = harness.runtime_dir / "auth-unknown"
        auth_dir.mkdir(parents=True, exist_ok=True)
        scenario.launch_m2(
            agent_type="claude",
            prompt="park quietly",
            extra_env={"CLAUDE_CONFIG_DIR": str(auth_dir)},
        )
        sid = scenario.session_id
        with pytest.raises(AgentdClientError, match="identity-unknown"):
            harness.client.resume_session(sid)
