# Start a web server

A web server is a program that waits for requests and answers them with files. Python has a tiny one built in.

The folder `~/site` holds a small web site (`index.html`, `data.json` and more). Start a web server that serves this folder on port `8080`, and leave it running in the background, so that you can keep using the terminal.

The command is `python3 -m http.server 8080`. It serves the folder you are in, so go into `~/site` first. Add `&` at the end of the command to run it in the background.

Hint: to see that it works, ask it for a page with `curl http://127.0.0.1:8080/index.html`.
