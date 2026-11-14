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

New-Item -ItemType Directory -Force $StateDir, $RunsDir | Out-Null
function Log($m) { "$(Get-Date -Format s)  $m" | Add-Content -Encoding utf8 $Log }

if (Test-Path (Join-Path $StateDir 'PAUSE')) { Log 'paused (PAUSE file present)'; exit 0 }

# One run at a time; a lock older than 3 hours is considered dead.
if (Test-Path $Lock) {
    if ((Get-Item $Lock).LastWriteTime -gt (Get-Date).AddHours(-3)) { Log 'skip: a pass is running'; exit 0 }
    Remove-Item $Lock -Force
}

# Daily budget.
$today = Get-Date -Format 'yyyy-MM-dd'
$countFile = Join-Path $StateDir "passes-$today.txt"
$passes = if (Test-Path $countFile) { [int](Get-Content $countFile) } else { 0 }
if ($passes -ge $MaxPassesPerDay) { Log "skip: daily budget reached ($passes passes)"; exit 0 }

# Cheap pre-check: is there anything to do?
try {
    # Filter in PowerShell: Windows PowerShell 5.1 strips the double quotes inside a jq
    # expression passed to a native exe, which silently turned this check into "0".
    $wanted = 'status:ready', 'status:in-progress', 'status:in-review', 'type:incident', 'needs:laptop'
    $issues = gh issue list -R $Repo --state open --limit 200 --json labels | ConvertFrom-Json
    $work = @($issues | Where-Object { @($_.labels.name | Where-Object { $wanted -contains $_ }).Count -gt 0 }).Count
    $prs  = [int](gh pr list -R $Repo --state open --json number --jq length)   # no quotes, safe in PS 5.1
} catch { Log "skip: gh failed ($($_.Exception.Message))"; exit 0 }
if ([int]$work -eq 0 -and [int]$prs -eq 0) { Log 'idle: nothing ready, no open PRs'; exit 0 }

New-Item -ItemType File -Force $Lock | Out-Null
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
    $runLog = Join-Path $RunsDir ("{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmm'))
    $job = Start-Job -ScriptBlock {
        param($wt, $claude, $model, $p, $out)
        Set-Location $wt
        & $claude -p $p --model $model --permission-mode auto *>&1 | Out-File -Encoding utf8 $out
    } -ArgumentList $Worktree, $Claude, $Model, $prompt, $runLog
    if (-not (Wait-Job $job -Timeout (($PassMinutes + 20) * 60))) { Stop-Job $job; Log 'stopped: pass exceeded its time limit' }
    Remove-Job $job -Force
    $tail = if (Test-Path $runLog) { (Get-Content $runLog -Tail 3) -join ' | ' } else { '(no output)' }
    Log "done. last lines: $tail"
} catch {
    Log "error: $($_.Exception.Message)"
} finally {
    Remove-Item $Lock -Force -ErrorAction SilentlyContinue
}
