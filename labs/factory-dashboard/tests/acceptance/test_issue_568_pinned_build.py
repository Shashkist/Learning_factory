"""Exam for issue #568 (D149): the dashboard is built from the factory package at a pinned SHA.

Reads .github/workflows/factory-dashboard.yml only.

  * exactly one job runs `python -m factory_tools.dashboard build --repos docs/factory/repos.yaml
    --out site/`; that job checks out Shashkist/factory at a full 40-hex SHA into its own path,
    references no `secrets.` and has no write permission;
  * the job with the publish secrets (`secrets.` / `environment: production`) only downloads the
    artifact and copies files: no checkout, no pip, no local action, nothing from the factory path;
  * the publish job `needs` the build job and runs only on refs/heads/main.

Criteria 1 and 3 (live snapshot.json, repository tag on every row) are checked live after deploy,
not here. The removal of build_snapshot.py and the page files is a follow-up issue (D149).
"""
from __future__ import annotations

import copy
import re
import shlex
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
WORKFLOW = REPO / ".github" / "workflows" / "factory-dashboard.yml"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SECRETS = re.compile(r"\bsecrets\s*[.\[]")
FACTORY = "Shashkist/factory"


def wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def jobs() -> dict:
    return wf()["jobs"]


def run_text(job: dict) -> str:
    return "\n".join(
        str(s.get("run", "")).replace("\\\n", " ") for s in job.get("steps", [])
    )


def dump(job: dict) -> str:
    return yaml.safe_dump(job)


def env_name(job: dict) -> str:
    env = job.get("environment")
    return str(env.get("name", "")) if isinstance(env, dict) else str(env or "")


def build_job() -> tuple[str, dict]:
    found = [
        (n, j) for n, j in jobs().items()
        if "factory_tools.dashboard build" in run_text(j)
    ]
    assert len(found) == 1, "expected exactly one job that runs factory_tools.dashboard build"
    return found[0]


def publish_job() -> tuple[str, dict]:
    found = [
        (n, j) for n, j in jobs().items()
        if SECRETS.search(dump(j)) or env_name(j) == "production"
    ]
    assert len(found) == 1, "expected exactly one job with the publish secrets / production"
    return found[0]


def factory_checkout(job: dict) -> dict:
    found = [
        s for s in job.get("steps", [])
        if str(s.get("uses", "")).startswith("actions/checkout")
        and (s.get("with") or {}).get("repository") == FACTORY
    ]
    assert len(found) == 1, "the build job needs exactly one actions/checkout of Shashkist/factory"
    return found[0]


def factory_path() -> str:
    path = str((factory_checkout(build_job()[1]).get("with") or {}).get("path", ""))
    return path.strip("/ ")


# ---- the build job ----------------------------------------------------------

def test_exactly_one_job_runs_the_factory_dashboard_build_with_repos_and_out():
    _, job = build_job()
    line = next(ln for ln in run_text(job).splitlines() if "factory_tools.dashboard build" in ln)
    assert re.search(r"python3?\s+-m\s+factory_tools\.dashboard\s+build\b", line)
    assert re.search(r"--repos\s+docs/factory/repos\.yaml(\s|$)", line)
    assert re.search(r"--out\s+site/?(\s|$)", line)


def test_build_job_checks_out_the_factory_repository_at_a_full_40_hex_sha():
    _, job = build_job()
    ref = str((factory_checkout(job).get("with") or {}).get("ref", ""))
    assert FULL_SHA.match(ref), f"ref must be a full lowercase 40-hex SHA, got {ref!r}"


def test_factory_checkout_goes_into_its_own_path():
    path = factory_path()
    assert path and path not in (".", "site"), "the factory checkout needs its own path"
    for s in build_job()[1]["steps"]:
        if str(s.get("uses", "")).startswith("actions/checkout") and (
            (s.get("with") or {}).get("repository") != FACTORY
        ):
            assert str((s.get("with") or {}).get("path", ".")).strip("/ ") != path


