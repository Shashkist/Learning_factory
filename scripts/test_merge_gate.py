"""Exam for #84: scripts/merge_gate.py <pr> --session <name> decides MERGE or HOLD.

NO NETWORK. The gate is driven through `merge_gate.main(argv, run=fake_gh)`, the
same seam as issue_lock and factory_pick: `run(argv)` receives the gh arguments
(without the leading `gh`) and returns stdout. The fake serves hand-written JSON:

* `pr view <pr> ... --json <fields>`: ONE object holding every field, whatever
  fields were asked for: `author.login`, `body`, `headRefOid`, `commits[].oid`,
  `statusCheckRollup[]` (CheckRun: `name`, `status`, `conclusion`) and
  `comments[]` (`author.login`, `body`, `createdAt`, oldest first).
* `api repos/<owner>/<repo>/commits/<oid>` (or with the `{owner}/{repo}`
  placeholder): `{"files": [{"filename": ...}]}`, plain JSON, no --jq.
* `api --paginate --slurp repos/.../pulls/9/commits`: the PR's commits as one page
  (`[[{"sha": ...}]]`), and `api repos/.../pulls/9`: `{"commits": <count>}` (#611).
* `issue view <n> --json comments`: the claim comments (via issue_lock.render).
* Anything that writes (pr merge, comment, review, edit, close, api -X) fails
  the test: the gate is read-only.
With --repo the gate reaches the repository the way issue_lock does (`-R repo`
on gh commands, the repo in api paths).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import issue_lock
import merge_gate
import pytest

HERE = issue_lock.HERE_REPO
OTHER = "Shashkist/factory"
HEAD = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
OLD = "0123456789abcdef0123456789abcdef01234567"
EXAM_FILE = "labs/manager/tests/acceptance/test_issue_5_x.py"
CODE_FILE = "labs/manager/app/thing.py"
WRITES = {"merge", "comment", "review", "edit", "close", "reopen", "ready"}


def check(name, conclusion="SUCCESS", status="COMPLETED"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def with_gate_defaults(rollup, exam_gate="manager-exam / gate", changes="changes"):
    """D160: a non-empty world lacking the exam gate check (and the `changes` check that
    vouches for a skipped docker) gets a SUCCESS one, so older cases test their own rule.
    A case sets gh.exam_gate / gh.changes to None for a world without it."""
    if not rollup:
        return rollup
    names = {c.get("name") or c.get("context") for c in rollup}
    out = list(rollup)
    for name in (exam_gate, changes):
        if name and name not in names:
            out.append(check(name))
    return out


def good_checks():
    return [check("unit"), check("exam-integrity")]


def verdict(kind="APPROVE", sha=HEAD, k=1, author="Shashkist", at="2026-11-24T10:00:00Z", body=None):
    first = body if body is not None else "Review round " + str(k) + ": " + kind + " at " + sha
    return {"author": {"login": author}, "body": first + "\n\nDetails follow.", "createdAt": at}


def claim_comment(session="lf-pass-1", released=False):
    if released:
        body = issue_lock.retraction(session, "done")
    else:
        now = issue_lock.now()
        body = issue_lock.render({"session": session, "branch": "f/5-x", "worktree": "C:/wt",
                                  "claimed": now, "heartbeat": now, "step": "ship"})
    return {"body": body, "url": "https://github.com/o/r/issues/5#issuecomment-77"}


class Gh:
    """Fake gh serving one PR (#9) that is good by default."""

    def __init__(self, **over):
        self.pr = {
            "author": {"login": "Shashkist"},
            "body": "What.\n\nCloses #5\n",
            "headRefOid": HEAD,
            "commits": [{"oid": OLD}, {"oid": HEAD}],
            "statusCheckRollup": good_checks(),
            "comments": [verdict()],
        }
        self.files = {OLD: [EXAM_FILE], HEAD: [CODE_FILE]}
        self.issue_comments = [claim_comment()]
        self.pr.update(over)
        self.calls = []
        self.exam_gate = "manager-exam / gate"
        self.changes = "changes"
        self.count = None  # `pulls/9` commit count; None means every listed commit
        self.listing_fails = False

    def __call__(self, argv):
        self.calls.append(list(argv))
        assert argv[0] not in WRITES and argv[:2] != ["pr", "merge"], "gate must be read-only"
        assert "-X" not in argv, "gate must be read-only"
        if argv[:2] == ["pr", "view"]:
            pr = dict(self.pr)
            pr["statusCheckRollup"] = with_gate_defaults(
                pr.get("statusCheckRollup") or [], self.exam_gate, self.changes)
            return json.dumps(pr)
        if argv[0] == "api":
            path = next(a for a in argv[1:] if a.startswith("repos/"))
            oids = [c["oid"] for c in self.pr.get("commits") or []]
            if path.endswith("/pulls/9"):
                return json.dumps({"commits": len(oids) if self.count is None else self.count})
            if path.endswith("/pulls/9/commits"):
                assert "--paginate" in argv and "--slurp" in argv, argv
                if self.listing_fails:
                    raise RuntimeError("gh api failed: HTTP 502")
                return json.dumps([[{"sha": o} for o in oids]])
            oid = path.rsplit("/commits/", 1)[1].split("?")[0]
            return json.dumps({"files": [{"filename": f} for f in self.files[oid]]})
        if argv[:2] == ["issue", "view"]:
            return json.dumps({"comments": self.issue_comments})
        raise AssertionError("unexpected gh call: " + repr(argv))


def gate(capsys, gh, *extra, session="lf-pass-1"):
    code = merge_gate.main(["9", "--session", session, *extra], run=gh)
    out = capsys.readouterr().out
    return code, [ln for ln in out.splitlines() if ln.strip()]


def holds(lines):
    return [ln for ln in lines if ln.startswith("HOLD: ")]


def assert_single_hold(code, lines, *words):
    assert code == 1
    assert len(lines) == 1 and lines[0].startswith("HOLD: "), lines
    for w in words:
        assert w.lower() in lines[0].lower(), (w, lines)


def code_commits(n, last=HEAD):
    """n commit ids ending in `last`, all changing code."""
    oids = [format(i + 1, "040x") for i in range(n - 1)] + [last]
    gh_commits = [{"oid": o} for o in oids]
    return oids, gh_commits


# ---- all good -------------------------------------------------------------

def test_all_facts_good_prints_merge_and_exits_zero(capsys):
    code, lines = gate(capsys, Gh())
    assert (code, lines) == (0, ["MERGE"])


def test_neutral_and_skipped_checks_count_as_passed(capsys):
    gh = Gh(statusCheckRollup=[check("unit"), check("exam-integrity"),
                               check("docker", "SKIPPED"), check("lint", "NEUTRAL")])
    assert gate(capsys, gh) == (0, ["MERGE"])


def test_gate_is_read_only_and_only_reads(capsys):
    gh = Gh()
    gate(capsys, gh)
    assert gh.calls and all(c[0] in ("pr", "api", "issue") for c in gh.calls)


# ---- CI check states -------------------------------------------------------

def test_pending_check_holds(capsys):
    gh = Gh(statusCheckRollup=[check("unit", "", "IN_PROGRESS"), check("exam-integrity")])
    assert_single_hold(*gate(capsys, gh), "unit", "pending")


def test_queued_check_holds(capsys):
    gh = Gh(statusCheckRollup=[check("unit", "", "QUEUED"), check("exam-integrity")])
    assert_single_hold(*gate(capsys, gh), "unit")


def test_failed_check_holds(capsys):
    gh = Gh(statusCheckRollup=[check("unit", "FAILURE"), check("exam-integrity")])
    assert_single_hold(*gate(capsys, gh), "unit", "failure")


def test_cancelled_check_holds(capsys):
    gh = Gh(statusCheckRollup=[check("unit", "CANCELLED"), check("exam-integrity")])
    assert_single_hold(*gate(capsys, gh), "unit", "cancelled")


def test_timed_out_and_action_required_checks_hold(capsys):
    for state in ("TIMED_OUT", "ACTION_REQUIRED"):
        gh = Gh(statusCheckRollup=[check("unit", state), check("exam-integrity")])
        assert_single_hold(*gate(capsys, gh), "unit")


def test_no_checks_at_all_holds(capsys):
    code, lines = gate(capsys, Gh(statusCheckRollup=[]))
    assert code == 1 and holds(lines)
    assert any("check" in ln.lower() for ln in holds(lines))
    assert "MERGE" not in lines


def test_missing_exam_integrity_check_holds(capsys):
    gh = Gh(statusCheckRollup=[check("unit")])
    assert_single_hold(*gate(capsys, gh), "exam-integrity")


def test_failed_exam_integrity_check_holds(capsys):
    gh = Gh(statusCheckRollup=[check("unit"), check("exam-integrity", "FAILURE")])
    assert_single_hold(*gate(capsys, gh), "exam-integrity")


# ---- verdict -----------------------------------------------------------------

def test_no_verdict_comment_holds(capsys):
    assert_single_hold(*gate(capsys, Gh(comments=[])), "verdict")


def test_changes_verdict_for_head_holds(capsys):
    gh = Gh(comments=[verdict("CHANGES")])
    assert_single_hold(*gate(capsys, gh), "verdict")


def test_latest_verdict_wins_changes_after_approve_holds(capsys):
    gh = Gh(comments=[verdict("APPROVE", at="2026-11-24T10:00:00Z"),
                      verdict("CHANGES", at="2026-11-24T11:00:00Z")])
    assert_single_hold(*gate(capsys, gh), "verdict")


def test_latest_verdict_wins_approve_after_changes_merges(capsys):
    gh = Gh(comments=[verdict("CHANGES", at="2026-11-24T10:00:00Z"),
                      verdict("APPROVE", sha=HEAD, k=2, at="2026-11-24T11:00:00Z")])
    assert gate(capsys, gh) == (0, ["MERGE"])


def test_approve_for_an_older_sha_holds(capsys):
    gh = Gh(comments=[verdict("APPROVE", sha=OLD)])
    assert_single_hold(*gate(capsys, gh), "verdict")


def test_short_sha_prefix_of_head_counts(capsys):
    gh = Gh(comments=[verdict("APPROVE", sha=HEAD[:7])])
    assert gate(capsys, gh) == (0, ["MERGE"])


def test_prose_line_containing_approve_is_not_a_verdict(capsys):
    gh = Gh(comments=[verdict(body="I APPROVE of this change at " + HEAD),
                      verdict(body="Verdict: APPROVE")])
    assert_single_hold(*gate(capsys, gh), "verdict")


def test_verdict_must_be_the_first_line(capsys):
    c = verdict()
    c["body"] = "Looks fine.\n" + c["body"]
    assert_single_hold(*gate(capsys, Gh(comments=[c])), "verdict")


def test_verdict_from_another_author_is_ignored(capsys):
    gh = Gh(comments=[verdict("APPROVE", author="passer-by")])
    assert_single_hold(*gate(capsys, gh), "verdict")


def test_other_authors_changes_does_not_override_an_approve(capsys):
    gh = Gh(comments=[verdict("APPROVE", at="2026-11-24T10:00:00Z"),
                      verdict("CHANGES", author="passer-by", at="2026-11-24T11:00:00Z")])
    assert gate(capsys, gh) == (0, ["MERGE"])


def test_author_option_names_who_may_post_the_verdict(capsys):
    gh = Gh(comments=[verdict("APPROVE", author="reviewer-bot")])
    assert gate(capsys, gh, "--author", "reviewer-bot") == (0, ["MERGE"])
    gh = Gh(comments=[verdict("APPROVE", author="Shashkist")])
    assert_single_hold(*gate(capsys, gh, "--author", "reviewer-bot"), "verdict")


# ---- rounds ------------------------------------------------------------------

def test_review_round_three_holds(capsys):
    gh = Gh(comments=[verdict("CHANGES", k=1, at="2026-11-24T08:00:00Z"),
                      verdict("CHANGES", k=2, sha=OLD, at="2026-11-24T09:00:00Z"),
                      verdict("APPROVE", k=3, at="2026-11-24T10:00:00Z")])
    assert_single_hold(*gate(capsys, gh), "review round")


def test_review_round_two_still_merges(capsys):
    gh = Gh(comments=[verdict("CHANGES", k=1, sha=OLD, at="2026-11-24T08:00:00Z"),
                      verdict("APPROVE", k=2, at="2026-11-24T10:00:00Z")])
    assert gate(capsys, gh) == (0, ["MERGE"])


def test_six_code_commits_hold(capsys):
    oids, commits = code_commits(6)
    gh = Gh(commits=commits)
    gh.files = {o: [CODE_FILE] for o in oids}
    assert_single_hold(*gate(capsys, gh), "ci round")


def test_five_code_commits_still_merge(capsys):
    oids, commits = code_commits(5)
    gh = Gh(commits=commits)
    gh.files = {o: [CODE_FILE] for o in oids}
    assert gate(capsys, gh) == (0, ["MERGE"])


def test_exam_only_commits_are_not_counted(capsys):
    exam = [format(100 + i, "040x") for i in range(4)]
    oids, commits = code_commits(5)
    gh = Gh(commits=[{"oid": o} for o in exam] + commits)
    gh.files = {o: [EXAM_FILE] for o in exam}
    gh.files.update({o: [CODE_FILE] for o in oids})
    # 9 commits, but only 5 of them change code: 5 CI rounds, within the limit.
    assert gate(capsys, gh) == (0, ["MERGE"])


# ---- every commit, docker on a workflow change (#611) -------------------------------

def test_commit_listing_failure_holds(capsys):
    gh = Gh()
    gh.listing_fails = True
    assert_single_hold(*gate(capsys, gh), "commit")


def test_short_commit_listing_holds(capsys):
    gh = Gh()
    gh.count = 3
    assert_single_hold(*gate(capsys, gh), "2 of the PR's 3 commits")


def test_workflow_change_without_docker_holds_naming_docker(capsys):
    gh = Gh()
    gh.files[HEAD] = [CODE_FILE, ".github/workflows/ci.yml"]
    assert_single_hold(*gate(capsys, gh), "docker", "missing or renamed")


def test_workflow_change_with_docker_skipped_holds_once(capsys):
    gh = Gh(statusCheckRollup=good_checks() + [check("docker", "SKIPPED")])
    gh.files[HEAD] = [".github/workflows/deploy.yml"]
    assert_single_hold(*gate(capsys, gh), "docker", "skipped")


def test_workflow_change_with_docker_success_merges(capsys):
    gh = Gh(statusCheckRollup=good_checks() + [check("docker")])
    gh.files[HEAD] = [".github/workflows/ci.yml"]
    assert gate(capsys, gh) == (0, ["MERGE"])


# ---- claim -------------------------------------------------------------------

def test_claim_held_by_another_session_holds(capsys):
    gh = Gh()
    gh.issue_comments = [claim_comment("lf-someone-else")]
    assert_single_hold(*gate(capsys, gh), "claim")


def test_released_claim_holds(capsys):
    gh = Gh()
    gh.issue_comments = [claim_comment("lf-pass-1", released=True)]
    assert_single_hold(*gate(capsys, gh), "claim")


def test_no_claim_holds(capsys):
    gh = Gh()
    gh.issue_comments = []
    assert_single_hold(*gate(capsys, gh), "claim")


def test_no_linked_issue_holds(capsys):
    gh = Gh(body="What. No closing keyword here, only #5 in prose.")
    assert_single_hold(*gate(capsys, gh), "issue")


def test_claim_is_read_for_the_issue_named_by_closes(capsys):
    gh = Gh(body="Text\n\nCloses #42\n")
    gate(capsys, gh)
    views = [c for c in gh.calls if c[:2] == ["issue", "view"]]
    assert views and views[0][2] == "42"


# ---- several reasons, repo option ---------------------------------------------------

def test_several_reasons_give_several_hold_lines(capsys):
    gh = Gh(statusCheckRollup=[check("unit", "FAILURE"), check("exam-integrity")],
            comments=[])
    gh.issue_comments = [claim_comment("lf-someone-else")]
    code, lines = gate(capsys, gh)
    assert code == 1
    assert len(lines) >= 3 and all(ln.startswith("HOLD: ") for ln in lines), lines
    text = "\n".join(lines).lower()
    for word in ("unit", "verdict", "claim"):
        assert word in text
    assert "MERGE" not in lines


def test_repo_option_reaches_that_repository_with_dash_R(capsys):
    gh = Gh()
    code, lines = gate(capsys, gh, "--repo", OTHER)
    assert (code, lines) == (0, ["MERGE"])
    for c in gh.calls:
        if c[0] == "api":
            paths = [a for a in c if a.startswith("repos/")]
            assert any(OTHER in a or "{owner}/{repo}" in a for a in paths), c
        else:
            assert c[c.index("-R") + 1] == OTHER, c


def test_without_repo_option_no_dash_R(capsys):
    gh = Gh()
    gate(capsys, gh)
    assert gh.calls and not any("-R" in c for c in gh.calls)


# ---- real merged PR, off by default ---------------------------------------------------

@pytest.mark.skipif(os.environ.get("LF_GATE_LIVE") != "1",
                    reason="live: set LF_GATE_LIVE=1 to run the gate on merged PR #560")
def test_live_merged_pr_holds_only_for_claim_and_verdict():
    script = Path(__file__).resolve().parent / "merge_gate.py"
    proc = subprocess.run(
        [sys.executable, str(script), "560", "--session", "exam-live-84",
         "--repo", "Shashkist/Learning_factory"],
        check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert lines and all(ln.startswith("HOLD: ") for ln in lines), lines
    low = [ln.lower() for ln in lines]
    assert all("claim" in ln or "verdict" in ln for ln in low), lines
    assert any("claim" in ln for ln in low), lines
    for word in ("check", "ci round", "review round", "issue"):
        assert not any(word in ln for ln in low), (word, lines)


# ---- docker rule per repository (#645) ----------------------------------------------

def _repos(tmp_path, monkeypatch, *entries):
    f = tmp_path / "repos.yaml"
    f.write_text("\n".join(f"- repo: {r}\n  default: false{extra}" for r, extra in entries) + "\n",
                 encoding="utf-8")
    monkeypatch.setenv("LF_REPOS_FILE", str(f))


def _workflow_pr():
    gh = Gh()
    gh.files[HEAD] = [".github/workflows/ci.yml"]
    return gh


def test_docker_check_false_repo_is_not_held_for_docker(capsys, tmp_path, monkeypatch):
    _repos(tmp_path, monkeypatch, ("o/fac", "\n  docker_check: false"))
    assert gate(capsys, _workflow_pr(), "--repo", "O/FAC") == (0, ["MERGE"])


def test_docker_check_true_repo_is_still_held_for_docker(capsys, tmp_path, monkeypatch):
    _repos(tmp_path, monkeypatch, ("o/lf", "\n  docker_check: true"))
    assert_single_hold(*gate(capsys, _workflow_pr(), "--repo", "o/lf"), "docker")


@pytest.mark.parametrize("extra", ["", "\n  docker_check: maybe"])
def test_docker_check_without_valid_key_fails_closed(capsys, tmp_path, monkeypatch, extra):
    _repos(tmp_path, monkeypatch, ("o/x", extra))
    assert_single_hold(*gate(capsys, _workflow_pr(), "--repo", "o/x"), "docker")


def test_docker_check_unlisted_repo_or_missing_file_fails_closed(tmp_path, monkeypatch):
    _repos(tmp_path, monkeypatch, ("o/fac", "\n  docker_check: false"))
    assert issue_lock.docker_check("o/other") is True
    monkeypatch.setenv("LF_REPOS_FILE", str(tmp_path / "missing.yaml"))
    assert issue_lock.docker_check("o/fac") is True


def test_docker_check_false_never_turns_the_rule_off_for_this_repository(capsys, tmp_path, monkeypatch):
    _repos(tmp_path, monkeypatch, (issue_lock.HERE_REPO, "\n  docker_check: false"))
    assert issue_lock.docker_check(issue_lock.HERE_REPO.upper()) is True
    assert issue_lock.docker_check() is True
    assert_single_hold(*gate(capsys, _workflow_pr(), "--repo", issue_lock.HERE_REPO), "docker")
    assert_single_hold(*gate(capsys, _workflow_pr()), "docker")


def test_docker_check_warns_on_an_unreadable_repos_file(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("LF_REPOS_FILE", str(tmp_path / "missing.yaml"))
    assert issue_lock.docker_check("o/fac") is True
    assert "repos.yaml" in capsys.readouterr().err


def _run(name, state, started=None, wf="ci"):
    c = {"name": name, "status": "COMPLETED", "conclusion": state, "workflowName": wf}
    if started:
        c["startedAt"] = started
    return c


def test_latest_runs_keeps_the_newest_per_workflow_and_name():
    old = _run("a", "FAILURE", "2026-01-01T00:00:00Z")
    new = _run("a", "SUCCESS", "2026-01-02T00:00:00Z")
    other = _run("a", "FAILURE", "2026-01-01T00:00:00Z", wf="other")
    kept, reasons = merge_gate._latest_runs([new, old, other])
    assert reasons == [] and kept == [new, other]


def test_latest_runs_uses_completed_at_and_ignores_the_zero_time():
    old = {"name": "a", "completedAt": "2026-01-01T00:00:00Z"}
    new = {"name": "a", "completedAt": "2026-01-02T00:00:00Z"}
    assert merge_gate._latest_runs([old, new]) == ([new], [])
    pending = {"name": "a", "completedAt": "0001-01-01T00:00:00Z"}
    kept, reasons = merge_gate._latest_runs([old, pending])
    assert kept == [] and "a" in reasons[0]


@pytest.mark.parametrize("runs", [
    [_run("a", "FAILURE"), _run("a", "SUCCESS")],
    [_run("a", "FAILURE", "2026-01-01T00:00:00Z"), _run("a", "SUCCESS")],
    [_run("a", "FAILURE", "2026-01-01T00:00:00Z"), _run("a", "SUCCESS", "2026-01-01T00:00:00Z")],
])
def test_latest_runs_fails_closed_when_the_latest_cannot_be_told(runs):
    kept, reasons = merge_gate._latest_runs(runs)
    assert kept == [] and "a" in reasons[0]


def test_latest_runs_single_entry_needs_no_timestamp():
    assert merge_gate._latest_runs([_run("a", "SUCCESS")]) == ([_run("a", "SUCCESS")], [])


def _url(run_id, job=1):
    return f"https://github.com/o/r/actions/runs/{run_id}/job/{job}"


def _with(run, url):
    return {**run, "detailsUrl": url}


def test_latest_runs_older_run_rerun_later_does_not_hide_newer_failure():
    old = _with(_run("a", "SUCCESS", "2026-01-03T00:00:00Z"), _url(10))
    new = _with(_run("a", "FAILURE", "2026-01-02T00:00:00Z"), _url(20))
    assert merge_gate._latest_runs([old, new]) == ([new], [])


def test_latest_runs_newer_run_id_passing_wins_over_older_failure():
    old = _with(_run("a", "FAILURE", "2026-01-03T00:00:00Z"), _url(10))
    new = _with(_run("a", "SUCCESS", "2026-01-02T00:00:00Z"), _url(20))
    assert merge_gate._latest_runs([old, new]) == ([new], [])


def test_latest_runs_same_run_id_attempts_are_ordered_by_start_time():
    first = _with(_run("a", "FAILURE", "2026-01-01T00:00:00Z"), _url(10, 1))
    second = _with(_run("a", "SUCCESS", "2026-01-01T00:05:00Z"), _url(10, 2))
    assert merge_gate._latest_runs([second, first]) == ([second], [])


def test_latest_runs_mixed_run_id_presence_holds():
    a = _with(_run("a", "FAILURE", "2026-01-01T00:00:00Z"), _url(10))
    b = _run("a", "SUCCESS", "2026-01-02T00:00:00Z")
    kept, reasons = merge_gate._latest_runs([a, b])
    assert kept == [] and "a" in reasons[0]


def test_latest_runs_mixed_started_and_completed_at_holds():
    a = {"name": "a", "startedAt": "2026-01-01T00:00:00Z"}
    b = {"name": "a", "completedAt": "2026-01-02T00:00:00Z"}
    kept, reasons = merge_gate._latest_runs([a, b])
    assert kept == [] and "a" in reasons[0]
