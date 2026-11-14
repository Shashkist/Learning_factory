# Learning Factory laptop poller (owner-approved, docs/factory/DECISIONS.md D7).
# Runs from Windows Task Scheduler at logon and every 30 minutes.
# Does nothing unless open issues carry the label needs:laptop; then it starts one
# headless Claude Code factory pass, in the factory's own worktree, limited to those issues.

$ErrorActionPreference = 'Stop'
$Repo      = 'Shashkist/Learning_factory'
$Main      = 'C:\projects\Learning_factory'
$Worktree  = 'C:\projects\Learning_factory-factory'
$StateDir  = Join-Path $env:LOCALAPPDATA 'learning-factory'
$Log       = Join-Path $StateDir 'poll.log'
$Lock      = Join-Path $StateDir 'poll.lock'
$Claude    = 'C:\nvm4w\nodejs\claude.ps1'
$MaxMinutes = 60

New-Item -ItemType Directory -Force $StateDir | Out-Null
function Log($m) { "$(Get-Date -Format s)  $m" | Add-Content -Encoding utf8 $Log }

# One run at a time; a lock older than 2 hours is considered dead.
if (Test-Path $Lock) {
    if ((Get-Item $Lock).LastWriteTime -gt (Get-Date).AddHours(-2)) { Log 'skip: a run is in progress'; exit 0 }
    Remove-Item $Lock -Force
}

try {
    $issues = gh issue list -R $Repo --label 'needs:laptop' --state open --json number --jq '.[].number'
} catch { Log "skip: gh failed ($($_.Exception.Message))"; exit 0 }
if (-not $issues) { Log 'idle: no needs:laptop issues'; exit 0 }

New-Item -ItemType File -Force $Lock | Out-Null
try {
    $list = ($issues -split "`n" | Where-Object { $_ }) -join ', #'
    Log "work: needs:laptop issues #$list"

    if (-not (Test-Path $Worktree)) { git -C $Main worktree add $Worktree main 2>&1 | Out-Null }
    git -C $Worktree fetch -q origin
    git -C $Worktree checkout -q main
    git -C $Worktree pull -q --ff-only

    # Windows notification (best effort).
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $n = New-Object System.Windows.Forms.NotifyIcon
        $n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true
        $n.ShowBalloonTip(8000, 'Learning Factory', "Laptop tasks started: #$list", 'Info')
    } catch {}

    $prompt = "Load the project skill 'factory' and run one pass in LAPTOP MODE: work only on open issues labelled needs:laptop (#$list), in this worktree. Commit by explicit paths only. When an issue no longer needs the laptop, remove the needs:laptop label. Stop after $MaxMinutes minutes and update docs/factory/STATE.md."
    $job = Start-Job -ScriptBlock {
        param($wt, $claude, $p)
        Set-Location $wt
        & $claude -p $p --permission-mode auto 2>&1
    } -ArgumentList $Worktree, $Claude, $prompt
    if (-not (Wait-Job $job -Timeout ($MaxMinutes * 60 + 300))) { Stop-Job $job; Log 'stopped: time limit reached' }
    Receive-Job $job | Select-Object -Last 20 | ForEach-Object { Log "claude: $_" }
    Remove-Job $job -Force
    Log 'done'
} finally {
    Remove-Item $Lock -Force -ErrorAction SilentlyContinue
}