def test_build_job_references_no_secrets_at_all():
    _, job = build_job()
    assert not SECRETS.search(dump(job))
    assert env_name(job) != "production"


def test_build_job_permissions_grant_no_write_scope():
    _, job = build_job()
    perms = job.get("permissions", wf().get("permissions"))
    assert perms is not None, "declare read-only permissions"
    assert perms != "write-all"
    if isinstance(perms, dict):
        assert "write" not in {str(v) for v in perms.values()}


def test_build_job_uploads_the_site_artifact():
    _, job = build_job()
    ups = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert ups and ups[0]["with"]["name"] == "factory-dashboard"


# ---- the publish job --------------------------------------------------------

def test_publish_job_is_a_different_job_from_the_build_job():
    assert build_job()[0] != publish_job()[0]


def test_publish_job_has_no_checkout_step():
    _, job = publish_job()
    assert not [s for s in job["steps"] if "checkout" in str(s.get("uses", ""))]


def test_publish_job_runs_nothing_from_the_factory_checkout():
    path = factory_path()
    assert path
    text = dump(publish_job()[1]).replace("/root/static/factory", "")
    assert "factory_tools" not in text
    assert FACTORY not in text
    assert not re.search(rf"(^|[\s'\"=/$]){re.escape(path)}(/|\s|$)", text)


def test_publish_job_installs_nothing_and_uses_no_local_action():
    _, job = publish_job()
    for s in job["steps"]:
        uses, run = str(s.get("uses", "")), str(s.get("run", ""))
        assert not uses.startswith("./"), "no local action in the secrets job"
        assert not uses.startswith("actions/setup-python")
        assert not re.search(r"\b(pip3?|uv|pipx|npm|poetry)\s+(install|add|run|sync)\b", run)
        assert "python" not in run, "the publish job runs no python"


def test_publish_job_only_downloads_the_artifact_and_copies_files():
    _, job = publish_job()
    uses = [str(s["uses"]).split("@")[0] for s in job["steps"] if "uses" in s]
    assert uses == ["actions/download-artifact"]


def test_publish_job_needs_the_build_job():
    needs = publish_job()[1].get("needs")
    needs = [needs] if isinstance(needs, str) else list(needs or [])
    assert build_job()[0] in needs


def test_publish_job_still_runs_only_on_main():
    job = publish_job()[1]
    assert "refs/heads/main" in str(job.get("if", ""))
    assert env_name(job) == "production"


# ---- review round 1: no reusable-workflow call, no inherited secrets, no execution of site/ ----

FACTORY_LOWER = FACTORY.lower()
SHELL_SPLIT = re.compile(r"&&|\|\||[;|&\n(){}`]")
INTERPRETERS = {
    "sh", "bash", "zsh", "dash", "ksh", "fish", "source", ".", "exec", "eval", "xargs",
    "node", "nodejs", "perl", "ruby", "php", "deno", "bun", "pwsh", "powershell",
}
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SITE_PATH = re.compile(r"""^["']?(\./|\$\{?GITHUB_WORKSPACE\}?/)?site(/|["']?$)""")


def job_level_violations(workflow: dict) -> list[str]:
    """Job-level `uses:` (reusable-workflow call) or `secrets:` (incl. `secrets: inherit`)."""
    bad = []
    for name, job in workflow["jobs"].items():
        for key in ("uses", "secrets"):
            if key in job:
                bad.append(f"{name}: job-level {key}")
    return bad


def factory_mention_violations(workflow: dict) -> list[str]:
    """Only the job that checks out the factory repository may mention it (case-insensitive)."""
    bad = []
    for name, job in workflow["jobs"].items():
        mentions = FACTORY_LOWER in yaml.safe_dump(job).lower()
        has_checkout = any(
            str(s.get("uses", "")).startswith("actions/checkout")
            and str((s.get("with") or {}).get("repository", "")).lower() == FACTORY_LOWER
            for s in job.get("steps", [])
        )
        if mentions and not has_checkout:
            bad.append(f"{name}: mentions {FACTORY} without being the build job")
    return bad


