import copy
import contextlib
import io
import sys
from unittest import mock
from datetime import datetime, timezone
import hashlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
APPLICATION_SOURCE=Path(os.environ.get("ACCOUNT_CANARY_APPLICATION_SOURCE",HERE.parent.parent.parent/"account-canary-work")).resolve()
spec=importlib.util.spec_from_file_location("fresh_runner_upgrade",HERE/"runner.py")
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
NOW=datetime(2026,11,1,tzinfo=timezone.utc)
NAMES={"Job/account-db-role-bootstrap", "Job/account-migration", "ConfigMap/account-role-bootstrap", "PVC/account-data", "Secret/account-db-bootstrap", "Secret/account-db-migration", "Secret/account-db-runtime"}

class Stateful:
    supports_existing_upgrade_mutations=True
    def __init__(self, plan_path, plan):
        self.upgrade_plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest()
        self.state={"target":copy.deepcopy(plan["target"]),"applicationRevision":plan["source"]["oldApplicationRevision"],"controllerImageDigest":"sha256:"+"1"*64,"protected":copy.deepcopy(plan["protected"]),"completedJobs":{"Job/account-db-role-bootstrap":True,"Job/account-migration":True}}
        self.calls=[]; self.reconciles=[];self.fail=None;self.read_failure=False;self.reconcile_result="unknown";self.runtime_converged=True
    def verify_controller_runtime(self, plan): return self.runtime_converged
    def observe(self):
        if self.read_failure: raise RuntimeError("unavailable")
        return copy.deepcopy(self.state)
    def run_upgrade_phase(self, phase, plan):
        self.calls.append(phase)
        if phase=="controller":self.state["controllerImageDigest"]=plan["artifacts"]["controllerImageDigest"]
        if phase=="account":self.state["applicationRevision"]=plan["source"]["newApplicationRevision"]
        if self.fail==phase:raise RuntimeError("unknown-outcome")
    def reconcile(self,phase,plan):self.reconciles.append(phase);return self.reconcile_result

