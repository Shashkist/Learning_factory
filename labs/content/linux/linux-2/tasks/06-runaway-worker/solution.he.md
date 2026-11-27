# פתרון

```bash
pgrep -a runaway
sudo kill PID_OF_RUNAWAY_WORKER
pgrep -a runaway
```

קחו מ-`pgrep -a runaway` את ה-PID של השורה שכתוב בה `runaway-worker` (ולא `runaway-worker-keep`). לפי שם, השתמשו ב-`sudo pkill -x runaway-worker`: `-x` מתאים לשם התהליך המלא. `pkill runaway-worker` פשוט, או `pkill -f runaway-worker`, מתאימים לשני התהליכים, כי השם של השני מתחיל בשם של הראשון, והתהליך המשני מת גם הוא.