def execution_violations(job: dict) -> list[str]:
    """Commands in the job's run steps that would execute something under site/."""
    bad = []
    for step in job.get("steps", []):
        run = str(step.get("run", "")).replace("\\n", " ")
        for cmd in SHELL_SPLIT.split(run):
            try:
                words = shlex.split(cmd, comments=True)
            except ValueError:
                words = cmd.split()
            while words and ENV_ASSIGN.match(words[0]):
                words = words[1:]
            if not words:
                continue
            head, args = words[0], words[1:]
            if SITE_PATH.match(head):
                bad.append(f"executes {head}")
            elif head.rsplit("/", 1)[-1].lower().startswith("python") or (
                head.rsplit("/", 1)[-1].lower() in INTERPRETERS
            ):
                if any(SITE_PATH.match(a) for a in args):
                    bad.append(f"{head} runs {' '.join(args)}")
    return bad


def mutated(extra_job: dict | None = None, publish_step: str | None = None) -> dict:
    w = copy.deepcopy(wf())
    if extra_job is not None:
        w["jobs"]["relay"] = extra_job
    if publish_step is not None:
        name, _ = publish_job()
        w["jobs"][name]["steps"].append({"name": "x", "run": publish_step})
    return w


def test_no_job_calls_a_reusable_workflow_or_declares_job_level_secrets():
    assert job_level_violations(wf()) == []


def test_only_the_build_job_mentions_the_factory_repository_case_insensitively():
    assert factory_mention_violations(wf()) == []
    name, job = build_job()
    assert FACTORY_LOWER in yaml.safe_dump(job).lower()
    for other, j in jobs().items():
        if other != name:
            assert FACTORY_LOWER not in yaml.safe_dump(j).lower(), other


def test_job_level_checks_reject_a_relay_job_with_uses_and_inherited_secrets():
    sha = "a" * 40
    relay = {
        "needs": "snapshot",
        "uses": f"Shashkist/factory/.github/workflows/p.yml@{sha}",
        "secrets": "inherit",
    }
    w = mutated(relay)
    assert job_level_violations(w)
    assert factory_mention_violations(w)
    assert job_level_violations(mutated({"uses": "./.github/workflows/x.yml"}))
    assert job_level_violations(mutated({"runs-on": "ubuntu-latest", "secrets": {"A": "b"}}))
    # a plain job that only mentions the factory repository in a step is also rejected
    steps = [{"run": "git clone https://github.com/SHASHKIST/Factory x"}]
    assert factory_mention_violations(mutated({"runs-on": "u", "steps": steps}))


def test_publish_job_never_mentions_the_factory_repository_in_any_letter_case():
    text = dump(publish_job()[1]).lower()
    assert FACTORY_LOWER not in text
    assert "factory_tools" not in text


def test_publish_job_checks_are_case_insensitive_for_a_piped_installer():
    url = "https://raw.githubusercontent.com/shashkist/factory/x/i.sh"
    w = mutated(publish_step=f"curl -s {url} | bash")
    name, _ = publish_job()
    assert FACTORY_LOWER in yaml.safe_dump(w["jobs"][name]).lower()
    assert factory_mention_violations(w)


def test_publish_job_executes_nothing_under_site():
    assert execution_violations(publish_job()[1]) == []


