#!/bin/bash
curl -s -m 5 -X POST -H 'Content-Type: application/json' -d '{"lab":"net-1","msg":"hello"}' http://127.0.0.1:9090/echo > ~/reply.json
