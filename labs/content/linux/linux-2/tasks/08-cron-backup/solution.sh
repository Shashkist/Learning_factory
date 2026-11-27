#!/bin/sh
chmod +x ~/backup.sh
( crontab -l 2>/dev/null; echo '*/5 * * * * ~/backup.sh' ) | crontab -
