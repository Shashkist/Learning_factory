# Solution

```bash
chmod +x ~/backup.sh
crontab -e
```

Add this line in the editor, save and exit:

```
*/5 * * * * ~/backup.sh
```

Then check it:

```bash
crontab -l
ls -l ~/backup.sh
```

The five fields are minute, hour, day of month, month and day of week. `*/5` in the minute field means every 5 minutes. Without `chmod +x`, cron cannot run the script. `0 * * * *` would run it once an hour, and `* * * * *` every minute.
