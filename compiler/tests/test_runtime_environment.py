from __future__ import annotations

import os
import subprocess
import sys


def test_package_disables_onnx_telemetry_before_native_import() -> None:
    environment = os.environ.copy()
    environment["ORT_DISABLE_TELEMETRY"] = "0"
    command = [
        sys.executable,
        "-c",
        (
            "import os,sys; "
            "import compiler_service; "
            "assert os.environ['ORT_DISABLE_TELEMETRY'] == '1'; "
            "assert 'compiler_service.api' not in sys.modules; "
            "assert not any(name == 'onnxruntime' or "
            "name.startswith('onnxruntime.') for name in sys.modules)"
        ),
    ]

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
