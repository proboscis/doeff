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
def test_s21_resume_and_fork(kind: str) -> None:  # noqa: PLR0915 - baseline cleanup keeps existing control flow unchanged
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
        assert launched
        assert launched[0]["mode"] == "fresh"
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
        # conversation is rejected with the law's name — and the typed
        # error_code machine consumers (ACP) match on.
        with pytest.raises(AgentdClientError, match="one-live-incarnation") as ei:
            harness.client.resume_session(sid)
        assert ei.value.error_code == "one_live_incarnation"

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
        assert fork_events
        assert fork_events[0]["parent"] == conv_id
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


_BINDING_SHAPE = {
    "claude": ("claude-code", "config_dir", "CLAUDE_CONFIG_DIR"),
    "codex": ("codex", "codex_home", "CODEX_HOME"),
}


def _binding_for(kind: str, auth_dir) -> dict[str, Any]:
    binding_kind, field, _env = _BINDING_SHAPE[kind]
    return {"kind": binding_kind, field: str(auth_dir)}


@pytest.mark.parametrize("kind", ["claude", "codex"])
def test_s21_cross_binding_resume(kind: str) -> None:
    """ADR-DOE-AGENTS-006 revision: resume into a DIFFERENT auth home.

    The host transplants the transcript by symlink (resume-physics.md
    2026-08-11 probes (b)/(c)), records the new binding as the incarnation's
    effective identity, and honors the caller-minted new_session_id — the
    revived fake CLI must inherit gen-1's marker THROUGH the link."""
    marker = f"S21X-MARKER-{kind.upper()}-G1"
    with AgentdHarness() as harness:
        scenario = harness.scenario(f"s21x-{kind}", _fresh_script(kind, marker))
        _, _, auth_key = _BINDING_SHAPE[kind]
        auth_a = harness.runtime_dir / f"auth-{kind}-a"
        auth_a.mkdir(parents=True, exist_ok=True)
        scenario.launch_m1(
            agent_type=kind,
            prompt="start the task",
            extra_env={auth_key: str(auth_a)},
            resume_script=_revived_script(kind),
        )
        sid = scenario.session_id
        wire = _wait_wire_conversation(harness, sid)
        conv_id = wire["conversation"]["session_id"]
        _wait_transcript_note(scenario, marker)
        harness.client.cancel_session(sid)

        auth_b = harness.runtime_dir / f"auth-{kind}-b"
        auth_b.mkdir(parents=True, exist_ok=True)
        new_sid = f"{sid}-x2"
        revived = harness.client.resume_session(
            sid,
            binding=_binding_for(kind, auth_b),
            new_session_id=new_sid,
        )
        harness._sessions.append(new_sid)

        # caller-minted id + lineage + same conversation, generation + 1
        assert revived["session_id"] == new_sid
        assert revived["generation"] == 2
        assert revived["conversation"]["session_id"] == conv_id
        assert revived["resumed_from_session_id"] == sid
        # the NEW binding is the incarnation's recorded effective identity
        assert revived["effective_identity"][auth_key] == str(auth_b)

        # the transplant physically linked the transcript into auth_b
        if kind == "claude":
            links = list((auth_b / "projects").glob(f"*/{conv_id}.jsonl"))
        else:
            links = list(
                (auth_b / "sessions").glob(f"*/*/*/rollout-*-{conv_id}.jsonl")
            )
        assert links, f"transplanted transcript link missing\n{harness.log_text()}"
        assert links[0].is_symlink()

        # context preservation THROUGH the link: the revived incarnation
        # journaled the inherited transcript containing gen-1's marker.
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


def test_s21_cross_binding_missing_transcript_rejects() -> None:
    """A cross-binding resume whose source transcript is gone must reject
    with the typed error_code and leave no new row (the real CLI would fail
    loud AFTER hosting — the host front-loads it to before the row exists)."""
    marker = "S21X-MARKER-MISSING-G1"
    with AgentdHarness() as harness:
        scenario = harness.scenario("s21x-missing", _fresh_script("claude", marker))
        auth_a = harness.runtime_dir / "auth-missing-a"
        auth_a.mkdir(parents=True, exist_ok=True)
        scenario.launch_m1(
            agent_type="claude",
            prompt="start the task",
            extra_env={"CLAUDE_CONFIG_DIR": str(auth_a)},
        )
        sid = scenario.session_id
        wire = _wait_wire_conversation(harness, sid)
        conv_id = wire["conversation"]["session_id"]
        _wait_transcript_note(scenario, marker)
        harness.client.cancel_session(sid)

        # destroy the source transcript, then ask for a cross-binding revive
        for transcript in (auth_a / "projects").glob(f"*/{conv_id}.jsonl"):
            transcript.unlink()
        auth_b = harness.runtime_dir / "auth-missing-b"
        auth_b.mkdir(parents=True, exist_ok=True)
        with pytest.raises(AgentdClientError) as excinfo:
            harness.client.resume_session(
                sid, binding=_binding_for("claude", auth_b)
            )
        assert excinfo.value.error_code == "transcript_not_discoverable"
        # no new incarnation row was created
        assert (
            harness.client.request("session.get", {"session_id": f"{sid}~g2"})
            is None
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
        with pytest.raises(AgentdClientError, match="identity-unknown") as ei:
            harness.client.resume_session(sid)
        assert ei.value.error_code == "identity_unknown"
