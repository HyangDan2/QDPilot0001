"""Phase 2.2.1 asset regression tests (WO-02 / WO-03).

These read the shipped files, not a fixture: the manifest is the contract, the
atlas on disk must match it, and the Run cycle must keep the motion properties
the artwork correction was about (two alternating steps, an airborne phase, a
loop seam that is no worse than the rest of the cycle).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SPRITES = ROOT / "static/public/assets/sprites"
MANIFEST = SPRITES / "manifest.json"
SERVICE_CELL = 384
AUTHORING_CELL = 768
BUDGET = 1024 * 1024


def png_header(path: Path) -> tuple[int, int, int, int]:
    data = path.read_bytes()[:26]
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{path.name} is not a PNG")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height, data[24], data[25]


def cells(path: Path, columns: int, rows: int, frames: int):
    image = Image.open(path).convert("RGBA")
    cw, ch = image.width // columns, image.height // rows
    out = []
    for index in range(frames):
        r, c = divmod(index, columns)
        out.append(np.array(image.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch))))
    return out


class SpriteAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_every_state_ships_a_384_service_atlas_within_budget(self):
        for state, entry in self.manifest["states"].items():
            source = entry["source"]
            path = SPRITES / source["file"]
            self.assertTrue(path.is_file(), f"{state} service atlas missing")
            self.assertLessEqual(path.stat().st_size, BUDGET, f"{state} over the 1 MiB budget")
            width, height, depth, colour = png_header(path)
            self.assertEqual((width, height), (source["width"], source["height"]), state)
            self.assertEqual(width // source["columns"], SERVICE_CELL, state)
            self.assertEqual(height // source["rows"], SERVICE_CELL, state)
            self.assertIn(colour, (3, 6), f"{state} must keep alpha")
            self.assertEqual(depth, 8, state)
            self.assertEqual(source["columns"] * source["rows"], entry["frames"], state)
            # The browser must never be pointed at a 768 px-cell master.
            self.assertNotIn("-v3-", source["file"], state)
            self.assertIn("-v31-", source["file"], state)

    def test_authoring_masters_are_declared_and_kept_at_768(self):
        self.assertEqual(self.manifest["authoring"]["cell"], [AUTHORING_CELL, AUTHORING_CELL])
        self.assertEqual(self.manifest["cell"], [SERVICE_CELL, SERVICE_CELL])
        for state, entry in self.manifest["states"].items():
            authoring = entry["authoring"]
            master = ROOT / authoring["file"]
            self.assertTrue(master.is_file(), f"{state} authoring master missing")
            width, height, depth, colour = png_header(master)
            self.assertEqual((width, height), (authoring["width"], authoring["height"]), state)
            columns, rows = entry["source"]["columns"], entry["source"]["rows"]
            self.assertEqual((width, height), (columns * AUTHORING_CELL, rows * AUTHORING_CELL), state)
            self.assertEqual((depth, colour), (8, 6), f"{state} master must be PNG-32")
            # Two resolutions, one picture: the service export is exactly half.
            self.assertEqual(width, entry["source"]["width"] * 2, state)
            self.assertEqual(height, entry["source"]["height"] * 2, state)

    def test_service_export_preserves_the_master_pixels(self):
        """A downscaled export must still be the same drawing (no cropping flip)."""
        for state, entry in self.manifest["states"].items():
            service = Image.open(SPRITES / entry["source"]["file"]).convert("RGBA")
            master = Image.open(ROOT / entry["authoring"]["file"]).convert("RGBA")
            expected = master.resize(service.size, Image.LANCZOS)
            a = np.array(service, dtype=np.float32)
            b = np.array(expected, dtype=np.float32)
            mse = float(((a - b) ** 2).mean())
            psnr = 100.0 if mse == 0 else 10 * np.log10(255.0**2 / mse)
            self.assertGreaterEqual(psnr, 30.0, f"{state} service export drifted from the master ({psnr:.1f} dB)")

    def test_run_cycle_keeps_its_motion_properties(self):
        entry = self.manifest["states"]["run"]
        source = entry["source"]
        self.assertEqual(entry["frames"], 12)
        self.assertEqual(entry["fps"], 12)
        self.assertTrue(entry["loop"])
        frames = cells(SPRITES / source["file"], source["columns"], source["rows"], entry["frames"])
        masks = [frame[:, :, 3] > 16 for frame in frames]
        # 1. no duplicated frame: the cycle really has twelve drawings
        digests = {frame.tobytes() for frame in frames}
        self.assertEqual(len(digests), 12)
        # 2. left/right steps: the second half is not a copy of the first half
        for first, second in ((0, 6), (1, 7), (2, 8), (3, 9), (4, 10), (5, 11)):
            a, b = masks[first], masks[second]
            inter = np.logical_and(a, b).sum()
            union = np.logical_or(a, b).sum()
            self.assertLess(inter / union, 0.75, f"frame {first} and {second} are the same step")
        # 3. bounce: some frames are airborne, the rest stand on the floor line
        soles = []
        for mask in masks:
            ys = np.nonzero(mask.any(axis=1))[0]
            soles.append(int(ys.max()))
        contact = max(set(soles), key=soles.count)
        lifted = [index for index, sole in enumerate(soles) if contact - sole >= 10]
        self.assertGreaterEqual(len(lifted), 2, "the run has no airborne phase")
        self.assertLessEqual(len(lifted), 4, "too much of the run is airborne")
        # 4. loop seam: frame 11 -> frame 0 is no worse than a normal step
        pairs = []
        for index in range(12):
            a, b = masks[index], masks[(index + 1) % 12]
            inter = np.logical_and(a, b).sum()
            union = np.logical_or(a, b).sum()
            pairs.append(inter / union)
        self.assertGreaterEqual(pairs[11], min(pairs) + 0.02,
                                f"loop seam {pairs[11]:.3f} is the worst step of the cycle {min(pairs):.3f}")
        # 5. no frame is clipped by the cell
        for index, mask in enumerate(masks):
            ys, xs = np.nonzero(mask)
            self.assertGreater(xs.min(), 0, f"frame {index} touches the left cell edge")
            self.assertLess(xs.max(), mask.shape[1] - 1, f"frame {index} touches the right cell edge")
            self.assertGreater(ys.min(), 0, f"frame {index} touches the top cell edge")

    def test_attack_hit_frame_is_a_strike_pose(self):
        entry = self.manifest["states"]["attack"]
        self.assertEqual(entry["hits"], [{"frame": 6, "type": "strike"}])
        frames = cells(SPRITES / entry["source"]["file"], entry["source"]["columns"],
                       entry["source"]["rows"], entry["frames"])
        hammer_y = []
        hammer_x = []
        for frame in frames:
            rgba = frame.astype(np.int16)
            r, g, b = rgba[:, :, 0], rgba[:, :, 1], rgba[:, :, 2]
            opaque = rgba[:, :, 3] > 128
            mx = np.maximum(np.maximum(r, g), b)
            mn = np.minimum(np.minimum(r, g), b)
            sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
            mask = opaque & (b > r) & (r > g) & (b - g > 45) & (sat > 0.35) & (mx > 70)
            ys, xs = np.nonzero(mask)
            self.assertGreater(len(xs), 500, "the hammer is not visible in this frame")
            # the hammer head is the right-most mass of the arm
            hammer_y.append(float(np.median(ys[xs > np.percentile(xs, 70)])))
            hammer_x.append(float(np.percentile(xs, 95)))
        # the strike frame points the hammer forward, at target height, and the
        # swing carries on downwards afterwards
        self.assertGreater(hammer_x[6], np.median(hammer_x), "frame 6 does not reach forward")
        self.assertTrue(min(hammer_y) < hammer_y[6] < max(hammer_y), "frame 6 is not at target height")
        self.assertGreater(hammer_y[7], hammer_y[6], "no follow-through after frame 6")

    def test_audit_tool_reports_no_problems(self):
        tmp = tempfile.mkdtemp()
        try:
            report = Path(tmp) / "audit.json"
            result = subprocess.run(
                [sys.executable, str(ROOT / "tools/wo0221_asset_audit.py"), "--json", str(report)],
                cwd=ROOT, capture_output=True, text=True, timeout=300,
            )
            self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["problems"], 0, result.stdout[-2000:])
            by_state = {row["state"]: row for row in data["states"]}
            self.assertEqual(by_state["run"]["frame_count"], 12)
            self.assertTrue(by_state["run"]["anchor"]["lifted_frames"],
                            "the audit does not see the run's airborne frames")
            self.assertTrue(all(by_state["attack"]["attack_hit_check"].values()),
                            by_state["attack"]["attack_hit_check"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
