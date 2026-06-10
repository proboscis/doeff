# doeff: workflow
import socket


def host_name() -> str:
    return socket.gethostname()
