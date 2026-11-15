#!/bin/sh
su student -s /bin/sh -c '
umask 022
cd /home/student || exit 1
rm -rf archive found.txt
mkdir -p archive/2022/winter archive/2023/spring/misc archive/2023/summer archive/old/docs
echo "flour, eggs, milk" > archive/2023/spring/misc/secret-recipe.txt
echo "soup" > archive/2022/winter/recipe.txt
echo "nothing here" > archive/2023/summer/secret-notes.txt
echo "bread" > archive/old/docs/old-recipe.txt
' </dev/null
exit $?
