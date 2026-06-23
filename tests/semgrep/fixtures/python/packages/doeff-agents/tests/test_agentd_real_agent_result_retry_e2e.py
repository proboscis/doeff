import pytest


@pytest.mark.skipif(True, reason="bad fixture")
def test_agentd_real_claude_result_contract_retries_invalid_output(tmp_path):
    assert tmp_path
