# Schedule a job with cron

The script `~/backup.sh` was written for you. It must run by itself.

1. Make `~/backup.sh` executable.
2. Add a line to the crontab of the user `student` (you) that runs `~/backup.sh` every 5 minutes.

Look at the crontab with `crontab -l`. The crontab already has one line. Keep it.

Hint: `crontab -e` opens your crontab in an editor. A line has five time fields and then the command. In a field, `*` means "every" and `*/N` means "every N".
