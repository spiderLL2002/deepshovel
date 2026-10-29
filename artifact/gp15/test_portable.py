#!/usr/bin/env python3
"""Boundary tests for the new comparison wrapper, using explicit mutations.

These synthetic mutations test the verifier, not scientific/system performance.
"""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from run_portable import ANALYSIS, PACKAGE, PortableComparison, encoded, sha


class ComparisonBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gp15-comparison-test-")
        self.addCleanup(self.temporary.cleanup)
        self.reference = Path(self.temporary.name) / "reference"
        self.working = Path(self.temporary.name) / "working"
        self.runtime = "3.12.12 (synthetic verifier test) [test compiler]"
        self.input_name = ANALYSIS + "selection-weight-domain-v1/input_manifest.json"
        self.output_name = ANALYSIS + "selection-weight-domain-v1/output_manifest.json"
        for name in (self.input_name, self.output_name):
            for root in (self.reference, self.working):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PACKAGE / "payload" / name, target)

    def compare(self, name):
        return PortableComparison(self.reference, self.working, self.runtime).compare_one(name)

    def runtime_change(self):
        path = self.working / self.input_name
        data = json.loads(path.read_text())
        data["interpreter"] = self.runtime
        path.write_bytes(encoded(data))

    def bind_changed_runtime(self):
        self.runtime_change()
        path = self.working / self.output_name
        data = json.loads(path.read_text())
        source = self.working / self.input_name
        data["outputs"][3].update(sha256=sha(source), bytes=source.stat().st_size)
        path.write_bytes(encoded(data))

    def test_unchanged_bytes_pass(self):
        result = self.compare(self.input_name)
        self.assertTrue(result["passed"])
        self.assertTrue(result["byte_identical"])

    def test_only_matching_runtime_is_allowed_and_reported(self):
        self.runtime_change()
        result = self.compare(self.input_name)
        self.assertTrue(result["passed"])
        self.assertFalse(result["byte_identical"])
        self.assertEqual(result["allowed_metadata_differences"][0]["actual"], self.runtime)

    def test_runtime_claim_must_match_executing_interpreter(self):
        self.runtime_change()
        result = PortableComparison(self.reference, self.working, "3.12.13 another build").compare_one(self.input_name)
        self.assertFalse(result["passed"])

    def test_runtime_cannot_hide_changed_scientific_definition(self):
        self.runtime_change()
        path = self.working / self.input_name
        data = json.loads(path.read_text())
        data["definitions"]["original_training_ph_values"][0] = "8.0"
        path.write_bytes(encoded(data))
        self.assertFalse(self.compare(self.input_name)["passed"])

    def test_runtime_cannot_hide_extra_field_or_formatting(self):
        self.runtime_change()
        path = self.working / self.input_name
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        self.assertFalse(self.compare(self.input_name)["passed"])
        data = json.loads(original)
        data["unexpected"] = "not whitelisted"
        path.write_bytes(encoded(data))
        self.assertFalse(self.compare(self.input_name)["passed"])

    def test_derived_runtime_hash_and_length_are_verified(self):
        self.bind_changed_runtime()
        result = self.compare(self.output_name)
        self.assertTrue(result["passed"])
        self.assertEqual({x["pointer"][-1] for x in result["allowed_metadata_differences"]}, {"sha256", "bytes"})

    def test_derived_manifest_cannot_hide_forged_hash(self):
        self.bind_changed_runtime()
        path = self.working / self.output_name
        data = json.loads(path.read_text())
        data["outputs"][3]["sha256"] = "0" * 64
        path.write_bytes(encoded(data))
        self.assertFalse(self.compare(self.output_name)["passed"])

    def test_csv_numeric_change_is_rejected(self):
        name = "synthetic-scientific-table.csv"
        (self.reference / name).write_text("x,y\n1,2\n")
        (self.working / name).write_text("x,y\n1,3\n")
        self.assertFalse(self.compare(name)["passed"])

    def test_png_change_is_rejected_and_diagnosed(self):
        from PIL import Image
        name = "synthetic-figure.png"
        Image.new("RGB", (2, 2), (255, 255, 255)).save(self.reference / name)
        changed = Image.new("RGB", (2, 2), (255, 255, 255))
        changed.putpixel((0, 0), (0, 0, 0))
        changed.save(self.working / name)
        result = self.compare(name)
        self.assertFalse(result["passed"])
        self.assertEqual(result["png_diagnostic"]["different_pixels"], 1)


if __name__ == "__main__":
    unittest.main()