def test_site_execution_check_rejects_every_form_of_running_an_artifact_file():
    forbidden = [
        "sh site/snapshot.json",
        "bash site/x",
        "./site/x",
        "site/x",
        "python site/x",
        "python3 ./site/x.py",
        "source site/x",
        ". site/x",
        "cd /tmp && bash -e site/x",
        "FOO=1 sh \"site/x\"",
        "echo hi; sh site/x",
    ]
    name, _ = publish_job()
    for cmd in forbidden:
        w = mutated(publish_step=cmd)
        assert execution_violations(w["jobs"][name]), cmd
    allowed = [
        "scp -i k site/index.html site/app.js host:/dest/",
        "ls site/ && cat site/snapshot.json",
        "echo site/x",
    ]
    for cmd in allowed:
        w = mutated(publish_step=cmd)
        assert execution_violations(w["jobs"][name]) == [], cmd


# ---- review round 2: secrets in any form, environment case, publish allowlist, uv pins ----

SECRETS_ANY = re.compile(r"\bsecrets\b", re.IGNORECASE)
ALLOWED_HEADS = {
    "if", "then", "else", "fi", "for", "do", "done", "echo", "exit", "umask", "printf",
    "chmod", "ssh", "scp", "sleep", "rm", "break", "[", "test",
}
# Words that start a clause and are followed by a real command in the same segment.
CLAUSE_LEADERS = {"if", "then", "else", "do", "elif", "while", "until", "!"}
SUB_PLACEHOLDER = "__SUBSTITUTION__"
UV_CALL = re.compile(r"(^|[\s;&|=])uv\s")


def join_continuations(text: str) -> str:
    """A shell line continuation is a backslash directly followed by a newline."""
    return text.replace("\\\n", " ")


def env_name_ci(job: dict) -> str:
    return env_name(job).strip().lower()


def prod_job_names(workflow: dict) -> list[str]:
    return [n for n, j in workflow["jobs"].items() if env_name_ci(j) == "production"]


def publish_job_ci() -> tuple[str, dict]:
    names = prod_job_names(wf())
    assert len(names) == 1, f"expected exactly one production job (any letter case), got {names}"
    return names[0], jobs()[names[0]]


def secrets_scope_violations(workflow: dict) -> list[str]:
    bad = []
    prod = prod_job_names(workflow)
    if len(prod) != 1:
        bad.append(f"expected exactly one production job (any letter case), got {prod}")
    top = {k: v for k, v in workflow.items() if k != "jobs"}
    if SECRETS_ANY.search(yaml.safe_dump(top)):
        bad.append("workflow-level mention of secrets (inherited by every job)")
    for name, job in workflow["jobs"].items():
        if name not in prod and SECRETS_ANY.search(yaml.safe_dump(job)):
            bad.append(f"{name}: mentions secrets")
    return bad


def split_shell(text: str) -> tuple[list[str], list[str]]:
    """Quote-aware split into command segments plus the bodies of $(...) and `...` substitutions."""
    segments: list[str] = []
    subs: list[str] = []
    cur: list[str] = []

    def flush() -> None:
        segments.append("".join(cur))
        cur.clear()

    i, n, quote = 0, len(text), ""
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "\\" and quote != "'":
            cur.append(c + nxt)
            i += 2
            continue
        if quote == "'":
            cur.append(c)
            if c == "'":
                quote = ""
            i += 1
            continue
        if c == "$" and nxt == "(":
            depth, j = 1, i + 2
            while j < n and depth:
                depth += {"(": 1, ")": -1}.get(text[j], 0)
                j += 1
            subs.append(text[i + 2 : j - 1])
            cur.append(SUB_PLACEHOLDER)
            i = j
            continue
        if c == "`":
            j = text.find("`", i + 1)
            j = n if j < 0 else j
            subs.append(text[i + 1 : j])
            cur.append(SUB_PLACEHOLDER)
            i = j + 1
            continue
        if quote == '"':
            cur.append(c)
            if c == '"':
                quote = ""
            i += 1
            continue
        if c in "'\"":
            quote = c
            cur.append(c)
            i += 1
            continue
        if c == "#" and (not cur or cur[-1] in " \t"):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c in ";\n":
            flush()
            i += 1
        elif c in "&|" and nxt == c:
            flush()
            i += 2
        elif c == "|":
            flush()
            i += 1
        elif c == "&" and nxt != ">" and (not cur or cur[-1] not in "<>"):
            flush()
            i += 1
        else:
            cur.append(c)
            i += 1
    flush()
    return segments, subs


