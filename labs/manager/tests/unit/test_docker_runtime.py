import subprocess

import pytest


@pytest.mark.docker
def test_gvisor_runtime_runs_a_container() -> None:
    result = subprocess.run(
        ["docker", "run", "--rm", "--runtime=runsc", "alpine", "true"],
        capture_output=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr.decode()
