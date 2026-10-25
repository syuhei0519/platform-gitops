import importlib.util
from pathlib import Path
import tempfile
import unittest


MODULE = Path(__file__).with_name("materialize.py")
SPEC = importlib.util.spec_from_file_location("materialize", MODULE)
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


class MaterializeTests(unittest.TestCase):
    def release_chart(self, root, token=False):
        release = root / "release-definition"
        (release / "templates").mkdir(parents=True)
        (release / "Chart.yaml").write_text("apiVersion: v2\nname: fresh\nversion: 0.1.0\n", encoding="utf-8")
        (release / "values.yaml").write_text("workloadRevision: ''\n", encoding="utf-8")
        value = "# __APPLICATION_MANIFEST_SHA__\n" if token else "kind: Application\n"
        (release / "templates" / "application.yaml").write_text(value, encoding="utf-8")
        return release

    def test_fixed_revisions_are_sealed_without_approval_claim(self):
        with tempfile.TemporaryDirectory(dir=contract.REPOSITORY.parent) as directory:
            root = Path(directory)
            release = self.release_chart(root)
            output = root / "sealed"
            platform_sha = "1" * 40
            application_sha = "0fc2ae19300af32f86643152e65597b022dcdc0f"
            path, manifest = contract.materialize(platform_sha, application_sha, release, output)
            self.assertTrue(path.is_file())
            self.assertFalse(manifest["liveApproved"])
            self.assertEqual(manifest["platformGitops"], platform_sha)
            self.assertEqual(len(manifest["sourceFiles"]), 5)
            self.assertEqual((output / "root/children/applications.yaml").read_bytes(),
                             (contract.HERE / "root/children/applications.yaml").read_bytes())
            for name in manifest["files"]:
                text = (output / name).read_text(encoding="utf-8")
                self.assertNotIn(contract.PLATFORM_TOKEN, text)
                self.assertNotIn(contract.APPLICATION_TOKEN, text)

    def test_invalid_sha_and_repository_output_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=contract.REPOSITORY.parent) as directory:
            release = self.release_chart(Path(directory))
            with self.assertRaises(contract.MaterializationError):
                contract.materialize("main", "2" * 40, release, Path(directory) / "out")
            with self.assertRaises(contract.MaterializationError):
                contract.materialize("1" * 40, "0fc2ae19300af32f86643152e65597b022dcdc0f",
                                     release, contract.HERE / "generated")

    def test_application_git_source_token_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=contract.REPOSITORY.parent) as directory:
            root = Path(directory)
            release = self.release_chart(root, token=True)
            with self.assertRaisesRegex(contract.MaterializationError, "application-source-revision-token"):
                contract.materialize("1" * 40, "0fc2ae19300af32f86643152e65597b022dcdc0f",
                                     release, root / "sealed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
