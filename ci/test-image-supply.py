import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

PATH = Path(__file__).with_name("image-supply.py")
SPEC = importlib.util.spec_from_file_location("image_supply", PATH)
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)


class InventoryTest(unittest.TestCase):
    def test_inventory_is_fixed_digest_and_mapped(self):
        images = MODULE.load()
        self.assertEqual(len(images), 14)
        self.assertTrue(all(i["source"].endswith("@" + i["sourceDigest"]) for i in images))
        self.assertTrue(all(i["destination"].startswith("ghcr.io/syuhei0519/platform-lab-images:") for i in images))

    def test_unknown_destination_digest_is_not_present(self):
        raw = json.loads((PATH.parents[1] / "supply/images.json").read_text())
        for item in raw["images"]:
            self.assertNotIn("destinationDigest", item)
            self.assertNotIn("@sha256:", item["destinationTag"])

    def test_duplicate_and_mutable_source_are_refused(self):
        original = json.loads((PATH.parents[1] / "supply/images.json").read_text())
        for mutate in (lambda d: d["images"][1].update(name=d["images"][0]["name"]),
                       lambda d: d["images"][0].update(source="docker.io/kindest/node:latest")):
            data = json.loads(json.dumps(original)); mutate(data)
            with tempfile.TemporaryDirectory() as directory:
                inventory = Path(directory) / "images.json"; inventory.write_text(json.dumps(data))
                with mock.patch.object(MODULE, "INVENTORY", inventory), self.assertRaises(ValueError):
                    MODULE.load()


if __name__ == "__main__":
    unittest.main()
