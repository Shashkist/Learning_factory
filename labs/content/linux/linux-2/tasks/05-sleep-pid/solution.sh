#!/bin/sh
nohup sleep 600 >/dev/null 2>&1 &
echo $! > ~/sleep.pid
