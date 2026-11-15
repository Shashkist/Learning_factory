# ruff: noqa: E501
"""Acceptance tests for issue #92 (D39), deploy side: the three Google values travel from the
`production` environment to the server's `.env` without ever being printed.

It reuses the stubbed-server harness of the #13 exam (`test_issue_13_deploy.py`): the real
`labs/deploy/deploy.sh` and the real `.github/workflows/deploy.yml` run steps, with docker,
curl, openssl, ssh and scp replaced by recording stubs. Nothing is deployed.

Interface (D39):
- stdin lines `LF_GOOGLE_CLIENT_ID=...`, `LF_GOOGLE_CLIENT_SECRET=...`, `LF_ADMIN_EMAILS=...`
  next to `LF_DATABASE_URL` (any may be absent or empty);
- deploy.sh writes each present value into `<root>/.env`, keeps an existing one when none is
  sent (as it does for LF_DATABASE_URL), refuses a value with a single quote, logs one skip
  line per value that is still missing afterwards (the line names the variable, contains the word
  "skip", never a value) and carries on: a missing Google value is not a failed or skipped deploy;
- deploy.yml: `LF_GOOGLE_CLIENT_ID` and `LF_GOOGLE_CLIENT_SECRET` from `secrets.*`,
  `LF_ADMIN_EMAILS` from `secrets.*` only (#99: no `vars.*` fallback), in the deploy step's `env:`, sent on the ssh stdin.
"""
import re

import pytest
import test_issue_13_deploy as t13

CLIENT_ID = "fake-client-92.example.test"
CLIENT_SECRET = "fake-client-" + "secret-92-not-real"  # synthetic
EMAILS = "admin.one@example.test,second.admin@example.test"
NAMES = ("LF_GOOGLE_CLIENT_ID", "LF_GOOGLE_CLIENT_SECRET", "LF_ADMIN_EMAILS")
VALUES = {"LF_GOOGLE_CLIENT_ID": CLIENT_ID, "LF_GOOGLE_CLIENT_SECRET": CLIENT_SECRET,
          "LF_ADMIN_EMAILS": EMAILS}  # fmt: skip
SECRET_BITS = (CLIENT_ID, CLIENT_SECRET, EMAILS, "admin.one@example.test", "second.admin@example.test")


@pytest.fixture
def server(tmp_path):
    return t13.Server(tmp_path)


def stdin_with(**google: str | None) -> str:
    base = t13._stdin()
    extra = "".join(f"{k}={v}\n" for k, v in google.items() if v is not None)
    return base + extra


def all_google() -> str:
    return stdin_with(**VALUES)


# ================================================================== deploy.sh


def test_absent_google_values_print_one_skip_line_each_and_the_deploy_still_succeeds(server):
    r = server.run(t13._stdin())
    t13._ok(r)
    out = (r.stdout + r.stderr).splitlines()
    for name in NAMES:
        lines = [ln for ln in out if name in ln]
        assert len(lines) == 1, (name, lines)
        assert re.search(r"skip", lines[0], re.I), lines[0]
    assert server.compose_up(), "the manager must still be deployed"
    assert (server.root / "deployed-tag").read_text().strip() == t13.NEW_TAG
    env = server.env_values()
    assert not any(env.get(n) for n in NAMES)


def test_one_missing_value_is_named_alone(server):
    r = server.run(stdin_with(LF_GOOGLE_CLIENT_ID=CLIENT_ID, LF_ADMIN_EMAILS=EMAILS))
    t13._ok(r)
    out = r.stdout + r.stderr
    skips = [ln for ln in out.splitlines() if re.search(r"skip", ln, re.I)]
    assert any("LF_GOOGLE_CLIENT_SECRET" in ln for ln in skips), out
    assert not any("LF_GOOGLE_CLIENT_ID" in ln or "LF_ADMIN_EMAILS" in ln for ln in skips), skips
    assert server.compose_up()


def test_empty_google_values_count_as_missing_not_as_a_failure(server):
    r = server.run(stdin_with(LF_GOOGLE_CLIENT_ID="", LF_GOOGLE_CLIENT_SECRET="", LF_ADMIN_EMAILS=""))
    t13._ok(r)
    assert not any(server.env_values().get(n) for n in NAMES)


def test_present_google_values_land_in_env(server):
    r = server.run(all_google())
    t13._ok(r)
    env = server.env_values()
    for name, value in VALUES.items():
        assert env.get(name) == value, name
    assert env.get("LF_DATABASE_URL") == t13.DSN, "the other values are untouched"
    assert not [ln for ln in (r.stdout + r.stderr).splitlines()
                if re.search(r"skip", ln, re.I) and any(n in ln for n in NAMES)]  # fmt: skip


