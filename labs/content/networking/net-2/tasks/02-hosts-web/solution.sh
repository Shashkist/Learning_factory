#!/bin/sh
echo '127.0.0.1 web.lab' | sudo tee -a /etc/hosts >/dev/null
curl -s http://web.lab:8081/ > ~/web-reply.txt
