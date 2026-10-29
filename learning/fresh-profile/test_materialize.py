import importlib.util
from pathlib import Path
import tempfile
import unittest
import yaml


MODULE = Path(__file__).with_name("materialize.py")
SPEC = importlib.util.spec_from_file_location("materialize", MODULE)
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


class MaterializeTests(unittest.TestCase):
    def runtime_inputs(self, root):
        paths = []
        for source_name, target_name in (
                ("profile.template.yaml", "profile.runtime.local.yaml"),
                ("image-supply.template.yaml", "image-supply.runtime.local.yaml"),
                ("acceptance.template.json", "acceptance.runtime.local.json")):
            path = root / target_name
            path.write_bytes((contract.HERE / source_name).read_bytes())
            paths.append(path)
        return paths

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
            runtime = self.runtime_inputs(root)
            platform_sha = "1" * 40
            application_sha = "3707f73581da5d04db0e4771dda7c1e82bd0f47c"
            path, manifest = contract.materialize(
                platform_sha, application_sha, release, *runtime, output)
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
            runtime = self.runtime_inputs(Path(directory))
            with self.assertRaises(contract.MaterializationError):
                contract.materialize(
                    "main", "2" * 40, release, *runtime, Path(directory) / "out")
            with self.assertRaises(contract.MaterializationError):
                contract.materialize(
                    "1" * 40, "3707f73581da5d04db0e4771dda7c1e82bd0f47c",
                    release, *runtime, contract.HERE / "generated")

    def test_tracked_runtime_template_is_rejected_as_runtime_evidence(self):
        with tempfile.TemporaryDirectory(dir=contract.REPOSITORY.parent) as directory:
            root = Path(directory)
            release = self.release_chart(root)
            runtime = self.runtime_inputs(root)
            runtime[0] = contract.HERE / "profile.template.yaml"
            with self.assertRaisesRegex(contract.MaterializationError,
                                        "runtime-input-must-be-ignored"):
                contract.materialize(
                    "1" * 40, "3707f73581da5d04db0e4771dda7c1e82bd0f47c",
                    release, *runtime, root / "sealed")

    def test_application_git_source_token_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=contract.REPOSITORY.parent) as directory:
            root = Path(directory)
            release = self.release_chart(root, token=True)
            runtime = self.runtime_inputs(root)
            with self.assertRaisesRegex(contract.MaterializationError, "application-source-revision-token"):
                contract.materialize(
                    "1" * 40, "3707f73581da5d04db0e4771dda7c1e82bd0f47c",
                    release, *runtime, root / "sealed")

    def test_plugin_materialization_seals_plugin_source_set(self):
        with tempfile.TemporaryDirectory(dir=contract.REPOSITORY.parent) as directory:
            root=Path(directory)
            app_root=root/"app"
            release=self.release_chart(app_root/"learning/fresh-profile")
            runtime=self.runtime_inputs(root)
            supply=yaml.safe_load(runtime[1].read_text(encoding="utf-8"))
            supply["spec"]["rolloutsPlugin"]={"profile":"fresh-account"}
            runtime[1].write_text(yaml.safe_dump(supply),encoding="utf-8")
            paths=["learning/account-release/metric-plugin/main.go", "learning/account-release/metric-plugin/runtime.go", "learning/account-release/metric-plugin/fresh_query.go", "learning/account-release/metric-plugin/queries.json", "learning/account-release/metric-plugin/go.mod", "learning/account-release/metric-plugin/go.sum", "learning/account-release/metric-plugin/Dockerfile", "learning/fresh-profile/workload-auto/kustomization.yaml"]
            for relative in paths:
                target=app_root/relative; target.parent.mkdir(parents=True,exist_ok=True); target.write_text("fixture",encoding="utf-8")
            _, manifest=contract.materialize("1"*40,"3707f73581da5d04db0e4771dda7c1e82bd0f47c",release,*runtime,root/"sealed")
            for relative in paths:
                self.assertIn("application-manifest@3707f73581da5d04db0e4771dda7c1e82bd0f47c:"+relative,manifest["sourceFiles"])
            self.assertIn("platform-gitops@"+"1"*40+":learning/fresh-profile/plugin_overlay.py",manifest["sourceFiles"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
