from doeff_core_effects.memo_handlers import memo_terminal


def test_memo_terminal_doc_mentions_deprecated():
    assert "deprecated" in (memo_terminal.__doc__ or "").lower()
