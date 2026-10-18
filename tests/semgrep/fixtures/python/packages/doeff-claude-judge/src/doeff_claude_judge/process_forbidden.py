import subprocess
from subprocess import run
import asyncio
import os


def judge_by_hand(argv, text):
    os.system("claude --version")
    asyncio.create_subprocess_exec(*argv)
    return subprocess.run(argv, input=text)


def judge_by_effect(request):
    return request
