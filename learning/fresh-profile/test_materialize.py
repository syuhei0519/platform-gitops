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
        workload = root / "workload"
        workload.mkdir()
        for relative in contract.APPLICATION_WORKLOAD_FILES:
            (workload / relative).write_text("{}\n", encoding="utf-8")
        return release

    def test_fixed_revisions_are_sealed_without_approval_claim(self):
        with tempfile.TemporaryDirectory(dir=contract.REPOSITORY.parent) as directory:
            root = Path(directory)
            release = self.release_chart(root)
            output = root / "sealed"
            platform_sha = "1" * 40
            application_sha = "f019cc9ad76abdd49a6be303bd4e5c88050abc85"
            path, manifest = contract.materialize(platform_sha, application_sha, release, output)
            self.assertTrue(path.is_file())
            self.assertFalse(manifest["liveApproved"])
            self.assertEqual(manifest["platformGitops"], platform_sha)
            self.assertEqual(len(manifest["sourceFiles"]), 9)
            self.assertEqual(
                set(name.rsplit(":", 1)[1] for name in manifest["sourceFiles"]
                    if name.startswith(f"platform-gitops@{platform_sha}:")),
                {f"learning/fresh-profile/{name}"
                 for name in contract.GIT_FETCHED_PLATFORM_FILES},
            )
            self.assertEqual(
                set(name.rsplit(":", 1)[1] for name in manifest["sourceFiles"]
                    if name.startswith(f"application-manifest@{application_sha}:")),
                {
                    *(f"learning/fresh-profile/release-definition/{name}"
                      for name in contract.APPLICATION_RELEASE_FILES),
                    *(f"learning/fresh-profile/workload/{name}"
                      for name in contract.APPLICATION_WORKLOAD_FILES),
                },
            )
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
                contract.materialize("1" * 40, "f019cc9ad76abdd49a6be303bd4e5c88050abc85",
                                     release, contract.HERE / "generated")

    def test_application_git_source_token_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=contract.REPOSITORY.parent) as directory:
            root = Path(directory)
            release = self.release_chart(root, token=True)
            with self.assertRaisesRegex(contract.MaterializationError, "application-source-revision-token"):
                contract.materialize("1" * 40, "f019cc9ad76abdd49a6be303bd4e5c88050abc85",
                                     release, root / "sealed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