class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        bundle=self.root/"bundle.json";bundle.write_text("{}")
        tool=self.root/"kubectl"; tool.write_bytes(b"fixture-kubectl")
        kubeconfig=self.root/"kubeconfig"; kubeconfig.write_text("fixture")
        self.plan={"tool":{"kubectlPath":str(tool),"kubectlSha256":hashlib.sha256(tool.read_bytes()).hexdigest(),"kubeconfigPath":str(kubeconfig)},"schemaVersion":"platform.lab/existing-account-upgrade/v1","target":{"clusterName":"learning-fresh-fixture","context":"kind-learning-fresh-fixture","clusterUID":"cluster-uid","applicationUID":"app-uid","parentApplicationUID":"parent-uid","rootApplicationUID":"root-uid","controllerUID":"controller-uid","rolloutUID":"rollout-uid"},"source":{"platformRevision":"a"*40,"oldApplicationRevision":"b"*40,"newApplicationRevision":"c"*40},"artifacts":{"oldControllerImageDigest":"sha256:"+"1"*64,"controllerImageDigest":"sha256:"+"d"*64,"binarySha256":"e"*64,"bundleSha256":hashlib.sha256(bundle.read_bytes()).hexdigest(),"bundlePath":str(bundle)},"protected":{n:{"uid":"uid-"+str(i),"resourceVersion":"12"} for i,n in enumerate(sorted(NAMES))}}
        self.plan["securityPolicy"]={"uid":"policy-uid","resourceVersion":"10","oldSpec":{},"desiredHashes":{name:str(i)*64 for i,name in enumerate(("initial","newPaused","finalAutomated","rollbackOld"),start=1)}}
        self.plan["controllerResources"]={"Deployment/fresh-control/argo-rollouts":{"state":"present","uid":"controller-uid","resourceVersion":"30","rollback":{"spec":{}}},"ConfigMap/fresh-control/argo-rollouts-config":{"state":"present","uid":"controller-config-uid","resourceVersion":"31","rollback":{"data":{}}},"Role/fresh-account/account-policy-reader":{"state":"absent"},"RoleBinding/fresh-account/account-policy-reader":{"state":"absent"}}
        self.plan["gitDesired"]={"root":{"uid":"root-uid","resourceVersion":"19","revision":"a"*40,"automated":None},"parent":{"uid":"parent-uid","resourceVersion":"20","oldRevision":"a"*40,"newRevision":"f"*40,"automated":None},"child":{"uid":"app-uid","resourceVersion":"21","oldRevision":"b"*40,"newRevision":"c"*40,"automated":{"prune":False,"selfHeal":True}},"protectedDigests":{name:{"old":"8"*64,"new":"8"*64} for name in {name for name in NAMES if not name.startswith("Secret/")} | {"StatefulSet/account-postgresql","Service/account-postgresql"}}}
        self.plan["gitDesired"]["analysisTemplate"]={"state":"absent"}
        self.plan["gitDesired"]["limitedSync"]={"prune":False,"resources":["Rollout/account-api","Service/account-api","DestinationRule/account-api","VirtualService/account-api","AnalysisTemplate/account-api-policy"]}
        self.plan["gitDesired"]["sourceRender"]={"platformRevision":"a"*40,"chartRevision":"a"*40,"workloadRevision":"b"*40,"newWorkloadRevision":"c"*40,"prePause":{"platformRevision":"a"*40,"chartRevision":"a"*40,"workloadRevision":"b"*40},"runtimeImage":"ghcr.io/syuhei0519/backend-app@sha256:"+"b"*64,"transitions":{"newPaused":{"platformRevision":"d"*40,"chartRevision":"f"*40},"finalAutomated":{"platformRevision":"e"*40,"chartRevision":"f"*40},"rollbackOld":{"platformRevision":"a"*40,"chartRevision":"a"*40}},"kustomizePath":str(tool),"kustomizeSha256":hashlib.sha256(tool.read_bytes()).hexdigest(),"gitPath":str(tool),"gitSha256":hashlib.sha256(tool.read_bytes()).hexdigest(),"helmPath":str(tool),"helmSha256":hashlib.sha256(tool.read_bytes()).hexdigest()}
        self.plan_path=self.root/"plan.json";self.plan_path.write_text(json.dumps(self.plan));self.approval_path=self.root/"approval.json"
        self.approval={"schemaVersion":"platform.lab/existing-account-upgrade-approval/v1","approved":True,"approvedBy":"fixture-reviewer","approvedAt":"2026-10-31T00:00:00Z","expiresAt":"2026-11-02T00:00:00Z","planSha256":hashlib.sha256(self.plan_path.read_bytes()).hexdigest()}
        self.approval_path.write_text(json.dumps(self.approval));self.journal=self.root/"journal.json";self.fake=Stateful(self.plan_path,self.plan)
    def execute(self,action):return runner.execute_existing_upgrade(action,self.plan_path,self.approval_path,self.journal,self.fake,now=NOW)
    def test_explicit_observe_is_read_only_without_journal(self):
        result=self.execute("observe")
        self.assertEqual(result["state"],"observed")
        self.assertFalse(self.journal.exists());self.assertFalse(self.journal.with_name(self.journal.name+".lock").exists())
        self.assertEqual(self.fake.calls,[])

    def test_success_rerun_is_read_only_and_preserves_all(self):
        baseline=copy.deepcopy(self.fake.state["protected"]);result=self.execute("first")
        self.assertEqual(self.fake.calls,["controller","account"]);self.assertEqual(self.fake.state["protected"],baseline)
        self.assertTrue(all(result["phases"][x]["state"]=="succeeded" for x in ("controller","account")))
        self.execute("rerun");self.assertEqual(self.fake.calls,["controller","account"])
        self.assertEqual(json.loads(self.journal.read_text())["protected"],baseline)
    def test_uncertain_resume_only_observes_and_continuation_never_replays(self):
        self.fake.fail="controller"
        with self.assertRaises(RuntimeError):self.execute("first")
        self.assertEqual(json.loads(self.journal.read_text())["phases"]["controller"]["state"],"uncertain")
        with self.assertRaisesRegex(ValueError,"uncertain-requires-observation"):self.execute("continue")
        result=self.execute("resume");self.assertEqual(result["phases"]["controller"]["state"],"uncertain");self.assertEqual(self.fake.calls,["controller"])
        self.fake.reconcile_result="succeeded";result=self.execute("resume");self.assertEqual(result["phases"]["controller"]["state"],"succeeded");self.assertEqual(self.fake.calls,["controller"])
        self.fake.fail=None;self.execute("continue");self.assertEqual(self.fake.calls,["controller","account"])
    def test_completed_controller_runtime_drift_rejects_read_only_rerun(self):
        self.execute("first")
        self.fake.runtime_converged=False
        for action in ("rerun", "resume"):
            with self.assertRaisesRegex(ValueError,"completed-controller-runtime-drift"):
                self.execute(action)
        self.assertEqual(self.fake.calls,["controller","account"])

    def test_new_approval_is_recorded_only_on_read_only_resume(self):
        self.fake.fail="controller"
        with self.assertRaises(RuntimeError):self.execute("first")
        original=json.loads(self.journal.read_text())["approvalSha256"]
        self.approval["approvedBy"]="second-reviewer";self.approval_path.write_text(json.dumps(self.approval))
        with self.assertRaisesRegex(ValueError,"approval-renewal-requires-resume"):self.execute("continue")
        self.fake.reconcile_result="succeeded";result=self.execute("resume")
        self.assertEqual(result["approvalSha256"],original)
        self.assertEqual(result["approvalRenewals"],[hashlib.sha256(self.approval_path.read_bytes()).hexdigest()])
        self.assertEqual(self.fake.calls,["controller"])
        self.fake.fail=None;self.execute("continue");self.assertEqual(self.fake.calls,["controller","account"])

    def test_account_write_uncertain_reconciles_without_replay(self):
        self.fake.fail="account"
        with self.assertRaises(RuntimeError):self.execute("first")
        self.assertEqual(self.fake.calls,["controller","account"])
        journal=json.loads(self.journal.read_text())
        self.assertEqual(journal["phases"]["controller"]["state"],"succeeded")
        self.assertEqual(journal["phases"]["account"]["state"],"uncertain")
        self.fake.reconcile_result="succeeded"
        result=self.execute("resume")
        self.assertEqual(result["phases"]["account"]["state"],"succeeded")
        self.assertEqual(self.fake.calls,["controller","account"])
        self.execute("rerun");self.assertEqual(self.fake.calls,["controller","account"])

    def test_unexpected_phase_transition_is_uncertain_and_never_replayed(self):
        original=self.fake.run_upgrade_phase
        def racing(phase, plan):
            original(phase,plan)
            if phase=="controller": self.fake.state["applicationRevision"]=plan["source"]["newApplicationRevision"]
        self.fake.run_upgrade_phase=racing
        with self.assertRaisesRegex(ValueError,"unexpected-application-transition"):self.execute("first")
        self.assertEqual(json.loads(self.journal.read_text())["phases"]["controller"]["state"],"uncertain")
        self.assertEqual(self.fake.calls,["controller"])
        self.fake.reconcile_result="succeeded"
        with self.assertRaisesRegex(ValueError,"unexpected-application-transition"):self.execute("resume")
        self.assertEqual(self.fake.calls,["controller"])

    def test_mismatch_and_preservation_fail_closed(self):
        for mutation in (lambda x:x.state["target"].update(clusterUID="other"),lambda x:x.state["target"].update(applicationUID="other"),lambda x:x.state["protected"]["Job/account-migration"].update(uid="other"),lambda x:x.state["protected"]["ConfigMap/account-role-bootstrap"].update(resourceVersion="changed"),lambda x:x.state["protected"]["PVC/account-data"].update(uid="other"),lambda x:x.state["protected"]["Secret/account-db-runtime"].update(resourceVersion="changed"),lambda x:x.state["completedJobs"].update({"Job/account-migration":False})):
            fake=Stateful(self.plan_path,self.plan);mutation(fake)
            with self.assertRaises(ValueError):runner.execute_existing_upgrade("first",self.plan_path,self.approval_path,self.root/"other.json",fake,now=NOW)
            self.assertEqual(fake.calls,[])
    def test_approval_artifact_and_observation_fail_before_mutation(self):
        self.approval["approved"]=False;self.approval_path.write_text(json.dumps(self.approval))
        with self.assertRaisesRegex(ValueError,"approval-binding"):self.execute("first")
        self.assertEqual(self.fake.calls,[])
        self.approval["approved"]=True;self.approval_path.write_text(json.dumps(self.approval));Path(self.plan["artifacts"]["bundlePath"]).write_text("changed")
        with self.assertRaisesRegex(ValueError,"bundle-content"):self.execute("first")
        self.assertEqual(self.fake.calls,[])
    def test_post_success_drift_and_unknown_observation_do_not_retry(self):
        self.execute("first");self.fake.state["applicationRevision"]=self.plan["source"]["oldApplicationRevision"]
        with self.assertRaisesRegex(ValueError,"completed-application-drift"):self.execute("rerun")
        self.assertEqual(self.fake.calls,["controller","account"])
        self.fake.read_failure=True
        with self.assertRaises(RuntimeError):self.execute("resume")
        self.assertEqual(self.fake.calls,["controller","account"])
    def test_adapter_observes_metadata_only_and_resume_never_mutates(self):
        from types import SimpleNamespace
        module=runner._load_contract_module("approved_live_adapter")
        self.fake.fail="controller"
        with self.assertRaises(RuntimeError): self.execute("first")
        snapshot=self.fake.state
        class ReadOnlyTransport:
            def __init__(self): self.calls=[]
            def run(t,argv,env):
                t.calls.append(argv)
                self.assertIn("get",argv); i=argv.index("get"); resource,name=argv[i+1:i+3]; field=argv[-1]
                if resource in ("replicasets", "pods"):
                    return SimpleNamespace(returncode=0,stdout='{"items":[]}',stderr="")
                if resource=="secret": self.assertIn(field,("jsonpath={.metadata.uid}","jsonpath={.metadata.resourceVersion}"))
                identity={"job":"Job","configmap":"ConfigMap","persistentvolumeclaim":"PVC","secret":"Secret"}.get(resource)
                if resource=="namespace":value=snapshot["target"]["clusterUID"]
                elif resource=="application":value=(snapshot["applicationRevision"] if "targetRevision" in field else snapshot["target"]["applicationUID" if name=="fresh-account-release" else ("rootApplicationUID" if name=="account-lightweight-root" else "parentApplicationUID")])
                elif resource=="deployment":value=("local/controller@"+snapshot["controllerImageDigest"] if "containers" in field else ("1" if any(x in field for x in ("replicas", "Replicas", "generation", "Generation")) else snapshot["target"]["controllerUID"]))
                elif resource=="rollout":value=snapshot["target"]["rolloutUID"]
                elif identity and "conditions" in field:value="True"
                elif identity:value=snapshot["protected"][identity+"/"+name]["uid" if field.endswith(".uid}") else "resourceVersion"]
                else: raise AssertionError(argv)
                return SimpleNamespace(returncode=0,stdout=value,stderr="")
        transport=ReadOnlyTransport(); live=module.ExistingUpgradeAdapter(self.plan_path,transport=transport)
        result=runner.execute_existing_upgrade("resume",self.plan_path,self.approval_path,self.journal,live,now=NOW)
        self.assertEqual(result["phases"]["controller"]["state"],"uncertain")
        self.assertTrue(transport.calls)
        self.assertTrue(all("get" in call for call in transport.calls))
        self.assertEqual(self.fake.calls,["controller"])

    def test_desired_plan_is_approval_bound_and_requires_preservation(self):
        module=runner._load_contract_module("existing_upgrade")
        for mutation in (
            lambda p:p["controllerResources"]["Deployment/fresh-control/argo-rollouts"].update(uid="wrong"),
            lambda p:p["controllerResources"]["ConfigMap/fresh-control/argo-rollouts-config"].update(state="absent"),
            lambda p:p["gitDesired"]["limitedSync"].update(prune=True),
            lambda p:p["gitDesired"]["limitedSync"]["resources"].append("Secret/account-db-runtime"),
            lambda p:p["gitDesired"]["child"].update(uid="wrong"),
            lambda p:p["gitDesired"]["child"].update(newRevision="d"*40),
            lambda p:p["gitDesired"]["parent"].update(newRevision="not-a-sha"),
            lambda p:p["gitDesired"]["protectedDigests"]["Job/account-migration"].update(new="9"*64),
        ):
            plan=copy.deepcopy(self.plan);mutation(plan)
            self.plan_path.write_text(json.dumps(plan))
            self.approval["planSha256"]=hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
            self.approval_path.write_text(json.dumps(self.approval))
            with self.assertRaises(ValueError):module.validate(self.plan_path,self.approval_path,now=NOW)
        self.plan_path.write_text(json.dumps(self.plan))
        with self.assertRaisesRegex(ValueError,"approval-binding"):
            module.validate(self.plan_path,self.approval_path,now=NOW)

    def test_pause_child_cas_and_no_replay_on_conflict_or_uncertainty(self):
        from types import SimpleNamespace
        module=runner._load_contract_module("approved_live_adapter")
        class FakeAdapter(module.ExistingUpgradeAdapter):
            supports_existing_upgrade_pause=True
            supports_synthetic_pause_without_policy=True
        class Transport:
            def __init__(t, change=None, raises=False):
                t.calls=[];t.raises=raises
                t.state={"/metadata/uid":"app-uid","/metadata/resourceVersion":"21",
                    "/spec/source/targetRevision":"b"*40,
                    "/spec/syncPolicy/automated":{"prune":False,"selfHeal":True}}
                if change:t.state.update(change)
            def run(t,argv,env):
                t.calls.append(argv)
                patch=json.loads(argv[argv.index("--patch")+1])
                if t.raises:raise OSError("unknown outcome")
                if any(t.state.get(op["path"])!=op["value"] for op in patch if op["op"]=="test"):
                    return SimpleNamespace(returncode=1,stdout="",stderr="conflict")
                t.state.pop("/spec/syncPolicy/automated")
                return SimpleNamespace(returncode=0,stdout="patched",stderr="")
        for change,raises in ((None,False),({"/metadata/resourceVersion":"22"},False),({"/metadata/uid":"other"},False),(None,True)):
            transport=Transport(change,raises);adapter=FakeAdapter(self.plan_path,transport=transport)
            if change or raises:
                with self.assertRaises((ValueError,OSError)):adapter.pause_child_autosync(self.plan)
            else:
                adapter.pause_child_autosync(self.plan)
                self.assertNotIn("/spec/syncPolicy/automated",transport.state)
            patch=json.loads(transport.calls[0][-1]);self.assertEqual([p["op"] for p in patch],["test","test","test","test","remove"])
            with self.assertRaisesRegex(ValueError,"replay-forbidden"):adapter.pause_child_autosync(self.plan)
            self.assertEqual(len(transport.calls),1)

    def test_journaled_pause_survives_restart_without_replaying_command(self):
        from types import SimpleNamespace
        module=runner._load_contract_module("approved_live_adapter")
        class FakeAdapter(module.ExistingUpgradeAdapter):
            supports_existing_upgrade_pause=True
            supports_synthetic_pause_without_policy=True
            def verify_initial_git_desired(self, plan, *, paused=False):return True
            def verify_reviewed_render(self, plan):
                root=copy.deepcopy(self.transport.apps["account-lightweight-root"]["spec"])
                parent=copy.deepcopy(self.transport.apps["fresh-release-definitions"]["spec"])
                child=copy.deepcopy(self.transport.apps["fresh-account-release"]["spec"])
                child["syncPolicy"].pop("automated",None)
                self.render_evidence={"rootSpec":root,"parentSpec":parent,"childSpec":child}
                return True
        class Transport:
            def __init__(t, fail):
                t.calls=[];t.fail=fail
                t.apps={}
                for key,name in (("root","account-lightweight-root"),("parent","fresh-release-definitions"),("child","fresh-account-release")):
                    x=self.plan["gitDesired"][key]
                    policy={} if x["automated"] is None else {"automated":copy.deepcopy(x["automated"])}
                    t.apps[name]={"metadata":{"uid":x["uid"],"resourceVersion":x["resourceVersion"]},"spec":{"source":{"targetRevision":x["revision" if key=="root" else "oldRevision"]},"syncPolicy":policy}}
            def run(t,argv,env):
                t.calls.append(argv)
                if "exec" in argv:return SimpleNamespace(returncode=0,stdout="ok\n",stderr="")
                if "patch" in argv:
                    t.apps["fresh-account-release"]["spec"]["syncPolicy"].pop("automated")
                    if t.fail: raise OSError("lost response")
                    return SimpleNamespace(returncode=0,stdout="patched",stderr="")
                name=argv[argv.index("get")+2]
                return SimpleNamespace(returncode=0,stdout=json.dumps(t.apps[name]),stderr="")
        for fail in (False,True):
            transport=Transport(fail);journal=self.root/("pause-failed.json" if fail else "pause-success.json")
            adapter=FakeAdapter(self.plan_path,transport=transport);adapter.observe=self.fake.observe
            if fail:
                with self.assertRaises(OSError):runner.execute_existing_upgrade("pause",self.plan_path,self.approval_path,journal,adapter,now=NOW)
                self.assertEqual(json.loads(journal.read_text())["state"],"uncertain")
            else:
                self.assertEqual(runner.execute_existing_upgrade("pause",self.plan_path,self.approval_path,journal,adapter,now=NOW)["state"],"succeeded")
            restarted=FakeAdapter(self.plan_path,transport=transport);restarted.observe=self.fake.observe
            self.assertEqual(runner.execute_existing_upgrade("resume",self.plan_path,self.approval_path,journal,restarted,now=NOW)["state"],"succeeded")
            original_approval=copy.deepcopy(self.approval)
            self.approval.update(approvedBy="renewal-reviewer",approvedAt="2026-11-02T00:00:00Z",expiresAt="2026-11-04T00:00:00Z")
            self.approval_path.write_text(json.dumps(self.approval))
            renewed=runner.execute_existing_upgrade("resume",self.plan_path,self.approval_path,journal,restarted,now=datetime(2026,11,3,tzinfo=timezone.utc))
            self.assertEqual(renewed["approvalRenewals"],[hashlib.sha256(self.approval_path.read_bytes()).hexdigest()])
            self.approval=original_approval;self.approval_path.write_text(json.dumps(self.approval))
            self.assertEqual(sum("patch" in call and "validatingadmissionpolicy" not in call for call in transport.calls),1)
            with self.assertRaisesRegex(ValueError,"journal-exists"):
                runner.execute_existing_upgrade("pause",self.plan_path,self.approval_path,journal,restarted,now=NOW)
            self.assertEqual(sum("patch" in call and "validatingadmissionpolicy" not in call for call in transport.calls),1)

    def test_controller_step_journal_restarts_without_replaying_uncertain_write(self):
        identities=["Role/fresh-account/account-policy-reader","RoleBinding/fresh-account/account-policy-reader","ConfigMap/fresh-control/argo-rollouts-config","Deployment/fresh-control/argo-rollouts"]
        class ControllerFake(Stateful):
            supports_existing_upgrade_pause=True
            supports_synthetic_pause_without_policy=True
            supports_existing_controller_mutations=True
            def __init__(inner,*args,shared):super().__init__(*args);inner.shared=shared
            def verify_reviewed_render(inner,plan):return True
            def verify_initial_git_desired(inner,plan):return True
            def verify_database_prerequisite(inner):return True
            def pause_child_autosync(inner,plan):inner.shared["calls"].append("pause")
            def observe_child_autosync_paused(inner,plan):return True
            def apply_controller_resource(inner,identity,plan):
                inner.shared["calls"].append(identity);inner.shared["applied"].add(identity)
                if identity==identities[-1]:inner.shared["image"]=plan["artifacts"]["controllerImageDigest"]
                if inner.shared.get("fail")==identity:raise OSError("unknown")
                old=plan["controllerResources"][identity]
                return old["uid"] if old["state"]=="present" else "observed-"+identity
            def verify_controller_resource(inner,identity,plan):
                old=plan["controllerResources"][identity]
                if identity not in inner.shared["applied"]:return False
                if inner.shared.get("replace_uid")==identity:return "replaced-uid"
                return old["uid"] if old["state"]=="present" else "observed-"+identity
            def observe(inner):
                state=super().observe();state["controllerImageDigest"]=inner.shared["image"];return state
        shared={"calls":[],"applied":set(),"image":self.plan["artifacts"]["oldControllerImageDigest"],"fail":identities[2]}
        first=ControllerFake(self.plan_path,self.plan,shared=shared)
        runner.execute_existing_upgrade("pause",self.plan_path,self.approval_path,self.journal,first,now=NOW)
        with self.assertRaises(OSError):runner.execute_existing_upgrade("continue",self.plan_path,self.approval_path,self.journal,first,now=NOW)
        saved=json.loads(self.journal.read_text());self.assertEqual(saved["controllerSteps"][identities[2]]["state"],"uncertain")
        restart=ControllerFake(self.plan_path,self.plan,shared=shared)
        with self.assertRaisesRegex(ValueError,"uncertain-requires-resume"):
            runner.execute_existing_upgrade("continue",self.plan_path,self.approval_path,self.journal,restart,now=NOW)
        runner.execute_existing_upgrade("resume",self.plan_path,self.approval_path,self.journal,restart,now=NOW)
        shared["fail"]=None
        result=runner.execute_existing_upgrade("continue",self.plan_path,self.approval_path,self.journal,restart,now=NOW)
        self.assertTrue(all(step["state"]=="succeeded" for step in result["controllerSteps"].values()))
        self.assertEqual(shared["calls"],["pause",*identities]);self.assertEqual(restart.state["protected"],self.plan["protected"])
        shared["replace_uid"]=identities[0]
        with self.assertRaisesRegex(ValueError,"controller-completed-drift"):
            runner.execute_existing_upgrade("resume",self.plan_path,self.approval_path,self.journal,restart,now=NOW)
        shared.pop("replace_uid")
        unknown={"calls":[],"applied":set(),"image":self.plan["artifacts"]["oldControllerImageDigest"],"fail":identities[0]}
        first=ControllerFake(self.plan_path,self.plan,shared=unknown);other=self.root/"unknown-created.json"
        runner.execute_existing_upgrade("pause",self.plan_path,self.approval_path,other,first,now=NOW)
        with self.assertRaises(OSError):runner.execute_existing_upgrade("continue",self.plan_path,self.approval_path,other,first,now=NOW)
        restart=ControllerFake(self.plan_path,self.plan,shared=unknown)
        resumed=runner.execute_existing_upgrade("resume",self.plan_path,self.approval_path,other,restart,now=NOW)
        self.assertEqual(resumed["controllerSteps"][identities[0]]["state"],"uncertain")
        self.assertIsNone(resumed["controllerSteps"][identities[0]]["uid"])
        with self.assertRaisesRegex(ValueError,"uncertain-requires-resume"):
            runner.execute_existing_upgrade("continue",self.plan_path,self.approval_path,other,restart,now=NOW)
        self.assertEqual(unknown["calls"],["pause",identities[0]])

    def test_controller_resource_cas_and_create_only_generated_commands(self):
        from types import SimpleNamespace
        module=runner._load_contract_module("approved_live_adapter")
        class FakeAdapter(module.ExistingUpgradeAdapter):supports_existing_controller_mutations=True
        class Transport:
            def __init__(t):t.calls=[];t.status=0;t.created=[]
            def run(t,argv,env):
                t.calls.append(argv)
                if "create" in argv:
                    obj=json.loads(Path(argv[argv.index("--filename")+1]).read_text());t.created.append(obj)
                    return SimpleNamespace(returncode=t.status,stdout=json.dumps({"metadata":{"uid":"created-"+obj["kind"]}}),stderr="")
                return SimpleNamespace(returncode=t.status,stdout="",stderr="")
        objects=[{"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"argo-rollouts","namespace":"fresh-control"},"spec":{"replicas":1}},
                 {"apiVersion":"v1","kind":"ConfigMap","metadata":{"name":"argo-rollouts-config","namespace":"fresh-control"},"data":{"metricProviderPlugins":"fixture"}},
                 {"apiVersion":"rbac.authorization.k8s.io/v1","kind":"Role","metadata":{"name":"account-policy-reader","namespace":"fresh-account"},"rules":[]},
                 {"apiVersion":"rbac.authorization.k8s.io/v1","kind":"RoleBinding","metadata":{"name":"account-policy-reader","namespace":"fresh-account"},"roleRef":{},"subjects":[]}]
        bundle=Path(self.plan["artifacts"]["bundlePath"]);bundle.write_text(json.dumps({"kind":"List","items":objects}))
        self.plan["artifacts"]["bundleSha256"]=hashlib.sha256(bundle.read_bytes()).hexdigest();self.plan_path.write_text(json.dumps(self.plan))
        transport=Transport();adapter=FakeAdapter(self.plan_path,transport=transport)
        for identity in self.plan["controllerResources"]:adapter.apply_controller_resource(identity,self.plan)
        self.assertEqual(len(transport.calls),4)
        for call,identity in zip(transport.calls[:2],list(self.plan["controllerResources"])[:2]):
            patch=json.loads(call[-1]);self.assertEqual(patch[0]["path"],"/metadata/uid");self.assertEqual(patch[1]["path"],"/metadata/resourceVersion")
            self.assertEqual(patch[0]["value"],self.plan["controllerResources"][identity]["uid"])
            self.assertEqual(patch[1]["value"],self.plan["controllerResources"][identity]["resourceVersion"])
        self.assertEqual([x["kind"] for x in transport.created],["Role","RoleBinding"])
        transport.status=1
        with self.assertRaisesRegex(ValueError,"outcome-uncertain"):adapter.apply_controller_resource("Deployment/fresh-control/argo-rollouts",self.plan)
        disabled=module.ExistingUpgradeAdapter(self.plan_path,transport=transport)
        with self.assertRaisesRegex(ValueError,"controller-mutation-capability-unavailable"):disabled.apply_controller_resource("Deployment/fresh-control/argo-rollouts",self.plan)

    def test_controller_rollback_delete_uses_uid_and_resource_version_preconditions(self):
        from types import SimpleNamespace
        module=runner._load_contract_module("approved_live_adapter")
        class FakeAdapter(module.ExistingUpgradeAdapter):supports_existing_controller_mutations=True
        role={"apiVersion":"rbac.authorization.k8s.io/v1","kind":"Role","metadata":{"name":"account-policy-reader","namespace":"fresh-account"},"rules":[]}
        bundle=Path(self.plan["artifacts"]["bundlePath"]);bundle.write_text(json.dumps({"kind":"List","items":[role]}))
        self.plan["artifacts"]["bundleSha256"]=hashlib.sha256(bundle.read_bytes()).hexdigest();self.plan_path.write_text(json.dumps(self.plan))
        class Transport:
            def __init__(t):t.calls=[];t.actual={**copy.deepcopy(role),"metadata":{**role["metadata"],"uid":"created-role-uid","resourceVersion":"77"}};t.options=None
            def run(t,argv,env):
                t.calls.append(argv)
                if "get" in argv:return SimpleNamespace(returncode=0,stdout=json.dumps(t.actual) if t.actual is not None else "",stderr="")
                t.options=json.loads(Path(argv[argv.index("--filename")+1]).read_text());t.actual=None
                return SimpleNamespace(returncode=0,stdout="deleted",stderr="")
        transport=Transport();adapter=FakeAdapter(self.plan_path,transport=transport);identity="Role/fresh-account/account-policy-reader"
        with self.assertRaisesRegex(ValueError,"rollback-drift"):adapter.rollback_controller_resource(identity,self.plan,"wrong-uid")
        self.assertEqual(len(transport.calls),1)
        adapter.rollback_controller_resource(identity,self.plan,"created-role-uid")
        command=transport.calls[-1]
        self.assertIn("--raw",command);self.assertEqual(command[command.index("--raw")+1],"/apis/rbac.authorization.k8s.io/v1/namespaces/fresh-account/roles/account-policy-reader")
        self.assertEqual(transport.options,{"apiVersion":"v1","kind":"DeleteOptions","preconditions":{"uid":"created-role-uid","resourceVersion":"77"}})
        self.assertTrue(adapter.verify_controller_rollback_resource(identity,self.plan,"created-role-uid"))
        disabled=module.ExistingUpgradeAdapter(self.plan_path,transport=transport)
        with self.assertRaisesRegex(ValueError,"controller-mutation-capability-unavailable"):disabled.rollback_controller_resource(identity,self.plan,"created-role-uid")

    def test_database_acl_prerequisite_is_read_only_and_fails_closed(self):
        from types import SimpleNamespace
        module=runner._load_contract_module("approved_live_adapter")
        class Transport:
            def __init__(t):t.calls=[];t.output="ok\n"
            def run(t,argv,env):
                t.calls.append(argv);return SimpleNamespace(returncode=0,stdout=t.output,stderr="")
        transport=Transport();adapter=module.ExistingUpgradeAdapter(self.plan_path,transport=transport)
        self.assertTrue(adapter.verify_database_prerequisite())
        argv=transport.calls[0];self.assertIn("exec",argv);self.assertIn("account-postgresql-0",argv)
        query=argv[-1];self.assertTrue(query.lstrip().startswith("SELECT CASE WHEN"))
        self.assertIn("has_table_privilege",query);self.assertIn("pg_default_acl",query);self.assertIn("has_sequence_privilege",query)
        self.assertNotIn("admin-password", " ".join(argv))
        transport.output="not-ok\n"
        with self.assertRaisesRegex(ValueError,"database-prerequisite"):adapter.verify_database_prerequisite()

    def test_policy_keeps_distinct_chart_and_workload_pins_when_chart_is_baseline(self):
        module=runner._load_contract_module("existing_upgrade")
        baseline="3707f73581da5d04db0e4771dda7c1e82bd0f47c";workload="c"*40
        spec=module.build_reviewed_policy("a"*40,baseline,workload,self.plan["gitDesired"]["sourceRender"]["runtimeImage"],"paused","fresh")
        expression=spec["validations"][0]["expression"]
        self.assertEqual(expression.count("targetRevision == '"+baseline+"'"),1)
        self.assertEqual(expression.count("targetRevision == '"+workload+"'"),1)

    def test_live_git_chain_preflight_rejects_active_operations_and_drift(self):
        from types import SimpleNamespace
        module=runner._load_contract_module("approved_live_adapter")
        class Transport:
            def __init__(t):
                t.apps={}
                for key,name in (("root","account-lightweight-root"),("parent","fresh-release-definitions"),("child","fresh-account-release")):
                    item=self.plan["gitDesired"][key]
                    policy={} if item["automated"] is None else {"automated":copy.deepcopy(item["automated"])}
                    t.apps[name]={"metadata":{"uid":item["uid"],"resourceVersion":item["resourceVersion"]},"spec":{"source":{"targetRevision":item.get("oldRevision",item.get("revision"))},"syncPolicy":policy}}
            def run(t,argv,env):
                name=argv[argv.index("get")+2]
                return SimpleNamespace(returncode=0,stdout=json.dumps(t.apps[name]),stderr="")
        transport=Transport();adapter=module.ExistingUpgradeAdapter(self.plan_path,transport=transport)
        adapter.render_evidence={}
        for key,name in (("root","account-lightweight-root"),("parent","fresh-release-definitions"),("child","fresh-account-release")):
            spec=copy.deepcopy(transport.apps[name]["spec"])
            if key=="child":spec["syncPolicy"].pop("automated")
            adapter.render_evidence[key+"Spec"]=spec
        transport.apps["fresh-account-release"]["spec"]["syncPolicy"].pop("automated")
        self.assertTrue(adapter.verify_initial_git_desired(self.plan))
        for name in transport.apps:
            transport.apps[name]["operation"]={"sync":{}}
            with self.assertRaisesRegex(ValueError,"operation-active"):adapter.verify_initial_git_desired(self.plan)
            transport.apps[name].pop("operation")
        transport.apps["account-lightweight-root"]["metadata"]["resourceVersion"]="changed"
        self.assertTrue(adapter.verify_initial_git_desired(self.plan))
        transport.apps["account-lightweight-root"]["metadata"]["resourceVersion"]="19"
        transport.apps["fresh-release-definitions"]["spec"]["source"]["path"]="wrong"
        with self.assertRaisesRegex(ValueError,"rendered-desired-mismatch:parent"):adapter.verify_initial_git_desired(self.plan)

    def test_reviewed_render_from_synthetic_git_commits(self):
        import subprocess, shutil, yaml
        module=runner._load_contract_module("existing_upgrade")
        runner_spec=importlib.util.spec_from_file_location("runner_cli_fixture",HERE/"test_runner.py")
        runner_tests=importlib.util.module_from_spec(runner_spec);runner_spec.loader.exec_module(runner_tests)
        git=Path(shutil.which("git")).resolve()
        helm_path=os.environ.get("HELM_BIN") or shutil.which("helm")
        kubectl_path=os.environ.get("KUBECTL_BIN") or shutil.which("kubectl")
        if not helm_path or not kubectl_path: self.skipTest("fixture helm or kubectl unavailable")
        helm=Path(helm_path).resolve(); renderer=Path(kubectl_path).resolve()
        app=self.root/"app";platform=self.root/"platform"
        def cmd(root,*args):
            return subprocess.run([str(git),"-C",str(root),*args],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True).stdout.strip()
        for root in (app,platform):
            root.mkdir();cmd(root,"init","-q");cmd(root,"config","user.name","fixture");cmd(root,"config","user.email","fixture@example.invalid")
        for repository,root,source in (("platform-gitops",platform,HERE.parent.parent),("application-manifest",app,APPLICATION_SOURCE)):
            for relative in runner_tests.runner.REQUIRED_SOURCE_PATHS[repository] | runner_tests.runner.PLUGIN_SOURCE_PATHS[repository]:
                src=source/relative;dest=root/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dest)
        chart=app/"learning/fresh-profile/release-definition";(chart/"templates").mkdir(parents=True,exist_ok=True)
        template=(chart/"templates/application.yaml").read_text()
        cmd(app,"add",".");cmd(app,"commit","-qm","chart");chart_rev=cmd(app,"rev-parse","HEAD")
        old_revision="3707f73581da5d04db0e4771dda7c1e82bd0f47c"
        cmd(app,"fetch","--no-tags",str(APPLICATION_SOURCE),old_revision)
        workload_rev=old_revision
        with tempfile.TemporaryDirectory() as directory:
            archive=subprocess.run([str(git),"-C",str(app),"archive",old_revision,"learning/fresh-profile/workload"],stdout=subprocess.PIPE,check=True).stdout
            subprocess.run(["tar","-x","-C",directory],input=archive,check=True)
            old_docs=[x for x in yaml.safe_load_all(subprocess.check_output([str(renderer),"kustomize",str(Path(directory)/"learning/fresh-profile/workload")])) if x]
        identities={name for name in NAMES if not name.startswith("Secret/")} | {"StatefulSet/account-postgresql","Service/account-postgresql"}
        objects=[]
        for item in old_docs:
            kind="PVC" if item["kind"]=="PersistentVolumeClaim" else item["kind"]
            if kind+"/"+item["metadata"]["name"] in identities:objects.append(item)
        overlay=app/"learning/fresh-profile/workload-existing";shutil.copytree(APPLICATION_SOURCE/"learning/fresh-profile/workload-existing",overlay)
        old_allowed={x["kind"]+"/"+x["metadata"]["name"] for x in old_docs if x["kind"]+"/"+x["metadata"]["name"] in module.SYNC_ALLOWLIST}
        self.assertEqual(old_allowed,module.ROLLBACK_SYNC_ALLOWLIST)
        self.assertNotIn("AnalysisTemplate/account-api-policy",old_allowed)
        allowed_docs=[x for x in yaml.safe_load_all(subprocess.check_output([str(renderer),"kustomize",str(overlay)])) if x]
        allowed=[x for x in allowed_docs if x["kind"]+"/"+x["metadata"]["name"] in module.SYNC_ALLOWLIST]
        def inventory(items):
            return {(item["apiVersion"].split("/",1)[0] if "/" in item["apiVersion"] else "",item["kind"],item["metadata"].get("namespace"),item["metadata"]["name"]) for item in items}
        old_inventory=inventory(old_docs);new_inventory=inventory(allowed_docs)
        stable_extra=[x for x in old_docs if x["kind"]+"/"+x["metadata"]["name"] not in module.SYNC_ALLOWLIST and ("PVC" if x["kind"]=="PersistentVolumeClaim" else x["kind"])+"/"+x["metadata"]["name"] not in identities]
        cmd(app,"add",".");cmd(app,"commit","-qm","new workload");new_workload_rev=cmd(app,"rev-parse","HEAD")
        if not renderer.is_file(): self.skipTest("fixture kubectl unavailable")
        bootstrap=platform/"learning/fresh-profile/root/bootstrap.yaml";bootstrap.parent.mkdir(parents=True,exist_ok=True)
        bootstrap.write_text(yaml.safe_dump({"apiVersion":"argoproj.io/v1alpha1","kind":"Application","metadata":{"name":"account-lightweight-root","namespace":"fresh-control"},"spec":{"source":{"repoURL":"https://github.com/syuhei0519/platform-gitops.git","targetRevision":"__PLATFORM_GITOPS_SHA__","path":"learning/fresh-profile/root/children"},"syncPolicy":{"syncOptions":["Prune=false"]}}}))
        parent_path=platform/"learning/fresh-profile/root/children/applications.yaml";parent_path.parent.mkdir(parents=True,exist_ok=True)
        parent={"apiVersion":"argoproj.io/v1alpha1","kind":"Application","metadata":{"name":"fresh-release-definitions","namespace":"fresh-control"},"spec":{"source":{"repoURL":"https://github.com/syuhei0519/application-manifest.git","targetRevision":chart_rev,"path":"learning/fresh-profile/release-definition","helm":{"releaseName":"fresh-account-release-definition","parameters":[{"name":"workloadRevision","value":workload_rev},{"name":"accountRuntimeImage","value":"ghcr.io/syuhei0519/backend-app@sha256:"+"b"*64},{"name":"promotionMode","value":"manual"},{"name":"syncMode","value":"paused"},{"name":"workloadVariant","value":"fresh"}]}},"syncPolicy":{"syncOptions":["Prune=false"]}}}
        def commit_parent(*, legacy=False):
            import re
            parent_path.write_text(yaml.safe_dump(parent))
            params={item["name"]:item["value"] for item in parent["spec"]["source"]["helm"]["parameters"]}
            policy=module.build_reviewed_policy("__PLATFORM_GITOPS_SHA__",parent["spec"]["source"]["targetRevision"],params["workloadRevision"],params["accountRuntimeImage"],params.get("syncMode","automated"),params.get("workloadVariant","fresh"))
            if legacy:
                expression=policy["validations"][1]["expression"].replace("parameters.size() == 5","parameters.size() == 2")
                expression=re.sub(r"\s*&&\s*object.spec.source.helm.parameters.exists\(p, p.name == '(promotionMode|syncMode|workloadVariant)'\)","",expression)
                expression=re.sub(r"\s*\|\|\s*\(p.name == '(promotionMode|syncMode|workloadVariant)' && p.value == '[^']*'\)","",expression)
                policy["validations"][1]["expression"]=expression
            docs=list(yaml.safe_load_all((HERE/"security/admission.yaml").read_text()))
            next(d for d in docs if d.get("kind")=="ValidatingAdmissionPolicy" and d.get("metadata",{}).get("name")=="fresh-application-boundary")["spec"]=policy
            policy_path=platform/"learning/fresh-profile/security/admission.yaml";policy_path.parent.mkdir(parents=True,exist_ok=True)
            policy_path.write_text(yaml.safe_dump_all(docs,sort_keys=False))
            cmd(platform,"add",".");cmd(platform,"commit","-qm","parent");return cmd(platform,"rev-parse","HEAD")
        params=parent["spec"]["source"]["helm"]["parameters"]
        parent["spec"]["source"]["targetRevision"]=old_revision
        del params[2:]
        pre_pause_platform=commit_parent(legacy=True)
        legacy_docs=list(yaml.safe_load_all((platform/"learning/fresh-profile/security/admission.yaml").read_text()))
        legacy_spec=copy.deepcopy(next(d for d in legacy_docs if d.get("kind")=="ValidatingAdmissionPolicy" and d.get("metadata",{}).get("name")=="fresh-application-boundary")["spec"])
        for validation in legacy_spec["validations"]:validation["expression"]=validation["expression"].replace("__PLATFORM_GITOPS_SHA__",pre_pause_platform)
        parent["spec"]["source"]["targetRevision"]=chart_rev
        params.extend([{"name":"promotionMode","value":"manual"},{"name":"syncMode","value":"paused"},{"name":"workloadVariant","value":"fresh"}])
        initial_platform=commit_parent()
        def param(name,value): next(p for p in params if p["name"]==name)["value"]=value
        parent["spec"]["source"]["targetRevision"]=new_workload_rev
        param("workloadRevision",new_workload_rev);param("workloadVariant","existing-upgrade")
        new_paused_platform=commit_parent()
        param("syncMode","automated");final_platform=commit_parent()
        rollback_platform=pre_pause_platform
        parent["spec"]["source"]["targetRevision"]=chart_rev
        param("workloadRevision",workload_rev);param("workloadVariant","fresh")
        cmd(platform,"checkout","--detach",initial_platform)
        param("syncMode","paused")
        plan=copy.deepcopy(self.plan);render=plan["gitDesired"]["sourceRender"]
        render["transitions"]={"newPaused":{"platformRevision":new_paused_platform,"chartRevision":new_workload_rev},"finalAutomated":{"platformRevision":final_platform,"chartRevision":new_workload_rev},"rollbackOld":{"platformRevision":rollback_platform,"chartRevision":old_revision}}
        render["prePause"]={"platformRevision":pre_pause_platform,"chartRevision":old_revision,"workloadRevision":workload_rev}
        image=render["runtimeImage"]
        desired_specs={"initial":module.build_reviewed_policy(initial_platform,chart_rev,workload_rev,image,"paused","fresh"),
                       "newPaused":module.build_reviewed_policy(new_paused_platform,new_workload_rev,new_workload_rev,image,"paused","existing-upgrade"),
                       "finalAutomated":module.build_reviewed_policy(final_platform,new_workload_rev,new_workload_rev,image,"automated","existing-upgrade"),
                       "rollbackOld":legacy_spec}
        plan["securityPolicy"]["oldSpec"]=legacy_spec
        plan["securityPolicy"]["desiredHashes"]={name:module.policy_digest(spec) for name,spec in desired_specs.items()}
        render.update(platformRevision=initial_platform,chartRevision=chart_rev,workloadRevision=workload_rev,newWorkloadRevision=new_workload_rev,kustomizePath=str(renderer),kustomizeSha256=hashlib.sha256(renderer.read_bytes()).hexdigest(),gitPath=str(git),gitSha256=hashlib.sha256(git.read_bytes()).hexdigest(),helmPath=str(helm),helmSha256=hashlib.sha256(helm.read_bytes()).hexdigest())
        for obj in objects:
            kind="PVC" if obj["kind"]=="PersistentVolumeClaim" else obj["kind"]
            key=kind+"/"+obj["metadata"]["name"]
            digest=hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
            plan["gitDesired"]["protectedDigests"][key]={"old":digest,"new":digest}
        self.assertTrue(module.verify_reviewed_render(plan,platform,app))
        transitions=render["transitions"]
        for stage,field,bad,reason in (("newPaused","platformRevision","f"*40,"unknown-revision"),
                                      ("newPaused","chartRevision",chart_rev,"security-policy-source-mismatch"),
                                      ("finalAutomated","platformRevision",new_paused_platform,"security-policy-source-mismatch"),
                                      ("rollbackOld","platformRevision",final_platform,"security-policy-source-mismatch")):
            old=transitions[stage][field];transitions[stage][field]=bad
            with self.assertRaisesRegex(ValueError,reason):module.verify_reviewed_render(plan,platform,app)
            transitions[stage][field]=old
        plan["source"].update(platformRevision=render["platformRevision"],oldApplicationRevision=workload_rev,newApplicationRevision=new_workload_rev)
        plan["gitDesired"]["root"]["revision"]=pre_pause_platform
        plan["gitDesired"]["child"]["newRevision"]=new_workload_rev
        plan["gitDesired"]["parent"]["oldRevision"]=old_revision
        plan["gitDesired"]["parent"]["newRevision"]=new_workload_rev
        plan["gitDesired"]["child"]["oldRevision"]=workload_rev
        self.plan_path.write_text(json.dumps(plan));self.approval["planSha256"]=hashlib.sha256(self.plan_path.read_bytes()).hexdigest();self.approval_path.write_text(json.dumps(self.approval))
        class FakeRunner(Stateful):
            supports_existing_upgrade_pause=True
            supports_synthetic_pause_without_policy=True
            def verify_reviewed_render(inner, value):return module.verify_reviewed_render(value,platform,app)
            def verify_initial_git_desired(inner, value):return True
            def verify_database_prerequisite(inner):return True
            def pause_child_autosync(inner, value):inner.calls.append("pause")
            def observe_child_autosync_paused(inner, value):return True
        fake=FakeRunner(self.plan_path,plan)
        result=runner.execute_existing_upgrade("pause",self.plan_path,self.approval_path,self.journal,fake,now=NOW)
        self.assertEqual(result["state"],"succeeded");self.assertEqual(fake.calls,["pause"])
        self.exercise_cli_sealed_fixture(runner_tests,platform,app,plan,module,old_allowed,old_inventory,new_inventory)
        for variant,reason in ((objects[:-1],"protected-missing"),(objects+[objects[0]],"git-render-kustomize"),(copy.deepcopy(objects),"protected-mismatch"),(objects+[{"apiVersion":"v1","kind":"Secret","metadata":{"name":"account-db-runtime","namespace":"fresh-account"},"data":{"fixture":"c2VjcmV0"}}],"external-secret-present")):
            if len(variant)==len(objects): variant[0]["metadata"]["annotations"]={"fixture":"changed"}
            (overlay/"kustomization.yaml").write_text("apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: [objects.yaml]\n")
            (overlay/"objects.yaml").write_text("\n---\n".join(json.dumps(x) for x in variant+allowed+stable_extra))
            cmd(app,"add",".");cmd(app,"commit","-qm",reason)
            render["newWorkloadRevision"]=cmd(app,"rev-parse","HEAD")
            with self.assertRaisesRegex(ValueError,reason):module.verify_reviewed_render(plan,platform,app)
            plan["source"]["newApplicationRevision"]=render["newWorkloadRevision"]
            plan["gitDesired"]["child"]["newRevision"]=render["newWorkloadRevision"]
            self.plan_path.write_text(json.dumps(plan));self.approval["planSha256"]=hashlib.sha256(self.plan_path.read_bytes()).hexdigest();self.approval_path.write_text(json.dumps(self.approval))
            rejected=FakeRunner(self.plan_path,plan);rejected_journal=self.root/(reason+".json")
            with self.assertRaisesRegex(ValueError,reason):
                runner.execute_existing_upgrade("pause",self.plan_path,self.approval_path,rejected_journal,rejected,now=NOW)
            self.assertFalse(rejected_journal.exists());self.assertEqual(rejected.calls,[])
        changed=copy.deepcopy(stable_extra);changed[0]["metadata"]["annotations"]={"fixture":"unexpected"}
        (overlay/"objects.yaml").write_text("\n---\n".join(json.dumps(x) for x in objects+allowed+changed))
        cmd(app,"add",".");cmd(app,"commit","-qm","nonallowlisted-change")
        render["newWorkloadRevision"]=cmd(app,"rev-parse","HEAD")
        with self.assertRaisesRegex(ValueError,"nonallowlisted-change"):module.verify_reviewed_render(plan,platform,app)
        render["newWorkloadRevision"]=new_workload_rev
        plan["source"]["newApplicationRevision"]=new_workload_rev
        plan["gitDesired"]["child"]["newRevision"]=new_workload_rev
        original_sha=render["kustomizeSha256"];render["kustomizeSha256"]="0"*64
        with self.assertRaisesRegex(ValueError,"tool-changed:kustomize"):module.verify_reviewed_render(plan,platform,app)
        render["kustomizeSha256"]=original_sha
        parent_path.write_text("dirty tree")
        self.assertTrue(module.verify_reviewed_render(plan,platform,app))
        render["chartRevision"]="f"*40
        with self.assertRaisesRegex(ValueError,"unknown-revision"):module.verify_reviewed_render(plan,platform,app)
        render["chartRevision"]=chart_rev
        parent["spec"]["source"]["path"]="../outside";render["platformRevision"]=commit_parent()
        plan["securityPolicy"]["desiredHashes"]["initial"]=module.policy_digest(module.build_reviewed_policy(render["platformRevision"],chart_rev,workload_rev,image,"paused","fresh"))
        with self.assertRaisesRegex(ValueError,"parent-source"):module.verify_reviewed_render(plan,platform,app)
        parent["spec"]["source"]["path"]="learning/fresh-profile/release-definition"
        (chart/"templates/application.yaml").write_text(template.replace('eq .Values.syncMode "automated"','eq .Values.syncMode "paused"'))
        cmd(app,"add",".");cmd(app,"commit","-qm","auto chart");auto_rev=cmd(app,"rev-parse","HEAD")
        parent["spec"]["source"]["targetRevision"]=auto_rev;render.update(platformRevision=commit_parent(),chartRevision=auto_rev)
        plan["securityPolicy"]["desiredHashes"]["initial"]=module.policy_digest(module.build_reviewed_policy(render["platformRevision"],auto_rev,workload_rev,image,"paused","fresh"))
        with self.assertRaisesRegex(ValueError,"child-autosync"):module.verify_reviewed_render(plan,platform,app)

    def exercise_cli_sealed_fixture(self, rt, platform, app, plan, contract, old_allowed, old_inventory, new_inventory):
        import contextlib, io, sys, subprocess, shutil
        from types import SimpleNamespace
        fixture=rt.RunnerTests("test_repository_dag_is_the_runner_step_machine")
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        with mock.patch.object(rt,"PLATFORM",plan["source"]["platformRevision"]), mock.patch.object(rt,"APPLICATION",plan["source"]["newApplicationRevision"]):
            approval,gate_approval,materialization_path,profile_path,journal,_,_=fixture.fixture(plugin=True)
        materialization=json.loads(materialization_path.read_text())
        for identity in materialization["sourceFiles"]:
            repository,revision_and_path=identity.split("@",1);revision,relative=revision_and_path.split(":",1)
            root=platform if repository=="platform-gitops" else app
            current=(root/relative).read_bytes()
            committed=subprocess.run([str(Path(shutil.which("git")).resolve()),"-C",str(root),"show",revision+":"+relative],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
            self.assertEqual(current,committed,"source bytes differ from pinned synthetic commit: "+identity)
            materialization["sourceFiles"][identity]=hashlib.sha256(current).hexdigest()
        materialization_path.write_text(json.dumps(materialization,sort_keys=True))
        approval["metadata"].update(approvedAt="2026-10-25T00:00:00Z",expiresAt="2026-11-23T00:00:00Z")
        fixture.reseal(approval,gate_approval,materialization_path,profile_path)
        gate_plan=materialization_path.parent/"live-step-plan.json"
        plugin=json.loads(gate_plan.read_text())["rolloutsPlugin"]
        plan=copy.deepcopy(plan)
        plan["target"].update(clusterName="learning-fresh-fixture-sandbox",context="kind-learning-fresh-fixture-sandbox")
        plan["artifacts"].update(controllerImageDigest=plugin["controllerImage"].split("@",1)[1],binarySha256=plugin["binarySha256"],bundleSha256=plugin["bundleSha256"],bundlePath=str(materialization_path.parent/"plugin-bundle.json"))
        plan_path=self.root/"cli-plan.json";plan_path.write_text(json.dumps(plan))
        upgrade_approval=copy.deepcopy(self.approval)
        upgrade_approval.update(approvedAt="2026-10-25T00:00:00Z",expiresAt="2026-11-23T00:00:00Z",planSha256=hashlib.sha256(plan_path.read_bytes()).hexdigest())
        upgrade_approval_path=self.root/"cli-approval.json";upgrade_approval_path.write_text(json.dumps(upgrade_approval))
        evidence=contract.verify_reviewed_render(plan,platform,app)
        legacy=evidence["prePause"]
        self.assertEqual(len(legacy["parentSpec"]["source"]["helm"]["parameters"]),2)
        self.assertEqual(legacy["parentSpec"]["source"]["targetRevision"],"3707f73581da5d04db0e4771dda7c1e82bd0f47c")
        self.assertEqual(legacy["childSpec"]["syncPolicy"]["automated"],{"prune":False,"selfHeal":True})
        self.assertEqual(evidence["transitions"]["rollbackOld"]["childSpec"],legacy["childSpec"])
        class FakeTransport:
            def __init__(t):
                t.calls=[];t.apps={};t.resources={};t.conflict=False;t.controller_new=False;t.policy_conflict=False;t.policy_lost=False;t.account_lost_layer=None;t.account_sync_lost=False;t.delete_lost_kind=None;t.acl_output="ok\n";t.sync_behavior="success";t.account_ready=True
                t.policy={"metadata":{"uid":plan["securityPolicy"]["uid"],"resourceVersion":plan["securityPolicy"]["resourceVersion"],"generation":1},"spec":copy.deepcopy(plan["securityPolicy"]["oldSpec"]),"status":{"observedGeneration":1,"typeChecking":{"expressionWarnings":[]}}}
                for identity,item in plan["controllerResources"].items():
                    if item["state"]=="present":
                        kind,namespace,name=identity.split("/")
                        t.resources[(kind,namespace,name)]={"metadata":{"uid":item["uid"],"resourceVersion":item["resourceVersion"]},**copy.deepcopy(item["rollback"])}
                for key,name in (("root","account-lightweight-root"),("parent","fresh-release-definitions"),("child","fresh-account-release")):
                    item=plan["gitDesired"][key];spec=copy.deepcopy(evidence["prePause"][key+"Spec"])
                    if key=="child":spec["syncPolicy"]["automated"]=copy.deepcopy(item["automated"])
                    t.apps[name]={"metadata":{"uid":item["uid"],"resourceVersion":item["resourceVersion"]},"spec":spec}
            def run(t,argv,env):
                t.calls.append(argv)
                if "exec" in argv:return SimpleNamespace(returncode=0,stdout=t.acl_output,stderr="")
                if "patch" in argv and argv[argv.index("patch")+1]=="validatingadmissionpolicy":
                    patch=json.loads(argv[argv.index("--patch")+1]);meta=t.policy["metadata"]
                    values={"/metadata/uid":meta["uid"],"/metadata/resourceVersion":meta["resourceVersion"],"/spec":t.policy["spec"]}
                    if t.policy_conflict or any(values.get(x["path"])!=x["value"] for x in patch if x["op"]=="test"):
                        return SimpleNamespace(returncode=1,stdout="",stderr="conflict")
                    t.policy["spec"]=patch[-1]["value"];meta["generation"]+=1;meta["resourceVersion"]=str(int(meta["resourceVersion"])+1);t.policy["status"]["observedGeneration"]=meta["generation"]
                    if t.policy_lost:raise OSError("lost policy response")
                    return SimpleNamespace(returncode=0,stdout="patched",stderr="")
                if "delete" in argv:
                    raw=argv[argv.index("--raw")+1];kind="RoleBinding" if "/rolebindings/" in raw else "Role"
                    key=(kind,"fresh-account","account-policy-reader");obj=t.resources[key]
                    options=json.loads(Path(argv[argv.index("--filename")+1]).read_text())
                    if options.get("preconditions")!={"uid":obj["metadata"]["uid"],"resourceVersion":obj["metadata"]["resourceVersion"]}:return SimpleNamespace(returncode=1,stdout="",stderr="conflict")
                    del t.resources[key]
                    if t.delete_lost_kind==kind:raise OSError("lost delete response")
                    return SimpleNamespace(returncode=0,stdout="deleted",stderr="")
                if "create" in argv:
                    obj=json.loads(Path(argv[argv.index("--filename")+1]).read_text());meta=obj["metadata"]
                    obj["metadata"]["uid"]="created-"+obj["kind"];obj["metadata"]["resourceVersion"]="1"
                    t.resources[(obj["kind"],meta["namespace"],meta["name"])]=obj
                    return SimpleNamespace(returncode=0,stdout=json.dumps(obj),stderr="")
                if "patch" in argv and argv[argv.index("patch")+1]!="application":
                    index=argv.index("patch");resource,name=argv[index+1:index+3];namespace=argv[argv.index("--namespace")+1]
                    kind={"deployment":"Deployment","configmap":"ConfigMap","role":"Role","rolebinding":"RoleBinding"}[resource]
                    obj=t.resources[(kind,namespace,name)];patch=json.loads(argv[argv.index("--patch")+1])
                    for op in patch:
                        parts=op["path"].strip("/").split("/");current=obj
                        for part in parts[:-1]:current=current[part]
                        if op["op"]=="test" and current[parts[-1]]!=op["value"]:return SimpleNamespace(returncode=1,stdout="",stderr="conflict")
                        if op["op"]=="replace":current[parts[-1]]=op["value"]
                    if kind=="Deployment":t.controller_new=(obj["spec"]!=plan["controllerResources"]["Deployment/fresh-control/argo-rollouts"]["rollback"]["spec"])
                    return SimpleNamespace(returncode=0,stdout="patched",stderr="")
                if "patch" in argv:
                    patch=json.loads(argv[argv.index("--patch")+1]);name=argv[argv.index("patch")+2];appobj=t.apps[name]
                    if patch[-1].get("path")=="/operation":
                        operation=patch[-1]["value"];revision=operation["sync"]["revision"]
                        behavior=t.sync_behavior if revision==plan["source"]["newApplicationRevision"] else "success"
                        if behavior=="unknown":raise OSError("operation outcome unknown")
                        if behavior=="active":
                            appobj["operation"]=copy.deepcopy(operation)
                            appobj["status"]={"operationState":{"phase":"Running","operation":operation}}
                            return SimpleNamespace(returncode=0,stdout="accepted",stderr="")
                        if revision==plan["source"]["oldApplicationRevision"]:t.account_ready=True
                        inventory={plan["source"]["oldApplicationRevision"]:old_inventory,plan["source"]["newApplicationRevision"]:new_inventory}.get(revision,set())
                        requested=operation["sync"]["resources"]
                        exists=lambda resource:(resource.get("group",""),resource["kind"],resource.get("namespace"),resource["name"]) in inventory
                        missing=[resource for resource in requested if not exists(resource)]
                        results=[{**resource,"status":"Synced" if exists(resource) else "SyncFailed"} for resource in requested]
                        if behavior in {"failed","partial"}:
                            failed=next(resource for resource in requested if resource["kind"]=="Rollout")
                            results=[{**failed,"status":"SyncFailed"}]
                            if behavior=="partial":results.append({**next(resource for resource in requested if resource["kind"]=="AnalysisTemplate"),"status":"Synced"})
                        if behavior=="notready":t.account_ready=False
                        analysis_key=("AnalysisTemplate","fresh-account","account-api-policy")
                        if not missing and behavior not in {"failed","unknown","active"} and any(resource["kind"]=="AnalysisTemplate" for resource in requested):
                            t.resources[analysis_key]={"apiVersion":"argoproj.io/v1alpha1","kind":"AnalysisTemplate","metadata":{"name":"account-api-policy","namespace":"fresh-account","uid":"analysis-uid","resourceVersion":"1","annotations":{"argocd.argoproj.io/compare-options":"IgnoreExtraneous","argocd.argoproj.io/sync-options":"Prune=false,Delete=false","argocd.argoproj.io/tracking-id":"fresh-account-release:argoproj.io/AnalysisTemplate:fresh-account/account-api-policy"}}}
                        retained=t.resources.get(analysis_key);sync_status="Synced" if not missing and behavior not in {"failed","partial"} and (retained is None or retained["metadata"]["annotations"].get("argocd.argoproj.io/compare-options")=="IgnoreExtraneous") else "OutOfSync"
                        appobj["status"]={"operationState":{"phase":"Failed" if missing or behavior in {"failed","partial"} else "Succeeded","finishedAt":"2026-11-01T00:00:00Z","operation":operation,"syncResult":{"revision":operation["sync"]["revision"],"resources":results}},"sync":{"status":sync_status,"revision":operation["sync"]["revision"],"comparedTo":{"source":copy.deepcopy(appobj["spec"]["source"]),"destination":copy.deepcopy(appobj["spec"]["destination"])}}}
                        if t.account_sync_lost:raise OSError("lost sync response")
                        return SimpleNamespace(returncode=0,stdout="patched",stderr="")
                    if any(item.get("path")=="/spec" for item in patch):
                        values={"/metadata/uid":appobj["metadata"]["uid"],"/metadata/resourceVersion":appobj["metadata"]["resourceVersion"],"/spec":appobj["spec"]}
                        if any(values.get(x["path"])!=x["value"] for x in patch if x["op"]=="test"):return SimpleNamespace(returncode=1,stdout="",stderr="conflict")
                        appobj["spec"]=copy.deepcopy(patch[-1]["value"]);appobj["metadata"]["resourceVersion"]=str(int(appobj["metadata"]["resourceVersion"])+1)
                        if t.account_lost_layer==name:raise OSError("lost application response")
                        return SimpleNamespace(returncode=0,stdout="patched",stderr="")
                    if t.conflict:return SimpleNamespace(returncode=1,stdout="",stderr="conflict")
                    values={"/metadata/uid":appobj["metadata"]["uid"],"/metadata/resourceVersion":appobj["metadata"]["resourceVersion"],"/spec/source/targetRevision":appobj["spec"]["source"]["targetRevision"],"/spec/syncPolicy/automated":appobj["spec"]["syncPolicy"].get("automated")}
                    if any(values.get(x["path"])!=x["value"] for x in patch if x["op"]=="test"):
                        return SimpleNamespace(returncode=1,stdout="",stderr="conflict")
                    appobj["spec"]["syncPolicy"].pop("automated")
                    return SimpleNamespace(returncode=0,stdout="patched",stderr="")
                i=argv.index("get");resource,name=argv[i+1:i+3];field=argv[-1]
                if resource=="validatingadmissionpolicy":value=json.dumps(t.policy)
                elif resource in ("replicasets","pods"):
                    namespace=argv[argv.index("--namespace")+1];is_account=namespace=="fresh-account"
                    owner=plan["target"]["rolloutUID" if is_account else "controllerUID"]
                    if resource=="replicasets":items=[{"metadata":{"uid":"replicaset-uid","ownerReferences":[{"uid":owner,"controller":True}]}}]
                    else:
                        digest=plan["gitDesired"]["sourceRender"]["runtimeImage"].rsplit("@",1)[1] if is_account else plan["artifacts"]["controllerImageDigest" if t.controller_new else "oldControllerImageDigest"]
                        items=[{"metadata":{"ownerReferences":[{"uid":"replicaset-uid","controller":True}]},"status":{"phase":"Running","conditions":[{"type":"Ready","status":"True"}],"containerStatuses":[{"name":"api" if is_account else "argo-rollouts","ready":True,"imageID":"containerd://image@"+digest}]}}]
                    value=json.dumps({"items":items})
                elif "--ignore-not-found" in argv:
                    namespace=argv[argv.index("--namespace")+1];kind={"deployment":"Deployment","configmap":"ConfigMap","role":"Role","rolebinding":"RoleBinding","analysistemplate":"AnalysisTemplate"}[resource]
                    item=t.resources.get((kind,namespace,name));value=json.dumps(item) if item is not None else ""
                elif resource=="application" and field=="json":value=json.dumps(t.apps[name])
                elif resource=="application":value=t.apps[name]["metadata"]["uid"] if ".uid" in field else t.apps[name]["spec"]["source"]["targetRevision"]
                elif resource=="namespace":value=plan["target"]["clusterUID"]
                elif resource=="deployment":value=("local/controller@"+plan["artifacts"]["controllerImageDigest" if t.controller_new else "oldControllerImageDigest"] if "containers" in field else ("1" if any(x in field for x in ("replicas","Replicas","generation","Generation")) else plan["target"]["controllerUID"]))
                elif resource=="rollout":value=(("0" if not t.account_ready and any(x in field for x in ("readyReplicas","updatedReplicas")) else "1") if any(x in field for x in ("replicas","Replicas","generation","Generation")) else plan["target"]["rolloutUID"])
                else:
                    kind={"job":"Job","configmap":"ConfigMap","persistentvolumeclaim":"PVC","secret":"Secret"}[resource]
                    item=plan["protected"][kind+"/"+name]
                    value="True" if "conditions" in field else item["uid" if ".uid" in field else "resourceVersion"]
                return SimpleNamespace(returncode=0,stdout=value,stderr="")
        transport=FakeTransport()
        # A fabricated old-revision request for the new AnalysisTemplate must
        # not produce the successful receipt that the real old render cannot.
        invalid_operation={"sync":{"revision":plan["source"]["oldApplicationRevision"],"resources":[{"group":"argoproj.io","kind":"AnalysisTemplate","name":"account-api-policy","namespace":"fresh-account"}],"prune":False}}
        invalid_patch=[{"op":"add","path":"/operation","value":invalid_operation}]
        transport.run(["fixture","patch","application","fresh-account-release","--patch",json.dumps(invalid_patch)],{})
        invalid_state=transport.apps["fresh-account-release"]["status"]["operationState"]
        self.assertEqual(invalid_state["phase"],"Failed")
        self.assertEqual(invalid_state["syncResult"]["resources"][0]["status"],"SyncFailed")
        self.assertNotIn(("AnalysisTemplate","fresh-account","account-api-policy"),transport.resources)
        transport.apps["fresh-account-release"].pop("status");transport.calls.clear()
        loader=rt.runner._load_contract_module;standalone=True
        def load(name):
            loaded=loader(name)
            if name=="approved_live_adapter":
                loaded.SubprocessTransport=lambda: transport
                if standalone:
                    base=loaded.ExistingUpgradeAdapter
                    class FakeAdapter(base):
                        supports_synthetic_pause_without_policy=True
                        supports_synthetic_standalone_policy=True
                    loaded.ExistingUpgradeAdapter=FakeAdapter
            return loaded
        argv=["runner.py","--mode","existing-upgrade","--upgrade-action","pause","--live-plan",str(plan_path),"--approval",str(upgrade_approval_path),"--journal",str(journal),"--gate-approval",str(gate_approval),"--gate-plan",str(gate_plan),"--materialization",str(materialization_path),"--profile",str(profile_path),"--platform-source-root",str(platform),"--application-source-root",str(app)]
        def invoke(action):
            argv[argv.index("--upgrade-action")+1]=action;output=io.StringIO()
            with mock.patch.object(sys,"argv",argv),contextlib.redirect_stdout(output): result=rt.runner.main()
            return result,output.getvalue()
        with mock.patch.object(rt.runner,"_load_contract_module",side_effect=load):
            policy_journal=self.root/"cli-policy.json";argv[argv.index("--journal")+1]=str(policy_journal)
            for action in ("policy-initial","policy-new-paused","policy-final-automated","policy-rollback-paused","policy-rollback-old"):
                result,output=invoke(action);self.assertEqual(result,0,output)
            self.assertEqual(json.loads(policy_journal.read_text())["current"],"rollbackOld")
            self.assertEqual(sum("patch" in call and "validatingadmissionpolicy" in call for call in transport.calls),5)
            transport.policy={"metadata":{"uid":plan["securityPolicy"]["uid"],"resourceVersion":plan["securityPolicy"]["resourceVersion"],"generation":1},"spec":copy.deepcopy(plan["securityPolicy"]["oldSpec"]),"status":{"observedGeneration":1,"typeChecking":{"expressionWarnings":[]}}}
            conflict_policy=self.root/"cli-policy-conflict.json";argv[argv.index("--journal")+1]=str(conflict_policy);transport.policy_conflict=True
            result,output=invoke("policy-initial");self.assertEqual(result,2);self.assertIn("security-policy-outcome-uncertain",output)
            result,output=invoke("resume");self.assertEqual(result,0,output)
            self.assertEqual(json.loads(conflict_policy.read_text())["steps"]["initial"]["state"],"uncertain")
            self.assertEqual(sum("patch" in call and "validatingadmissionpolicy" in call for call in transport.calls),6)
            transport.policy_conflict=False;transport.policy_lost=True
            lost_policy=self.root/"cli-policy-lost.json";argv[argv.index("--journal")+1]=str(lost_policy)
            result,output=invoke("policy-initial");self.assertEqual(result,2)
            transport.policy_lost=False
            result,output=invoke("resume");self.assertEqual(result,0,output)
            self.assertEqual(json.loads(lost_policy.read_text())["steps"]["initial"]["state"],"succeeded")
            self.assertEqual(sum("patch" in call and "validatingadmissionpolicy" in call for call in transport.calls),7)
            transport.policy={"metadata":{"uid":plan["securityPolicy"]["uid"],"resourceVersion":plan["securityPolicy"]["resourceVersion"],"generation":1},"spec":copy.deepcopy(plan["securityPolicy"]["oldSpec"]),"status":{"observedGeneration":1,"typeChecking":{"expressionWarnings":[]}}}
            standalone=False
            argv[argv.index("--journal")+1]=str(journal)
            upgrade_approval["approved"]=False;upgrade_approval_path.write_text(json.dumps(upgrade_approval));calls_before=len(transport.calls)
            result,output=invoke("policy-initial");self.assertEqual(result,2);self.assertIn("approval-binding",output);self.assertEqual(len(transport.calls),calls_before)
            upgrade_approval["approved"]=True;upgrade_approval_path.write_text(json.dumps(upgrade_approval))
            acl_journal=self.root/"cli-acl-rejected.json";argv[argv.index("--journal")+1]=str(acl_journal)
            transport.acl_output="not-ok\n";writes_before=sum("patch" in call for call in transport.calls)
            result,output=invoke("policy-initial");self.assertEqual(result,2);self.assertIn("database-prerequisite",output)
            self.assertEqual(sum("patch" in call for call in transport.calls),writes_before)
            transport.acl_output="ok\n";argv[argv.index("--journal")+1]=str(journal)
            result,output=invoke("policy-initial");self.assertEqual(result,0,output)
            result,output=invoke("pause");self.assertEqual(result,0,output)
            self.assertEqual(json.loads(journal.read_text())["policy"]["current"],"initial")
            result,output=invoke("resume");self.assertEqual(result,0,output)
            upgrade_approval["approvedBy"]="renewal-reviewer";upgrade_approval_path.write_text(json.dumps(upgrade_approval))
            result,output=invoke("resume");self.assertEqual(result,0,output)
            self.assertEqual(len(json.loads(journal.read_text())["approvalRenewals"]),1)
            self.assertEqual(sum("patch" in call and "validatingadmissionpolicy" not in call for call in transport.calls),1)
            standalone=True
            child=transport.apps["fresh-account-release"]
            child["spec"]["syncPolicy"]["automated"]=copy.deepcopy(plan["gitDesired"]["child"]["automated"])
            child["metadata"]["resourceVersion"]="conflict"
            conflict_journal=self.root/"cli-preflight-conflict.json"
            argv[argv.index("--journal")+1]=str(conflict_journal)
            result,output=invoke("pause");self.assertEqual(result,2);self.assertIn("pre-pause-resource-version:child",output)
            self.assertFalse(conflict_journal.exists());self.assertEqual(sum("patch" in call and "validatingadmissionpolicy" not in call for call in transport.calls),1)
            child["metadata"]["resourceVersion"]=plan["gitDesired"]["child"]["resourceVersion"]
            transport.conflict=True
            result,output=invoke("pause");self.assertEqual(result,2);self.assertIn("pause-outcome-uncertain",output)
            self.assertEqual(json.loads(conflict_journal.read_text())["state"],"uncertain")
            result,output=invoke("resume");self.assertEqual(result,2)
            self.assertEqual(sum("patch" in call and "validatingadmissionpolicy" not in call for call in transport.calls),2)
            transport.conflict=False;child["spec"]["syncPolicy"].pop("automated")
            standalone=False
            argv[argv.index("--journal")+1]=str(journal)
            before_prepare=journal.read_bytes();apps_before_prepare=copy.deepcopy(transport.apps)
            result,output=invoke("prepare-continue");self.assertEqual(result,0,output)
            apps_after_prepare=copy.deepcopy(transport.apps)
            prepare_unknown=self.root/"prepare-unknown.json";prepare_unknown.write_bytes(before_prepare)
            argv[argv.index("--journal")+1]=str(prepare_unknown);transport.apps=copy.deepcopy(apps_before_prepare);transport.account_lost_layer="fresh-release-definitions"
            result,output=invoke("prepare-continue");self.assertEqual(result,2)
            self.assertEqual(json.loads(prepare_unknown.read_text())["prepareSteps"]["parent"]["state"],"uncertain")
            writes_before=sum("patch" in call for call in transport.calls);transport.account_lost_layer=None
            result,output=invoke("resume");self.assertEqual(result,0,output)
            self.assertEqual(sum("patch" in call for call in transport.calls),writes_before)
            result,output=invoke("prepare-continue");self.assertEqual(result,0,output)
            self.assertTrue(all(step["state"]=="succeeded" for step in json.loads(prepare_unknown.read_text())["prepareSteps"].values()))
            argv[argv.index("--journal")+1]=str(journal);transport.apps=apps_after_prepare
            result,output=invoke("continue");self.assertEqual(result,0,output)
            controller=json.loads(journal.read_text())["controllerSteps"]
            self.assertTrue(all(step["state"]=="succeeded" and step["uid"] for step in controller.values()))
            result,output=invoke("resume");self.assertEqual(result,0,output)
            result,output=invoke("policy-new-paused");self.assertEqual(result,0,output)
            self.assertEqual(json.loads(journal.read_text())["policy"]["current"],"newPaused")
            before_account=journal.read_bytes();apps_before=copy.deepcopy(transport.apps);resources_before_account=copy.deepcopy(transport.resources);policy_before_account=copy.deepcopy(transport.policy)
            result,output=invoke("account-continue");self.assertEqual(result,0,output)
            self.assertTrue(all(x["state"]=="succeeded" for x in json.loads(journal.read_text())["accountSteps"].values()))
            self.assertEqual(json.loads(journal.read_text())["accountSync"]["state"],"succeeded")
            self.assertEqual(transport.apps["fresh-account-release"]["spec"],evidence["transitions"]["newPaused"]["childSpec"])
            result_entry=transport.apps["fresh-account-release"]["status"]["operationState"]["syncResult"]["resources"][0]
            result_entry["status"]="Pruned"
            result,output=invoke("resume");self.assertEqual(result,2);self.assertIn("account-sync-completed-drift",output)
            result_entry["status"]="Synced"
            observed_sync=transport.apps["fresh-account-release"]["status"]["operationState"]["operation"]["sync"]
            observed_sync["source"]={"path":"unexpected"}
            result,output=invoke("resume");self.assertEqual(result,2);self.assertIn("account-sync-completed-drift",output)
            observed_sync.pop("source")
            restart_journal=self.root/"account-unknown.json";restart_journal.write_bytes(before_account)
            argv[argv.index("--journal")+1]=str(restart_journal);transport.apps=copy.deepcopy(apps_before);transport.resources=copy.deepcopy(resources_before_account)
            transport.account_lost_layer="fresh-release-definitions"
            result,output=invoke("account-continue");self.assertEqual(result,2)
            self.assertEqual(json.loads(restart_journal.read_text())["accountSteps"]["parent"]["state"],"uncertain")
            transport.account_lost_layer=None
            result,output=invoke("resume");self.assertEqual(result,0,output)
            result,output=invoke("account-continue");self.assertEqual(result,0,output)
            self.assertTrue(all(x["state"]=="succeeded" for x in json.loads(restart_journal.read_text())["accountSteps"].values()))
            parent_patches=[call for call in transport.calls if "patch" in call and "fresh-release-definitions" in call]
            self.assertEqual(len(parent_patches),4)
            lost_sync=self.root/"account-sync-unknown.json";lost_sync.write_bytes(before_account)
            argv[argv.index("--journal")+1]=str(lost_sync);transport.apps=copy.deepcopy(apps_before);transport.resources=copy.deepcopy(resources_before_account);transport.account_sync_lost=True
            result,output=invoke("account-continue");self.assertEqual(result,2)
            self.assertEqual(json.loads(lost_sync.read_text())["accountSync"]["state"],"uncertain")
            sync_writes=lambda:sum("patch" in call and "fresh-account-release" in call and '"/operation"' in call[-1] for call in transport.calls)
            count=sync_writes();transport.account_sync_lost=False
            result,output=invoke("resume");self.assertEqual(result,0,output)
            self.assertEqual(json.loads(lost_sync.read_text())["accountSync"]["state"],"succeeded")
            self.assertEqual(sync_writes(),count)
            result,output=invoke("policy-final-automated");self.assertEqual(result,0,output)
            self.assertEqual(json.loads(lost_sync.read_text())["policy"]["current"],"finalAutomated")
            before_restore=lost_sync.read_bytes();apps_before_restore=copy.deepcopy(transport.apps)
            result,output=invoke("restore-continue");self.assertEqual(result,0,output)
            self.assertEqual(json.loads(lost_sync.read_text())["outcome"],"upgraded")
            self.assertEqual(transport.apps["fresh-account-release"]["spec"],evidence["transitions"]["finalAutomated"]["childSpec"])
            result,output=invoke("resume");self.assertEqual(result,0,output)
            restore_unknown=self.root/"restore-unknown.json";restore_unknown.write_bytes(before_restore)
            argv[argv.index("--journal")+1]=str(restore_unknown);transport.apps=copy.deepcopy(apps_before_restore);transport.account_lost_layer="fresh-release-definitions"
            result,output=invoke("restore-continue");self.assertEqual(result,2)
            self.assertEqual(json.loads(restore_unknown.read_text())["restoreSteps"]["parent"]["state"],"uncertain")
            writes_before=sum("patch" in call for call in transport.calls);transport.account_lost_layer=None
            result,output=invoke("resume");self.assertEqual(result,0,output)
            self.assertEqual(sum("patch" in call for call in transport.calls),writes_before)
            result,output=invoke("restore-continue");self.assertEqual(result,0,output)
            self.assertEqual(json.loads(restore_unknown.read_text())["outcome"],"upgraded")
            analysis=transport.resources[("AnalysisTemplate","fresh-account","account-api-policy")]
            self.assertEqual(json.loads(restore_unknown.read_text())["retainedAnalysisUID"],"analysis-uid")
            original_uid=analysis["metadata"]["uid"];analysis["metadata"]["uid"]="replacement-uid"
            result,output=invoke("policy-rollback-paused");self.assertEqual(result,2);self.assertIn("rollback-precondition",output)
            analysis["metadata"]["uid"]=original_uid
            annotation=analysis["metadata"]["annotations"].pop("argocd.argoproj.io/compare-options")
            result,output=invoke("policy-rollback-paused");self.assertEqual(result,2);self.assertIn("rollback-precondition",output)
            analysis["metadata"]["annotations"]["argocd.argoproj.io/compare-options"]=annotation
            result,output=invoke("policy-rollback-paused");self.assertEqual(result,0,output)
            before_rollback=restore_unknown.read_bytes();apps_before_rollback=copy.deepcopy(transport.apps);resources_before_rollback=copy.deepcopy(transport.resources);policy_before_rollback=copy.deepcopy(transport.policy)
            result,output=invoke("rollback-continue");self.assertEqual(result,0,output)
            rolled=json.loads(restore_unknown.read_text())
            self.assertEqual(rolled["rollbackSync"]["state"],"succeeded")
            sync_ops=[json.loads(call[-1])[-1]["value"] for call in transport.calls if "patch" in call and "fresh-account-release" in call and '"/operation"' in call[-1]]
            rollback_sync=sync_ops[-1]["sync"]
            self.assertEqual(rollback_sync["revision"],plan["source"]["oldApplicationRevision"])
            self.assertEqual({r["kind"]+"/"+r["name"] for r in rollback_sync["resources"]},old_allowed)
            self.assertFalse(rollback_sync["prune"])
            self.assertEqual(transport.resources[("AnalysisTemplate","fresh-account","account-api-policy")]["metadata"]["uid"],"analysis-uid")
            old_receipt=transport.apps["fresh-account-release"]["status"]["operationState"]["syncResult"]["resources"]
            old_receipt.append({"group":"argoproj.io","kind":"AnalysisTemplate","name":"account-api-policy","namespace":"fresh-account","status":"Synced"})
            result,output=invoke("resume");self.assertEqual(result,2);self.assertIn("rollback-sync-completed-drift",output)
            old_receipt.pop()
            self.assertTrue(all(step["state"]=="succeeded" for step in rolled["controllerRollbackSteps"].values()))
            deletes=[call for call in transport.calls if "delete" in call]
            self.assertEqual(len(deletes),2);self.assertTrue(all("--raw" in call for call in deletes))
            result,output=invoke("policy-rollback-old");self.assertEqual(result,0,output)
            before_rollback_restore=restore_unknown.read_bytes();apps_before_rollback_restore=copy.deepcopy(transport.apps);resources_before_rollback_restore=copy.deepcopy(transport.resources);policy_before_rollback_restore=copy.deepcopy(transport.policy)
            result,output=invoke("rollback-restore");self.assertEqual(result,0,output)
            self.assertEqual(json.loads(restore_unknown.read_text())["outcome"],"rolled-back")
            self.assertEqual(transport.apps["fresh-account-release"]["spec"],evidence["transitions"]["rollbackOld"]["childSpec"])
            result,output=invoke("resume");self.assertEqual(result,0,output)
            # A lost delete response is reconciled by observing the UID-bound
            # resource absent; it is never resent after restart.
            rollback_unknown=self.root/"rollback-unknown.json";rollback_unknown.write_bytes(before_rollback)
            argv[argv.index("--journal")+1]=str(rollback_unknown);transport.apps=copy.deepcopy(apps_before_rollback);transport.resources=copy.deepcopy(resources_before_rollback);transport.policy=copy.deepcopy(policy_before_rollback);transport.controller_new=True;transport.delete_lost_kind="RoleBinding"
            result,output=invoke("rollback-continue");self.assertEqual(result,2)
            self.assertEqual(json.loads(rollback_unknown.read_text())["controllerRollbackSteps"]["RoleBinding/fresh-account/account-policy-reader"]["state"],"uncertain")
            delete_count=sum("delete" in call for call in transport.calls);transport.delete_lost_kind=None
            result,output=invoke("resume");self.assertEqual(result,0,output)
            self.assertEqual(sum("delete" in call for call in transport.calls),delete_count)
            result,output=invoke("rollback-continue");self.assertEqual(result,0,output)
            result,output=invoke("policy-rollback-old");self.assertEqual(result,0,output)
            result,output=invoke("rollback-restore");self.assertEqual(result,0,output)
            self.assertEqual(json.loads(rollback_unknown.read_text())["outcome"],"rolled-back")
            # A lost response during final old desired restoration also remains
            # observation-only until an explicit continuation.
            rollback_restore_unknown=self.root/"rollback-restore-unknown.json";rollback_restore_unknown.write_bytes(before_rollback_restore)
            argv[argv.index("--journal")+1]=str(rollback_restore_unknown);transport.apps=copy.deepcopy(apps_before_rollback_restore);transport.resources=copy.deepcopy(resources_before_rollback_restore);transport.policy=copy.deepcopy(policy_before_rollback_restore);transport.controller_new=False;transport.account_lost_layer="fresh-release-definitions"
            result,output=invoke("rollback-restore");self.assertEqual(result,2)
            self.assertEqual(json.loads(rollback_restore_unknown.read_text())["rollbackRestoreSteps"]["parent"]["state"],"uncertain")
            writes_before=sum("patch" in call for call in transport.calls);transport.account_lost_layer=None
            result,output=invoke("resume");self.assertEqual(result,0,output)
            self.assertEqual(sum("patch" in call for call in transport.calls),writes_before)
            result,output=invoke("rollback-restore");self.assertEqual(result,0,output)
            self.assertEqual(json.loads(rollback_restore_unknown.read_text())["outcome"],"rolled-back")
            # A terminal failed/partial sync or a completed sync with unready
            # runtime is not replayed; an explicit rollback can recover the old
            # desired state after the exact bounded operation is observed.
            for behavior in ("failed","partial","notready"):
                failure_journal=self.root/("sync-failure-"+behavior+".json");failure_journal.write_bytes(before_account)
                argv[argv.index("--journal")+1]=str(failure_journal)
                transport.apps=copy.deepcopy(apps_before);transport.resources=copy.deepcopy(resources_before_account);transport.policy=copy.deepcopy(policy_before_account)
                transport.controller_new=True;transport.sync_behavior=behavior;transport.account_ready=True
                result,output=invoke("account-continue");self.assertEqual(result,2,output)
                self.assertEqual(json.loads(failure_journal.read_text())["accountSync"]["state"],"uncertain")
                writes_before=sync_writes()
                result,output=invoke("resume");self.assertEqual(result,0,output)
                self.assertEqual(sync_writes(),writes_before)
                self.assertEqual(json.loads(failure_journal.read_text())["accountSync"]["state"],"uncertain")
                result,output=invoke("policy-rollback-paused");self.assertEqual(result,0,output)
                self.assertEqual(json.loads(failure_journal.read_text())["rollbackFrom"],"newPaused")
                result,output=invoke("rollback-continue");self.assertEqual(result,0,output)
                result,output=invoke("policy-rollback-old");self.assertEqual(result,0,output)
                result,output=invoke("rollback-restore");self.assertEqual(result,0,output)
                self.assertEqual(json.loads(failure_journal.read_text())["outcome"],"rolled-back")
                self.assertEqual(transport.apps["fresh-account-release"]["spec"],evidence["prePause"]["childSpec"])
                if behavior=="failed":self.assertNotIn(("AnalysisTemplate","fresh-account","account-api-policy"),transport.resources)
                else:self.assertEqual(json.loads(failure_journal.read_text())["retainedAnalysisUID"],"analysis-uid")
            # If there is no terminal matching receipt, observation cannot
            # authorize rollback and the unknown operation is never resent.
            for behavior in ("unknown","active"):
                unknown_journal=self.root/("sync-no-receipt-"+behavior+".json");unknown_journal.write_bytes(before_account)
                argv[argv.index("--journal")+1]=str(unknown_journal)
                transport.apps=copy.deepcopy(apps_before);transport.resources=copy.deepcopy(resources_before_account);transport.policy=copy.deepcopy(policy_before_account)
                transport.controller_new=True;transport.sync_behavior=behavior;transport.account_ready=True
                result,output=invoke("account-continue");self.assertEqual(result,2,output)
                writes_before=sync_writes();result,output=invoke("resume")
                self.assertEqual(result,0 if behavior=="unknown" else 2,output)
                result,output=invoke("policy-rollback-paused");self.assertEqual(result,2)
                self.assertIn("rollback-precondition" if behavior=="unknown" else "operation-active",output)
                self.assertEqual(sync_writes(),writes_before)
            protected_names={name.split("/",1)[1] for name in NAMES}
            for call in transport.calls:
                if any(verb in call for verb in ("patch","create","delete")):
                    self.assertFalse(protected_names.intersection(call),"protected object was directly targeted")
            self.assertEqual(plan["protected"],self.plan["protected"])
            source_file=app/"learning/fresh-profile/workload/account.yaml";original=source_file.read_bytes()
            source_file.write_bytes(original+b"\n# source tampered\n")
            result,output=invoke("resume");self.assertEqual(result,2);self.assertIn("source-file-digest",output)
            source_file.write_bytes(original)
            (materialization_path.parent/"image-supply.lock.yaml").write_text("tampered")
            result,output=invoke("resume");self.assertEqual(result,2);self.assertIn("materialization-file-digest",output)

    def test_controller_runtime_proof_uses_owned_running_pods(self):
        from types import SimpleNamespace
        module=runner._load_contract_module("approved_live_adapter")
        class Transport:
            def __init__(t):
                t.calls=[]; t.digest=self.plan["artifacts"]["controllerImageDigest"]
            def run(t, argv, env):
                t.calls.append(argv); resource=argv[argv.index("get")+1]
                if resource=="replicasets":
                    data={"items":[{"metadata":{"uid":"rs", "ownerReferences":[{"uid":self.plan["target"]["controllerUID"],"controller":True}]}}]}
                elif resource=="pods":
                    data={"items":[{"metadata":{"ownerReferences":[{"uid":"rs","controller":True}]},"status":{"phase":"Running","conditions":[{"type":"Ready","status":"True"}],"containerStatuses":[{"name":"argo-rollouts","ready":True,"imageID":"containerd://local/controller@"+t.digest}]}}]}
                else:
                    return SimpleNamespace(returncode=0,stdout="1",stderr="")
                return SimpleNamespace(returncode=0,stdout=json.dumps(data),stderr="")
        transport=Transport(); adapter=module.ExistingUpgradeAdapter(self.plan_path,transport=transport)
        self.assertTrue(adapter._controller_converged(self.plan))
        transport.digest="sha256:"+"9"*64
        self.assertFalse(adapter._controller_converged(self.plan))
        self.assertTrue(all("get" in call for call in transport.calls))

    def test_busy_journal_and_tamper_never_mutate(self):
        lock=self.journal.with_name(self.journal.name+".lock")
        with lock.open("w") as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError,"journal-busy"):self.execute("first")
            self.assertEqual(self.fake.calls,[])
        self.execute("first")
        journal=json.loads(self.journal.read_text());journal["target"]["rolloutUID"]="tampered";self.journal.write_text(json.dumps(journal))
        with self.assertRaisesRegex(ValueError,"journal-binding"):self.execute("rerun")
        self.assertEqual(self.fake.calls,["controller","account"])

    def test_observed_controller_and_protected_shape_are_strict(self):
        self.fake.state["controllerImageDigest"]="sha256:"+"9"*64
        with self.assertRaisesRegex(ValueError,"observed-controller-digest"):self.execute("first")
        self.assertEqual(self.fake.calls,[])
        self.fake.state["controllerImageDigest"]=self.plan["artifacts"]["oldControllerImageDigest"]
        self.fake.state["protected"]["Secret/account-db-runtime"]["data"]="not-allowed"
        with self.assertRaisesRegex(ValueError,"protected-drift"):self.execute("first")
        self.assertEqual(self.fake.calls,[])

    def test_normal_cli_selects_upgrade_and_fails_closed_without_mutation_capability(self):
        self.approval["approvedAt"]="2020-01-01T00:00:00Z";self.approval["expiresAt"]="2100-01-01T00:00:00Z";self.approval_path.write_text(json.dumps(self.approval))
        args=["runner.py","--mode","existing-upgrade","--upgrade-action","first","--live-plan",str(self.plan_path),"--approval",str(self.approval_path),"--journal",str(self.journal)]
        output=io.StringIO()
        with mock.patch.object(sys,"argv",args), contextlib.redirect_stdout(output):
            self.assertEqual(runner.main(),2)
        self.assertIn("existing-upgrade-inputs-required",output.getvalue())
        self.assertFalse(self.journal.exists())

    def test_sealed_gate_binds_upgrade_identity_and_artifacts(self):
        spec={"target":self.plan["target"], "images":[{"name":"account-runtime","ref":self.plan["gitDesired"]["sourceRender"]["runtimeImage"]}], "repositories":{
            "platform-gitops":{"commit":self.plan["source"]["platformRevision"]},
            "application-manifest":{"commit":self.plan["source"]["newApplicationRevision"]}}}
        plugin={"sourceRevision":self.plan["source"]["newApplicationRevision"],
            "controllerImage":"local/controller@"+self.plan["artifacts"]["controllerImageDigest"],
            "binarySha256":self.plan["artifacts"]["binarySha256"],
            "bundleSha256":self.plan["artifacts"]["bundleSha256"]}
        sealed={"profile":{"spec":spec},"adapterSupplyContract":{"rolloutsPlugin":plugin}}
        args=(self.plan_path, self.approval_path, self.root/"gate-approval", self.root/"materialization",
              self.root/"profile", self.root/"platform", self.root/"app", self.root/"gate-plan")
        (self.root/"security").mkdir(exist_ok=True)
        (self.root/"security/admission.yaml").write_bytes((HERE/"security/admission.yaml").read_bytes())
        for relative in runner.PLUGIN_SOURCE_PATHS["platform-gitops"]:
            reviewed=self.root/"platform"/relative
            reviewed.parent.mkdir(parents=True,exist_ok=True)
            reviewed.write_bytes((HERE.parent.parent/relative).read_bytes())
        with mock.patch.object(runner,"validate_live_gate",return_value=sealed) as gate:
            adapter=runner.create_existing_upgrade_adapter(*args,now=NOW)
            self.assertFalse(adapter.supports_existing_upgrade_mutations)
            gate.assert_called_once()
            for field in ("sourceRevision","controllerImage","binarySha256","bundleSha256"):
                old=plugin[field];plugin[field]="different"
                with self.assertRaisesRegex(ValueError,"sealed-supply-mismatch"):
                    runner.create_existing_upgrade_adapter(*args,now=NOW)
                plugin[field]=old
            for name in ("existing_upgrade.py", "approved_live_adapter.py", "runner.py", "validate_profile.py", "image-supply.schema.json"):
                reviewed=self.root/"platform"/"learning/fresh-profile"/name
                reviewed.write_text("tampered reviewed source")
                with self.assertRaisesRegex(ValueError,"executing-source-mismatch"):
                    runner.create_existing_upgrade_adapter(*args,now=NOW)
                reviewed.write_bytes((HERE/name).read_bytes())
            spec["target"]=dict(spec["target"],context="kind-other")
            with self.assertRaisesRegex(ValueError,"sealed-supply-mismatch"):
                runner.create_existing_upgrade_adapter(*args,now=NOW)

    def test_live_adapter_pause_is_disabled_before_any_transport_call(self):
        module=runner._load_contract_module("approved_live_adapter")
        transport=mock.Mock()
        adapter=module.ExistingUpgradeAdapter(self.plan_path,transport=transport)
        with self.assertRaisesRegex(ValueError,"upgrade-pause-capability-unavailable"):
            runner.execute_existing_upgrade("pause",self.plan_path,self.approval_path,self.journal,adapter,now=NOW)
        self.assertFalse(self.journal.exists())
        with self.assertRaisesRegex(ValueError,"upgrade-pause-capability-unavailable"):
            adapter.pause_child_autosync(self.plan)
        transport.run.assert_not_called()

    def test_live_adapter_is_mutation_disabled(self):
        module=runner._load_contract_module("approved_live_adapter");adapter=module.ExistingUpgradeAdapter(self.plan_path)
        with self.assertRaisesRegex(ValueError,"upgrade-mutation-capability-unavailable"):
            runner.execute_existing_upgrade("first",self.plan_path,self.approval_path,self.journal,adapter,now=NOW)
        self.assertFalse(self.journal.exists())