def command_heads(text: str) -> list[str]:
    heads = []
    segments, subs = split_shell(join_continuations(text))
    for seg in segments:
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        while words and ENV_ASSIGN.match(words[0]):
            words = words[1:]
        while words and words[0] in CLAUSE_LEADERS:
            words = words[1:]
            while words and ENV_ASSIGN.match(words[0]):
                words = words[1:]
        if words:
            heads.append(words[0])
    for sub in subs:
        heads.extend(command_heads(sub))
    return heads


def allowlist_violations(job: dict) -> list[str]:
    bad = []
    for step in job.get("steps", []):
        if "shell" in step:
            bad.append(f"custom shell: {step['shell']}")
        for head in command_heads(str(step.get("run", ""))):
            if head not in ALLOWED_HEADS:
                bad.append(f"command not in the allowlist: {head}")
    return bad


def uv_pin_violations(job: dict) -> list[str]:
    bad = []
    setup = [s for s in job.get("steps", []) if "setup-uv" in str(s.get("uses", ""))]
    if len(setup) != 1:
        bad.append("expected exactly one setup-uv step")
    elif not str((setup[0].get("with") or {}).get("version", "")).strip():
        bad.append("setup-uv has no version input")
    for step in job.get("steps", []):
        if UV_CALL.search(str(step.get("run", ""))):
            e = {**(job.get("env") or {}), **(step.get("env") or {})}
            if str(e.get("UV_PYTHON_DOWNLOADS", "")).strip() != "never":
                label = step.get("name", step.get("run"))
                bad.append(f"step {label!r}: UV_PYTHON_DOWNLOADS is not never")
    return bad


def publish_job_with_step(run: str, **extra: str) -> dict:
    name, job = publish_job_ci()
    job = copy.deepcopy(job)
    job["steps"].append({"name": "x", "run": run, **extra})
    return job


def test_no_job_other_than_publish_mentions_secrets_in_any_form():
    assert secrets_scope_violations(wf()) == []
    name, _ = build_job()
    assert name not in prod_job_names(wf())
    for other, j in jobs().items():
        if other != publish_job_ci()[0]:
            assert not SECRETS_ANY.search(dump(j)), other


def test_exactly_one_job_has_the_production_environment_in_any_letter_case():
    assert len(prod_job_names(wf())) == 1
    name, job = publish_job_ci()
    assert name == publish_job()[0]
    assert env_name(job) == "production"
    assert name != build_job()[0]


def test_secrets_scope_check_rejects_uppercase_environment_and_toJSON_secrets():
    sname = build_job()[0]
    w = copy.deepcopy(wf())
    w["jobs"][sname]["environment"] = "Production"
    w["jobs"][sname]["env"] = {"ALL": "${{ toJSON(secrets) }}"}
    assert secrets_scope_violations(w)
    w = copy.deepcopy(wf())
    w["jobs"][sname]["environment"] = {"name": "PRODUCTION"}
    assert secrets_scope_violations(w)
    w = copy.deepcopy(wf())
    w["jobs"][sname]["env"] = {"ALL": "${{ toJSON(secrets) }}"}
    assert secrets_scope_violations(w)
    exprs = (
        "${{ secrets.X }}", "${{ secrets['X'] }}", "${{ SECRETS.X }}", "${{ toJson(Secrets) }}",
    )
    for expr in exprs:
        w = copy.deepcopy(wf())
        w["jobs"][sname]["steps"][0].setdefault("env", {})["V"] = expr
        assert secrets_scope_violations(w), expr
    w = copy.deepcopy(wf())
    w["env"] = {"V": "${{ secrets.X }}"}
    assert secrets_scope_violations(w)
    w = copy.deepcopy(wf())
    w["jobs"]["relay"] = {"environment": "Production", "runs-on": "u", "steps": []}
    assert secrets_scope_violations(w)


