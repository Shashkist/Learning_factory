#!/bin/sh
awk '$1 == "nameserver" {print $2; exit}' /etc/resolv.conf > ~/nameserver.txt
