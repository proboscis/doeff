"""zeus の worker の tmpfs の上限の和が、機体の dir の memory-ledger.md の取り分の内にある事を、宣言の file から検める。"""

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "k8s"
# /cargo-build 16Gi・/tmp 8Gi・/dev/shm 8Gi・/memory-scratch 1536Mi の行の和(memory-ledger.md)。tmpfs の page は回収されず、機体の余白を減らす。
ZEUS_MEMORY_VOLUMES_CAP = 34 * 2**30
IN_MEMORY_VOLUME = re.compile(r"\{ name: ([\w-]+), emptyDir: \{ medium: Memory, sizeLimit: (\d+)(Gi|Mi) \} \}")
UNIT = {"Gi": 2**30, "Mi": 2**20}


def zeus_memory_volumes() -> dict[str, int]:
    files = [*sorted((DEPLOY / "nodes" / "zeus").glob("*.yaml")), DEPLOY / "worker" / "worker.yaml"]
    found = (match for path in files for match in IN_MEMORY_VOLUME.finditer(path.read_text(encoding="utf-8")))
    return {name: int(size) * UNIT[unit] for name, size, unit in (match.groups() for match in found)}


def test_zeus_worker_memory_volumes_stay_inside_the_machine_ledger() -> None:
    volumes = zeus_memory_volumes()
    assert {"tmp", "dshm", "memory-scratch"} <= set(volumes)
    assert sum(volumes.values()) <= ZEUS_MEMORY_VOLUMES_CAP
