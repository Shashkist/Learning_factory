# Waiting for the owner

The factory never waits on these. It works around them and comes back when they are ticked. Tick a box (`[x]`) and push, or tell any session "done: O2".

## Actions

> 2026-11-14: O1–O3 done by `scripts/owner/setup-secrets.sh` (owner pre-approval D7): `labs` and `factory` A records, a new repo-only deploy key, and role `lf_app` with schema `learning_factory` (login verified). O4 skipped by the owner's choice.

- [x] **O1 · DNS record for `labs.shashkist.com`.** DigitalOcean → Networking → Domains → `shashkist.com` → add an **A record**: hostname `labs`, value `207.154.193.238`, TTL 3600. (Or add a `digitalocean_record` in `EMEW-AI/Digital Ocean/terraform/main.tf` and `terraform apply`.) Until then the factory tests through the server IP with a `Host:` header; the certificate step (D4) waits for this.
- [x] **O2 · Deploy secrets for this repo** (the same values e-sign uses). Run from a shell that has them:
  ```bash
  gh secret set SERVER_HOST     -R Shashkist/Learning_factory   # paste the value when prompted
  gh secret set SERVER_USER     -R Shashkist/Learning_factory
  gh secret set SSH_PRIVATE_KEY -R Shashkist/Learning_factory < path/to/deploy_key
  ```
- [x] **O3 · Database access for the new schema.** In Supabase (the JSoul project) → SQL editor, run the script the factory writes to `labs/deploy/db/001-create-schema-and-role.sql` **once it exists** (it creates schema `learning_factory` and a role that can use only that schema). Then store the role's connection string (pooler, port 6543):
  ```bash
  gh secret set LF_DATABASE_URL -R Shashkist/Learning_factory
  ```
- [ ] **O4 · (optional, skipped for now) Second-opinion reviewer.** `gh secret set OPENROUTER_API_KEY -R Shashkist/Learning_factory`. Without it, the cross-company PR review is skipped and the PRs say so.
- [x] **O5 · Copy the labs nginx block** into `EMEW-AI/Digital Ocean/default.conf` — **done 2026-11-15 through that repo (commit 0f4dbc3): the file is now byte-identical to the server's live `default.conf` with the real `labs` and `factory` blocks; its pipeline redeployed it (nginx restarted, address unchanged).** Repeat when the factory changes a block: copy the server's file, never hand-merge (D3).

- [x] **O6 · Claude Code sign-in for the laptop runner** — **owner decision 2026-11-15: no long-lived token; the owner signs in again (`claude auth login`) when the runner's Windows alert asks. Do not remind the owner about the expiry date in reports.** When a pass fails because the sign-in has expired, the runner shows a Windows alert "Learning Factory: sign in again" and the factory waits until then.
- [x] **O7 · GitHub Actions minutes** — **resolved 2026-11-15: the repository is public (D43), so GitHub-hosted Actions minutes are free.**

- [x] **O8 · Move the server deploy secrets into the `production` environment** — **done 2026-11-14 (both steps; publish verified with the new key)**. Step 1 done 2026-11-14 by `scripts/owner/setup-production-env.sh` (owner pre-approval D7): env secrets `SSH_PRIVATE_KEY` (new key `lf-deploy-prod`), `SERVER_HOST`, `SERVER_USER` are set; the old repo-level key still works. **Step 2, after #25 merges:** `bash scripts/owner/setup-production-env.sh --finish` (deletes the repo-level secrets and the old `lf-deploy` key on the server). Original note: (issue #25, D15). Only workflow runs on `main` can then read the root SSH key. After the factory has created the environment (it will note that on #25), run:
  ```bash
  gh secret set SSH_PRIVATE_KEY --env production -R Shashkist/Learning_factory < path/to/deploy_key
  gh secret set SERVER_HOST     --env production -R Shashkist/Learning_factory
  gh secret set SERVER_USER     --env production -R Shashkist/Learning_factory
  gh secret delete SSH_PRIVATE_KEY -R Shashkist/Learning_factory
  gh secret delete SERVER_HOST     -R Shashkist/Learning_factory
  gh secret delete SERVER_USER     -R Shashkist/Learning_factory
  ```
  Do the deletes only after #25 is merged (the workflows then use `environment: production`); until then nothing changes.

- [x] **O10 · Move `LF_DATABASE_URL` into the `production` environment** — **done 2026-11-15: copied unchanged from the server's `.env` into the `production` environment (no rotation, the running manager untouched), repo-level copy deleted; `DB check` run 43722854463 connected with the environment copy only.** Original note (from the review of #63, same reason as O8): a repo-level secret is readable by a workflow on any branch. For reference, the manual way:
  ```bash
  gh secret set LF_DATABASE_URL --env production -R Shashkist/Learning_factory   # paste the value when prompted
  gh secret delete LF_DATABASE_URL -R Shashkist/Learning_factory
  ```
  Until then nothing breaks. The factory verifies by behaviour (the next `DB check` run still connects), never by reading the value.

