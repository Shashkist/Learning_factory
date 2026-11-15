r"""Acceptance tests for issue #49, Docker part (CI `docker` job, runsc).

* `created` containers are listed as running by the real `DockerRuntime`; only `exited` and
  `dead` are stopped.
* The student's write surface: in a lab container started the way the manager starts it
  (`DockerRuntime.start(container_spec(...))`), everything `student` can write or owns lives on a
  capped tmpfs. The issue's command is `find / -xdev \( -perm -0002 -o -user student \)`; this
  exam adds `! -type l`, because symlinks always show mode 0777 and hold no data. A listed path
  counts as capped when `stat -f` says tmpfs and `df` reports a total of at most 160 MB (the
  largest cap is the 128 MB home). With `-xdev` the mount points themselves are listed, the files
  below them are not. `/var/mail/student` (the mail spool `useradd -m` creates, owned by
  `student`) and `/dev/shm` are checked by name as well, including a 100 MB write that must fail.
Harness: fixtures of the #7 Docker exam, imported from the sibling module.
"""
# ruff: noqa: F811, F401  (fixtures imported from the #7 exams are used by name)
import shlex
import subprocess
import uuid

import pytest

# fixtures and helpers of the #7 Docker exam
from test_issue_7_session_runtime_docker import (  # noqa: F401
    MB,
    Started,
    _field,
    _listed,
    _ready,
    _start,
    box,
    call,
    docker,
    image,
    lab,
    loop,
    network,
    runtime,
)

pytestmark = pytest.mark.docker

CAP = 160 * MB


def _rm_later(request, name: str) -> None:
    request.addfinalizer(
        lambda: subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)
    )


def test_a_created_container_is_listed_as_running(runtime, call, image, request):
    session = str(uuid.uuid4())
    name = f"lab-{session}"
    _rm_later(request, name)
    docker("create", "--name", name, "--label", "lf=1", "--label", f"lf.session={session}",
           image, "sleep", "60")
    assert docker("inspect", "-f", "{{.State.Status}}", name).stdout.strip() == "created"
    item = _listed(runtime, call, name)
    assert item is not None, "a created lab container must be listed"
    assert _field(item, "running", 1) is True


def test_an_exited_container_is_listed_as_stopped(runtime, call, image, request):
    session = str(uuid.uuid4())
    name = f"lab-{session}"
    _rm_later(request, name)
    docker("run", "-d", "--name", name, "--label", "lf=1", "--label", f"lf.session={session}",
           "--entrypoint", "true", image)
    docker("wait", name)
    assert docker("inspect", "-f", "{{.State.Status}}", name).stdout.strip() == "exited"
    item = _listed(runtime, call, name)
    assert item is not None
    assert _field(item, "running", 1) is False


def _fs(box: Started, path: str) -> tuple[str, int]:
    q = shlex.quote(path)
    kind = box.exec("root", f"stat -f -c %T {q}").stdout.strip()
    row = box.exec("root", f"df -Pk {q} | tail -n 1").stdout.split()
    assert len(row) >= 2, (path, row)
    return kind, int(row[1]) * 1024


def _assert_capped_tmpfs(box: Started, path: str) -> None:
    kind, total = _fs(box, path)
    assert kind == "tmpfs", f"{path} is on {kind!r}, not a tmpfs"
    assert total <= CAP, f"{path} sits on a tmpfs of {total // MB} MB, which is not capped"


def test_everything_student_can_write_or_owns_is_on_a_capped_tmpfs(box):
    res = box.exec(
        "student",
        r"find / -xdev \( -perm -0002 -o -user student \) ! -type l -print 2>/dev/null",
    )
    paths = [p for p in res.stdout.splitlines() if p.strip()]
    assert paths, "find listed nothing: /home/student and /tmp should show up as mount points"
    bad = []
    for path in paths:
        kind, total = _fs(box, path)
        if kind != "tmpfs" or total > CAP:
            bad.append(f"{path} ({kind}, {total // MB} MB)")
    assert not bad, "student-writable or student-owned paths outside a capped tmpfs: " + ", ".join(
        bad
    )


def test_the_mail_spool_of_student_is_not_an_uncapped_file(box):
    exists = box.exec("root", "test -e /var/mail/student").returncode == 0
    if exists:
        _assert_capped_tmpfs(box, "/var/mail/student")
    res = box.exec("student", "dd if=/dev/zero of=/var/mail/student bs=1M count=200 2>&1")
    assert res.returncode != 0, "student wrote 200 MB into /var/mail/student"
    assert box.inspect()["State"]["Running"] is True
    box.exec("root", "test -s /var/mail/student && : > /var/mail/student; true")


def test_dev_shm_is_capped(box):
    _assert_capped_tmpfs(box, "/dev/shm")
    res = box.exec("student", "dd if=/dev/zero of=/dev/shm/big bs=1M count=200 2>&1")
    assert res.returncode != 0, "student wrote 200 MB into /dev/shm"
    assert box.inspect()["State"]["Running"] is True
    box.exec("student", "rm -f /dev/shm/big")


def test_student_cannot_write_a_file_on_the_root_filesystem(box):
    res = box.exec(
        "student",
        "for d in / /etc /usr /var /var/mail /var/spool /opt /srv /mnt /media; do "
        "f=$d/lf-probe-$$; if (: > $f) 2>/dev/null; then echo \"$f\"; rm -f $f; fi; done",
    )
    assert res.stdout.strip() == "", f"student can create files in: {res.stdout.split()}"
