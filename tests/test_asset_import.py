"""Import-gate tests for tools/import_phase22_atlas.py (WO-06).

A synthetic delivery is built in a temporary repository so the gate can be
exercised without touching the real manifest: dry run, import, re-import,
corrupt delivery, ambiguous delivery, wrong grid, duplicated frames, clipped
frames and preservation of every other state.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools/import_phase22_atlas.py"

import sys

sys.path.insert(0, str(ROOT / "tools"))
import import_phase22_atlas as importer  # noqa: E402


def synthetic_atlas(columns=4, rows=3, cell=768, blank_frame=None, duplicate=False):
    atlas = Image.new("RGBA", (columns * cell, rows * cell), (0, 0, 0, 0))
    for index in range(columns * rows):
        r, c = divmod(index, columns)
        body = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
        draw = np.zeros((cell, cell, 4), dtype=np.uint8)
        # a distinct block per frame, well inside the cell
        x0 = 200 + (index % 3) * 40
        y0 = 150 + (index // 3) * 40
        draw[y0:y0 + 300, x0:x0 + 200] = (200, 180, 255, 255)
        if duplicate and index == 1:
            draw = np.array(atlas.crop((0, 0, cell, cell)))[:, :, :]
            draw[:, :, :] = np.array(atlas.crop((0, 0, cell, cell)))
        body = Image.fromarray(draw, "RGBA")
        if blank_frame == index:
            body = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
        atlas.alpha_composite(body, (c * cell, r * cell))
    return atlas


class ImportGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        (self.repo / "static/public/assets/sprites").mkdir(parents=True)
        shutil.copy(ROOT / "static/public/assets/sprites/manifest.json",
                    self.repo / "static/public/assets/sprites/manifest.json")
        (self.repo / "art/hyangdani/export").mkdir(parents=True)
        self.bundle = self.tmp / "bundle"
        self.bundle.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_bundle(self, image: Image.Image, name="hyangdani-run-v9-4x3.png"):
        path = self.bundle / name
        image.save(path, "PNG")
        return path

    def manifest(self):
        return json.loads((self.repo / "static/public/assets/sprites/manifest.json").read_text())

    def test_check_mode_writes_nothing(self):
        self.write_bundle(synthetic_atlas())
        manifest_path = self.repo / "static/public/assets/sprites/manifest.json"
        before = manifest_path.read_bytes()
        summary = importer.install("run", self.bundle, self.repo, check=True)
        self.assertFalse(summary["written"])
        self.assertFalse((self.repo / summary["master"]).exists())
        self.assertFalse((self.repo / "static/public/assets/sprites" / summary["service"]).exists())
        self.assertEqual(manifest_path.read_bytes(), before)

    def test_import_writes_master_and_service_and_only_touches_run(self):
        self.write_bundle(synthetic_atlas())
        before = self.manifest()
        summary = importer.install("run", self.bundle, self.repo, check=False)
        after = self.manifest()
        master = self.repo / summary["master"]
        service = self.repo / "static/public/assets/sprites" / summary["service"]
        self.assertTrue(master.is_file())
        self.assertTrue(service.is_file())
        with Image.open(master) as opened:
            self.assertEqual(opened.size, (4 * 768, 3 * 768))
        with Image.open(service) as opened:
            self.assertEqual(opened.size, (4 * 384, 3 * 384))
        self.assertLessEqual(service.stat().st_size, 1024 * 1024)
        self.assertEqual(after["states"]["run"]["source"]["file"], summary["service"])
        self.assertEqual(after["states"]["run"]["authoring"]["width"], 4 * 768)
        self.assertEqual(after["states"]["run"]["fallback"], before["states"]["run"]["fallback"])
        for state in ("idle", "attack", "repair", "levelup", "install"):
            self.assertEqual(after["states"][state], before["states"][state], state)

    def test_reimport_is_idempotent_and_conflicts_are_refused(self):
        self.write_bundle(synthetic_atlas())
        importer.install("run", self.bundle, self.repo, check=False)
        manifest_bytes = (self.repo / "static/public/assets/sprites/manifest.json").read_bytes()
        importer.install("run", self.bundle, self.repo, check=False)
        self.assertEqual((self.repo / "static/public/assets/sprites/manifest.json").read_bytes(),
                         manifest_bytes)
        other = synthetic_atlas()
        other = other.resize((4 * 768, 3 * 768))
        arr = np.array(other)
        arr[..., 0] = np.clip(arr[..., 0].astype(np.int16) + 20, 0, 255)
        self.write_bundle(Image.fromarray(arr, "RGBA"), "hyangdani-run-v9-4x3.png")
        with self.assertRaises(importer.Refused):
            importer.install("run", self.bundle, self.repo, check=False)

    def test_wrong_grid_is_refused(self):
        self.write_bundle(synthetic_atlas(columns=4, rows=2))
        with self.assertRaises(importer.Refused):
            importer.install("run", self.bundle, self.repo, check=True)

    def test_empty_and_clipped_frames_are_refused(self):
        self.write_bundle(synthetic_atlas(blank_frame=4))
        with self.assertRaises(importer.Refused):
            importer.install("run", self.bundle, self.repo, check=True)

    def test_ambiguous_bundle_is_refused(self):
        self.write_bundle(synthetic_atlas(), "hyangdani-run-v9-4x3.png")
        self.write_bundle(synthetic_atlas(), "hyangdani-run-v7-4x3.png")
        with self.assertRaises(importer.Refused):
            importer.install("run", self.bundle, self.repo, check=True)

    def test_non_png_is_refused(self):
        path = self.bundle / "hyangdani-run-v9-4x3.png"
        path.write_bytes(b"not a png at all")
        with self.assertRaises(importer.Refused):
            importer.install("run", self.bundle, self.repo, check=True)


if __name__ == "__main__":
    unittest.main()
