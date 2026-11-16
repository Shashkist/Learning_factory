# Learning Factory Labs: design

Date: 2026-11-14 · Owner: Ilia · Status: draft for approval

## 1. Intent

Ilia teaches a 63-lesson course (4 hours each) for the partner college, "Multi-Skilled Tech & AI Specialist": Linux, networking, Python, Full Stack, Git/Docker/Cloud, AI agents. Students need to practise on real machines, the way KodeKloud labs work: tasks on the left, a live Linux terminal (or VS Code) on the right, a **Check** button that verifies the work on the machine, then the next task.

The platform is Ilia's own version of that, in **English and Hebrew**, for his students only. It lives in `C:\projects\Learning_factory\labs` (repo `Shashkist/Learning_factory`) and runs on his existing DigitalOcean server.

Success: a student logs in with the number and password Ilia gave them, opens "Linux 1", gets a fresh machine in seconds, works through the tasks in Hebrew or English, sees ✓ per task, and Ilia sees the class progress in one table.

## 2. How KodeKloud does it (observed 2026-11-14, used as the reference)

- The main site only embeds the lab in an iframe. Each session gets its own random host (`<id>.labs.kodekloud.com`) serving the task app plus a small API: `/config`, `/question`, `/check_config` (runs the check, pass/fail), `/skip_question`, `/heart_beat` (keepalive; the session dies when it stops), `/remaining_time`.
- Terminal labs: **ttyd** on `terminal-<id>`. The shell is wrapped in `script -f` (everything typed is logged) and drops to user `bob` via `sudo su - bob`.
- VS Code labs: **code-server** on the same `terminal-<id>` slot, opened at `?folder=/root`.
- Each task has its own setup step ("Setting things up") that puts the machine in the state the task needs.
- Task types: multiple choice, and hands-on with a checklist of what Check verifies. Success shows confetti and a **Next** button. A Solution tab shows the commands. A timer (55–60 min) runs per lab.

## 3. Decisions made by Ilia

| Topic | Decision |
|---|---|
| Hosting | Stay on the existing server (DO fra1, 4 vCPU, 7.8 GB RAM), with hardening. **No resize.** Ilia may stop some existing containers to free memory. Every finished session must release all its memory and disk (section 6). |
| Concurrency | Up to 20 students at once; the design must be able to scale beyond that. |
| Language | Task text and UI in **English and Hebrew** (switchable). Terminal, commands and files are English. |
| Accounts | Login = the student's **ID number + one shared password per class** (for example `course2027`). Only students Ilia added can log in. Stored per student: **name + hashed ID**, nothing else; the teacher panel shows names so Ilia knows who is who. Access expires automatically after a set period (about half a year) for the whole class; Ilia can extend it. Known trade-off, accepted: a classmate who knows another student's ID can log in as them. |
| One lab at a time | A student may have only one open lab. Opening another (for example in a new tab) shows "Another lab is open" with **End it and start this one** / **Cancel**. |
| VS Code | Some labs use VS Code in the browser (as in the KodeKloud Terraform lab). |
| Progress | Labs start fresh, but the system remembers which tasks and labs each student completed. Continuing where they stopped is welcome if easy. |
| Teacher view | Ilia (manager and teacher) sees, by student number, what each student has done. |
| Database | The existing Supabase project (used by JSoul), in a **new schema**. |
| AI use | No AI-mode badges and no blocking for now. Instead, **flag suspiciously fast work** for the teacher (likely done by a browser agent such as Claude in Chrome). A flag is only a hint for Ilia, never a penalty. |

## 4. Goals, non-goals, anti-goals

**Goals**
- Fresh, isolated machine per session, ready in under ~15 s for terminal labs.
- Task panel with multiple-choice and hands-on tasks, per-task setup and Check, Next, Solution, timer.
- English/Hebrew toggle; the panel is right-to-left in Hebrew.
- Student login with an expiry date; teacher dashboard and admin actions.
- Labs authored as plain files in git, so Ilia (or an AI agent) can write a new lab without touching platform code.

**Scope of v1 (my call).** Labs that run in **one container**: Linux, networking from the client side (`ping`, `curl`, `dig`, `ss`, `traceroute`), Python, and VS Code labs, including opening the student's own web app through code-server's built-in port proxy (`/proxy/<port>/`) for Full Stack. Not in v1, with the upgrade path noted so it fits later without a redesign:
- **Docker inside a lab** (Git/Docker/CI module): add the Sysbox runtime for those labs only;
- **multi-machine labs or `NET_ADMIN` tools** (`ip link`, `tcpdump`, `iptables`): a session becomes a small set of containers on a private network;
- **cloud or LLM credentials** (AWS, AI-agent modules): short-lived, per-student, scoped keys injected by the manager.

