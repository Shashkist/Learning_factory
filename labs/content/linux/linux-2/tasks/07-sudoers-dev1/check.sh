#!/bin/sh
# Task 07: /etc/sudoers.d/dev1 exists, parses, has mode 0440 owned by root, and `sudo -l -U dev1`
# lists exactly one command, /usr/bin/systemctl status *. State only; the rule text is not compared,
# sudo itself says what dev1 may run, so NOPASSWD or a different runas list are fine.
F=/etc/sudoers.d/dev1
WANT='/usr/bin/systemctl status *'
rc=0

if [ -f "$F" ]; then
  echo "PASS 1"
else
  echo "FAIL 1 the file /etc/sudoers.d/dev1 does not exist; create it, for example with sudo tee /etc/sudoers.d/dev1"
  rc=1
fi

if [ -f "$F" ] && visudo -cf "$F" >/dev/null 2>&1; then
  echo "PASS 2"
elif [ -f "$F" ]; then
  echo "FAIL 2 visudo -cf /etc/sudoers.d/dev1 reports an error; run it to see which line is wrong, and fix the syntax"
  rc=1
else
  echo "FAIL 2 there is no file to check yet; create /etc/sudoers.d/dev1 first"
  rc=1
fi

mode=$(stat -c '%a %u' "$F" 2>/dev/null)
if [ "$mode" = "440 0" ]; then
  echo "PASS 3"
elif [ -f "$F" ]; then
  echo "FAIL 3 the file has mode ${mode%% *} or an owner other than root; sudo wants 0440 and root, use chmod 0440 /etc/sudoers.d/dev1"
  rc=1
else
  echo "FAIL 3 there is no file yet, so no mode to check; create /etc/sudoers.d/dev1 first"
  rc=1
fi

# What may dev1 run? One command per line, without the (runas) part and the NOPASSWD: style tags.
cmds=$(sudo -n -l -U dev1 2>/dev/null \
  | sed -n '/may run the following commands/,$p' | sed '1d' \
  | sed -E 's/^[[:space:]]+//; s/^\([^)]*\)[[:space:]]*//; s/(^|, )[A-Z_]+:[[:space:]]*/\1/g' \
  | sed 's/, /\n/g' | sed '/^$/d')
total=$(printf '%s' "$cmds" | grep -c .)
want=$(printf '%s\n' "$cmds" | grep -cxF "$WANT")

if [ "$want" -ge 1 ]; then
  echo "PASS 4"
else
  echo "FAIL 4 dev1 is not allowed to run $WANT through sudo yet; check what sudo -l -U dev1 says, and fix the rule (and item 2 first if the file has an error)"
  rc=1
fi

if [ "$total" -ge 1 ] && [ "$total" -eq "$want" ]; then
  echo "PASS 5"
elif [ "$total" -eq 0 ]; then
  echo "FAIL 5 dev1 has no sudo rule that works yet; fix the earlier items first"
  rc=1
else
  echo "FAIL 5 dev1 may run more than the status command; the rule must allow only $WANT, not ALL, not systemctl with any subcommand (see sudo -l -U dev1)"
  rc=1
fi
exit $rc
