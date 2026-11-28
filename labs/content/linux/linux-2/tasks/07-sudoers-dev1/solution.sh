#!/bin/sh
echo 'dev1 ALL=(root) /usr/bin/systemctl status *' | sudo tee /etc/sudoers.d/dev1 >/dev/null
sudo chmod 0440 /etc/sudoers.d/dev1
sudo visudo -cf /etc/sudoers.d/dev1