**Non-goals (for now)**
- Public sign-up, payments, other teachers or organisations.
- AI tutor or hints inside the lab (possible later; the course has a "checkpoint without AI" rule anyway).
- Keeping a student's files between sessions.
- Multi-server operation (designed for, not built).
- Mobile layouts.

**Anti-goals: these make it a failure even if it works**
- A student can affect Ilia's other services (OpenClaw, n8n, client apps) or read their secrets.
- Labs slow or crash the server so Ilia's bots go down.
- Students can read the check scripts or solutions from inside their machine.
- Writing a new lab requires changing platform code.

## 5. Architecture (my calls unless marked above)

```
Browser ──https──► nginx-proxy3 (labs.shashkist.com, Let's Encrypt)
                        │
                        ▼
                 lab-manager (FastAPI, one container)
                 ├─ UI: student app + teacher dashboard (static build)
                 ├─ API: auth, labs, sessions, check, progress, admin
                 ├─ reverse proxy for /s/<session>/term and /s/<session>/ide (HTTP + WebSocket)
                 ├─ Docker API → starts / execs / removes lab containers (runtime: gVisor runsc)
                 └─ Postgres (Supabase, schema learning_factory, dedicated role)
                        │
           lab network (isolated) ──► lab-<session> containers
                                       image: lf-lab-term (Ubuntu + ttyd) or lf-lab-code (+ code-server)
```

- **One entry point, path-based routing.** All session traffic goes through `labs.shashkist.com/s/<session-id>/…` and the manager proxies to the container by name on the lab network. No wildcard DNS or certificate; one nginx server block. The proxy checks that the logged-in student owns the session.
- **Lab images hold no lab content.** The manager keeps the lab definitions and runs each task's `setup.sh` and `check.sh` inside the container as root with `docker exec`, and nothing is left behind. Students never see checks or solutions on the machine.
- **Student shell.** ttyd runs `su - student` (non-root, no sudo by default; a lab can grant sudo when its tasks need it). The shell is wrapped in `script -f` to a root-only log, so checks can look at command history when a task is about using a command rather than its result.
- **Heartbeat and reaper.** The UI pings the manager every ~15 s. A reaper removes a session's container after 5 min without heartbeat, when its timer ends, or when the student ends it. On startup the manager removes orphaned `lab-*` containers.
- **One session per student** is enforced by the manager. Opening the lab that is already running reconnects to it; opening a different lab returns "another lab is open" with the choice to end it.
- **Setup contract (my call; every lab is written against it).** Each task's `setup.sh` is **self-contained**: on a fresh container it alone creates that task's full starting state, including anything earlier tasks would have produced that this task depends on. It must also be **idempotent on top of the student's current machine**, because it runs there when the student moves on. For example, it replaces the files the task is about, as KodeKloud does ("each task replaces `practice.yaml`").
  - **Next / Skip / Previous** during a live session run the target task's `setup.sh` on the current machine; no reset.
  - **Continue from task N** starts a fresh container and runs only setup N.
  - **Restart task** (a button) runs setup N again.
  - The student's own earlier files are not kept across sessions.
- **Checks and root (my call).** In labs without sudo, students cannot see or influence checks. In labs that grant sudo, a determined student who is root in the container could fake a check. This is accepted: the labs are for practice, not exams. Labs use sudo only when a task needs it.
- **Scaling path.** The manager talks to Docker through one "lab host" setting. Later a second server can run lab containers (Docker over TLS or SSH) with the manager placing sessions on the least-loaded host. Not built now.

## 6. Isolation and limits (the condition for using this server)

Per lab container:
- runtime **gVisor (`runsc`)**; no `--privileged`; drop all capabilities; `no-new-privileges`;
- memory **256 MB** for terminal labs and **768 MB** for VS Code labs; `--cpus 0.5`; `--pids-limit 256`; disk-limited writable layer or tmpfs size cap;
- own Docker network `lf-labs` with **no access to the host or other containers**: iptables (DOCKER-USER) drops traffic from `lf-labs` to the host IPs and the private ranges;
- **internet (my call):** off by default. A lab with `internet: true` gets access **only through an allowlist proxy** (apt/Ubuntu mirrors, PyPI, npm, GitHub, plus per-lab extra domains in `lab.yaml`). Never full outbound access: a student scanning, spamming or mining from Ilia's server IP could get it blocklisted and take his bots down with it;
- no Docker socket, no host mounts, no secrets in the environment.

