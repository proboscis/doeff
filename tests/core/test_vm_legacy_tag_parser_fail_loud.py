import doeff_vm
import pytest

from doeff import do


class _LegacyParserProbeEffect(doeff_vm.EffectBase):
    def __init__(self, tag):
        self.tag = tag


class _LegacyControlMissingPayload:
    def __init__(self, tag, continuation):
        self.tag = tag
        self.continuation = continuation


@do
def _legacy_parser_probe_program(tag):
    return (yield _LegacyParserProbeEffect(tag))


def _run_legacy_missing_payload(tag):
    def handler(effect, k):
        if not isinstance(effect, _LegacyParserProbeEffect):
            return doeff_vm.Pass(effect, k)
        return _LegacyControlMissingPayload(tag, k)

    vm = doeff_vm.PyVM()
    return vm.run(doeff_vm.WithHandler(handler, _legacy_parser_probe_program(tag)))


@pytest.mark.parametrize(
    ("tag", "tag_name", "attribute"),
    [
        (6, "Resume", "value"),
        (7, "Transfer", "value"),
        (8, "Delegate", "effect"),
        (19, "Pass", "effect"),
        (21, "ResumeThrow", "exception"),
        (22, "TransferThrow", "exception"),
    ],
)
def test_legacy_tag_parser_missing_payload_attributes_fail_loud(tag, tag_name, attribute):
    with pytest.raises(RuntimeError, match=rf"{tag_name}: missing '{attribute}' attribute"):
        _run_legacy_missing_payload(tag)
