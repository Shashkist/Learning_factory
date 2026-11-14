#!/bin/sh
set -eu
base="${LF_BASE_PATH:-/}"
exec ttyd -p 7681 -W -b "$base" \
  script -q -f /var/log/lf/terminal.log -a -c 'su - student'
