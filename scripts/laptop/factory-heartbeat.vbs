' Learning Factory live-view heartbeat (scheduled task LearningFactoryHeartbeat, every minute).
' Between passes nothing else publishes, so the page would call an idle-but-awake laptop "offline".
' Pushes live.json only when the last push is >= 50 s old (passes push more often; this adds nothing
' then). Runs hidden through wscript: no console window flashes. Owner infrastructure (D35).
Option Explicit
Dim sh, fso, stateDir, script, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
stateDir = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\learning-factory"
If Not fso.FileExists(stateDir & "\progress_key") Then WScript.Quit 0
If fso.FileExists(stateDir & "\PAUSE") Then WScript.Quit 0
' The main checkout's publisher: it has --min-age even when the factory worktree is on an older main.
script = "C:\projects\Learning_factory\scripts\factory_progress.py"
If Not fso.FileExists(script) Then WScript.Quit 0
sh.Environment("Process")("LF_PROGRESS_FILE") = stateDir & "\progress.jsonl"
sh.Environment("Process")("LF_CREW_DIR") = sh.ExpandEnvironmentStrings("%USERPROFILE%") & "\.claude\projects\C--projects-Learning-factory-factory"
cmd = "python """ & script & """ publish --key """ & stateDir & "\progress_key"" --host root@207.154.193.238 --min-age 50"
sh.Run cmd, 0, True
