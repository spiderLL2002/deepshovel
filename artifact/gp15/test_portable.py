#!/usr/bin/env python3
"""Boundary tests for the new comparison wrapper, using explicit mutations.

These synthetic mutations test the verifier, not scientific/system performance.
"""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from run_portable import ANALYSIS, FIGURES, PRIMARY, PACKAGE, PortableComparison, encoded, sha


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

    def png_fixture(self, final=False, changed_pixels=0, channel_difference=1, metadata_change=False):
        """Synthetic 200x200 fixture: four changed pixels are exactly 0.0001."""
        from PIL import Image, PngImagePlugin
        name = FIGURES + "fig01_sampling_support.png" if final else PRIMARY + "ree_ph_transfer.png"
        original = Image.new("RGBA", (200, 200), (100, 100, 100, 255))
        changed = original.copy()
        for index in range(changed_pixels):
            changed.putpixel((index, 0), (100 + channel_difference, 100, 100, 255))
        for root, picture, level in ((self.reference, original, 0), (self.working, changed, 9)):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("Fixture", "altered" if root == self.working and metadata_change else "synthetic test")
            picture.save(path, compress_level=level, pnginfo=metadata)
            if final:
                path.with_suffix(".svg").write_text("<svg><!-- synthetic byte-identity fixture --></svg>\n")
                path.with_suffix(".pdf").write_bytes(b"synthetic vector-byte fixture\n")
        return name

    def test_primary_png_compression_only_change_passes_exact_pixels(self):
        name = self.png_fixture()
        result = self.compare(name)
        self.assertFalse(result["byte_identical"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["comparison"], "PNG_DECODED_RGBA_IDENTICAL")
        self.assertEqual(result["png_diagnostic"]["different_pixels"], 0)

    def test_primary_png_single_pixel_change_is_rejected(self):
        name = self.png_fixture(changed_pixels=1)
        self.assertFalse(self.compare(name)["passed"])

    def test_final_png_exact_threshold_passes_with_identical_vectors(self):
        name = self.png_fixture(final=True, changed_pixels=4, channel_difference=2)
        result = self.compare(name)
        self.assertTrue(result["passed"])
        self.assertEqual(result["png_diagnostic"]["different_pixel_ratio"], 0.0001)
        self.assertEqual(result["png_diagnostic"]["maximum_channel_difference"], 2)

    def test_final_png_above_pixel_ratio_or_channel_limit_is_rejected(self):
        for count, level in ((5, 1), (1, 3)):
            with self.subTest(changed_pixels=count, channel_difference=level):
                name = self.png_fixture(final=True, changed_pixels=count, channel_difference=level)
                self.assertFalse(self.compare(name)["passed"])

    def test_final_png_requires_both_unchanged_vectors(self):
        for extension in (".svg", ".pdf"):
            with self.subTest(extension=extension):
                name = self.png_fixture(final=True, changed_pixels=1)
                (self.working / name).with_suffix(extension).write_bytes(b"changed vector\n")
                self.assertFalse(self.compare(name)["passed"])

    def test_png_metadata_change_is_rejected(self):
        name = self.png_fixture(final=True, metadata_change=True)
        self.assertFalse(self.compare(name)["passed"])

    def test_png_manifest_only_accepts_verified_binding_and_unchanged_science(self):
        name = self.png_fixture()
        manifest_name = PRIMARY + "run_manifest.json"
        reference = json.loads((PACKAGE / "payload" / manifest_name).read_text())
        reference["output_files"][5]["sha256"] = sha(self.reference / name)
        actual = json.loads(encoded(reference))
        actual["output_files"][5]["sha256"] = sha(self.working / name)
        # Match the original exporter's format, independently of the wrapper.
        (self.reference / manifest_name).write_text(json.dumps(reference, indent=2))
        (self.working / manifest_name).write_text(json.dumps(actual, indent=2))
        self.assertTrue(self.compare(manifest_name)["passed"])
        actual["output_files"][5]["sha256"] = "0" * 64
        (self.working / manifest_name).write_text(json.dumps(actual, indent=2))
        self.assertFalse(self.compare(manifest_name)["passed"])
        actual["output_files"][5]["sha256"] = sha(self.working / name)
        actual["n_eligible_pairs_in_depth_union"] += 1
        (self.working / manifest_name).write_text(json.dumps(actual, indent=2))
        self.assertFalse(self.compare(manifest_name)["passed"])

    def real_primary_manifest_fixture(self):
        """Unchanged real reference bytes plus lossless PNG re-encoding."""
        from PIL import Image, PngImagePlugin
        png_name = PRIMARY + "ree_ph_transfer.png"
        manifest_name = PRIMARY + "run_manifest.json"
        for root in (self.reference, self.working):
            for name in (png_name, manifest_name):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PACKAGE / "payload" / name, target)
        with Image.open(self.reference / png_name) as image:
            metadata = PngImagePlugin.PngInfo()
            for key, value in image.info.items():
                if isinstance(value, str):
                    metadata.add_text(key, value)
            image.save(self.working / png_name, compress_level=0, pnginfo=metadata, dpi=image.info["dpi"])
        original = (self.reference / manifest_name).read_bytes()
        old_hash, new_hash = sha(self.reference / png_name), sha(self.working / png_name)
        self.assertNotEqual(old_hash, new_hash)
        self.assertEqual(original.count(old_hash.encode()), 1)
        changed = original.replace(old_hash.encode(), new_hash.encode(), 1)
        (self.working / manifest_name).write_bytes(changed)
        return manifest_name, original, changed

    def test_real_primary_manifest_no_newline_format_accepts_verified_png_hash(self):
        name, original, changed = self.real_primary_manifest_fixture()
        self.assertEqual(len(original), 1861)
        self.assertEqual(len(changed), len(original))
        self.assertFalse(original.endswith(b"\n"))
        self.assertFalse(changed.endswith(b"\n"))
        result = self.compare(name)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["allowed_metadata_differences"][0]["pointer"], ["output_files", 5, "sha256"])

    def test_real_primary_manifest_rejects_changed_scientific_field(self):
        name, _, changed = self.real_primary_manifest_fixture()
        data = json.loads(changed)
        data["n_eligible_pairs_in_depth_union"] += 1
        (self.working / name).write_text(json.dumps(data, indent=2))
        self.assertFalse(self.compare(name)["passed"])

    def test_real_primary_manifest_rejects_added_trailing_newline(self):
        name, _, changed = self.real_primary_manifest_fixture()
        (self.working / name).write_bytes(changed + b"\n")
        self.assertFalse(self.compare(name)["passed"])


if __name__ == "__main__":
    unittest.main()