Global:
- memory limits are ceilings, not reservations. Admission is decided by **real free memory**: a new session starts only if free memory after it stays above a reserve kept for Ilia's other services (default 1.5 GB), plus config caps (default 20 terminal, 8 VS Code). When full: a friendly "lab capacity is full, try in a few minutes". Expected real use: terminal session ~50 MB, VS Code session ~250–400 MB;
- add a 4 GB swap file on the server (it has none today) as a safety margin for Ilia's other services;
- the manager connects to Postgres with a role that can only use schema `learning_factory`, never the JSoul service key.

**Cleanup guarantees (Ilia's requirement: the droplet must never run out of RAM or disk because of labs).** Decided by Ilia: the droplet is not resized. Everything below is required and tested:
- A lab container holds nothing that outlives it: **no named volumes, no host mounts**. The student's home is on the container's writable layer, with a disk-size cap per container (writable layer quota, or a tmpfs home sized ≤ 512 MB that counts against the memory limit). Ending a session is `docker rm -f -v`, so its memory and disk return immediately.
- Container logs: `--log-driver none` for lab containers (ttyd's output is not needed). The `script` log lives inside the container and disappears with it.
- Sessions end on: the End lab button, the timer, 5 min without heartbeat, account expiry, and manager restart (orphan sweep of all `lab-*` containers not in the database as running).
- **Janitor** every minute: removes `lab-*` containers with no live session, and stopped `lab-*` containers. It also checks free memory and free disk and logs a warning to the admin dashboard when either is under its reserve.
- **Disk guard:** no new session starts when free disk is under a reserve (default 10 GB). Image builds clean their build cache (`docker builder prune`) and remove the previous image versions (`docker image prune` with the lf label).
- **Acceptance test:** start and end 30 sessions in a row, including forced kills (closed tab, timer, manager restart). Afterwards no `lab-*` containers remain, no orphan volumes exist, and server free memory and free disk are within 50 MB of the starting values.

**VS Code weeks without a resize (consequence, accepted).** With ~3.6 GB free today (more if Ilia stops some containers), about **8–12 VS Code sessions** fit at once, not 20. When the class opens a VS Code lab together, the rest get "capacity full, try in a few minutes" until someone finishes. To push the number up, the VS Code image is kept lean: only the extensions a lab needs, no heavy language server by default, telemetry and auto-update off. The second-server path in section 5 stays available if this becomes a real problem.

**Database.** Supabase is hosted (`*.supabase.co`), so it uses no memory on this server. The manager connects through Supabase's connection pooler. The schema is **not** added to Supabase's exposed API schemas: only the manager reads it.

## 7. Lab content format

Lab content lives in the repo, separate from the platform code:

```
labs/content/<course>/<lab-id>/
  lab.yaml
  tasks/01-<slug>/task.en.md  task.he.md  setup.sh  check.sh  solution.en.md  solution.he.md
  tasks/02-<slug>/...
```

`lab.yaml`: id, title `{en, he}`, short description `{en, he}`, `image` (`term` | `code`), `time_limit_min` (default 60), `internet` (bool) and `allow_domains` (list), `sudo` (bool), `status` (`draft` | `published`), and task list order.

**Hebrew and English text (my call).** The AI drafts both languages. A lab is `draft` until Ilia has read it in both languages and set `published`. Draft labs are visible and runnable only from the admin account, so Ilia can test them as a student would.

Each task is either:
- **quiz**: question `{en, he}`, options, the correct option; graded by the manager; or
- **hands-on**: `task.*.md` (Markdown, with code spans shown left-to-right inside right-to-left text), a checklist of items `{en, he}`, `setup.sh`, `check.sh`.

`check.sh` contract: runs as root in the container, prints one line per checklist item (`PASS <n>` / `FAIL <n> <optional hint>`), exit code 0 only when all pass. Timeout 20 s.

**Script authoring rules.** `setup.sh` and `check.sh` are streamed to the interpreter on stdin and killed (SIGKILL, the interpreter and its descendants) when they outlive their timeout. So: a command that reads stdin (`read`, a bare `cat`) takes the rest of the script as its input, give it `</dev/null`; a background service must be started as `cmd >/dev/null 2>&1 &`, or it holds the exec's output open. The validator warns about both (the lab is still served).

The manager validates every lab on start (files present, both languages, scripts executable) and the dashboard shows broken labs instead of serving them.

## 8. Data model (Postgres schema `learning_factory`)

- `cohorts`: id, name, password_hash (the shared class password, argon2), access_until (date)
- `students`: id_hash (unique, the login key), name, note (optional, teacher-only), cohort_id, access_until (nullable override), disabled
- `admins`: username, password_hash (Ilia)

**ID hashing (my call).** `id_hash = HMAC-SHA256(server_secret, normalized_id)`, where `normalized_id` strips spaces and leading zeros, and `server_secret` lives only in the manager's environment on the server, never in the database. An ID number has only ~10⁹ values, so a plain hash could be reversed by trying them all; the secret prevents that if the database leaks. Login: hash the typed ID, look up the student, verify the typed password against their cohort's `password_hash`. The raw ID is never stored or logged.
- `lab_sessions`: id, student, lab_id, container, status (starting / running / ended / expired), started_at, ended_at, last_heartbeat
- `task_results`: student, lab_id, session_id, task_id, shown_at (when the task first appeared in this session), passed_at, attempts, fast_flag
- `lab_runs`: student, lab_id, session_id, first_completion (bool), active_seconds (time with heartbeat, summed over the run), fast_flag

**Fast-work flag (my calls on the thresholds).** Hints for the teacher, shown with ⚡:
- **Task:** a hands-on task passed less than `min_task_seconds` after it appeared (default 20 s; `lab.yaml` can override per task). Typing the commands by hand practically never takes less than that.
- **Lab:** a first completion with `active_seconds` below `fast_lab_min` from `lab.yaml`, or, once at least 5 students have completed the lab, below 30 % of the class median for it.
- **Never flagged:** quiz tasks for speed alone, and repeat completions of a lab the student already finished (practice runs are meant to be fast).
- `lab_completions`: student, lab_id, completed_at (all tasks passed)

Access is valid while today ≤ (student override or cohort `access_until`) and the student is not disabled. Progress is kept after access ends.

## 9. Screens

**Student**
- **Login** (ID number + class password). Rate-limited per IP and per ID to slow guessing.
- **My labs**: lab cards grouped by module, each with ✓ done / 5/12 / not started and Start / Continue / Redo.
- **Lab screen**: left task panel (Task / Solution tabs, "Question N of M" progress bar, timer, Check, checklist with ✓/✗ and hints, Next / Skip / Previous); right panel = terminal or VS Code, resizable splitter; top: EN / עב toggle, End lab. The Hebrew panel is right-to-left; the terminal is always left-to-right.
- **Another lab is open** dialog, as decided.

**Teacher/admin**
- **Class table**: students down the side, labs across the top, cells ✓ / n/m / –, a ⚡ on cells with a fast-work flag, last activity; filter by cohort and "flagged only"; CSV export.
- **Student page**: per lab: tasks passed, attempts, time spent per task next to the class median, ⚡ flags with the reason ("task 4 passed 9 s after it appeared"); actions: extend access, disable, dismiss a flag.
- **Cohorts**: create a cohort with a name, class password and access date; change the password or the date later; paste a list "ID, name" to add students (the IDs are hashed on arrival and the list is not kept). Students are shown by name; two students with the same name get a manual note field to tell them apart.
- **Live sessions**: who is in which lab now; end a session.

## 10. Stack (my calls)

- Manager: Python 3.12, FastAPI, SQLAlchemy/asyncpg, Docker SDK for Python, httpx/websockets for the proxy. Passwords with argon2. Session cookie (HttpOnly, Secure).
- UI: React + Vite + TypeScript, i18n with en/he JSON files, CSS logical properties for RTL. Built into static files served by the manager.
- Lab images: `lf-lab-term` (Ubuntu 24.04, ttyd, common CLI tools, user `student`); `lf-lab-code` (+ code-server, opened at `/home/student/work`).
- Deploy: Docker Compose on the server (`/root/learning-factory`), images built on the server or pushed as `shashkist/lf-*` like Ilia's other apps; nginx server block plus certbot for `labs.shashkist.com`; DNS A record for `labs.shashkist.com` (Ilia adds it if the domain's DNS isn't wildcard).

## 11. First lab (my call)

**Linux 1: navigation, files, permissions** (Module 1). About 10 tasks mixing quiz and hands-on, for example: `pwd`/`ls` quiz; create a directory tree; create a file with given content; copy and move files; find a file with `find`; read a log with `grep`; `chmod` to given permissions; quiz on `rwx`; change ownership (sudo lab). It is written in both languages and proves every part: setup, check, quiz, Solution, timer, continue, completion.

## 12. Build order (my call)

1. **Phase 1:** terminal labs end to end: login, my labs, lab screen with quiz and hands-on tasks, setup/check, timer, heartbeat/reaper, one-lab-at-a-time, progress with timings and the fast-work flag, teacher class table, cohorts and student import, isolation and limits, deploy at `labs.shashkist.com`, the Linux 1 lab.
2. **Phase 2:** VS Code labs and the web-app preview, Continue / Restart task, student page details, live sessions, CSV export, the allowlist proxy.
3. **Later:** the items listed under "not in v1".

## 13. Left to the builder

Exact UI styling (clean, dark terminal panel, similar density to KodeKloud), the reaper and heartbeat intervals within the ranges above, the internal API shape, log retention for `script` logs (suggest delete with the container), test strategy, and the free-memory threshold value.
