"""Semgrep hit fixture: session registration gated behind the ready wait.

Reproduces the pre-fix handle_launch shape (issue
agentd-session-registration-after-ready-gate): the BOOTING snapshot is
recorded only AFTER deliver_prompt_when_ready returns, so the session is
externally unobservable for the whole (up to 120s) readiness wait.
Guarded by doeff-agents-session-registration-must-precede-ready-gate.
"""


def deliver_prompt_when_ready(*args, **kwargs):
    raise NotImplementedError("fixture stub")


class RegistrationAfterReadyGateForbidden:
    def handle_launch(self, effect, session_info, adapter):
        if adapter.injection_method == "tmux":
            deliver_prompt_when_ready(
                self._backend,
                session_info.pane_id,
                adapter,
                effect.prompt,
                session_name=effect.session_name,
                ready_timeout=effect.ready_timeout,
            )
        handle = object()
        self._sessions[effect.session_name] = handle
        self._record_snapshot("session_started", handle, "booting")
        return handle
