#!/bin/sh
set -eu
# A tmpfs over /home/student hides the image home: add the /etc/skel files, never overwriting
# (a task setup may have written into the home before this ran).
# The spec mounts it owned by student (uid=1001); root has no CAP_CHOWN, so copy as student.
su student -s /bin/sh -c 'cp -Rn /etc/skel/. /home/student/' || true
# Sudo labs only (the spec sets LF_SUDO=1): the image itself has no sudoers entry for student.
if [ "${LF_SUDO:-}" = "1" ]; then
  echo 'student ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/student
  chmod 0440 /etc/sudoers.d/student
fi
base="${LF_BASE_PATH:-/}"
exec ttyd -p 7681 -W -b "$base" \
  script -q -f /var/log/lf/terminal.log -a -c 'su - student'
