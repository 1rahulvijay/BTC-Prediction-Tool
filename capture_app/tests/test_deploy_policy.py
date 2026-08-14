from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[1]
DEPLOY = APP / "deploy_gcp.sh"


def _bash() -> str | None:
    if os.name == "nt":
        for candidate in (
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        ):
            if candidate.exists():
                return str(candidate)
    found = shutil.which("bash")
    if found:
        return found
    return None


@unittest.skipUnless(_bash(), "bash is required to validate deploy_gcp.sh")
class DeploymentStoragePolicyTests(unittest.TestCase):
    def _run(self, policy: str, *, existing: bool = False) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / "gcloud-fake"
            log = root / "gcloud.log"
            fake.write_text(
                "#!/usr/bin/env bash\n"
                "set -eu\n"
                "case \"$*\" in\n"
                "  'config get-value project'*) echo btc-test ;;\n"
                "  'billing projects describe btc-test'*) echo true ;;\n"
                "  'storage buckets describe gs://btc-test-bucket'*) "
                "    [[ \"${FAKE_BUCKET_EXISTS:-0}\" == 1 ]] ;;\n"
                "  *) printf '%s\\n' \"$*\" >>\"${GCLOUD_LOG}\" ;;\n"
                "esac\n",
                encoding="utf-8",
                newline="\n",
            )
            fake.chmod(0o755)
            env = {
                **os.environ,
                "GCLOUD_COMMAND": str(fake),
                "GCLOUD_LOG": str(log),
                "FAKE_BUCKET_EXISTS": "1" if existing else "0",
                "CAPTURE_GCS_BUCKET": "btc-test-bucket",
                "CAPTURE_STORAGE_POLICY": policy,
            }
            result = subprocess.run(
                [_bash(), str(DEPLOY), "bucket-create"],
                cwd=APP.parent,
                env=env,
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout, log.read_text(encoding="utf-8") if log.exists() else ""

    def test_monthly_download_new_bucket_has_no_cold_lifecycle(self):
        output, calls = self._run("download-monthly")
        self.assertIn("download-monthly policy", output)
        self.assertIn("storage buckets create", calls)
        self.assertNotIn("lifecycle-file", calls)

    def test_monthly_download_existing_bucket_clears_old_lifecycle(self):
        output, calls = self._run("download-monthly", existing=True)
        self.assertIn("download-monthly policy", output)
        self.assertIn("--clear-lifecycle", calls)
        self.assertIn("--default-storage-class=STANDARD", calls)

    def test_archive_policy_keeps_explicit_cold_transitions(self):
        output, calls = self._run("archive")
        self.assertIn("archive policy", output)
        self.assertIn("--lifecycle-file=", calls)


if __name__ == "__main__":
    unittest.main()
