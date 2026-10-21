"""The reported bug, end to end: a panel colour picked in the Customize tab and applied at once
must be the colour of the real panel, also while a window touches it.

Opt in with PLASMIFY_SANDBOX=1 (runs the real window in the throwaway desktop).
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from sandbox import Sandbox, available  # noqa: E402

ENABLED = os.environ.get("PLASMIFY_SANDBOX") == "1" and available()


def close(a: str, b: str, tolerance: int = 6) -> bool:
    return all(abs(int(a[i : i + 2], 16) - int(b[i : i + 2], 16)) <= tolerance for i in (1, 3, 5))


@unittest.skipUnless(ENABLED, "set PLASMIFY_SANDBOX=1 on a Plasma 6 desktop to run")
class GuiFlowTests(unittest.TestCase):
    def test_panel_colour_sticks(self):
        shots = Path(os.environ.get("PLASMIFY_SANDBOX_SHOTS") or tempfile.mkdtemp(prefix="plasmify-gui-"))
        shots.mkdir(parents=True, exist_ok=True)
        with Sandbox(seed=("icons", "plasmify")) as box:
            proc = box.run([sys.executable, str(ROOT / "tests/integration/gui_flow.py"), str(shots)], timeout=900)
        lines = [line for line in proc.stdout.splitlines() if line.startswith("[")]
        self.assertTrue(lines, proc.stderr[-3000:])
        results = json.loads(lines[-1])
        for result in results[:-1]:
            with self.subTest(result["choice"]):
                self.assertTrue(close(result["panel"], result["expected"]), result)
        self.assertEqual(results[-1]["shown_after_apply"], "surface")


if __name__ == "__main__":
    unittest.main()
