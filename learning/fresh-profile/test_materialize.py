import importlib.util
from pathlib import Path
import tempfile
import unittest


MODULE = Path(__file__).with_name("materialize.py")
SPEC = importlib.util.spec_from_file_location("materialize", MODULE)
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


class MaterializeTests(unittest.TestCase):
    def test_fixed_revisions_are_sealed_without_approval_claim(self):
        with tempfile.TemporaryDirectory(dir=contract.REPOSITORY.parent) as directory:
            root = Path(directory)
            release = root / "release.yaml"
            release.write_text("targetRevision: __APPLICATION_MANIFEST_SHA__\n", encoding="utf-8")
            output = root / "sealed"
            platform_sha = "1" * 40
            application_sha = "d9150a4de69d2447c1b767823fc23784edea0abc"
            path, manifest = contract.materialize(platform_sha, application_sha, release, output)
            self.assertTrue(path.is_file())
            self.assertFalse(manifest["liveApproved"])
            self.assertEqual(manifest["platformGitops"], platform_sha)
            for name in manifest["files"]:
                text = (output / name).read_text(encoding="utf-8")
                self.assertNotIn(contract.PLATFORM_TOKEN, text)
                self.assertNotIn(contract.APPLICATION_TOKEN, text)

    def test_invalid_sha_and_repository_output_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=contract.REPOSITORY.parent) as directory:
            release = Path(directory) / "release.yaml"
            release.write_text("targetRevision: __APPLICATION_MANIFEST_SHA__\n", encoding="utf-8")
            with self.assertRaises(contract.MaterializationError):
                contract.materialize("main", "2" * 40, release, Path(directory) / "out")
            with self.assertRaises(contract.MaterializationError):
                contract.materialize("1" * 40, "d9150a4de69d2447c1b767823fc23784edea0abc",
                                     release, contract.HERE / "generated")


if __name__ == "__main__":
    unittest.main(verbosity=2)