- [x] **O11 · First admin** — **replaced 2026-11-15 by owner decision: admins sign in with Google, allow-list only (#92, see O14). No password admin is created; `create-admin` stays as break-glass.** The old instructions, for break-glass only:
  ```bash
  docker exec -i lf-manager python -m app.cli create-admin <username>
  ```
  Type the password when prompted (or pipe it: `printf '%s\n' "$PW" | docker exec -i lf-manager python -m app.cli create-admin <username>`). To change it later add `--reset`. The deploy needs the production environment to hold `SSH_PRIVATE_KEY`, `SERVER_HOST`, `SERVER_USER`, `LF_DATABASE_URL` (O10) and the variable `SERVER_HOST_KEY`; without them it prints which are missing and does nothing.
- [x] **O12 · Back up `/root/learning-factory/.env`** — **owner decision 2026-11-15: skipped. If the file is ever lost, students re-register ("we will remake it").** Do not ask again; a factory change that would make the loss worse (e.g. more secrets in that file) must say so in its PR. Original note: the first deploy generates `LF_ID_SECRET` there; every student ID hash depends on it.
- [x] **O13** removed by #93, D132.

- [x] **O9 · What `lf_app` can reach through `PUBLIC`** — **handed to the manager by the owner (2026-11-15): issue #61.** The original note and query are kept below for the manager.
  The role sees tables only in `learning_factory`, but like every role it inherits `PUBLIC`'s rights: it can call functions in schema `public` and read the catalog. A `SECURITY DEFINER` function in `public` would run with its owner's rights. Check:
  ```sql
  select p.oid::regprocedure as fn, p.prosecdef as security_definer
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and has_function_privilege('lf_app', p.oid, 'EXECUTE')
  order by 2 desc, 1;
  ```

- [ ] **O29 · (5 minutes) Switch the laptop runner to the factory plugin at v0.2.0** — #614, D175. The runner starts taking the factory methodology from the plugin of Shashkist/factory at tag `v0.2.0` instead of the project skill. What the change does: a shallow clone of Shashkist/factory tag `v0.2.0` (commit `29a613e65389cbee2dc646cec25e71863d00064c`) under `%LOCALAPPDATA%\learning-factory\factory-v0.2.0`, refused if the tag moved (its HEAD must be that exact SHA); `uv tool install` of `factory-tools` from that clone, once per SHA (again when the marker file is missing or `factory-tools --help` does not answer); two shims, `factory-tools.cmd` (PowerShell, cmd) and an extension-less `factory-tools` shell script (Git Bash: the hooks and the agents' Bash calls), written to `%LOCALAPPDATA%\learning-factory\bin`, which goes first on PATH: both run the tool environment's `python.exe -P -m factory_tools` (`-P`: the caller's folder is not put first on `sys.path`), because this laptop refuses the `.exe` launcher that uv writes ("Access is denied"), so uv's own tool folder is not put on PATH; `claude` starts with `--plugin-dir`, the pass prompt names the plugin skill `factory:factory` and its role agents `factory:exam-writer`, `factory:implementer`, `factory:reviewer`, `factory:researcher` (not the project's agents of the same names); the runner's progress calls (each best-effort, a failure never aborts a pass) and the hooks go through `factory-tools progress ...` (same events file); the guard hook is untouched. The clone, the install and the shims run best-effort at the top of every tick (so the live view works before the first pass) and are required again inside a pass. Prerequisite: `uv` and `git` on PATH. Steps, in an owner session in `C:\projects\Learning_factory`: (1) `git pull`, so the diff meets today's runner files; (2) extract the diff below (it is indented inside this list item) with `python -c "import pathlib,textwrap; t=pathlib.Path('docs/factory/OWNER.md').read_text(encoding='utf-8'); f=chr(96)*3; b=t.index(f+'diff', t.index('**O29'))+7; e=t.index(f, b); pathlib.Path('o29.patch').write_text(textwrap.dedent(t[b:e]).lstrip(), encoding='utf-8', newline='\n')"`, which writes `o29.patch`; (3) `git apply o29.patch`, then delete `o29.patch`; (4) commit both runner files as one commit (`chore(runner): switch to the factory plugin v0.2.0 (#614)`), and push. How to check it worked: wait for one pass; then the clone folder `factory-v0.2.0`, the marker file `factory-tools-29a613e65389cbee2dc646cec25e71863d00064c` and the folder `bin` (with the two shims) exist under `%LOCALAPPDATA%\learning-factory`, `& "$env:LOCALAPPDATA\learning-factory\bin\factory-tools.cmd" --help` prints the tool's help (the shim folder is on PATH only inside the runner), `poll.log` has the line `installed factory-tools from v0.2.0`, and the pass's lane shows on the live page factory.shashkist.com. Then comment on #614 and tick this box (the issue closes on your report of that first pass). Switch back: `git revert <that commit>`, push, and delete the marker file `%LOCALAPPDATA%\learning-factory\factory-tools-29a613e65389cbee2dc646cec25e71863d00064c` (otherwise a later switch skips the install; optionally also `uv tool uninstall factory-tools`). This revert path works only while the ported progress script and the project skill `factory` exist: it ends when #615 removes them.
  ```diff
  diff --git a/scripts/laptop/factory-hooks.json b/scripts/laptop/factory-hooks.json
  index 17bd644..d382509 100644
  --- a/scripts/laptop/factory-hooks.json
  +++ b/scripts/laptop/factory-hooks.json
  @@ -17,7 +17,7 @@
           "hooks": [
             {
               "type": "command",
  -            "command": "python \"${CLAUDE_PROJECT_DIR:-.}/scripts/factory_progress.py\" hook || true",
  +            "command": "factory-tools progress hook || true",
               "timeout": 5
             }
           ]
  @@ -29,7 +29,7 @@
           "hooks": [
             {
               "type": "command",
  -            "command": "python \"${CLAUDE_PROJECT_DIR:-.}/scripts/factory_progress.py\" hook || true",
  +            "command": "factory-tools progress hook || true",
               "timeout": 5
             }
           ]
  @@ -40,7 +40,7 @@
           "hooks": [
             {
               "type": "command",
  -            "command": "python \"${CLAUDE_PROJECT_DIR:-.}/scripts/factory_progress.py\" hook || true",
  +            "command": "factory-tools progress hook || true",
               "timeout": 5
             }
           ]
  @@ -51,7 +51,7 @@
           "hooks": [
             {
               "type": "command",
  -            "command": "python \"${CLAUDE_PROJECT_DIR:-.}/scripts/factory_progress.py\" hook || true",
  +            "command": "factory-tools progress hook || true",
               "timeout": 5
             }
           ]
  @@ -62,7 +62,7 @@
           "hooks": [
             {
               "type": "command",
  -            "command": "python \"${CLAUDE_PROJECT_DIR:-.}/scripts/factory_progress.py\" hook || true",
  +            "command": "factory-tools progress hook || true",
               "timeout": 5
             }
           ]
  @@ -73,7 +73,7 @@
           "hooks": [
             {
               "type": "command",
  -            "command": "python \"${CLAUDE_PROJECT_DIR:-.}/scripts/factory_progress.py\" hook || true",
  +            "command": "factory-tools progress hook || true",
               "timeout": 5
             }
           ]
  diff --git a/scripts/laptop/factory-poll.ps1 b/scripts/laptop/factory-poll.ps1
  index 3da353c..379c7e0 100644
  --- a/scripts/laptop/factory-poll.ps1
  +++ b/scripts/laptop/factory-poll.ps1
  @@ -19,16 +19,21 @@ $PassMinutes = 90            # the manager stops itself after ~90 min or 3 merge
   $MaxPassesPerDay = 0         # cost guard; 0 = unlimited (owner, 2026-11-15: unlimited for now). Claude plan limits still apply
   $ProgressKey = Join-Path $StateDir 'progress_key'   # forced-command key: can only write live.json
   $ServerHost  = 'root@207.154.193.238'
  +# The factory methodology (plugin + factory-tools), pinned (#614, D175). A moved tag is refused: the clone's HEAD must be this SHA.
  +$FactoryTag  = 'v0.2.0'
  +$FactorySha  = '29a613e65389cbee2dc646cec25e71863d00064c'
  +$FactoryDir  = Join-Path $StateDir "factory-$FactoryTag"   # shallow clone: plugin\ and the factory-tools source
  +$FactoryBin  = Join-Path $StateDir 'bin'                   # shims for factory-tools (uv's own .exe launcher is refused on this laptop)
  +$env:PATH    = $FactoryBin + ';' + $env:PATH               # first on PATH: the runner, claude and its hooks find factory-tools here
   $env:LF_PROGRESS_FILE = Join-Path $StateDir 'progress.jsonl'
   $env:LF_CREW_DIR = Join-Path $env:USERPROFILE '.claude\projects\C--projects-Learning-factory-factory'   # crew view: factory transcripts only
   $HooksFile   = Join-Path $Main 'scripts/laptop/factory-hooks.json'   # live-view hooks, factory runs only (D28)
  -$Progress    = if (Test-Path (Join-Path $Worktree 'scripts/factory_progress.py')) { Join-Path $Worktree 'scripts/factory_progress.py' } else { Join-Path $Main 'scripts/factory_progress.py' }
   function Publish-Live([string]$state = '', [string]$note = '', [switch]$Force) {
       # Best effort: never let the live view break the factory.
       try {
  -        if ($state) { & python $Progress runner-state --state $state --note $note 2>$null | Out-Null }
  +        if ($state) { & factory-tools progress runner-state --state $state --note $note 2>$null | Out-Null }
           $extra = @(); if ($Force) { $extra = @('--force') }   # final state of a pass: never dropped by the 15 s gap
  -        if (Test-Path $ProgressKey) { & python $Progress publish --key $ProgressKey --host $ServerHost @extra 2>$null | Out-Null }
  +        if (Test-Path $ProgressKey) { & factory-tools progress publish --key $ProgressKey --host $ServerHost @extra 2>$null | Out-Null }
       } catch {}
   }
   
  @@ -47,7 +52,7 @@ function Stop-OrphanHooks {
       try {
           $cutoff = (Get-Date).AddMinutes(-2)
           $old = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
  -            $_.CommandLine -match '[\/]factory_progress\.py"?\s+hook\b|[\/]factory_guard\.py' -and $_.CreationDate -lt $cutoff })
  +            $_.CommandLine -match '[\/]factory_progress\.py"?\s+hook\b|[\/]factory_guard\.py|factory_tools"?\s+progress\s+hook\b' -and $_.CreationDate -lt $cutoff })
           foreach ($p in $old) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
           if ($old.Count -gt 0) { Log "stopped $($old.Count) orphaned hook process(es) (#230)" }
       } catch {}
  @@ -72,7 +77,40 @@ function Enter-PassJob {
       } catch { return $false }
   }
   
  +# The factory methodology at the pinned tag (#614, D175): clone once, refuse a moved tag, install factory-tools once per SHA.
  +# uv's own factory-tools.exe launcher is refused on this laptop ("Access is denied"), so the tool runs through two shims
  +# in $FactoryBin that start the tool environment's python: factory-tools.cmd (PowerShell, cmd) and factory-tools (Git Bash).
  +function Test-FactoryTools {
  +    try { $global:LASTEXITCODE = 1; & factory-tools --help *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false }
  +}
  +function Install-Factory {
  +    # $true when factory-tools answers; $false (and a log line) when the clone, the tag or the install is not right.
  +    $toolsMarker = Join-Path $StateDir "factory-tools-$FactorySha"
  +    if (-not (Test-Path (Join-Path $FactoryDir '.git'))) {
  +        git clone -q --depth 1 --branch $FactoryTag https://github.com/Shashkist/factory $FactoryDir 2>$null
  +        if ($LASTEXITCODE -ne 0) { Log "clone of factory $FactoryTag failed ($LASTEXITCODE)"; return $false }
  +        Log "cloned factory $FactoryTag"
  +    }
  +    $head = "$(git -C $FactoryDir rev-parse HEAD)".Trim()
  +    if ($head -ne $FactorySha) { Log "factory $FactoryTag is at $head, expected ${FactorySha}: the tag moved, refusing it"; return $false }
  +    if ((Test-Path $toolsMarker) -and (Test-FactoryTools)) { return $true }
  +    uv tool install --force $FactoryDir 2>$null | Out-Null
  +    if ($LASTEXITCODE -ne 0) { Log "uv tool install of factory-tools failed ($LASTEXITCODE)"; return $false }
  +    $py = Join-Path "$(uv tool dir)".Trim() 'factory-tools\Scripts\python.exe'
  +    if (-not (Test-Path $py)) { Log "factory-tools environment has no python at $py"; return $false }
  +    New-Item -ItemType Directory -Force $FactoryBin | Out-Null
  +    Set-Content -Encoding ascii (Join-Path $FactoryBin 'factory-tools.cmd') "@`"$py`" -P -m factory_tools %*"
  +    $pyFwd = $py -replace '\\', '/'
  +    [IO.File]::WriteAllText((Join-Path $FactoryBin 'factory-tools'), (@('#!/bin/sh', "exec `"$pyFwd`" -P -m factory_tools `"`$@`"") -join "`n") + "`n")
  +    if (-not (Test-FactoryTools)) { Log 'factory-tools does not answer --help through its shim'; return $false }
  +    New-Item -ItemType File -Force $toolsMarker | Out-Null
  +    Log "installed factory-tools from $FactoryTag"
  +    return $true
  +}
  +
   Stop-OrphanHooks
  +# Best effort, before the first Publish-Live (the live view needs factory-tools), never while a pass holds the lock; a pass requires it again below.
  +try { if (-not (Test-Path $Lock) -and -not (Install-Factory)) { Log 'factory-tools not ready yet; a pass will not start without it' } } catch { Log "factory install failed: $($_.Exception.Message)" }
   
   function Notify($title, $text, $key) {
       # Rate-limited Windows notification: the same $key alerts at most once every 3 hours.
  @@ -111,7 +149,7 @@ if ($alive.Count -gt 0) {
           foreach ($p in $alive) { & taskkill.exe /PID $p.ProcessId /T /F 2>$null | Out-Null }
           Log "stopped: orphaned manager (pid $($alive.ProcessId -join ',')) had no runner and started over 3 h ago"
           Notify 'Learning Factory: orphaned pass stopped' 'A manager outlived its runner for over 3 hours and was stopped. The next pass resumes from STATE.md.' 'orphan'
  -        & python $Progress pass-end --result timeout 2>$null | Out-Null
  +        try { & factory-tools progress pass-end --result timeout 2>$null | Out-Null } catch {}
           Publish-Live -Force
           exit 0
       }
  @@ -206,30 +244,34 @@ try {
           $n.ShowBalloonTip(8000, 'Learning Factory', 'Factory pass started', 'Info')
       } catch {}
   
  +    if (-not (Install-Factory)) { throw "factory $FactoryTag is not ready (see poll.log): a pass does not start without it" }
  +    $pluginDir = Join-Path $FactoryDir 'plugin'
  +
       $prompt = @"
   You are the manager of the Learning Factory software factory, running one pass on the owner's laptop in the worktree $Worktree. The owner approved hands-off operation in advance (docs/factory/DECISIONS.md D1, D7).
   This worktree is on the local branch 'factory' which follows origin/main: push state commits with 'git push origin HEAD:main' after 'git pull --rebase origin main'; feature work goes on f/<n>-<slug> branches as usual.
  -Read CLAUDE.md, then load the project skill 'factory' and follow it exactly, starting from docs/factory/STATE.md. Take issues labelled needs:laptop first, then the normal queue.
  +Read CLAUDE.md, then load the plugin skill 'factory:factory' (the factory methodology at $FactoryTag, not the project skill 'factory') and follow it exactly, starting from docs/factory/STATE.md; project facts come from 'factory-tools methodology render'. Dispatch the plugin's role agents 'factory:exam-writer', 'factory:implementer', 'factory:reviewer' and 'factory:researcher', not the project's agents of the same names. Take issues labelled needs:laptop first, then the normal queue.
   Decide yourself and record decisions in docs/factory/DECISIONS.md; put owner-only blockers in docs/factory/OWNER.md and move on. Commits, pushes and squash-merges of green, reviewed PRs are pre-approved; commit by explicit paths only; never add attribution or mention Claude, AI or agents in commits, PRs or comments.
   Stop after about $PassMinutes minutes of work or 3 merged PRs, and update docs/factory/STATE.md so the next pass resumes exactly where you stopped.
   "@
       $budgetArg = if ($MaxPassesPerDay -gt 0) { @('--budget', $MaxPassesPerDay) } else { @() }   # unlimited: the page shows just 'pass N'
  -    & python $Progress pass-start --pass-no ($passes + 1) @budgetArg 2>$null | Out-Null
  +    try { & factory-tools progress pass-start --pass-no ($passes + 1) @budgetArg 2>$null | Out-Null } catch {}
       Publish-Live
       $runLog = Join-Path $RunsDir ("{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmm'))
       # From here on, everything this runner starts dies with it (#230). Joined this late on purpose: the
       # browser that the one-time dashboard notice may open must outlive the runner.
       if (-not (Enter-PassJob)) { Log 'job object unavailable: hook orphans are cleaned on the next tick instead' }
       $job = Start-Job -ScriptBlock {
  -        param($wt, $claude, $model, $p, $out, $progressFile, $hooks)
  +        param($wt, $claude, $model, $p, $out, $progressFile, $hooks, $pluginDir)
           Set-Location $wt
           $env:LF_PROGRESS_FILE = $progressFile        # hooks record only when this is set
           # Headless claude stops background subagents 10 min after the manager's last turn; pass 9 lost an
           # implementer mid-fix that way. Wait for them: the awake-time limit below still bounds the pass.
           $env:CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS = '3600000'   # 60 min: long enough for a slow implementer, never forever
           $extra = if (Test-Path $hooks) { @('--settings', $hooks) } else { @() }
  +        $extra += @('--plugin-dir', $pluginDir)
           & $claude -p $p --model $model --permission-mode auto @extra *>&1 | Out-File -Encoding utf8 $out
  -    } -ArgumentList $Worktree, $Claude, $Model, $prompt, $runLog, $env:LF_PROGRESS_FILE, $HooksFile
  +    } -ArgumentList $Worktree, $Claude, $Model, $prompt, $runLog, $env:LF_PROGRESS_FILE, $HooksFile, $pluginDir
       $result = 'done'
       # The limit counts awake time only: a gap of more than a minute between two ticks means the laptop
       # slept, and that time is not charged to the pass (a night of sleep used to "time out" a working pass).
  @@ -248,7 +290,7 @@ Stop after about $PassMinutes minutes of work or 3 merged PRs, and update docs/f
       Remove-Job $job -Force
       $tail = if (Test-Path $runLog) { (Get-Content $runLog -Tail 3) -join ' | ' } else { '(no output)' }
       Log "$result. last lines: $tail"
  -    & python $Progress pass-end --result $result 2>$null | Out-Null
  +    try { & factory-tools progress pass-end --result $result 2>$null | Out-Null } catch {}
       Publish-Live -Force
       if ((Test-Path $runLog) -and ((Get-Item $runLog).Length -eq 0)) {
           Notify 'Learning Factory: pass produced no output' "The last pass wrote nothing. Is Claude Code logged in? See $runLog" 'empty'
  ```
- [ ] **O28 · (2 minutes) Apply the #627 IPv6 guard hardening on the server** — #627, PR #640 (522950d). The merge ran the bootstrap **dry run** (run 45768715118, success): the IPv4 and IPv6 rules are already in place, the read-only `bridge-nf-call-ip6tables` check passed (so the live value is 1), and the apply would only replace the boot copy `/usr/local/sbin/lf-labs-iptables` and re-enable its unit; no rule changes. The squash message had no `[bootstrap apply]` line, and the factory's own dispatch of the apply was refused by this laptop's permission settings, so the new boot copy (fails closed at boot, keeps the drops) is not installed yet. Run: `gh workflow run bootstrap.yml -R Shashkist/Learning_factory --ref main -f apply=true`, then check the run ends with `BOOTSTRAP RESULT: changed` (or `ok`) and the baseline line shows your other services unchanged. Tick this box; the next pass runs the server isolation smoke and closes #627. (Or allow the factory to dispatch `bootstrap.yml` with `apply=true` in future.)
- [ ] **O27 · (1 minute) factory PR #90 is approved but over the review-round limit** — factory#67, an exam-only PR. Rounds 1 to 3 asked for changes and the manager decided each one (factory D31 to D33). Round 4 approved head e9874e1, with CI green (175 cases, 0 skipped). `merge_gate.py` holds it with `review round 4 is over the limit of 2`. Moving the same commits to a new PR to reset the count was refused as a way around the gate, so the factory will not do that. Pick one: (a) merge it yourself: `gh pr merge 90 -R Shashkist/factory --squash --delete-branch --match-head-commit e9874e1a78fd07cdbe292e602eea988fed5f64bd`; or (b) say how the gate should treat rounds beyond 2 that the manager decided and a fresh reviewer then approved (for example, allow a merge when every round past 2 is backed by a D-entry). Then tick this box; the next pass closes #67 with the main CI run.

## Questions

_None yet._ When the factory meets something only Ilia can answer, it lands here with the issue link and the assumption it made meanwhile.
- [x] **O14 · Own Google sign-in client for labs (for #92)** — **done 2026-11-15** (owner + a session in Chrome). Google Cloud project `learning-factory-514815` ("Learning Factory"); consent screen app name **Learning Factory Labs**, authorized domain `shashkist.com`, audience External but **kept in Testing on purpose**: the only test user is the owner, so Google itself refuses every other account before labs sees it (a second allow-list next to `LF_ADMIN_EMAILS`). Publishing would need a privacy-policy page; do that only if non-admins ever sign in with Google. Web client **labs**: origin `https://labs.shashkist.com`, redirect `https://labs.shashkist.com/auth/google/callback`. `LF_GOOGLE_CLIENT_ID`/`LF_GOOGLE_CLIENT_SECRET` set in the `production` environment from the downloaded JSON (never printed; the file was deleted). Scopes: `openid email profile` only. Google notes that a new client can take from five minutes to a few hours to work.
- [x] **O15 · Admin allow-list is a secret** — **done 2026-11-15 15:36 UTC: `LF_ADMIN_EMAILS` set as a `production` secret (piped, not echoed), the variable deleted.** No Deploy had read the variable yet (#95 not merged), so the address never reached a log.
- [x] **O16 · GitHub Actions minutes were used up (incident #96)** — **resolved 2026-11-15 by making the repository public (D43).** Kept for the record: 
  1. **Spending limit** (keeps the repo private): GitHub → Settings → Billing and licensing → Budgets and alerts → Actions budget, e.g. $10 for the month (Linux runners cost $0.006/min, so $10 ≈ 1,600 min). **Size it knowing the current rate:** a busy factory day used ~900 min (~$5); #98 cuts that (docs-only pushes skip CI, heavy jobs only when their paths change). If the cause is really a failed payment, fix the card under Payment information first.
  2. **Make the repo public** (Actions become free; check `courses/` and the docs for anything private first). This is O7's option; the command is in O7.
  3. **Wait** for the reset on 2026-12-01 (16 days without merges or deploys).
  Then tick this box (or tell any session "done: O16"); the next pass re-runs main CI first.
- [x] **O17 · Read Linux 1 (en + he) and publish it** — #12. **Published 2026-11-21 on the owner's request (PR #368, deploy 44882193200).** The owner reviews the tasks by taking the lab as a student; wording fixes come as normal issues. The lab is in `labs/content/linux/linux-1/` with `status: draft` (students do not see it). Please read the ten tasks in English and Hebrew (wording, difficulty, whether the Hebrew sounds natural). When you are happy, set `status: published` in its `lab.yaml` (or tell any session "publish linux-1") and tick this box. The ownership part uses `chgrp` and a quiz instead of `sudo chown` (D42).
- [ ] **O18 · Re-save the Google values without a byte-order mark, then sign in** — #110. The live check after #92 showed that the `production` environment value `LF_GOOGLE_CLIENT_ID` starts with an invisible byte-order mark (U+FEFF), which Windows PowerShell 5.1 adds when it saves text. `LF_GOOGLE_CLIENT_SECRET` probably has one too. Since #110 the manager strips it on read, so sign-in works either way. When convenient, open Settings → Environments → production in the GitHub web UI and type or paste both values again. Then sign in once at https://labs.shashkist.com/admin with Google and tick this box.
- [ ] **O19 · Break-glass admin login (reference; nothing to do now)** — #99. Since #99, while Google sign-in is configured, a password admin session is refused at once, so if Google sign-in ever breaks (a wrong client secret, Google down) this is the only way in. On the server, as root: add the line `LF_ADMIN_PASSWORD_LOGIN='1'` to `/root/learning-factory/.env` (Deploy keeps lines it does not manage); recreate only the manager with `cd /root/learning-factory && docker compose -p learning-factory -f compose.yml up -d lf-manager`; create or reset a password admin with `docker exec -i lf-manager python -m app.cli create-admin <username>` (add `--reset` for an existing one) and sign in at https://labs.shashkist.com/admin. When Google works again, delete that line and recreate `lf-manager` the same way: every password session stops at the next request. Tick this box once you have read it.
- [ ] **O20 · Laptop is choked by hung processes (pass lf-x2v7, 2026-11-17)** — the factory pass could not work: every Git Bash call hung (even `echo hi`), and `uv run pytest` did not finish in 10 minutes from PowerShell. CPU was at 100 %. Found: **~500 `python.exe ~/.claude/qa-statusline.py`** processes (your status-line script; the oldest about 40 hours old; each refresh starts one that never exits), **53 `bash.exe`**, and old hook processes (`scripts/factory_progress.py hook` up to ~32 h old, `scripts/factory_guard.py` up to ~27 h old). The factory did not kill them (your tooling, not `lf-`). Please end them (for example `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? CommandLine -like '*qa-statusline.py*' | % { Stop-Process -Id $_.ProcessId -Force }`, or a reboot), and fix the status-line script so it exits (a timeout on whatever it waits for). Tick this box, and the next pass resumes the three lanes in `STATE.md`.
- [x] **O21 · Bound the guard hook's stdin read (the guard half of #230)** — **Done 2026-11-23 in an owner session (PR #546):** the guard has its own 3 s bounded read (below its 5 s hook timeout) and fails closed when it runs; a hook timeout still allows, as before. The orphans' real cause was processes left suspended by a killed Git Bash, fixed in the runner (job object plus sweep). Original item: the progress hook got its bound in PR #248 (`scripts/_hook_io.py`: `read_stdin()` with a 10 s join, `start_watchdog(code)` after 30 s). The guard hook still reads with a blocking `sys.stdin.read()` and can hang forever like the 51 orphans of 2026-11-19, but the guard and its tests are owner infrastructure (D35), so a factory run cannot change them. In an owner session, after #248 merges, in the guard script: replace the blocking read with `raw = read_stdin()` (import from `_hook_io`); on `None` print a "hook input could not be read" reason to stderr and return 2 (**fail closed**: a live parent always sends the payload at once, so a timeout means a dead or broken parent and denying costs nothing); start `start_watchdog(2)` in its `__main__` block; keep the existing fail-open on malformed JSON (complete input it does not understand). Add a test like `test_hook_exits_when_stdin_never_closes` in `scripts/test_factory_progress.py` (never-closed stdin pipe, `LF_HOOK_STDIN_TIMEOUT=1`, exits 2 within 5 s). Then tick this box and close #230.
- [ ] **O22 · (optional) Keep the dashboard fresh while the factory is idle** — #155, D77. GitHub's scheduler drops the Factory dashboard cron for this repository (no scheduled run in 2+ hours, even off the busy minutes), so between factory passes the GitHub part of factory.shashkist.com can lag for hours. Pushes still publish. If you want it fresh all the time, pick one: (a) have the laptop runner's heartbeat also run `gh workflow run factory-dashboard.yml -R Shashkist/Learning_factory` every 15 minutes (your existing `gh` login, no new secret; the runner is yours under D35), or (b) a server crontab line with a fine-grained token that can only dispatch workflows on this repository. Then tick this box; the next pass closes #155 when dispatched runs show up. Fine with push-only freshness? Tick it with "skip" and the factory closes #155.
- [x] **O23 · Choose how #332's card action names its lab (one exam line to correct)** — #332. **Answered 2026-11-22: "332: a" (D109 in force).** Acceptance criterion 3 ("the main action on each card has an accessible name that includes the lab title") cannot hold together with criterion 6 ("existing tests unchanged"): the #129 and #147 exams and `e2e/main-flow.spec.ts` pin the card button's name to exactly "Start" / "Continue" / "Review". The factory's choice is **(a)**: the button keeps its word as its name, and `aria-describedby` points at the card heading, so a screen reader says "Start, button, Shell basics". That needs the new #332 exam's AC3 case (`labs/web/tests/acceptance/issue-332-login-mylabs.test.tsx`, branch `f/332-login-mylabs-design`) to check the description instead of the name. The factory's guard refused that exam edit from a manager session, so the lane is parked. Reply **"332: a"** (the factory corrects the exam in an exam-only commit, then builds), **"332: b"** (an `aria-label` like "Start: Shell basics", and the factory loosens the three older name checks to "contains"), or make the exam edit yourself. Everything else in #332's exam is ready.
- [x] **O26 · (2 minutes) Install the live-view heartbeat task** — **Done 2026-11-24 in an owner session: task `LearningFactoryHeartbeat` created (every minute), first run result 0.** #558, D147. `scripts/laptop/factory-heartbeat.vbs` says it runs as the scheduled task `LearningFactoryHeartbeat` every minute, but no such task exists on the laptop (2026-11-23, `Get-ScheduledTask`). So between passes nothing publishes and the page calls the laptop offline, and during a pass the runner's loop is the only publisher. In PowerShell, as yourself (no admin needed): `schtasks /Create /TN LearningFactoryHeartbeat /SC MINUTE /MO 1 /TR "wscript.exe \"C:\projects\Learning_factory\scripts\laptop\factory-heartbeat.vbs\"" /F`. Then tick this box; the next pass checks that `live.json` `generated_at` moves between passes.
- [x] **O25 · (optional, 2 minutes) Run Linux 1 again on the live site** — **Checked 2026-11-24 in an owner session (desktop browser): task 1 answered, Next to task 2, task 2 solved and Check green (3/3), Next to task 3, Skip to task 4. The terminal kept its history and the countdown ran on throughout. Each switch took about 5 to 10 s with the buttons disabled.** #427 is fixed and deployed (fe13829). Start Linux 1, answer task 1 and press **Next**: task 2 should show in the task pane while the terminal and the countdown stay as they are. Skip on to task 3 the same way. If anything is off, comment on #427.
- [ ] **O24 · (optional, 1 minute) Try the lab screen on your phone** — #349. Open a lab on a phone held upright, tap **Terminal**: the prompt should fill the pane and typing should work; tap **Tasks** and back: focus should not jump into the hidden terminal. Turn the phone sideways: both panes show and the terminal keeps the keyboard. If anything is off, comment on #349 (it is closed on deploy evidence; the automated 390 px typed-command proof is part of #319).
