# Learning Factory laptop runner (owner-approved, docs/factory/DECISIONS.md D1 + D7).
# Windows Task Scheduler starts it at logon and every 30 minutes. It runs one headless
# Claude Code manager pass when there is work, in the factory's own worktree.
#
# Pause:   New-Item "$env:LOCALAPPDATA\learning-factory\PAUSE"     (delete the file to resume)
# Logs:    $env:LOCALAPPDATA\learning-factory\poll.log  and  ...\runs\<time>.log

$ErrorActionPreference = 'Continue'   # native git/gh write progress to stderr; never treat that as fatal
$Repo        = 'Shashkist/Learning_factory'
$Main        = 'C:\projects\Learning_factory'
$Worktree    = 'C:\projects\Learning_factory-factory'
$StateDir    = Join-Path $env:LOCALAPPDATA 'learning-factory'
$RunsDir     = Join-Path $StateDir 'runs'
$Log         = Join-Path $StateDir 'poll.log'
$Lock        = Join-Path $StateDir 'poll.lock'
$Claude      = 'C:\nvm4w\nodejs\claude.ps1'
$Model       = 'claude-opus-5-5'
$PassMinutes = 90            # the manager stops itself after ~90 min or 3 merged PRs
$MaxPassesPerDay = 10        # cost guard
$ProgressKey = Join-Path $StateDir 'progress_key'   # forced-command key: can only write live.json
$ServerHost  = 'root@207.154.193.238'
$env:LF_PROGRESS_FILE = Join-Path $StateDir 'progress.jsonl'
$HooksFile   = Join-Path $Main 'scripts/laptop/factory-hooks.json'   # live-view hooks, factory runs only (D28)
$Progress    = if (Test-Path (Join-Path $Worktree 'scripts/factory_progress.py')) { Join-Path $Worktree 'scripts/factory_progress.py' } else { Join-Path $Main 'scripts/factory_progress.py' }
function Publish-Live([string]$state = '', [string]$note = '', [switch]$Force) {
    # Best effort: never let the live view break the factory.
    try {
        if ($state) { & python $Progress runner-state --state $state --note $note 2>$null | Out-Null }
        $extra = @(); if ($Force) { $extra = @('--force') }   # final state of a pass: never dropped by the 15 s gap
        if (Test-Path $ProgressKey) { & python $Progress publish --key $ProgressKey --host $ServerHost @extra 2>$null | Out-Null }
    } catch {}
}

New-Item -ItemType Directory -Force $StateDir, $RunsDir | Out-Null
function Log($m) { "$(Get-Date -Format s)  $m" | Add-Content -Encoding utf8 $Log }

function Notify($title, $text, $key) {
    # Rate-limited Windows notification: the same $key alerts at most once every 3 hours.
    $f = Join-Path $StateDir "alert-$key"
    if ((Test-Path $f) -and ((Get-Item $f).LastWriteTime -gt (Get-Date).AddHours(-3))) { return }
    New-Item -ItemType File -Force $f | Out-Null
    Log "ALERT [$key] $text"
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $n = New-Object System.Windows.Forms.NotifyIcon
        $n.Icon = [System.Drawing.SystemIcons]::Warning; $n.Visible = $true
        $n.ShowBalloonTip(20000, $title, $text, 'Warning')
        Start-Sleep -Seconds 20; $n.Dispose()
    } catch {}
}

if (Test-Path (Join-Path $StateDir 'PAUSE')) { Log 'paused (PAUSE file present)'; Publish-Live 'paused' 'PAUSE file present'; exit 0 }

# One run at a time; a lock older than 3 hours is considered dead.
if (Test-Path $Lock) {
    if ((Get-Item $Lock).LastWriteTime -gt (Get-Date).AddHours(-3)) { Log 'skip: a pass is running'; exit 0 }
    Remove-Item $Lock -Force
}

# A manager that outlived its runner (Stop-Job does not kill claude.exe) may still be working in the
# worktree: never start a second one next to it.
function Get-ManagerProcs { @(Get-CimInstance Win32_Process -Filter "Name='claude.exe'" | Where-Object { $_.CommandLine -like '*manager of the Learning Factory*' }) }
$alive = @(Get-ManagerProcs)   # @() here too: PS 5.1 unwraps a one-item array returned from a function, and a lone CimInstance has no .Count
if ($alive.Count -gt 0) { Log "skip: a manager is still running (pid $($alive.ProcessId -join ','))"; Publish-Live; exit 0 }

