import shutil
import subprocess


def launch_worker_bad() -> None:
    worker = shutil.which("codex")
    subprocess.run([worker, "exec"], check=False)
