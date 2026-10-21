"""The one-click light/dark switch, end to end on a real (throwaway) Plasma desktop.

Opt in with PLASMIFY_SANDBOX=1.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from sandbox import Sandbox, available  # noqa: E402

from plasmify import presets  # noqa: E402
from plasmify.palette import is_dark  # noqa: E402

ENABLED = os.environ.get("PLASMIFY_SANDBOX") == "1" and available()


def hex_of(rgb: str) -> str:
    return "#" + "".join(f"{int(c):02x}" for c in rgb.split(","))


@unittest.skipUnless(ENABLED, "set PLASMIFY_SANDBOX=1 on a Plasma 6 desktop to run")
class ModeSwitchTests(unittest.TestCase):
    def test_switch_to_light_and_back(self):
        shots = Path(os.environ.get("PLASMIFY_SANDBOX_SHOTS") or tempfile.mkdtemp(prefix="plasmify-mode-"))
        shots.mkdir(parents=True, exist_ok=True)
        with Sandbox(seed=("icons", "plasmify")) as box:
            proc = box.run([sys.executable, str(ROOT / "tests/integration/mode_flow.py"), str(shots)], timeout=1200)
        lines = [line for line in proc.stdout.splitlines() if line.startswith("[")]
        self.assertTrue(lines, proc.stderr[-3000:])
        start, light, dark = json.loads(lines[-1])
        nord = presets.find("nord-minimal").palette
        self.assertEqual(hex_of(start["window"]), nord.base)
        self.assertEqual(start["button"], "Switch to light")
        self.assertFalse(is_dark(hex_of(light["window"])), light)
        self.assertEqual(light["icons"], "Plasmify-nord-minimal")  # the overlay, now over Papirus
        self.assertEqual(light["button"], "Switch to dark")
        self.assertEqual(hex_of(dark["window"]), nord.base)  # back to the preset's own palette
        self.assertEqual(dark["button"], "Switch to light")


if __name__ == "__main__":
    unittest.main()
