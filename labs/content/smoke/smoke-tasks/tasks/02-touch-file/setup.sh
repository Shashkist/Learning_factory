#!/bin/sh
# Leaves the home without ~/smoke-ok; safe to run again.
# Root has no DAC override here (D45), so the student removes it.
su student -s /bin/sh -c 'rm -f /home/student/smoke-ok' </dev/null
exit $?
