"""Review round 1 of #84 (D148 amendment 1): unit cases beside the exam."""

import merge_gate
import test_merge_gate as t


def run_gate(gh, capsys, *extra):
    code = merge_gate.main(["9", "--session", "lf-pass-1", *extra], run=gh)
    return code, [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]


def test_skipped_exam_integrity_holds(capsys):
    gh = t.Gh(statusCheckRollup=[t.check("unit"), t.check("exam-integrity", "SKIPPED")])
    t.assert_single_hold(*run_gate(gh, capsys), "exam-integrity")


def test_neutral_exam_integrity_holds(capsys):
    gh = t.Gh(statusCheckRollup=[t.check("unit"), t.check("exam-integrity", "NEUTRAL")])
    t.assert_single_hold(*run_gate(gh, capsys), "exam-integrity")


def test_scripts_test_files_are_not_ci_rounds(capsys):
    exam = "0" * 39 + "9"
    oids, commits = t.code_commits(5)
    gh = t.Gh(commits=[{"oid": exam}] + commits)
    gh.files = {o: [t.CODE_FILE] for o in oids}
    gh.files[exam] = ["scripts/test_x.py", "scripts/ci/test_y.py"]
    assert run_gate(gh, capsys) == (0, ["MERGE"])


def test_cross_repository_pr_holds(capsys):
    t.assert_single_hold(*run_gate(t.Gh(isCrossRepository=True), capsys), "cross-repository")


def test_same_repository_pr_merges(capsys):
    assert run_gate(t.Gh(isCrossRepository=False), capsys) == (0, ["MERGE"])


def test_closes_in_the_middle_of_a_sentence_is_ignored(capsys):
    gh = t.Gh(body="This partially closes #7 of the plan.")
    t.assert_single_hold(*run_gate(gh, capsys), "issue")


def test_every_closes_line_needs_the_claim(capsys):
    class OneClaim(t.Gh):
        def __call__(self, argv):
            if argv[:3] == ["issue", "view", "7"]:
                self.calls.append(list(argv))
                return '{"comments": []}'
            return super().__call__(argv)

    gh = OneClaim(body="What.\n\nCloses #5\nCloses #7\n")
    code, lines = run_gate(gh, capsys)
    assert code == 1 and len(lines) == 1 and "#7" in lines[0] and "claim" in lines[0]


def test_two_closes_lines_both_claimed_merge(capsys):
    gh = t.Gh(body="Closes #5\nCloses #7\n")
    assert run_gate(gh, capsys) == (0, ["MERGE"])


# Review round 2 of #84 (D148 amendment 2).


def test_closing_keyword_anywhere_needs_the_claim(capsys):
    class OneClaim(t.Gh):
        def __call__(self, argv):
            if argv[:3] == ["issue", "view", "7"]:
                self.calls.append(list(argv))
                return '{"comments": []}'
            return super().__call__(argv)

    gh = OneClaim(body="Closes #5\nThis also fixes #7.\n",
                  closingIssuesReferences=[{"number": 5}, {"number": 7}])
    code, lines = run_gate(gh, capsys)
    assert code == 1 and len(lines) == 1 and "#7" in lines[0] and "claim" in lines[0]


def test_closing_an_issue_in_another_repository_holds(capsys):
    gh = t.Gh(url="https://github.com/o/r/pull/9",
              closingIssuesReferences=[{"number": 5, "url": "https://github.com/o/r/issues/5"},
                                       {"number": 3, "url": "https://github.com/x/y/issues/3"}])
    t.assert_single_hold(*run_gate(gh, capsys), "another repository")


def test_tests_acceptance_commits_are_not_ci_rounds(capsys):
    exam = "0" * 39 + "9"
    oids, commits = t.code_commits(5)
    gh = t.Gh(commits=[{"oid": exam}] + commits)
    gh.files = {o: [t.CODE_FILE] for o in oids}
    gh.files[exam] = ["tests/acceptance/test_issue_14_x.py"]
    assert run_gate(gh, capsys) == (0, ["MERGE"])
