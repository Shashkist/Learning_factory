#!/bin/sh
set -eu
# A tmpfs over /home/student hides the image's home: fill an empty one from /etc/skel.
# The spec mounts it owned by student (uid=1001); root has no CAP_CHOWN, so copy as student.
if [ -z "$(ls -A /home/student 2>/dev/null)" ]; then
  su student -s /bin/sh -c 'cp -R /etc/skel/. /home/student/' || true
fi
# Sudo labs only (the spec sets LF_SUDO=1): the image itself has no sudoers entry for student.
if [ "${LF_SUDO:-}" = "1" ]; then
  echo 'student ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/student
  chmod 0440 /etc/sudoers.d/student
fi
base="${LF_BASE_PATH:-/}"
exec ttyd -p 7681 -W -b "$base" \
  script -q -f /var/log/lf/terminal.log -a -c 'su - student'
