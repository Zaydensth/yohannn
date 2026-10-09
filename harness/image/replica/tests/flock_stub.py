#!/usr/bin/env python3
"""flock_stub.py [-n | -w SECS | -u] FD - a REAL flock(2) on an inherited file descriptor (macOS has no flock(1)).

A flock lock belongs to the open file description, not to the process that took it: taking it here, on the fd
the calling shell opened (`exec 8>FILE; flock -n 8`), leaves the shell holding it after this process exits -
exactly util-linux flock(1)'s behaviour. So a test can see a lock that leaks into an orphaned child (a `sleep`
that inherited fd 8), and a second instance that is correctly refused. Exit 0 = locked/unlocked, 1 = busy.
"""
import fcntl
import sys
import time

args = sys.argv[1:]
fd = int(args[-1])
if "-u" in args:
    fcntl.flock(fd, fcntl.LOCK_UN)
    sys.exit(0)
wait = float(args[args.index("-w") + 1]) if "-w" in args else 0.0
deadline = time.time() + wait
while True:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sys.exit(0)
    except BlockingIOError:
        if time.time() >= deadline:
            sys.exit(1)
        time.sleep(0.2)
