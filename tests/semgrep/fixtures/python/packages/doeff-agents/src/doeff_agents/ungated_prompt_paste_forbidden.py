"""Hit fixture: launch prompts must not be pasted without the ready gate."""


def handle_launch_sample(backend, pane_id, effect):
    backend.send_keys(pane_id, effect.prompt)


def launch_session_sample(backend, pane_id, config):
    backend.send_keys(pane_id, config.prompt, literal=True)
