# Give a name to a local service

A small web service runs on this machine, on port `8081`. You can reach it by address: `curl http://127.0.0.1:8081/`. Now give it a name, like DNS does for a real site.

1. Make the name `web.lab` point to `127.0.0.1` in `/etc/hosts`. A line in this file is the address, then one or more names.
2. Fetch the page of the service by its name, with `curl -s http://web.lab:8081/`, and save it into `~/web-reply.txt`.

Check yourself: `getent hosts web.lab` must answer `127.0.0.1`.

Hint: if a command answers with an error, read the message. It tells you what is wrong.
