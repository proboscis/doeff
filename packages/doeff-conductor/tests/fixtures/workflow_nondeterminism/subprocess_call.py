import subprocess


def run_tests() -> None:
    subprocess.run(["pytest"], check=True)
