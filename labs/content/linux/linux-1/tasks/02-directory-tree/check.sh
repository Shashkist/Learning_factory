#!/bin/sh
base=/home/student/projects/website
rc=0

if [ -d "$base" ]; then
  echo "PASS 1"
else
  echo "FAIL 1 there is no folder ~/projects/website yet; try mkdir with the full path"
  rc=1
fi

if [ -d "$base/css" ]; then
  echo "PASS 2"
else
  echo "FAIL 2 there is no folder css inside website; check the spelling and where it is"
  rc=1
fi

if [ -d "$base/images" ]; then
  echo "PASS 3"
else
  echo "FAIL 3 there is no folder images inside website; check the spelling and where it is"
  rc=1
fi

exit $rc
