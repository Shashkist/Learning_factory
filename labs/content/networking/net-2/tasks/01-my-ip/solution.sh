#!/bin/sh
ip -4 -o addr show scope global | awk '{print $4}' | head -n 1 > ~/myip.txt
