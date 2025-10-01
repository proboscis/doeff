def migrate_file(filepath: Path) -> MigrationResult: ...

def main() -> Any: ...

class MigrationResult:
    changed: bool
    count: int

