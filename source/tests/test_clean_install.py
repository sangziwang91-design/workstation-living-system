import subprocess
import sys
import os
import tempfile
import shutil
from pathlib import Path

def test_clean_install_and_smoke():
    # Build wheel
    source_root = Path(__file__).parent.parent.resolve()
    subprocess.run([sys.executable, "-m", "build", str(source_root)], check=True, capture_output=True)

    # Find built wheel
    dist_dir = source_root / "dist"
    wheels = list(dist_dir.glob("*.whl"))
    assert wheels
    wheel_path = wheels[0]

    with tempfile.TemporaryDirectory() as tmpdir:
        venv_path = Path(tmpdir) / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)

        # Determine pip and python paths
        if os.name == "nt":
            pip_exe = venv_path / "Scripts" / "pip.exe"
            python_exe = venv_path / "Scripts" / "python.exe"
            wls_exe = venv_path / "Scripts" / "wls.exe"
        else:
            pip_exe = venv_path / "bin" / "pip"
            python_exe = venv_path / "bin" / "python"
            wls_exe = venv_path / "bin" / "wls"

        # Install wheel
        subprocess.run([str(pip_exe), "install", str(wheel_path)], check=True, capture_output=True)

        # CLI help check
        res = subprocess.run([str(wls_exe), "--help"], capture_output=True, text=True)
        assert res.returncode == 0
        assert "Workstation Living System" in res.stdout

        # Import smoke test from outside source tree
        # Current directory is already outside source tree (it's the pytest run dir)
        # but let's be explicit
        res = subprocess.run([str(python_exe), "-c", "import wls; print(wls.__file__)"], capture_output=True, text=True, cwd=tmpdir)
        assert res.returncode == 0
        # It should NOT be from source/src
        assert str(source_root / "src") not in res.stdout

        # Functional smoke test
        smoke_script = """
import wls
from wls.config import default_config
from wls.runtime import LivingSystem
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    config = default_config(home)
    config.secret_path.parent.mkdir(parents=True, exist_ok=True)
    config.secret_path.write_bytes(b'a'*32)
    home.joinpath('secrets').mkdir(parents=True, exist_ok=True)
    home.joinpath('secrets/approval.key').write_bytes(b'b'*32)

    runtime = LivingSystem(config)
    runtime.run_cycle()
    ok, details = runtime.ledger.verify()
    if not ok: raise Exception('Verification failed')
    print('SUCCESS')
"""
        res = subprocess.run([str(python_exe), "-c", smoke_script], capture_output=True, text=True, cwd=tmpdir)
        assert res.returncode == 0
        assert "SUCCESS" in res.stdout

if __name__ == "__main__":
    test_clean_install_and_smoke()
