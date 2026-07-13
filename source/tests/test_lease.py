from __future__ import annotations

from wls.lease import ProcessLease


def test_process_lease_removes_own_lock_file_on_release(tmp_path) -> None:
    lock_path = tmp_path / "runtime.lock"

    with ProcessLease(lock_path):
        assert lock_path.exists()

    assert lock_path.exists() is False