def test_google_values_never_reach_the_output_or_any_command_line(server):
    r = server.run(all_google())
    t13._ok(r)
    seen = r.stdout + r.stderr + "\n".join(server.calls())
    for bit in SECRET_BITS:
        assert bit not in seen, f"{bit!r} leaked"
    # the missing-value path names the variable but must not print a value either
    r2 = server.run(stdin_with(LF_GOOGLE_CLIENT_SECRET=CLIENT_SECRET))
    t13._ok(r2)
    assert CLIENT_SECRET not in r2.stdout + r2.stderr + "\n".join(server.calls())


def test_google_values_are_kept_when_a_later_deploy_sends_none_and_replaced_when_new(server):
    t13._ok(server.run(all_google()))
    t13._ok(server.run(t13._stdin()))
    env = server.env_values()
    for name, value in VALUES.items():
        assert env.get(name) == value, f"an empty stdin must not erase {name}"
    t13._ok(server.run(stdin_with(LF_ADMIN_EMAILS="new.admin@example.test")))
    env = server.env_values()
    assert env["LF_ADMIN_EMAILS"] == "new.admin@example.test"
    assert env["LF_GOOGLE_CLIENT_ID"] == CLIENT_ID


def test_values_already_in_env_do_not_produce_a_skip_line(server):
    t13._ok(server.run(all_google()))
    r = server.run(t13._stdin())
    t13._ok(r)
    assert not [ln for ln in (r.stdout + r.stderr).splitlines()
                if re.search(r"skip", ln, re.I) and any(n in ln for n in NAMES)]  # fmt: skip


@pytest.mark.parametrize("name", NAMES)
def test_a_single_quote_in_a_google_value_fails_the_deploy_without_echoing_it(server, name):
    bad = "bad'value-92-sentinel"
    r = server.run(stdin_with(**{n: (bad if n == name else VALUES[n]) for n in NAMES}))
    assert r.returncode != 0
    assert "bad'value-92-sentinel" not in r.stdout + r.stderr + "\n".join(server.calls())
    assert server.compose_up() == [], "nothing may start with a value that would break .env"


# ================================================================== deploy.yml


@pytest.fixture
def fake_values(monkeypatch):
    """Make the #13 harness give the three names distinct synthetic values."""
    original = t13._fake_for

    def fake_for(expr: str) -> str:
        for name, value in VALUES.items():
            if name in expr:
                return value
        return original(expr)

    monkeypatch.setattr(t13, "_fake_for", fake_for)


def _deploy_step() -> dict:
    steps = t13._job(t13._workflow())["steps"]
    for step in steps:
        if "deploy.sh" in step.get("run", ""):
            return step
    pytest.fail("no step runs deploy.sh")


def test_workflow_reads_secrets_for_the_id_the_secret_and_the_allow_list():
    env = _deploy_step().get("env") or {}
    assert "secrets.LF_GOOGLE_CLIENT_ID" in str(env.get("LF_GOOGLE_CLIENT_ID")), env
    assert "secrets.LF_GOOGLE_CLIENT_SECRET" in str(env.get("LF_GOOGLE_CLIENT_SECRET")), env
    assert "secrets.LF_ADMIN_EMAILS" in str(env.get("LF_ADMIN_EMAILS")), env
    assert "vars." not in str(env.get("LF_ADMIN_EMAILS")), env  # #99: secrets only


def test_workflow_still_keeps_expressions_out_of_run_scripts():
    for step in t13._job(t13._workflow())["steps"]:
        assert "${{" not in step.get("run", ""), step.get("name")


def test_google_values_travel_on_ssh_stdin_and_never_on_a_command_line(tmp_path, fake_values):
    run = t13._run_workflow_steps(tmp_path)
    for name, rc, out in run["steps"]:
        assert rc == 0, f"step {name} failed: {out}"
        for bit in SECRET_BITS:
            assert bit not in out, f"step {name} printed {bit!r}"
    call = t13._deploy_call(run)
    for name, value in VALUES.items():
        assert f"{name}={value}" in call["stdin"], name
    for c in run["calls"]:
        joined = " ".join(c["argv"])
        for bit in SECRET_BITS:
            assert bit not in joined, f"{bit!r} on a command line: {c['argv'][0]}"


@pytest.mark.parametrize("name", NAMES)
def test_a_missing_google_value_sends_nothing_and_does_not_fail_the_workflow(
    tmp_path, fake_values, name
):
    run = t13._run_workflow_steps(tmp_path, blank=(name,))
    assert all(rc == 0 for _, rc, _ in run["steps"]), run["steps"]
    call = t13._deploy_call(run)
    assert not re.search(rf"^{name}=\S", call["stdin"], re.M)
    for other in NAMES:
        if other != name:
            assert f"{other}={VALUES[other]}" in call["stdin"]
