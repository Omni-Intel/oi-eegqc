"""Spawn targets for fault injection; no scientific dependencies."""
import os
import time


def blocked_scores(connection):
    connection.recv()
    connection.send(("phase", "评分"))
    time.sleep(60)


def fault_scores(connection):
    request = connection.recv()
    if request["path"] == "timeout":
        time.sleep(60)
    elif request["path"] == "crash":
        os._exit(7)
    else:
        connection.send(("result", {"availability": "Available", "gqi": 90}))
        connection.recv()
