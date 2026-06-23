def bad_real_agent_launcher(client):
    client.launch_session(
        session_id="bad",
        session_name="bad",
        agent_type="claude",
        command="fake-agent",
    )