# One-time owner notification when the observability page goes live (owner request, 2026-11-14).
$DashFlag = Join-Path $StateDir 'dashboard-notified'
if (-not (Test-Path $DashFlag)) {
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 'https://factory.shashkist.com/snapshot.json'
        if ($r.StatusCode -eq 200 -and $r.Content.Length -gt 50) {
            New-Item -ItemType File -Force $DashFlag | Out-Null
            Log 'dashboard is live: owner notified'
            Add-Type -AssemblyName System.Windows.Forms
            $n = New-Object System.Windows.Forms.NotifyIcon
            $n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true
            Register-ObjectEvent $n BalloonTipClicked -Action { Start-Process 'https://factory.shashkist.com' } | Out-Null
            $n.ShowBalloonTip(30000, 'Learning Factory', 'The factory dashboard is live: factory.shashkist.com (click to open)', 'Info')
            Start-Sleep -Seconds 30; $n.Dispose()
        }
    } catch { }   # not live yet
}

# Daily budget.
$today = Get-Date -Format 'yyyy-MM-dd'
$countFile = Join-Path $StateDir "passes-$today.txt"
$passes = if (Test-Path $countFile) { [int](Get-Content $countFile) } else { 0 }
if ($passes -ge $MaxPassesPerDay) {
    Log "skip: daily budget reached ($passes passes)"
    Publish-Live 'budget' "daily budget of $MaxPassesPerDay passes used"
    Notify 'Learning Factory: daily budget used' "All $MaxPassesPerDay passes for today are used; the factory resumes tomorrow. Raise MaxPassesPerDay in factory-poll.ps1 to allow more." 'budget'
    exit 0
}

# Cheap pre-check: is there anything to do?
try {
    # Filter in PowerShell: Windows PowerShell 5.1 strips the double quotes inside a jq
    # expression passed to a native exe, which silently turned this check into "0".
    $wanted = 'status:ready', 'status:in-progress', 'status:in-review', 'type:incident', 'needs:laptop'
    $issues = gh issue list -R $Repo --state open --limit 200 --json labels | ConvertFrom-Json
    $work = @($issues | Where-Object { @($_.labels.name | Where-Object { $wanted -contains $_ }).Count -gt 0 }).Count
    $prs  = [int](gh pr list -R $Repo --state open --json number --jq length)   # no quotes, safe in PS 5.1
} catch { Log "skip: gh failed ($($_.Exception.Message))"; exit 0 }
if ([int]$work -eq 0 -and [int]$prs -eq 0) { Log 'idle: nothing ready, no open PRs'; Publish-Live 'idle' 'nothing ready'; exit 0 }