def test_publish_job_runs_only_allowlisted_commands():
    _, job = publish_job_ci()
    assert allowlist_violations(job) == []
    heads = set()
    for step in job["steps"]:
        heads.update(command_heads(str(step.get("run", ""))))
    assert {"ssh", "scp", "rm", "printf", "chmod"} <= heads


def test_publish_key_removal_step_passes_the_allowlist():
    _, job = publish_job_ci()
    step = next(s for s in job["steps"] if str(s.get("run", "")).lstrip().startswith("rm "))
    assert command_heads(step["run"]) == ["rm"]
    assert allowlist_violations({"steps": [step]}) == []


def test_allowlist_rejects_every_way_of_running_an_artifact_file_or_a_wrapper():
    forbidden = [
        "cat site/x | sh",
        'bash -c "$(cat site/x)"',
        'eval "$(cat site/x)"',
        "env bash site/x",
        "sudo bash site/x",
        "timeout 5 bash site/x",
        "nohup sh site/x",
        "cd site && sh x",
        'bash "$PWD/site/x"',
        "awk -f site/x",
        "bash \\\n-e site/x",
        "python site/x",
        "FOO=1 sh site/x",
        "echo `cat site/x`",
        "echo hi & sh site/x",
        "if true; then sh site/x; fi",
        "./site/x",
    ]
    for cmd in forbidden:
        assert allowlist_violations(publish_job_with_step(cmd)), cmd
    allowed = [
        'rm -f "$RUNNER_TEMP/publish_key"',
        "echo 'a; b | sh' >&2",
        "VAR=1\nscp -i k site/index.html host:/dest/",
        "for i in 1 2; do ssh h true && break; sleep 1; done",
        '[ -z "$X" ] || test -n "$Y"',
        "# bash site/x is only a comment\necho ok",
    ]
    for cmd in allowed:
        assert allowlist_violations(publish_job_with_step(cmd)) == [], cmd
    assert allowlist_violations(publish_job_with_step("echo hi", shell="python {0}"))


def test_line_continuations_are_joined_only_when_a_backslash_ends_the_line():
    assert join_continuations("a \\\nb") == "a  b"
    assert join_continuations("a \\nb") == "a \\nb"
    assert command_heads("echo a \\\n bash x") == ["echo"]
    assert command_heads("echo a\n bash x") == ["echo", "bash"]


def test_build_job_pins_the_uv_version_on_setup_uv():
    _, job = build_job()
    setup = [s for s in job["steps"] if "setup-uv" in str(s.get("uses", ""))]
    assert len(setup) == 1
    assert str((setup[0].get("with") or {}).get("version", "")).strip()


def test_every_build_job_step_running_uv_sets_uv_python_downloads_never():
    _, job = build_job()
    uv_steps = [s for s in job["steps"] if UV_CALL.search(str(s.get("run", "")))]
    assert len(uv_steps) >= 2, "the install and the build steps both run uv"
    assert uv_pin_violations(job) == []


def test_uv_pin_check_rejects_a_missing_version_or_a_missing_never():
    _, job = build_job()
    j = copy.deepcopy(job)
    for s in j["steps"]:
        if "setup-uv" in str(s.get("uses", "")):
            s.pop("with", None)
    assert uv_pin_violations(j)
    j = copy.deepcopy(job)
    j.pop("env", None)
    for s in j["steps"]:
        if UV_CALL.search(str(s.get("run", ""))):
            s["env"] = {**(s.get("env") or {}), "UV_PYTHON_DOWNLOADS": "automatic"}
    assert uv_pin_violations(j)
    j = copy.deepcopy(job)
    for s in j["steps"]:
        s.get("env", {}).pop("UV_PYTHON_DOWNLOADS", None)
    j.pop("env", None)
    assert uv_pin_violations(j)
