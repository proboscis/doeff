from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents.session import _dismiss_onboarding_dialogs  # noqa: E402


class FakeBackend:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.index = 0
        self.sent: list[tuple[str, str, bool, bool]] = []

    def capture_pane(self, target: str, lines: int = 50):
        if self.index >= len(self.outputs):
            return self.outputs[-1]
        out = self.outputs[self.index]
        self.index += 1
        return out

    def send_keys(
        self,
        target: str,
        keys: str,
        *,
        literal: bool = True,
        enter: bool = True,
    ) -> None:
        self.sent.append((target, keys, literal, enter))


def test_dismiss_onboarding_does_not_treat_theme_prompt_as_ready() -> None:
    backend = FakeBackend(
        [
            "Choose the text style that looks best with your terminal\n❯ 1. Dark mode",
            "❯ Ready for input",
        ]
    )

    dismissed = _dismiss_onboarding_dialogs(
        "%pane",
        [r"Choose the text style", r"Yes, I trust this folder"],
        timeout=2.0,
        backend=backend,
    )

    assert dismissed == 1
    assert backend.sent[0] == ("%pane", "", True, True)