New-Item -ItemType File -Force $Lock | Out-Null
# Keep the laptop awake while the pass runs (idle sleep is 5 min on AC), the way a video player does.
# It only blocks idle sleep for this process's lifetime: no power setting changes, the screen may still
# turn off, and closing the lid or choosing Sleep still sleeps.
try {
    Add-Type -Namespace LF -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
    [void][LF.Power]::SetThreadExecutionState([uint32]'0x80000001')   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
} catch { Log "keep-awake unavailable: $($_.Exception.Message)" }
try {
    Set-Content $countFile ($passes + 1)
    Log "pass $($passes + 1)/$MaxPassesPerDay today: $work issue(s) with work, $prs open PR(s)"

    # The factory's worktree lives on its own local branch 'factory' that follows origin/main
    # (main itself is checked out in the shared checkout and cannot be checked out twice).
    if (-not (Test-Path $Worktree)) {
        git -C $Main fetch -q origin
        git -C $Main worktree add -q -B factory $Worktree origin/main
        if ($LASTEXITCODE -ne 0) { throw "worktree add failed ($LASTEXITCODE)" }
    }
    git -C $Worktree fetch -q origin
    $dirty = git -C $Worktree status --porcelain
    if ($dirty) { Log 'worktree has uncommitted changes from a previous pass: the manager will handle them' }
    else {
        git -C $Worktree checkout -q factory
        git -C $Worktree reset -q --hard origin/main
    }

    try {
        Add-Type -AssemblyName System.Windows.Forms
        $n = New-Object System.Windows.Forms.NotifyIcon
        $n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true
        $n.ShowBalloonTip(8000, 'Learning Factory', 'Factory pass started', 'Info')
    } catch {}

    $prompt = @"
You are the manager of the Learning Factory software factory, running one pass on the owner's laptop in the worktree $Worktree. The owner approved hands-off operation in advance (docs/factory/DECISIONS.md D1, D7).
This worktree is on the local branch 'factory' which follows origin/main: push state commits with 'git push origin HEAD:main' after 'git pull --rebase origin main'; feature work goes on f/<n>-<slug> branches as usual.
Read CLAUDE.md, then load the project skill 'factory' and follow it exactly, starting from docs/factory/STATE.md. Take issues labelled needs:laptop first, then the normal queue.
Decide yourself and record decisions in docs/factory/DECISIONS.md; put owner-only blockers in docs/factory/OWNER.md and move on. Commits, pushes and squash-merges of green, reviewed PRs are pre-approved; commit by explicit paths only; never add attribution or mention Claude, AI or agents in commits, PRs or comments.
Stop after about $PassMinutes minutes of work or 3 merged PRs, and update docs/factory/STATE.md so the next pass resumes exactly where you stopped.
"@
    & python $Progress pass-start --pass-no ($passes + 1) --budget $MaxPassesPerDay 2>$null | Out-Null
    Publish-Live
    $runLog = Join-Path $RunsDir ("{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmm'))
    $job = Start-Job -ScriptBlock {
        param($wt, $claude, $model, $p, $out, $progressFile, $hooks)
        Set-Location $wt
        $env:LF_PROGRESS_FILE = $progressFile        # hooks record only when this is set
        # Headless claude stops background subagents 10 min after the manager's last turn; pass 9 lost an
        # implementer mid-fix that way. Wait for them: the awake-time limit below still bounds the pass.
        $env:CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS = '0'
        $extra = if (Test-Path $hooks) { @('--settings', $hooks) } else { @() }
        & $claude -p $p --model $model --permission-mode auto @extra *>&1 | Out-File -Encoding utf8 $out
    } -ArgumentList $Worktree, $Claude, $Model, $prompt, $runLog, $env:LF_PROGRESS_FILE, $HooksFile
    $result = 'done'
    # The limit counts awake time only: a gap of more than a minute between two ticks means the laptop
    # slept, and that time is not charged to the pass (a night of sleep used to "time out" a working pass).
    $limit = [TimeSpan]::FromMinutes($PassMinutes + 20); $awake = [TimeSpan]::Zero; $tick = Get-Date
    while ($job.State -eq 'Running' -and $awake -lt $limit) {
        Publish-Live; Start-Sleep -Seconds 5                                      # publish skips unchanged content
        $now = Get-Date; $gap = $now - $tick; $tick = $now
        if ($gap.TotalSeconds -lt 60) { $awake += $gap }
    }
    if ($job.State -eq 'Running') {
        Stop-Job $job
        foreach ($p in Get-ManagerProcs) { & taskkill.exe /PID $p.ProcessId /T /F 2>$null | Out-Null }   # Stop-Job leaves claude.exe running
        $result = 'timeout'; Log 'stopped: pass exceeded its time limit'
        Notify 'Learning Factory: pass timed out' "A pass ran longer than $($PassMinutes + 20) min and was stopped. See $runLog" 'timeout'
    }
    Remove-Job $job -Force
    $tail = if (Test-Path $runLog) { (Get-Content $runLog -Tail 3) -join ' | ' } else { '(no output)' }
    Log "$result. last lines: $tail"
    & python $Progress pass-end --result $result 2>$null | Out-Null
    Publish-Live -Force
    if ((Test-Path $runLog) -and ((Get-Item $runLog).Length -eq 0)) {
        Notify 'Learning Factory: pass produced no output' "The last pass wrote nothing. Is Claude Code logged in? See $runLog" 'empty'
    }
    # Expired sign-in (owner decision O6: no long-lived token, the owner signs in again when asked).
    # The early warning "this sign-in ends between ..." appears in every log and is not a failure.
    if (Test-Path $runLog) {
        $authFail = Get-Content $runLog | Where-Object { $_ -notmatch 'sign-in ends' -and $_ -match 'authentication_error|OAuth token has expired|Please run /login|Not logged in|Invalid API key|status 401' }
        if ($authFail) {
            Log 'auth: Claude Code sign-in has expired'
            Notify 'Learning Factory: sign in again' 'Claude Code is signed out, so the factory is waiting. Open a terminal and run: claude auth login' 'auth'
        }
    }
    # No-progress detector: 3 passes in a row without a new commit on origin/main.
    git -C $Worktree fetch -q origin
    $sha = (git -C $Worktree rev-parse origin/main).Trim()
    $pf = Join-Path $StateDir 'progress.json'
    $prog = if (Test-Path $pf) { Get-Content $pf -Raw | ConvertFrom-Json } else { [pscustomobject]@{ sha = ''; stale = 0 } }
    if ($prog.sha -eq $sha) { $prog.stale = [int]$prog.stale + 1 } else { $prog.sha = $sha; $prog.stale = 0 }
    $prog | ConvertTo-Json | Set-Content -Encoding utf8 $pf
    if ($prog.stale -ge 3) {
        Notify 'Learning Factory: no progress' "3 passes in a row ended without a new commit on main. Check docs/factory/STATE.md and $runLog" 'stalled'
    }
    [pscustomobject]@{ ended = (Get-Date -Format s); result = $result; log = $runLog; main = $sha } |
        ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $StateDir 'last-pass.json')
} catch {
    Log "error: $($_.Exception.Message)"
    Notify 'Learning Factory: pass failed' "$($_.Exception.Message)" 'error'
} finally {
    try { [void][LF.Power]::SetThreadExecutionState([uint32]'0x80000000') } catch {}   # ES_CONTINUOUS: allow sleep again
    Remove-Item $Lock -Force -ErrorAction SilentlyContinue
}
