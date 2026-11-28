#!/bin/bash
curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/index.html > ~/status-ok.txt
curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/nothing-here.html > ~/status-missing.txt
