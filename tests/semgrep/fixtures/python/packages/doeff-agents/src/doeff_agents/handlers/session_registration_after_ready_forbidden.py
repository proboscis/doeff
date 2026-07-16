from doeff_agents.handlers.production import SessionState
from doeff_agents.session import deliver_prompt_when_ready


def launch_with_hidden_physical_session(self, effect):
    session_info = self._backend.new_session(effect.session_name)
    deliver_prompt_when_ready(self._backend, session_info.pane_id, effect.prompt)
    self._sessions[effect.session_name] = SessionState(session_info=session_info)
