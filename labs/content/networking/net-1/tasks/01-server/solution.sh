#!/bin/bash
cd ~/site || exit 1
setsid nohup python3 -m http.server 8080 </dev/null >/dev/null 2>&1 &
sleep 1
curl -s -m 5 http://127.0.0.1:8080/index.html >/dev/null
