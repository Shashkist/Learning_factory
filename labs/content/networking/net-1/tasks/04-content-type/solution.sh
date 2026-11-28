#!/bin/bash
curl -s -m 5 -o /dev/null -w '%{content_type}' http://127.0.0.1:8080/data.json > ~/content-type.txt
