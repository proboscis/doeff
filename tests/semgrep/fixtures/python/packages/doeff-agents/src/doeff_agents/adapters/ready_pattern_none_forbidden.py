"""Hit fixture: built-in adapters must not regress to ready_pattern=None."""


class SampleAdapter:
    @property
    def ready_pattern(self) -> str | None:
        return None
