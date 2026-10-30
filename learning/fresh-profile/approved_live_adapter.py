#!/usr/bin/env python3
"""Approved, target-bound CLI adapter for the fresh-profile runner.

This module does not run during materialization or dry-run.  In a separately
approved live invocation it executes only argv arrays from the approval-bound
plan, never a shell string.  Every command is pinned to an executable hash and
to the fresh kubeconfig/context or kind cluster name.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
from typing import Protocol

ALLOWED_TOOLS = {"kubectl", "kind", "helm", "istioctl", "argocd", "rollouts"}
STEPS = (
    "preflight", "image-source", "cluster", "empty-node-cache", "image-import",
    "crds", "controllers", "security-boundary", "credential-bootstrap",
    "custom-resources", "database", "role-bootstrap", "migration", "application",
    "acceptance",
)
SENSITIVE_FRAGMENTS = ("--password", "--token", "--auth-token", "password=", "token=",
                       "-----begin ", "secretvalue")
REQUIRED_STEP_HEALTH = {
    "empty-node-cache": {"node-cache-empty"},
    "image-import": {"node-images-present"},
    "crds": {"conditions"},
    "controllers": {"workload-ready"},
    "database": {"argocd-operation-succeeded", "statefulset-ready", "pvc-bound"},
    "role-bootstrap": {"argocd-operation-succeeded", "job-complete"},
    "migration": {"argocd-operation-succeeded", "job-complete"},
    "application": {"argocd-application-ready", "rollout-ready"},
}
STEP_RESOURCE_CONTRACTS = {
    "crds": {
        ("apiextensions.k8s.io/v1",
         "CustomResourceDefinition/None/analysisruns.argoproj.io", "conditions"),
        ("apiextensions.k8s.io/v1",
         "CustomResourceDefinition/None/analysistemplates.argoproj.io", "conditions"),
        ("apiextensions.k8s.io/v1",
         "CustomResourceDefinition/None/clusteranalysistemplates.argoproj.io", "conditions"),
        ("apiextensions.k8s.io/v1",
         "CustomResourceDefinition/None/experiments.argoproj.io", "conditions"),
        ("apiextensions.k8s.io/v1",
         "CustomResourceDefinition/None/rollouts.argoproj.io", "conditions"),
    },
    "database": {
        ("argoproj.io/v1alpha1", "Application/fresh-control/fresh-account-release",
         "argocd-operation-succeeded"),
        ("v1", "PersistentVolumeClaim/fresh-account/account-data", "pvc-bound"),
        ("v1", "Service/fresh-account/account-postgresql", "none"),
        ("apps/v1", "StatefulSet/fresh-account/account-postgresql", "statefulset-ready"),
    },
    "role-bootstrap": {
        ("argoproj.io/v1alpha1", "Application/fresh-control/fresh-account-release",
         "argocd-operation-succeeded"),
        ("batch/v1", "Job/fresh-account/account-db-role-bootstrap", "job-complete"),
    },
    "migration": {
        ("argoproj.io/v1alpha1", "Application/fresh-control/fresh-account-release",
         "argocd-operation-succeeded"),
        ("batch/v1", "Job/fresh-account/account-migration", "job-complete"),
    },
    "application": {
        ("argoproj.io/v1alpha1", "Application/fresh-control/fresh-account-release",
         "argocd-application-ready"),
        ("argoproj.io/v1alpha1", "Rollout/fresh-account/account-api", "rollout-ready"),
        ("v1", "Service/fresh-account/account-api", "none"),
        ("networking.istio.io/v1beta1", "DestinationRule/fresh-account/account-api", "none"),
        ("networking.istio.io/v1beta1", "VirtualService/fresh-account/account-api", "none"),
    },
}
STEP_PHASES = {
    "preflight": "pre-cluster", "image-source": "pre-cluster",
    "cluster": "cluster-create",
    **{step: "post-cluster" for step in STEPS[3:]},
}
GITOPS_STEPS = {"database", "role-bootstrap", "migration", "application"}
ARGO_SYNC_RESOURCES = {
    "database": [
        ":PersistentVolumeClaim:account-data", ":Service:account-postgresql",
        "apps:StatefulSet:account-postgresql",
    ],
    "role-bootstrap": ["batch:Job:account-db-role-bootstrap"],
    "migration": ["batch:Job:account-migration"],
    "application": [
        "argoproj.io:Rollout:account-api", ":Service:account-api",
        "networking.istio.io:DestinationRule:account-api",
        "networking.istio.io:VirtualService:account-api",
    ],
}
KUBECTL_RESOURCES = {
    "application": ("argoproj.io/v1alpha1", "Application"),
    "analysistemplate": ("argoproj.io/v1alpha1", "AnalysisTemplate"),
    "configmap": ("v1", "ConfigMap"),
    "customresourcedefinition": ("apiextensions.k8s.io/v1", "CustomResourceDefinition"),
    "deployment": ("apps/v1", "Deployment"),
    "destinationrule": ("networking.istio.io/v1beta1", "DestinationRule"),
    "job": ("batch/v1", "Job"),
    "namespace": ("v1", "Namespace"),
    "node": ("v1", "Node"),
    "persistentvolumeclaim": ("v1", "PersistentVolumeClaim"),
    "pvc": ("v1", "PersistentVolumeClaim"),
    "rollout": ("argoproj.io/v1alpha1", "Rollout"),
    "role": ("rbac.authorization.k8s.io/v1", "Role"),
    "rolebinding": ("rbac.authorization.k8s.io/v1", "RoleBinding"),
    "service": ("v1", "Service"),
    "serviceaccount": ("v1", "ServiceAccount"),
    "statefulset": ("apps/v1", "StatefulSet"),
    "virtualservice": ("networking.istio.io/v1beta1", "VirtualService"),
}
HELM_RELEASES = {
    "controllers": {
        ("argo-cd", "fresh-control"),
        ("argo-rollouts", "fresh-control"),
        ("istiod", "istio-system"),
    },
}
ROLLOUTS_CHART_VERSION = "2.43.7"
ROLLOUTS_CHART_SHA256 = "166506174f4be91ed2758fccbca340e209cba35aa72ac08b5863a7b797942487"
ROLLOUTS_CRD_LIST_SHA256 = "9c4794e462b522f45077f0dabaf39df1ba3a35d29ec28787f6007ea989ddf07b"
ROLLOUTS_CONTROLLER_TAG = (
    "v1.10.1@sha256:"
    "fbcc4cf0a29da25d86230992b1dfdeadf30b6f1baa74ea7c9c98e3e580769396"
)
ROLLOUTS_CONTROLLER_IMAGE = "ghcr.io/syuhei0519/platform-lab-images:argo-rollouts-" + ROLLOUTS_CONTROLLER_TAG


class AdapterRejected(ValueError):
    pass


class Transport(Protocol):
    def run(self, argv: list[str], env: dict[str, str]): ...


class SubprocessTransport:
    """The live transport.  It deliberately uses ``shell=False``."""

    def run(self, argv: list[str], env: dict[str, str]):
        return subprocess.run(argv, shell=False, capture_output=True, text=True,
                              check=False, env=env)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _content_sha256(path: Path) -> str:
    if path.is_file() and not path.is_symlink():
        return _sha256(path)
    if path.is_dir() and not path.is_symlink():
        files = sorted(item for item in path.rglob("*") if item.is_file())
        if any(item.is_symlink() for item in files):
            raise AdapterRejected("command-content-symlink")
        manifest = {item.relative_to(path).as_posix(): _sha256(item) for item in files}
        return _mapping_digest(manifest)
    raise AdapterRejected("command-content-path")


def _command_digest(results) -> str:
    payload = [{"returncode": item.returncode, "stdout": item.stdout, "stderr": item.stderr}
               for item in results]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _mapping_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def plan_input_manifest(plan: dict) -> dict:
    """Return every host byte sequence on which a live mutation may depend."""
    inputs = {}
    for step, actions in plan["steps"].items():
        for artifact in actions.get("hostArtifacts", []):
            key = f"host:{step}:{artifact['id']}:{artifact['path']}"
            inputs[key] = {
                "sha256": artifact["sha256"], "sizeBytes": artifact["sizeBytes"],
                "sourceIdentity": artifact["sourceIdentity"],
            }
        for section in ("verify", "reconcile", "run"):
            for index, command in enumerate(actions.get(section, [])):
                if command.get("contentSha256") is not None:
                    key = f"command:{step}:{section}:{index}:{command['tool']}"
                    inputs[key] = {"sha256": command["contentSha256"],
                                   "path": _bound_content_path(command)}
    return inputs


def plan_input_set_digest(plan: dict) -> str:
    return _mapping_digest(plan_input_manifest(plan))


def supply_plan_contract(supply_lock: dict, profile_spec: dict) -> dict:
    """Translate a validated G2 lock into the only plan identities it permits."""
    profile_images = {item["name"]: item["ref"] for item in profile_spec["images"]}
    if "kind-node" not in profile_images:
        raise AdapterRejected("supply-kind-node-missing")
    if profile_images.get("argo-rollouts") != ROLLOUTS_CONTROLLER_IMAGE:
        raise AdapterRejected("supply-rollouts-controller-image")
    artifacts = {}
    for item in supply_lock["spec"]["images"]:
        source = item["source"]
        if source["kind"] != "retained-oci-archive":
            continue
        manifest = source["manifest"]
        selected_digest = manifest.get("childDigest", manifest.get("manifestDigest"))
        archive_digest = source.get("archiveSha256")
        if (not isinstance(archive_digest, str) or not archive_digest.startswith("sha256:")
                or not isinstance(selected_digest, str)):
            raise AdapterRejected("supply-archive-identity:" + item["name"])
        artifact_id = item["name"] + "-archive"
        source_identity = ":".join((
            "image-supply", item["name"], source["kind"], source["location"],
            archive_digest, selected_digest,
        ))
        artifacts[artifact_id] = {
            "sha256": archive_digest.removeprefix("sha256:"),
            "sourceIdentity": source_identity,
        }
    plugin = supply_lock["spec"].get("rolloutsPlugin")
    if plugin is not None:
        if (supply_lock["spec"]["decision"]["status"] != "approved"
                or profile_images.get("argo-rollouts-plugin") != plugin.get("controllerImage")
                or profile_spec["repositories"]["application-manifest"]["commit"] != plugin.get("sourceRevision")):
            raise AdapterRejected("plugin-supply-contract")
    return {
        "supplyMode": supply_lock["spec"].get("decision", {}).get(
            "selectedMode", "legacy-archive"),
        **({"rolloutsPlugin": plugin, "applicationRevision": profile_spec["repositories"]["application-manifest"]["commit"]} if plugin is not None else {}),
        "kindNodeRef": profile_images["kind-node"],
        "hostArtifacts": artifacts,
        "helmCharts": {
            "argo-rollouts": {
                "version": ROLLOUTS_CHART_VERSION,
                "sha256": ROLLOUTS_CHART_SHA256,
                "crdListSha256": ROLLOUTS_CRD_LIST_SHA256,
            },
        },
    }


def _bound_content_path(command: dict) -> str:
    args = command["args"]
    tool = command["tool"]
    if tool == "kubectl" and "--filename" in args:
        return args[args.index("--filename") + 1]
    if tool == "istioctl" and "--filename" in args:
        return args[args.index("--filename") + 1]
    if tool == "kind" and args[:2] == ["load", "image-archive"]:
        return args[2]
    if tool == "helm" and "--install" in args:
        return args[args.index("--install") + 2]
    raise AdapterRejected("command-content-unbound")


def _json_pointer(value, pointer: str):
    if not pointer.startswith("/"):
        raise AdapterRejected("observation-pointer")
    current = value
    for raw in pointer[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not key.isdigit() or int(key) >= len(current):
                raise AdapterRejected("observation-pointer")
            current = current[int(key)]
        elif isinstance(current, dict) and key in current:
            current = current[key]
        else:
            raise AdapterRejected("observation-pointer")
    return current


class ExistingUpgradeAdapter:
    """Read-only identity observer; mutation remains explicitly disabled.

    Secret reads request only individual metadata fields through JSONPath. No
    Secret object or value is returned, logged, or placed in the journal.
    """
    supports_existing_upgrade_mutations = False
    # Parent Git desired can restore child auto-sync; do not expose a live
    # pause until parent reconciliation and recovery are controlled.
    supports_existing_upgrade_pause = False
    supports_existing_controller_mutations = False
    supports_existing_policy_mutations = False
    supports_existing_account_mutations = False

    def __init__(self, plan_path: Path, transport: Transport | None = None, render_verifier=None):
        self.render_verifier = render_verifier
        self.upgrade_plan_sha256 = _sha256(Path(plan_path))
        self.plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        self.transport = transport or SubprocessTransport()

    def _get(self, resource, name, namespace, field):
        tool=self.plan["tool"]
        if _sha256(Path(tool["kubectlPath"])) != tool["kubectlSha256"]:
            raise AdapterRejected("existing-upgrade-tool-changed")
        args=[tool["kubectlPath"], "--kubeconfig", tool["kubeconfigPath"], "--context", self.plan["target"]["context"], "get", resource, name, "--namespace", namespace, "-o", "jsonpath={"+field+"}"]
        result=self.transport.run(args,dict(os.environ))
        if result.returncode != 0 or not isinstance(result.stdout,str) or not result.stdout.strip() or len(result.stdout)>1024:
            raise AdapterRejected("existing-upgrade-observe-failed")
        return result.stdout.strip()

    def observe(self):
        target=self.plan["target"]
        actual={"clusterName":target["clusterName"], "context":target["context"],
            "clusterUID":self._get("namespace","kube-system","kube-system",".metadata.uid"),
            "applicationUID":self._get("application","fresh-account-release","fresh-control",".metadata.uid"),
            "parentApplicationUID":self._get("application","fresh-release-definitions","fresh-control",".metadata.uid"),
            "rootApplicationUID":self._get("application","account-lightweight-root","fresh-control",".metadata.uid"),
            "controllerUID":self._get("deployment","argo-rollouts","fresh-control",".metadata.uid"),
            "rolloutUID":self._get("rollout","account-api","fresh-account",".metadata.uid")}
        resources={"Job/account-db-role-bootstrap":"job", "Job/account-migration":"job", "ConfigMap/account-role-bootstrap":"configmap", "PVC/account-data":"persistentvolumeclaim", "Secret/account-db-bootstrap":"secret", "Secret/account-db-migration":"secret", "Secret/account-db-runtime":"secret"}
        protected={}
        for identity,resource in resources.items():
            name=identity.split("/",1)[1]
            protected[identity]={"uid":self._get(resource,name,"fresh-account",".metadata.uid"),"resourceVersion":self._get(resource,name,"fresh-account",".metadata.resourceVersion")}
        completed={}
        for identity in ("Job/account-db-role-bootstrap","Job/account-migration"):
            name=identity.split("/",1)[1]
            completed[identity]=self._get("job",name,"fresh-account",'.status.conditions[?(@.type=="Complete")].status')=="True"
        image=self._get("deployment","argo-rollouts","fresh-control",'.spec.template.spec.containers[?(@.name=="argo-rollouts")].image')
        if "@sha256:" not in image:
            raise AdapterRejected("existing-upgrade-controller-image-unpinned")
        return {"target":actual, "applicationRevision":self._get("application","fresh-account-release","fresh-control",".spec.source.targetRevision"), "controllerImageDigest":"sha256:"+image.rsplit("@sha256:",1)[1], "protected":protected, "completedJobs":completed}

    def verify_database_prerequisite(self):
        """Read ACL catalog booleans only; never return credentials or rows."""
        sql = """SELECT CASE WHEN
          has_table_privilege('account_runtime','account.accounts','SELECT') AND
          has_table_privilege('account_runtime','account.accounts','INSERT') AND
          has_table_privilege('account_runtime','account.accounts','UPDATE') AND
          has_table_privilege('account_runtime','account.accounts','DELETE') AND
          has_table_privilege('account_runtime','account.schema_migrations','SELECT') AND
          NOT has_table_privilege('account_runtime','account.schema_migrations','INSERT') AND
          NOT has_table_privilege('account_runtime','account.schema_migrations','UPDATE') AND
          NOT has_table_privilege('account_runtime','account.schema_migrations','DELETE') AND
          pg_get_serial_sequence('account.accounts','id') IS NOT NULL AND
          NOT COALESCE(has_sequence_privilege('account_runtime',pg_get_serial_sequence('account.accounts','id'),'USAGE'),false) AND
          NOT COALESCE(has_sequence_privilege('account_runtime',pg_get_serial_sequence('account.accounts','id'),'SELECT'),false) AND
          NOT COALESCE(has_sequence_privilege('account_runtime',pg_get_serial_sequence('account.accounts','id'),'UPDATE'),false) AND
          NOT EXISTS (SELECT 1 FROM pg_default_acl d, LATERAL aclexplode(d.defaclacl) a
              WHERE d.defaclrole='account_migrator'::regrole AND d.defaclobjtype IN ('r','S')
                AND d.defaclnamespace IN (0,'account'::regnamespace)
                AND a.grantee IN (0,'account_runtime'::regrole))
          THEN 'ok' ELSE 'not-ok' END"""
        tool=self.plan["tool"]
        if _sha256(Path(tool["kubectlPath"])) != tool["kubectlSha256"]:
            raise AdapterRejected("existing-upgrade-tool-changed")
        args=[tool["kubectlPath"],"--kubeconfig",tool["kubeconfigPath"],"--context",self.plan["target"]["context"],
              "exec","account-postgresql-0","--namespace","fresh-account","--container","postgresql","--",
              "sh","-c",'PGCONNECT_TIMEOUT=5 PGOPTIONS="-c statement_timeout=5000" PGPASSWORD="$POSTGRES_PASSWORD" exec psql -X -A -t -v ON_ERROR_STOP=1 -h 127.0.0.1 -U postgres -d account -c "$1"',"sh",sql]
        result=self.transport.run(args,dict(os.environ))
        if result.returncode != 0 or not isinstance(result.stdout,str) or result.stdout.strip()!="ok":
            raise AdapterRejected("existing-upgrade-database-prerequisite")
        return True

    def verify_reviewed_render(self, plan):
        if self.render_verifier is None:
            raise AdapterRejected("git-render-verifier-unavailable")
        self.render_evidence = self.render_verifier(plan)
        return bool(self.render_evidence)

    def _pre_pause_spec(self, layer, *, paused=False):
        evidence=getattr(self,"render_evidence",None)
        if not isinstance(evidence,dict) or not isinstance(evidence.get("prePause"),dict):raise AdapterRejected("pre-pause-evidence-unavailable")
        spec=json.loads(json.dumps(evidence["prePause"][layer+"Spec"]))
        if layer=="child" and paused:spec.get("syncPolicy",{}).pop("automated",None)
        return spec

    def verify_pre_pause_chain(self, plan, *, paused=False, completed=frozenset(), uncertain=None, initial_versions=False):
        for layer in ("root","parent","child"):
            obj,_,_=self._read_transition_application(plan,layer)
            before=self._pre_pause_spec(layer,paused=paused);after=self._transition_spec(layer,"initial")
            allowed=[after] if layer in completed else [before]
            if layer==uncertain:allowed=[before,after]
            if obj["spec"] not in allowed:raise AdapterRejected("pre-pause-chain-drift:"+layer)
            if initial_versions:
                item=plan["gitDesired"][layer]
                if obj["metadata"].get("resourceVersion")!=item["resourceVersion"]:raise AdapterRejected("pre-pause-resource-version:"+layer)
        return True

    def apply_prepare_transition(self, plan, layer):
        if self.supports_existing_account_mutations is not True:raise AdapterRejected("account-mutation-capability-unavailable")
        if plan!=self.plan:raise AdapterRejected("existing-upgrade-plan-mismatch")
        obj,base,name=self._read_transition_application(plan,layer)
        before=self._pre_pause_spec(layer,paused=True);after=self._transition_spec(layer,"initial")
        if obj["spec"]!=before:raise AdapterRejected("prepare-previous-drift:"+layer)
        if before==after:return
        metadata=obj["metadata"];version=metadata.get("resourceVersion")
        if not isinstance(version,str) or not version:raise AdapterRejected("account-transition-resource-version")
        patch=[{"op":"test","path":"/metadata/uid","value":metadata["uid"]},{"op":"test","path":"/metadata/resourceVersion","value":version},{"op":"test","path":"/spec","value":before},{"op":"replace","path":"/spec","value":after}]
        result=self.transport.run(base+["patch","application",name,"--namespace","fresh-control","--type=json","--patch",json.dumps(patch,separators=(",",":"))],dict(os.environ))
        if result.returncode!=0:raise AdapterRejected("prepare-outcome-uncertain:"+layer)

    def verify_prepare_transition(self, plan, layer):
        obj,_,_=self._read_transition_application(plan,layer)
        return obj["spec"]==self._transition_spec(layer,"initial")

    def verify_initial_git_desired(self, plan, *, paused=False):
        for layer in ("root","parent","child"):
            obj,_,_=self._read_transition_application(plan,layer)
            expected=self._transition_spec(layer,"initial")
            if obj["spec"]!=expected:raise AdapterRejected("existing-upgrade-rendered-desired-mismatch:"+layer)
        return True

    def observe_child_autosync_paused(self, plan):
        self.verify_initial_git_desired(plan, paused=True)
        tool=plan["tool"]
        if _sha256(Path(tool["kubectlPath"])) != tool["kubectlSha256"]:
            raise AdapterRejected("existing-upgrade-tool-changed")
        args=[tool["kubectlPath"],"--kubeconfig",tool["kubeconfigPath"],"--context",plan["target"]["context"],"get","application","fresh-account-release","--namespace","fresh-control","-o","json"]
        result=self.transport.run(args,dict(os.environ))
        if result.returncode != 0 or not isinstance(result.stdout,str) or len(result.stdout)>1_000_000:
            raise AdapterRejected("existing-upgrade-pause-observe-failed")
        try: application=json.loads(result.stdout)
        except ValueError as error: raise AdapterRejected("existing-upgrade-pause-observe-failed") from error
        metadata=application.get("metadata",{});spec=application.get("spec",{})
        if metadata.get("uid") != plan["gitDesired"]["child"]["uid"] or spec.get("source",{}).get("targetRevision") != plan["gitDesired"]["child"]["oldRevision"]:
            raise AdapterRejected("existing-upgrade-pause-observed-identity")
        return "automated" not in spec.get("syncPolicy",{})

    def pause_child_autosync(self, plan):
        """Issue one bounded CAS patch; the journal must record intent first.

        The pause journal records intent before this call. A nonzero result or
        transport exception has an uncertain outcome.
        """
        if self.supports_existing_upgrade_pause is not True:
            raise AdapterRejected("upgrade-pause-capability-unavailable")
        if getattr(self, "_pause_attempted", False):
            raise AdapterRejected("existing-upgrade-pause-replay-forbidden")
        if plan != self.plan:
            raise AdapterRejected("existing-upgrade-plan-mismatch")
        app = plan["gitDesired"]["child"]
        if app["automated"] is None:
            raise AdapterRejected("existing-upgrade-autosync-already-disabled")
        tool = plan["tool"]
        if _sha256(Path(tool["kubectlPath"])) != tool["kubectlSha256"]:
            raise AdapterRejected("existing-upgrade-tool-changed")
        patch = [
            {"op":"test", "path":"/metadata/uid", "value":app["uid"]},
            {"op":"test", "path":"/metadata/resourceVersion", "value":app["resourceVersion"]},
            {"op":"test", "path":"/spec/source/targetRevision", "value":app["oldRevision"]},
            {"op":"test", "path":"/spec/syncPolicy/automated", "value":app["automated"]},
            {"op":"remove", "path":"/spec/syncPolicy/automated"},
        ]
        argv = [tool["kubectlPath"], "--kubeconfig", tool["kubeconfigPath"],
                "--context", plan["target"]["context"], "patch", "application",
                "fresh-account-release", "--namespace", "fresh-control", "--type=json",
                "--patch", json.dumps(patch, separators=(",", ":"))]
        self._pause_attempted = True
        result = self.transport.run(argv, dict(os.environ))
        if result.returncode != 0:
            raise AdapterRejected("existing-upgrade-pause-outcome-uncertain")

    def _json_list(self, resource, namespace):
        tool = self.plan["tool"]
        if _sha256(Path(tool["kubectlPath"])) != tool["kubectlSha256"]:
            raise AdapterRejected("existing-upgrade-tool-changed")
        args = [tool["kubectlPath"], "--kubeconfig", tool["kubeconfigPath"],
                "--context", self.plan["target"]["context"], "get", resource,
                "--namespace", namespace, "-o", "json"]
        result = self.transport.run(args, dict(os.environ))
        if result.returncode != 0 or not isinstance(result.stdout, str) or len(result.stdout) > 4_000_000:
            raise AdapterRejected("existing-upgrade-runtime-observe-failed")
        try:
            value = json.loads(result.stdout)
        except ValueError as error:
            raise AdapterRejected("existing-upgrade-runtime-observe-failed") from error
        if not isinstance(value, dict) or not isinstance(value.get("items"), list):
            raise AdapterRejected("existing-upgrade-runtime-observe-failed")
        return value["items"]

    def _controller_converged(self, plan, *, digest=None):
        # Follow owner UIDs, rather than labels, so an unrelated Pod cannot
        # satisfy the controller runtime proof. Every extant owned Pod must be
        # ready and run the approved digest, including terminating Pods.
        namespace = "fresh-control"
        replicasets = self._json_list("replicasets", namespace)
        pods = self._json_list("pods", namespace)
        uid = plan["target"]["controllerUID"]
        owned = {r.get("metadata", {}).get("uid") for r in replicasets
                 if any(o.get("uid") == uid and o.get("controller") is True
                        for o in r.get("metadata", {}).get("ownerReferences", []))}
        owned.discard(None)
        selected = [p for p in pods if any(o.get("uid") in owned and o.get("controller") is True
                    for o in p.get("metadata", {}).get("ownerReferences", []))]
        desired = self._get("deployment", "argo-rollouts", namespace, ".spec.replicas")
        observed = self._get("deployment", "argo-rollouts", namespace, ".status.observedGeneration")
        generation = self._get("deployment", "argo-rollouts", namespace, ".metadata.generation")
        ready = self._get("deployment", "argo-rollouts", namespace, ".status.readyReplicas")
        updated = self._get("deployment", "argo-rollouts", namespace, ".status.updatedReplicas")
        if not all(x.isdecimal() for x in (desired, observed, generation, ready, updated)):
            return False
        count = int(desired)
        if count < 1 or int(observed) < int(generation) or int(ready) != count or int(updated) != count or len(selected) != count:
            return False
        digest = digest or plan["artifacts"]["controllerImageDigest"]
        for pod in selected:
            if pod.get("metadata", {}).get("deletionTimestamp") or pod.get("status", {}).get("phase") != "Running":
                return False
            containers = pod.get("status", {}).get("containerStatuses", [])
            matches = [c for c in containers if c.get("name") == "argo-rollouts"]
            if len(matches) != 1 or matches[0].get("ready") is not True or not matches[0].get("imageID", "").endswith("@" + digest):
                return False
            if not any(c.get("type") == "Ready" and c.get("status") == "True" for c in pod.get("status", {}).get("conditions", [])):
                return False
        return True

    def _read_transition_application(self, plan, layer, *, allow_active=False):
        names={"root":("account-lightweight-root","rootApplicationUID"),"parent":("fresh-release-definitions","parentApplicationUID"),"child":("fresh-account-release","applicationUID")}
        if layer not in names:raise AdapterRejected("account-transition-layer")
        name,uid_key=names[layer];tool=plan["tool"]
        if _sha256(Path(tool["kubectlPath"]))!=tool["kubectlSha256"]:raise AdapterRejected("existing-upgrade-tool-changed")
        base=[tool["kubectlPath"],"--kubeconfig",tool["kubeconfigPath"],"--context",plan["target"]["context"]]
        result=self.transport.run(base+["get","application",name,"--namespace","fresh-control","-o","json"],dict(os.environ))
        if result.returncode!=0 or not isinstance(result.stdout,str) or len(result.stdout)>1_000_000:raise AdapterRejected("account-transition-observe-failed")
        try:obj=json.loads(result.stdout)
        except ValueError as error:raise AdapterRejected("account-transition-observe-failed") from error
        if not isinstance(obj,dict) or not isinstance(obj.get("metadata"),dict) or not isinstance(obj.get("spec"),dict):raise AdapterRejected("account-transition-observe-failed")
        if obj["metadata"].get("uid")!=plan["target"][uid_key]:raise AdapterRejected("account-transition-uid:"+layer)
        phase=obj.get("status",{}).get("operationState",{}).get("phase")
        if not allow_active and (obj.get("operation") is not None or phase in {"Running","Pending","Terminating","Progressing","Waiting"}):raise AdapterRejected("account-transition-operation-active:"+layer)
        return obj,base,name

    def _transition_spec(self, layer, stage):
        evidence=getattr(self,"render_evidence",None)
        if not isinstance(evidence,dict):raise AdapterRejected("git-render-evidence-unavailable")
        state=evidence if stage in {"initial","rollbackPaused"} else evidence.get("transitions",{}).get(stage)
        if not isinstance(state,dict) or not isinstance(state.get(layer+"Spec"),dict):raise AdapterRejected("account-transition-evidence-unavailable")
        return state[layer+"Spec"]

    def verify_desired_chain(self, plan, completed, uncertain=None, *, before_stage="initial", after_stage="newPaused"):
        if (before_stage,after_stage) not in {("initial","newPaused"),("newPaused","finalAutomated"),("newPaused","initial"),("finalAutomated","initial"),("initial","rollbackOld")}:raise AdapterRejected("account-transition-stage")
        for layer in ("root","parent","child"):
            obj,_,_=self._read_transition_application(plan,layer)
            before=self._transition_spec(layer,before_stage);after=self._transition_spec(layer,after_stage)
            allowed=[after] if layer in completed else [before]
            if layer==uncertain:allowed=[before,after]
            if obj["spec"] not in allowed:raise AdapterRejected("account-transition-chain-drift:"+layer)
        return True

    def apply_desired_transition(self, plan, layer, *, before_stage="initial", after_stage="newPaused"):
        if (before_stage,after_stage) not in {("initial","newPaused"),("newPaused","finalAutomated"),("newPaused","initial"),("finalAutomated","initial"),("initial","rollbackOld")}:raise AdapterRejected("account-transition-stage")
        if self.supports_existing_account_mutations is not True:raise AdapterRejected("account-mutation-capability-unavailable")
        if plan!=self.plan:raise AdapterRejected("existing-upgrade-plan-mismatch")
        obj,base,name=self._read_transition_application(plan,layer)
        before=self._transition_spec(layer,before_stage);after=self._transition_spec(layer,after_stage)
        if obj["spec"]!=before or before==after:raise AdapterRejected("account-transition-previous-drift:"+layer)
        metadata=obj["metadata"];version=metadata.get("resourceVersion")
        if not isinstance(version,str) or not version:raise AdapterRejected("account-transition-resource-version")
        patch=[{"op":"test","path":"/metadata/uid","value":metadata["uid"]},
               {"op":"test","path":"/metadata/resourceVersion","value":version},
               {"op":"test","path":"/spec","value":before},
               {"op":"replace","path":"/spec","value":after}]
        result=self.transport.run(base+["patch","application",name,"--namespace","fresh-control","--type=json","--patch",json.dumps(patch,separators=(",",":"))],dict(os.environ))
        if result.returncode!=0:raise AdapterRejected("account-transition-outcome-uncertain:"+layer)

    def verify_desired_transition(self, plan, layer, *, stage="newPaused"):
        if stage not in {"initial","newPaused","finalAutomated","rollbackOld"}:raise AdapterRejected("account-transition-stage")
        obj,_,_=self._read_transition_application(plan,layer)
        return obj["spec"]==self._transition_spec(layer,stage)

    def _account_sync_resources(self, plan, *, stage="newPaused"):
        if stage not in {"newPaused","initial"}:raise AdapterRejected("account-sync-stage")
        groups={"Rollout":"argoproj.io","Service":"","DestinationRule":"networking.istio.io","VirtualService":"networking.istio.io","AnalysisTemplate":"argoproj.io"}
        expected={"Rollout/account-api","Service/account-api","DestinationRule/account-api","VirtualService/account-api","AnalysisTemplate/account-api-policy"}
        sync=plan["gitDesired"]["limitedSync"]
        if sync.get("prune") is not False or set(sync.get("resources",[]))!=expected or len(sync["resources"])!=len(expected):raise AdapterRejected("limited-sync-scope")
        if stage=="initial":expected=expected-{"AnalysisTemplate/account-api-policy"}
        result=[]
        for identity in sorted(expected):
            kind,name=identity.split("/");result.append({"group":groups[kind],"kind":kind,"name":name,"namespace":"fresh-account"})
        return result

    def _read_analysis_template(self, plan):
        tool=plan["tool"]
        if _sha256(Path(tool["kubectlPath"]))!=tool["kubectlSha256"]:raise AdapterRejected("existing-upgrade-tool-changed")
        args=[tool["kubectlPath"],"--kubeconfig",tool["kubeconfigPath"],"--context",plan["target"]["context"],"get","analysistemplate","account-api-policy","--namespace","fresh-account","--ignore-not-found","-o","json"]
        result=self.transport.run(args,dict(os.environ))
        if result.returncode!=0 or not isinstance(result.stdout,str) or len(result.stdout)>1_000_000:raise AdapterRejected("analysis-template-observe-failed")
        if not result.stdout.strip():return None
        try:obj=json.loads(result.stdout)
        except ValueError as error:raise AdapterRejected("analysis-template-observe-failed") from error
        if not isinstance(obj,dict) or not isinstance(obj.get("metadata"),dict):raise AdapterRejected("analysis-template-observe-failed")
        return obj

    def verify_analysis_absent(self, plan):
        return self._read_analysis_template(plan) is None

    def retained_analysis_template_uid(self, plan):
        obj=self._read_analysis_template(plan)
        if obj is None:return None
        meta=obj["metadata"];annotations=meta.get("annotations",{});uid=meta.get("uid")
        allowed_tracking={"fresh-account-release:argoproj.io/AnalysisTemplate:fresh-account/account-api-policy", "fresh-control/fresh-account-release:argoproj.io/AnalysisTemplate:fresh-account/account-api-policy"}
        if (obj.get("apiVersion")!="argoproj.io/v1alpha1" or obj.get("kind")!="AnalysisTemplate" or meta.get("name")!="account-api-policy" or meta.get("namespace")!="fresh-account" or meta.get("deletionTimestamp") or not isinstance(uid,str) or not uid or not isinstance(annotations,dict) or annotations.get("argocd.argoproj.io/compare-options")!="IgnoreExtraneous" or annotations.get("argocd.argoproj.io/sync-options")!="Prune=false,Delete=false" or annotations.get("argocd.argoproj.io/tracking-id") not in allowed_tracking):return None
        return uid

    def verify_retained_analysis_template(self, plan, expected_uid):
        return isinstance(expected_uid,str) and bool(expected_uid) and self.retained_analysis_template_uid(plan)==expected_uid

    def start_account_sync(self, plan, nonce, *, stage="newPaused"):
        if stage not in {"newPaused","initial"}:raise AdapterRejected("account-sync-stage")
        if self.supports_existing_account_mutations is not True:raise AdapterRejected("account-mutation-capability-unavailable")
        if plan!=self.plan or not isinstance(nonce,str) or not re.fullmatch(r"[0-9a-f]{32}",nonce):raise AdapterRejected("account-sync-binding")
        if stage=="newPaused" and not self.verify_analysis_absent(plan):raise AdapterRejected("analysis-template-not-absent")
        obj,base,name=self._read_transition_application(plan,"child")
        spec=self._transition_spec("child",stage)
        if obj["spec"]!=spec:raise AdapterRejected("account-sync-desired-drift")
        metadata=obj["metadata"];version=metadata.get("resourceVersion")
        if not isinstance(version,str) or not version:raise AdapterRejected("account-transition-resource-version")
        operation={"sync":{"revision":plan["source"]["newApplicationRevision" if stage=="newPaused" else "oldApplicationRevision"],"prune":False,"resources":self._account_sync_resources(plan,stage=stage),"syncOptions":["ApplyOutOfSyncOnly=true","Prune=false","RespectIgnoreDifferences=true"]},"info":[{"name":"platform.lab/existing-upgrade","value":nonce}]}
        patch=[{"op":"test","path":"/metadata/uid","value":metadata["uid"]},
               {"op":"test","path":"/metadata/resourceVersion","value":version},
               {"op":"test","path":"/spec","value":spec},
               {"op":"add","path":"/operation","value":operation}]
        result=self.transport.run(base+["patch","application",name,"--namespace","fresh-control","--type=json","--patch",json.dumps(patch,separators=(",",":"))],dict(os.environ))
        if result.returncode!=0:raise AdapterRejected("account-sync-outcome-uncertain")

    def _account_runtime_converged(self, plan):
        namespace="fresh-account";uid=plan["target"]["rolloutUID"]
        replicasets=self._json_list("replicasets",namespace);pods=self._json_list("pods",namespace)
        owned={r.get("metadata",{}).get("uid") for r in replicasets if any(o.get("uid")==uid and o.get("controller") is True for o in r.get("metadata",{}).get("ownerReferences",[]))};owned.discard(None)
        selected=[p for p in pods if any(o.get("uid") in owned and o.get("controller") is True for o in p.get("metadata",{}).get("ownerReferences",[]))]
        desired=self._get("rollout","account-api",namespace,".spec.replicas")
        generation=self._get("rollout","account-api",namespace,".metadata.generation")
        observed=self._get("rollout","account-api",namespace,".status.observedGeneration")
        ready=self._get("rollout","account-api",namespace,".status.readyReplicas")
        updated=self._get("rollout","account-api",namespace,".status.updatedReplicas")
        if not all(x.isdecimal() for x in (desired,generation,observed,ready,updated)):return False
        count=int(desired)
        if count<1 or int(observed)<int(generation) or int(ready)!=count or int(updated)!=count or len(selected)!=count:return False
        digest=plan["gitDesired"]["sourceRender"]["runtimeImage"].rsplit("@",1)[1]
        for pod in selected:
            if pod.get("metadata",{}).get("deletionTimestamp") or pod.get("status",{}).get("phase")!="Running":return False
            containers=[x for x in pod.get("status",{}).get("containerStatuses",[]) if x.get("name")=="api"]
            if len(containers)!=1 or containers[0].get("ready") is not True or not containers[0].get("imageID","").endswith("@"+digest):return False
            if not any(x.get("type")=="Ready" and x.get("status")=="True" for x in pod.get("status",{}).get("conditions",[])):return False
        return True

    def verify_account_sync_terminal(self, plan, nonce, *, stage="newPaused"):
        """Prove a particular bounded sync ended; no readiness is inferred."""
        if stage not in {"newPaused","initial"}:raise AdapterRejected("account-sync-stage")
        obj,_,_=self._read_transition_application(plan,"child",allow_active=True)
        permitted=("newPaused","finalAutomated") if stage=="newPaused" else ("initial","rollbackOld")
        if obj["spec"] not in tuple(self._transition_spec("child",name) for name in permitted) or obj.get("operation") is not None:return False
        status=obj.get("status",{}).get("operationState",{})
        if not isinstance(status,dict) or status.get("phase") not in {"Succeeded","Failed","Error"} or not status.get("finishedAt"):return False
        operation=status.get("operation",{});sync=operation.get("sync",{})
        if not isinstance(sync,dict) or set(sync)-{"revision","prune","resources","syncOptions","dryRun"}:return False
        if {"name":"platform.lab/existing-upgrade","value":nonce} not in operation.get("info",[]):return False
        revision=plan["source"]["newApplicationRevision" if stage=="newPaused" else "oldApplicationRevision"]
        if sync.get("revision")!=revision or sync.get("prune",False) is not False or sync.get("dryRun",False) is not False:return False
        expected=self._account_sync_resources(plan,stage=stage);requested=sync.get("resources")
        if not isinstance(requested,list) or len(requested)!=len(expected) or any(not isinstance(item,dict) for item in requested):return False
        scope={(x.get("group",""),x.get("kind"),x.get("name"),x.get("namespace")) for x in requested}
        expected_scope={(x["group"],x["kind"],x["name"],x["namespace"]) for x in expected}
        if scope!=expected_scope or sync.get("syncOptions")!=["ApplyOutOfSyncOnly=true","Prune=false","RespectIgnoreDifferences=true"]:return False
        result=status.get("syncResult")
        if result is not None:
            if not isinstance(result,dict) or result.get("revision")!=revision:return False
            resources=result.get("resources",[])
            if not isinstance(resources,list):return False
            seen=set()
            for item in resources:
                if not isinstance(item,dict):return False
                identity=(item.get("group",""),item.get("kind"),item.get("name"),item.get("namespace"))
                if identity not in expected_scope or identity in seen or item.get("status") not in {"Synced","SyncFailed"}:return False
                seen.add(identity)
        return True

    def verify_terminal_analysis_applied(self, plan, nonce):
        if not self.verify_account_sync_terminal(plan,nonce):return False
        obj,_,_=self._read_transition_application(plan,"child",allow_active=True)
        resources=obj.get("status",{}).get("operationState",{}).get("syncResult",{}).get("resources",[])
        return any(isinstance(item,dict) and item.get("group")=="argoproj.io" and item.get("kind")=="AnalysisTemplate" and item.get("name")=="account-api-policy" and item.get("namespace")=="fresh-account" and item.get("status")=="Synced" for item in resources)

    def verify_analysis_retention_state(self, plan, expected_uid):
        return self.verify_analysis_absent(plan) if expected_uid is None else self.verify_retained_analysis_template(plan,expected_uid)

    def verify_account_sync(self, plan, nonce, *, stage="newPaused"):
        if stage not in {"newPaused","initial"}:raise AdapterRejected("account-sync-stage")
        if not self.verify_account_sync_terminal(plan,nonce,stage=stage):return False
        obj,_,_=self._read_transition_application(plan,"child",allow_active=True)
        permitted=("newPaused","finalAutomated") if stage=="newPaused" else ("initial","rollbackOld")
        if obj["spec"] not in tuple(self._transition_spec("child",name) for name in permitted) or obj.get("operation") is not None:return False
        revision=plan["source"]["newApplicationRevision" if stage=="newPaused" else "oldApplicationRevision"]
        status=obj.get("status",{}).get("operationState",{})
        if not isinstance(status,dict) or status.get("phase")!="Succeeded" or not status.get("finishedAt"):return False
        operation=status.get("operation",{});sync=operation.get("sync",{});result=status.get("syncResult",{})
        if {"name":"platform.lab/existing-upgrade","value":nonce} not in operation.get("info",[]):return False
        requested=sync.get("resources")
        if not isinstance(requested,list) or any(not isinstance(x,dict) for x in requested):return False
        scope={(x.get("group",""),x.get("kind"),x.get("name"),x.get("namespace")) for x in requested}
        expected_scope={(x["group"],x["kind"],x["name"],x["namespace"]) for x in self._account_sync_resources(plan,stage=stage)}
        if sync.get("revision")!=revision or sync.get("prune",False) is not False or scope!=expected_scope or len(requested)!=len(expected_scope):return False
        if result.get("revision")!=revision:return False
        resources=result.get("resources")
        if not isinstance(resources,list):return False
        actual={(x.get("group", ""),x.get("kind"),x.get("name"),x.get("namespace")) for x in resources if isinstance(x,dict) and x.get("status")=="Synced"}
        expected={(x["group"],x["kind"],x["name"],x["namespace"]) for x in self._account_sync_resources(plan,stage=stage)}
        if actual!=expected or len(resources)!=len(expected):return False
        return self._account_runtime_converged(plan)

    def verify_full_account_synced(self, plan, *, stage="newPaused"):
        if stage not in {"newPaused","initial"}:raise AdapterRejected("account-sync-stage")
        obj,_,_=self._read_transition_application(plan,"child")
        spec=obj["spec"];status=obj.get("status",{}).get("sync",{})
        compared=status.get("comparedTo",{}) if isinstance(status,dict) else {}
        return (isinstance(status,dict) and status.get("status")=="Synced"
                and status.get("revision")==plan["source"]["newApplicationRevision" if stage=="newPaused" else "oldApplicationRevision"]
                and compared.get("source")==spec.get("source") and compared.get("destination")==spec.get("destination"))

    def verify_ready_to_restore(self, plan, nonce, *, stage="newPaused"):
        if stage not in {"newPaused","initial"}:raise AdapterRejected("account-sync-stage")
        if not self.verify_account_sync(plan,nonce,stage=stage):return False
        obj,_,_=self._read_transition_application(plan,"child")
        spec=self._transition_spec("child",stage);status=obj.get("status",{}).get("sync",{})
        compared=status.get("comparedTo",{}) if isinstance(status,dict) else {}
        final=self._transition_spec("child","finalAutomated" if stage=="newPaused" else "rollbackOld").get("syncPolicy",{})
        return (isinstance(status,dict) and status.get("status")=="Synced"
                and status.get("revision")==plan["source"]["newApplicationRevision" if stage=="newPaused" else "oldApplicationRevision"]
                and compared.get("source")==spec.get("source") and compared.get("destination")==spec.get("destination")
                and final.get("automated")=={"prune":False,"selfHeal":True}
                and (stage=="initial" or ("Prune=false" in final.get("syncOptions",[]) and "ApplyOutOfSyncOnly=true" in final.get("syncOptions",[]))))

    def _read_security_policy(self, plan):
        tool=plan["tool"]
        if _sha256(Path(tool["kubectlPath"]))!=tool["kubectlSha256"]: raise AdapterRejected("existing-upgrade-tool-changed")
        base=[tool["kubectlPath"],"--kubeconfig",tool["kubeconfigPath"],"--context",plan["target"]["context"]]
        result=self.transport.run(base+["get","validatingadmissionpolicy","fresh-application-boundary","-o","json"],dict(os.environ))
        if result.returncode!=0 or not isinstance(result.stdout,str) or len(result.stdout)>2_000_000: raise AdapterRejected("security-policy-observe-failed")
        try: policy=json.loads(result.stdout)
        except ValueError as error: raise AdapterRejected("security-policy-observe-failed") from error
        if not isinstance(policy,dict) or not isinstance(policy.get("metadata"),dict) or not isinstance(policy.get("spec"),dict): raise AdapterRejected("security-policy-observe-failed")
        if policy["metadata"].get("uid")!=plan["securityPolicy"]["uid"]:raise AdapterRejected("security-policy-uid-drift")
        return policy,base

    def _desired_security_policy(self, plan, stage):
        evidence=getattr(self,"render_evidence",None)
        if not isinstance(evidence,dict): raise AdapterRejected("git-render-evidence-unavailable")
        state=evidence if stage in {"initial","rollbackPaused"} else evidence.get("transitions",{}).get(stage)
        if not isinstance(state,dict) or not isinstance(state.get("policySpec"),dict):raise AdapterRejected("security-policy-evidence-unavailable")
        spec=state["policySpec"]
        digest=hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
        if digest!=plan["securityPolicy"]["desiredHashes"]["initial" if stage=="rollbackPaused" else stage]:raise AdapterRejected("security-policy-digest")
        return spec

    def apply_security_policy(self, plan, stage, previous_spec, *, initial=False):
        if self.supports_existing_policy_mutations is not True: raise AdapterRejected("security-policy-capability-unavailable")
        if plan!=self.plan: raise AdapterRejected("existing-upgrade-plan-mismatch")
        desired=self._desired_security_policy(plan,stage);policy,base=self._read_security_policy(plan)
        metadata=policy["metadata"];version=metadata.get("resourceVersion")
        status=policy.get("status",{})
        if (not isinstance(status,dict) or not isinstance(metadata.get("generation"),int)
                or status.get("observedGeneration")!=metadata["generation"]
                or status.get("typeChecking",{}).get("expressionWarnings",[])):
            raise AdapterRejected("security-policy-not-ready")
        if not isinstance(version,str) or not version or (initial and version!=plan["securityPolicy"]["resourceVersion"]) or policy["spec"]!=previous_spec:
            raise AdapterRejected("security-policy-previous-drift")
        patch=[{"op":"test","path":"/metadata/uid","value":plan["securityPolicy"]["uid"]},
               {"op":"test","path":"/metadata/resourceVersion","value":version},
               {"op":"test","path":"/spec","value":previous_spec},
               {"op":"replace","path":"/spec","value":desired}]
        result=self.transport.run(base+["patch","validatingadmissionpolicy","fresh-application-boundary","--type=json","--patch",json.dumps(patch,separators=(",",":"))],dict(os.environ))
        if result.returncode!=0:raise AdapterRejected("security-policy-outcome-uncertain")

    def verify_security_policy(self, plan, stage):
        desired=self._desired_security_policy(plan,stage);policy,_=self._read_security_policy(plan)
        metadata=policy["metadata"];status=policy.get("status",{})
        if not isinstance(status,dict) or not isinstance(metadata.get("generation"),int) or status.get("observedGeneration")!=metadata["generation"]:return False
        warnings=status.get("typeChecking",{}).get("expressionWarnings",[])
        if not isinstance(warnings,list) or warnings:return False
        return policy["spec"]==desired

    def apply_controller_resource(self, identity, plan):
        """One CAS or create-only operation; caller must journal intent first."""
        if self.supports_existing_controller_mutations is not True:
            raise AdapterRejected("controller-mutation-capability-unavailable")
        if plan != self.plan:
            raise AdapterRejected("existing-upgrade-plan-mismatch")
        allowed={"Deployment/fresh-control/argo-rollouts":("deployment",("spec",)),
                 "ConfigMap/fresh-control/argo-rollouts-config":("configmap",("data",)),
                 "Role/fresh-account/account-policy-reader":("role",("rules",)),
                 "RoleBinding/fresh-account/account-policy-reader":("rolebinding",("roleRef","subjects"))}
        if identity not in allowed: raise AdapterRejected("controller-resource-not-allowed")
        path=Path(plan["artifacts"]["bundlePath"])
        if _sha256(path)!=plan["artifacts"]["bundleSha256"]: raise AdapterRejected("controller-bundle-changed")
        try: bundle=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,ValueError) as error: raise AdapterRejected("controller-bundle-invalid") from error
        if not isinstance(bundle,dict) or bundle.get("kind")!="List" or not isinstance(bundle.get("items"),list):
            raise AdapterRejected("controller-bundle-invalid")
        selected=[]
        for obj in bundle["items"]:
            if not isinstance(obj,dict) or not isinstance(obj.get("metadata"),dict): raise AdapterRejected("controller-bundle-invalid")
            meta=obj["metadata"];key=obj.get("kind","")+"/"+str(meta.get("namespace"))+"/"+str(meta.get("name"))
            if key==identity:selected.append(obj)
        if len(selected)!=1: raise AdapterRejected("controller-bundle-identity")
        desired=selected[0];resource,fields=allowed[identity]
        if any(field not in desired for field in fields): raise AdapterRejected("controller-bundle-fields")
        item=plan["controllerResources"][identity];kind,namespace,name=identity.split("/")
        tool=plan["tool"]
        if _sha256(Path(tool["kubectlPath"]))!=tool["kubectlSha256"]: raise AdapterRejected("existing-upgrade-tool-changed")
        base=[tool["kubectlPath"],"--kubeconfig",tool["kubeconfigPath"],"--context",plan["target"]["context"]]
        if item["state"]=="absent":
            if resource not in {"role","rolebinding"}: raise AdapterRejected("controller-create-forbidden")
            fd,path_name=tempfile.mkstemp(prefix="controller-resource-",suffix=".json")
            try:
                with os.fdopen(fd,"w",encoding="utf-8") as file: json.dump(desired,file,sort_keys=True)
                result=self.transport.run(base+["create","--filename",path_name,"-o","json"],dict(os.environ))
            finally: os.unlink(path_name)
        else:
            patch=[{"op":"test","path":"/metadata/uid","value":item["uid"]},
                   {"op":"test","path":"/metadata/resourceVersion","value":item["resourceVersion"]}]
            for field in fields:
                patch.extend([{"op":"test","path":"/"+field,"value":item["rollback"][field]},
                              {"op":"replace","path":"/"+field,"value":desired[field]}])
            result=self.transport.run(base+["patch",resource,name,"--namespace",namespace,"--type=json","--patch",json.dumps(patch,separators=(",",":"))],dict(os.environ))
        if result.returncode!=0: raise AdapterRejected("controller-resource-outcome-uncertain")
        if item["state"]=="absent":
            try: uid=json.loads(result.stdout)["metadata"]["uid"]
            except (ValueError,KeyError,TypeError) as error: raise AdapterRejected("controller-resource-outcome-uncertain") from error
            if not isinstance(uid,str) or not uid: raise AdapterRejected("controller-resource-outcome-uncertain")
            return uid
        return item["uid"]

    def verify_controller_resource(self, identity, plan):
        allowed={"Deployment/fresh-control/argo-rollouts":("deployment",("spec",)),
                 "ConfigMap/fresh-control/argo-rollouts-config":("configmap",("data",)),
                 "Role/fresh-account/account-policy-reader":("role",("rules",)),
                 "RoleBinding/fresh-account/account-policy-reader":("rolebinding",("roleRef","subjects"))}
        if identity not in allowed: raise AdapterRejected("controller-resource-not-allowed")
        path=Path(plan["artifacts"]["bundlePath"])
        if _sha256(path)!=plan["artifacts"]["bundleSha256"]: raise AdapterRejected("controller-bundle-changed")
        try: bundle=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,ValueError) as error: raise AdapterRejected("controller-bundle-invalid") from error
        if not isinstance(bundle,dict) or bundle.get("kind")!="List" or not isinstance(bundle.get("items"),list): raise AdapterRejected("controller-bundle-invalid")
        kind,namespace,name=identity.split("/")
        selected=[x for x in bundle["items"] if isinstance(x,dict) and x.get("kind")==kind and x.get("metadata",{}).get("namespace")==namespace and x.get("metadata",{}).get("name")==name]
        if len(selected)!=1: raise AdapterRejected("controller-bundle-identity")
        resource,fields=allowed[identity];tool=plan["tool"]
        if _sha256(Path(tool["kubectlPath"]))!=tool["kubectlSha256"]: raise AdapterRejected("existing-upgrade-tool-changed")
        args=[tool["kubectlPath"],"--kubeconfig",tool["kubeconfigPath"],"--context",plan["target"]["context"],"get",resource,name,"--namespace",namespace,"--ignore-not-found","-o","json"]
        result=self.transport.run(args,dict(os.environ))
        if result.returncode!=0 or not isinstance(result.stdout,str) or len(result.stdout)>2_000_000: raise AdapterRejected("controller-resource-observe-failed")
        if not result.stdout.strip():return False
        try: actual=json.loads(result.stdout)
        except ValueError as error:raise AdapterRejected("controller-resource-observe-failed") from error
        if not isinstance(actual,dict) or not isinstance(actual.get("metadata"),dict):raise AdapterRejected("controller-resource-observe-failed")
        uid=actual["metadata"].get("uid"); expected=plan["controllerResources"][identity]
        if not isinstance(uid,str) or not uid or (expected["state"]=="present" and uid!=expected["uid"]):return False
        if any(actual.get(field)!=selected[0].get(field) for field in fields):return False
        if identity=="Deployment/fresh-control/argo-rollouts" and not self.verify_controller_runtime(plan):return False
        return uid

    def _controller_rollback_current(self, identity, plan):
        allowed={"Deployment/fresh-control/argo-rollouts":("deployment",("spec",)),
                 "ConfigMap/fresh-control/argo-rollouts-config":("configmap",("data",)),
                 "Role/fresh-account/account-policy-reader":("role",("rules",)),
                 "RoleBinding/fresh-account/account-policy-reader":("rolebinding",("roleRef","subjects"))}
        if identity not in allowed:raise AdapterRejected("controller-resource-not-allowed")
        path=Path(plan["artifacts"]["bundlePath"])
        if _sha256(path)!=plan["artifacts"]["bundleSha256"]:raise AdapterRejected("controller-bundle-changed")
        try:bundle=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,ValueError) as error:raise AdapterRejected("controller-bundle-invalid") from error
        kind,namespace,name=identity.split("/");items=bundle.get("items",[]) if isinstance(bundle,dict) and bundle.get("kind")=="List" else []
        found=[x for x in items if isinstance(x,dict) and x.get("kind")==kind and x.get("metadata",{}).get("namespace")==namespace and x.get("metadata",{}).get("name")==name]
        if len(found)!=1:raise AdapterRejected("controller-bundle-identity")
        resource,fields=allowed[identity];tool=plan["tool"]
        if _sha256(Path(tool["kubectlPath"]))!=tool["kubectlSha256"]:raise AdapterRejected("existing-upgrade-tool-changed")
        base=[tool["kubectlPath"],"--kubeconfig",tool["kubeconfigPath"],"--context",plan["target"]["context"]]
        result=self.transport.run(base+["get",resource,name,"--namespace",namespace,"--ignore-not-found","-o","json"],dict(os.environ))
        if result.returncode!=0 or not isinstance(result.stdout,str) or len(result.stdout)>2_000_000:raise AdapterRejected("controller-resource-observe-failed")
        if not result.stdout.strip():return None,found[0],base,resource,fields,namespace,name
        try:actual=json.loads(result.stdout)
        except ValueError as error:raise AdapterRejected("controller-resource-observe-failed") from error
        if not isinstance(actual,dict) or not isinstance(actual.get("metadata"),dict):raise AdapterRejected("controller-resource-observe-failed")
        return actual,found[0],base,resource,fields,namespace,name

    def rollback_controller_resource(self, identity, plan, expected_uid):
        if self.supports_existing_controller_mutations is not True:raise AdapterRejected("controller-mutation-capability-unavailable")
        if plan!=self.plan or not isinstance(expected_uid,str) or not expected_uid:raise AdapterRejected("controller-rollback-binding")
        actual,desired,base,resource,fields,namespace,name=self._controller_rollback_current(identity,plan)
        if actual is None:raise AdapterRejected("controller-rollback-missing")
        meta=actual["metadata"];version=meta.get("resourceVersion")
        if meta.get("uid")!=expected_uid or not isinstance(version,str) or not version or any(actual.get(f)!=desired.get(f) for f in fields):raise AdapterRejected("controller-rollback-drift")
        previous=plan["controllerResources"][identity]
        if previous["state"]=="absent":
            if resource not in {"role","rolebinding"}:raise AdapterRejected("controller-delete-forbidden")
            path="/apis/rbac.authorization.k8s.io/v1/namespaces/fresh-account/"+("roles" if resource=="role" else "rolebindings")+"/account-policy-reader"
            options={"apiVersion":"v1","kind":"DeleteOptions","preconditions":{"uid":expected_uid,"resourceVersion":version}}
            fd,filename=tempfile.mkstemp(prefix="controller-delete-",suffix=".json")
            try:
                with os.fdopen(fd,"w",encoding="utf-8") as stream:json.dump(options,stream,sort_keys=True)
                result=self.transport.run(base+["delete","--raw",path,"--filename",filename],dict(os.environ))
            finally:os.unlink(filename)
        else:
            if previous["uid"]!=expected_uid:raise AdapterRejected("controller-rollback-uid")
            patch=[{"op":"test","path":"/metadata/uid","value":expected_uid},{"op":"test","path":"/metadata/resourceVersion","value":version}]
            for field in fields:patch.extend([{"op":"test","path":"/"+field,"value":desired[field]},{"op":"replace","path":"/"+field,"value":previous["rollback"][field]}])
            result=self.transport.run(base+["patch",resource,name,"--namespace",namespace,"--type=json","--patch",json.dumps(patch,separators=(",",":"))],dict(os.environ))
        if result.returncode!=0:raise AdapterRejected("controller-rollback-outcome-uncertain")

    def verify_controller_rollback_resource(self, identity, plan, expected_uid):
        actual,_,_,_,fields,_,_=self._controller_rollback_current(identity,plan)
        previous=plan["controllerResources"][identity]
        if previous["state"]=="absent":return actual is None
        if actual is None or actual["metadata"].get("uid")!=expected_uid or expected_uid!=previous["uid"]:return False
        if any(actual.get(field)!=previous["rollback"][field] for field in fields):return False
        if identity=="Deployment/fresh-control/argo-rollouts":return self._controller_converged(plan,digest=plan["artifacts"]["oldControllerImageDigest"])
        return True

    def verify_controller_runtime(self, plan):
        return self._controller_converged(plan)

    def reconcile(self, phase, plan):
        if phase == "controller":
            obs = self.observe()
            if (obs["controllerImageDigest"] == plan["artifacts"]["controllerImageDigest"]
                    and obs["applicationRevision"] == plan["source"]["oldApplicationRevision"]
                    and self._controller_converged(plan)):
                return "succeeded"
        # Account rollout convergence is not yet proven.
        return "unknown"

    def run_upgrade_phase(self, phase, plan):
        raise AdapterRejected("existing-upgrade-live-mutation-unavailable")


class ApprovedLiveAdapter:
    """Concrete runner adapter backed by a sealed command plan.

    ``identity``, ``verify`` and ``reconcile`` are read-only plan sections.
    Only ``run`` uses the mutation section.  Exit code 3 in a reconcile probe
    means observable incomplete state; all other non-zero exits fail closed.
    """

    # The v3 plan has no metadata-only Secret/CA receipt transport.  Keep full
    # execution disabled before credential-bootstrap can mutate anything;
    # the controller-only prefix does not require this capability.
    supports_credential_runtime_proof = False

    def __init__(self, plan_path: Path, transport: Transport | None = None,
                 expected_target: dict | None = None,
                 expected_versions: dict | None = None,
                 expected_image_digests: set[str] | None = None,
                 expected_supply_contract: dict | None = None):
        self.plan_path = Path(plan_path)
        self.plan_sha256 = _sha256(self.plan_path)
        self.plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        self.transport = transport or SubprocessTransport()
        self._validate_plan(expected_target, expected_versions, expected_image_digests,
                            expected_supply_contract)
        self.command_env = self._command_environment()

    def _validate_plan(self, expected_target, expected_versions, expected_image_digests,
                       expected_supply_contract):
        required = {"schemaVersion", "target", "tools", "identity", "steps"}
        if set(self.plan) not in (required, required | {"rolloutsPlugin"}):
            raise AdapterRejected("plan-fields")
        self.plugin_contract = self.plan.get("rolloutsPlugin")
        self.supply_mode = (expected_supply_contract or {}).get(
            "supplyMode", "legacy-archive")
        if "rolloutsPlugin" in self.plan and self.plugin_contract is None:
            raise AdapterRejected("plugin-unapproved-contract")
        if self.plugin_contract is not None:
            trusted = (expected_supply_contract or {}).get("rolloutsPlugin")
            self._validate_plugin_contract(self.plugin_contract, trusted)
            if (expected_supply_contract or {}).get("applicationRevision") != self.plugin_contract["sourceRevision"]:
                raise AdapterRejected("plugin-source-not-approved")

        if self.plan["schemaVersion"] != "platform.lab/approved-live-step-plan/v3":
            raise AdapterRejected("plan-version")
        target = self.plan["target"]
        if set(target) != {"clusterName", "context", "kubeconfig"}:
            raise AdapterRejected("target-fields")
        if (not target["clusterName"].startswith("learning-fresh-")
                or not target["context"].startswith("kind-learning-fresh-")
                or not Path(target["kubeconfig"]).is_absolute()):
            raise AdapterRejected("target-boundary")
        if expected_target is not None and any(
                target.get(name) != expected_target.get(name)
                for name in ("clusterName", "context")):
            raise AdapterRejected("target-contract")
        supply_contract = expected_supply_contract or {}
        self.expected_kind_node_ref = supply_contract.get("kindNodeRef")
        self.expected_helm_charts = supply_contract.get("helmCharts", {})
        if expected_supply_contract is not None:
            expected_chart = self.expected_helm_charts.get("argo-rollouts")
            if (not isinstance(expected_chart, dict)
                    or set(expected_chart) != {"version", "sha256", "crdListSha256"}
                    or expected_chart["version"] != ROLLOUTS_CHART_VERSION
                    or expected_chart["sha256"] != ROLLOUTS_CHART_SHA256
                    or expected_chart["crdListSha256"] != ROLLOUTS_CRD_LIST_SHA256):
                raise AdapterRejected("supply-rollouts-chart")
        self._reject_environment_override(target)
        if set(self.plan["tools"]) != ALLOWED_TOOLS:
            raise AdapterRejected("tool-set")
        for alias, item in self.plan["tools"].items():
            if set(item) != {"path", "sha256", "version"}:
                raise AdapterRejected("tool-fields:" + alias)
            path = Path(item["path"])
            if not path.is_absolute() or not path.is_file() or _sha256(path) != item["sha256"]:
                raise AdapterRejected("tool-identity:" + alias)
            if (not isinstance(item["version"], str) or not item["version"]
                    or (expected_versions is not None
                        and item["version"] != expected_versions.get(alias))):
                raise AdapterRejected("tool-version:" + alias)
        if set(self.plan["identity"]) != {"clusterUID"}:
            raise AdapterRejected("identity-fields")
        if list(self.plan["steps"]) != list(STEPS):
            raise AdapterRejected("step-set")
        for command in self.plan["identity"].values():
            self._validate_command(command, section="identity", step=None,
                                   resource_identities=[])
        for step, actions in self.plan["steps"].items():
            if set(actions) != {"phase", "verify", "reconcile", "run", "resourceIdentities",
                                "observations", "hostArtifacts"}:
                raise AdapterRejected("step-fields:" + step)
            if actions["phase"] != STEP_PHASES[step]:
                raise AdapterRejected("step-phase:" + step)
            if not all(isinstance(actions[name], list)
                       for name in ("verify", "reconcile", "run", "hostArtifacts")):
                raise AdapterRejected("step-actions:" + step)
            if any(not isinstance(command, dict)
                   for name in ("verify", "reconcile", "run")
                   for command in actions[name]):
                raise AdapterRejected("step-command-object:" + step)
            if step == "crds" and (len(actions["run"]) != 1
                                    or actions["run"][0].get("tool") != "kubectl"):
                raise AdapterRejected("crds-single-apply")
            if step == "controllers":
                rollout_installs = []
                for command in actions["run"]:
                    args = command.get("args", [])
                    if (command.get("tool") == "helm" and "--install" in args
                            and args.index("--install") + 1 < len(args)
                            and args[args.index("--install") + 1] == "argo-rollouts"):
                        rollout_installs.append(command)
                if len(rollout_installs) != 1:
                    raise AdapterRejected("rollouts-controller-install-count")
                if self.plugin_contract is not None:
                    bundle_applies = [c for c in actions["run"] if c.get("tool") == "kubectl" and c.get("contentSha256") == self.plugin_contract["bundleSha256"]]
                    if len(bundle_applies) != 1 or actions["run"].index(bundle_applies[0]) <= actions["run"].index(rollout_installs[0]):
                        raise AdapterRejected("plugin-controller-apply-order")
            if (not isinstance(actions["resourceIdentities"], list)
                    or not actions["resourceIdentities"]
                    or not all(isinstance(item, str) and item
                               for item in actions["resourceIdentities"])):
                raise AdapterRejected("step-resources:" + step)
            observations = actions["observations"]
            if (not isinstance(observations, list)
                    or any(not isinstance(item, dict) for item in observations)):
                raise AdapterRejected("step-observations:" + step)
            if step == "preflight":
                self._validate_preflight_step(actions)
                continue
            if step == "image-source":
                self._validate_image_source_step(actions, expected_supply_contract)
                continue
            if step == "image-import" and self.supply_mode == "public-ghcr":
                if actions["hostArtifacts"]:
                    raise AdapterRejected("registry-image-import-artifacts")
                self._validate_node_image_step(
                    step, actions, expected_image_digests,
                    expected_health="node-cache-empty")
                for name in ("verify", "reconcile", "run"):
                    if len(actions[name]) != 1 or actions[name][0]["tool"] != "kubectl":
                        raise AdapterRejected("registry-image-import-command")
                    self._validate_command(actions[name][0], section=name, step=step,
                                           resource_identities=actions["resourceIdentities"])
                continue
            if not observations or actions["hostArtifacts"]:
                raise AdapterRejected("step-cluster-contract:" + step)
            if not all(actions[name] for name in ("verify", "reconcile", "run")):
                raise AdapterRejected("step-cluster-commands:" + step)
            observation_identities = []
            for item in observations:
                self._validate_observation(item, step)
                observation_identities.append(item["identity"])
            if step == "crds" and any(
                    item["generationPolicy"] != "none"
                    or item["healthPolicy"] != "conditions"
                    or item["requiredConditions"] != {"Established": "True"}
                    for item in observations):
                raise AdapterRejected("crd-established-contract")
            if (len(observation_identities) != len(set(observation_identities))
                    or observation_identities != actions["resourceIdentities"]):
                raise AdapterRejected("step-observation-identities:" + step)
            required_health = REQUIRED_STEP_HEALTH.get(step)
            if required_health and not any(item["healthPolicy"] in required_health
                                           for item in observations):
                raise AdapterRejected("step-semantic-health:" + step)
            required_resources = STEP_RESOURCE_CONTRACTS.get(step)
            if step == "application" and self.plugin_contract is not None:
                required_resources = required_resources | {("argoproj.io/v1alpha1", "AnalysisTemplate/fresh-account/account-api-policy", "none")}
            actual_resources = {
                (item["apiVersion"], item["identity"], item["healthPolicy"])
                for item in observations
            }
            if (step == "controllers"
                    and ("apps/v1", "Deployment/fresh-control/argo-rollouts",
                         "workload-ready") not in actual_resources):
                raise AdapterRejected("rollouts-controller-observation")
            if required_resources is not None:
                if actual_resources != required_resources:
                    raise AdapterRejected("step-resource-contract:" + step)
            if step in {"empty-node-cache", "image-import"}:
                self._validate_node_image_step(step, actions, expected_image_digests)
            for name in ("verify", "reconcile"):
                for command in actions[name]:
                    self._validate_command(command, section=name, step=step,
                                           resource_identities=actions["resourceIdentities"])
            for command in actions["run"]:
                self._validate_command(command, section="run", step=step,
                                       resource_identities=actions["resourceIdentities"])
            if (step == "image-import" and self.supply_mode != "public-ghcr"
                    and not any(command["tool"] == "kind"
                                and command["args"][:2] == ["load", "image-archive"]
                                for command in actions["run"])):
                raise AdapterRejected("archive-image-import-command")

    @staticmethod
    def _validate_plugin_contract(contract, trusted):
        fields = {"profile", "sourceRevision", "controllerImage", "binarySha256", "bundleSha256"}
        if not isinstance(contract, dict) or set(contract) != fields or contract != trusted:
            raise AdapterRejected("plugin-unapproved-contract")
        if contract["profile"] != "fresh-account" or not isinstance(contract["sourceRevision"], str) or not re.fullmatch(r"[0-9a-f]{40}", contract["sourceRevision"]) or contract["sourceRevision"] == "0" * 40:
            raise AdapterRejected("plugin-source-revision")
        if not isinstance(contract["controllerImage"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9./-]*@sha256:[0-9a-f]{64}", contract["controllerImage"]):
            raise AdapterRejected("plugin-image-digest")
        if contract["controllerImage"].endswith("0" * 64):
            raise AdapterRejected("plugin-image-digest")
        if any(not isinstance(contract[k], str) or not re.fullmatch(r"[0-9a-f]{64}", contract[k]) or contract[k] == "0" * 64 for k in ("binarySha256", "bundleSha256")):
            raise AdapterRejected("plugin-checksum")

    def _validate_plugin_bundle(self, path, digest):
        c = self.plugin_contract
        if not c or digest != c["bundleSha256"] or _sha256(path) != digest:
            raise AdapterRejected("plugin-bundle-digest")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            docs = value["items"] if value["apiVersion"] == "v1" and value["kind"] == "List" else []
            objects = {(x["kind"], x["metadata"].get("namespace"), x["metadata"]["name"]): x for x in docs}
            expected = {("Deployment", "fresh-control", "argo-rollouts"), ("ConfigMap", "fresh-control", "argo-rollouts-config"), ("Role", "fresh-account", "account-policy-reader"), ("RoleBinding", "fresh-account", "account-policy-reader")}
            if len(docs) != 4 or set(objects) != expected: raise ValueError()
            pod = objects[("Deployment", "fresh-control", "argo-rollouts")]["spec"]["template"]["spec"]
            containers = pod["containers"]
            if len(containers) != 1 or containers[0]["image"] != c["controllerImage"]: raise ValueError()
            env_list = containers[0]["env"]
            env = {e["name"]:e.get("value") for e in env_list}
            if len(env) != len(env_list) or env.get("ACCOUNT_POLICY_PROFILE") != "fresh-account" or env.get("ACCOUNT_POLICY_SHA256") != c["binarySha256"]: raise ValueError()
            config = objects[("ConfigMap", "fresh-control", "argo-rollouts-config")]["data"]["metricProviderPlugins"]
            import yaml
            if yaml.safe_load(config) != [{"name":"learning-local/account-policy", "location":"file:///opt/account-policy", "sha256":c["binarySha256"]}]: raise ValueError()
            role = objects[("Role", "fresh-account", "account-policy-reader")]
            if role["rules"] != [
                {"apiGroups":[""], "resources":["pods"], "verbs":["list"]},
                {"apiGroups":["apps"], "resources":["replicasets"], "verbs":["list"]},
                {"apiGroups":["argoproj.io"], "resources":["rollouts"], "verbs":["get"], "resourceNames":["account-api"]},
                {"apiGroups":["argoproj.io"], "resources":["analysisruns"], "verbs":["get"]},
                {"apiGroups":["networking.istio.io"], "resources":["destinationrules","virtualservices"], "verbs":["get"], "resourceNames":["account-api"]},
            ]: raise ValueError()
            binding = objects[("RoleBinding", "fresh-account", "account-policy-reader")]
            if binding["subjects"] != [{"kind":"ServiceAccount", "name":pod["serviceAccountName"], "namespace":"fresh-control"}] or binding["roleRef"] != {"apiGroup":"rbac.authorization.k8s.io", "kind":"Role", "name":"account-policy-reader"}: raise ValueError()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AdapterRejected("plugin-bundle-contract") from exc

    def _validate_preflight_step(self, actions):
        if (actions["hostArtifacts"] or actions["observations"]
                or actions["resourceIdentities"] != ["HostState/None/target-cluster-absent"]):
            raise AdapterRejected("preflight-contract")
        for section in ("verify", "reconcile", "run"):
            if len(actions[section]) != 1:
                raise AdapterRejected("preflight-command-count")
            self._validate_command(actions[section][0], section=section, step="preflight",
                                   resource_identities=actions["resourceIdentities"])

    def _validate_image_source_step(self, actions, expected_supply_contract):
        if actions["observations"] or any(actions[name] for name in ("verify", "reconcile", "run")):
            raise AdapterRejected("image-source-command-contract")
        artifacts = actions["hostArtifacts"]
        if self.supply_mode == "public-ghcr":
            if (artifacts or actions["resourceIdentities"] !=
                    ["SupplyMode/None/public-ghcr"]
                    or (expected_supply_contract or {}).get("hostArtifacts") != {}):
                raise AdapterRejected("registry-image-source-contract")
            return
        if not artifacts or any(not isinstance(item, dict) for item in artifacts):
            raise AdapterRejected("image-source-artifacts")
        identities = []
        for artifact in artifacts:
            if set(artifact) != {"id", "path", "sha256", "sizeBytes", "sourceIdentity"}:
                raise AdapterRejected("host-artifact-fields")
            if (not isinstance(artifact["id"], str) or not artifact["id"]
                    or not isinstance(artifact["sourceIdentity"], str)
                    or not artifact["sourceIdentity"]):
                raise AdapterRejected("host-artifact-identity")
            path = Path(artifact["path"])
            if (not path.is_absolute() or not path.is_file()
                    or path.stat().st_size != artifact["sizeBytes"]
                    or _sha256(path) != artifact["sha256"]):
                raise AdapterRejected("host-artifact-content:" + artifact["id"])
            identities.append("HostArtifact/None/" + artifact["id"])
        if identities != actions["resourceIdentities"] or len(identities) != len(set(identities)):
            raise AdapterRejected("host-artifact-resource-set")
        if expected_supply_contract is not None:
            expected = expected_supply_contract.get("hostArtifacts")
            if not isinstance(expected, dict) or set(expected) != {
                    item["id"] for item in artifacts}:
                raise AdapterRejected("host-artifact-supply-set")
            for artifact in artifacts:
                contract = expected[artifact["id"]]
                if (artifact["sha256"] != contract.get("sha256")
                        or artifact["sourceIdentity"] != contract.get("sourceIdentity")):
                    raise AdapterRejected("host-artifact-supply-identity:" + artifact["id"])

    def _validate_node_image_step(self, step, actions, expected_image_digests,
                                  expected_health=None):
        expected_identity = ("Node/None/" + self.plan["target"]["clusterName"]
                             + "-control-plane")
        if len(actions["observations"]) != 1:
            raise AdapterRejected("node-image-observation-count:" + step)
        observation = actions["observations"][0]
        expected_health = expected_health or ("node-cache-empty" if step == "empty-node-cache"
                                               else "node-images-present")
        if (observation["apiVersion"] != "v1" or observation["identity"] != expected_identity
                or observation["healthPolicy"] != expected_health):
            raise AdapterRejected("node-image-contract:" + step)
        digests = observation["requiredImageDigests"]
        if not digests or (expected_image_digests is not None
                           and set(digests) != set(expected_image_digests)):
            raise AdapterRejected("node-image-digest-set:" + step)

    def _validate_command(self, command: dict, section: str, step: str | None,
                          resource_identities: list[str]):
        if (set(command) != {"tool", "args", "contentSha256"}
                or command["tool"] not in self.plan["tools"]):
            raise AdapterRejected("command-fields")
        if not isinstance(command["args"], list) or not all(isinstance(arg, str) for arg in command["args"]):
            raise AdapterRejected("command-args")
        combined = " ".join(command["args"]).lower()
        if any(fragment in combined for fragment in SENSITIVE_FRAGMENTS):
            raise AdapterRejected("secret-in-command")
        target = self.plan["target"]
        alias = command["tool"]
        args = command["args"]
        content_digest = command["contentSha256"]
        if content_digest is not None and (not isinstance(content_digest, str)
                or len(content_digest) != 64
                or any(character not in "0123456789abcdef" for character in content_digest)):
            raise AdapterRejected("command-content-digest")
        validators = {
            "kubectl": self._validate_kubectl_command,
            "kind": self._validate_kind_command,
            "helm": self._validate_helm_command,
            "istioctl": self._validate_istioctl_command,
            "argocd": self._validate_argocd_command,
            "rollouts": self._validate_rollouts_command,
        }
        validators[alias](args, section, step, resource_identities, target, content_digest)

    @staticmethod
    def _exact_prefix(args: list[str], expected: list[str]) -> list[str]:
        if args[:len(expected)] != expected:
            raise AdapterRejected("command-target-prefix")
        return args[len(expected):]

    def _validate_kubectl_command(self, args, section, step, resources, target, content_digest):
        rest = self._exact_prefix(args, ["--kubeconfig", target["kubeconfig"],
                                         "--context", target["context"]])
        if step == "empty-node-cache" and section == "run":
            expected = ["get", "node", target["clusterName"] + "-control-plane",
                        "--output", "json"]
            if rest != expected or content_digest is not None:
                raise AdapterRejected("kubectl-node-proof-command")
            return
        if section == "identity":
            if content_digest is not None:
                raise AdapterRejected("kubectl-identity-content")
            if rest != ["get", "namespace", "kube-system", "--output",
                        "jsonpath={.metadata.uid}"]:
                raise AdapterRejected("kubectl-identity-command")
            return
        if section in {"verify", "reconcile"} or (section == "run" and step == "image-import"):
            if content_digest is not None:
                raise AdapterRejected("kubectl-read-content")
            if step in {"empty-node-cache", "image-import"}:
                expected = ["get", "node", target["clusterName"] + "-control-plane",
                            "--output", "json"]
                if rest != expected:
                    raise AdapterRejected("kubectl-node-read-command")
                return
            if step == "crds":
                expected_suffix = (["--ignore-not-found=true", "--output", "json"]
                                   if section == "reconcile" else ["--output", "json"])
                if (len(rest) != 3 + len(expected_suffix) or rest[0] != "get"
                        or rest[1] != "customresourcedefinition"
                        or rest[3:] != expected_suffix):
                    raise AdapterRejected("kubectl-crd-read-command")
                api_kind = KUBECTL_RESOURCES["customresourcedefinition"]
                identity = f"{api_kind[1]}/None/{rest[2]}"
                expected_targets = {
                    (item["apiVersion"], item["identity"])
                    for item in self.plan["steps"][step]["observations"]
                }
                if (api_kind[0], identity) not in expected_targets:
                    raise AdapterRejected("kubectl-read-target")
                return
            if (len(rest) != 7 or rest[0] != "get" or rest[3] != "--namespace"
                    or rest[5:] != ["--output", "json"]):
                raise AdapterRejected("kubectl-read-command")
            resource, name, namespace = rest[1], rest[2], rest[4]
            api_kind = KUBECTL_RESOURCES.get(resource)
            if api_kind is None:
                raise AdapterRejected("kubectl-read-resource")
            identity = f"{api_kind[1]}/{namespace}/{name}"
            expected_targets = {
                (item["apiVersion"], item["identity"])
                for item in self.plan["steps"][step]["observations"]
            }
            if (api_kind[0], identity) not in expected_targets:
                raise AdapterRejected("kubectl-read-target")
            return
        if section == "run":
            if step in GITOPS_STEPS:
                raise AdapterRejected("kubectl-gitops-bypass:" + step)
            if len(rest) != 3 or rest[:2] != ["apply", "--filename"]:
                raise AdapterRejected("kubectl-run-command")
            manifest_path = Path(rest[2])
            if (not manifest_path.is_absolute() or not manifest_path.is_file()
                    or content_digest is None or _sha256(manifest_path) != content_digest):
                raise AdapterRejected("kubectl-manifest-path")
            if step == "controllers" and self.plugin_contract is not None and content_digest == self.plugin_contract["bundleSha256"]:
                self._validate_plugin_bundle(manifest_path, content_digest)
            elif step == "controllers" and self.plugin_contract is not None:
                raise AdapterRejected("plugin-controller-unbound")
            if step == "crds" and content_digest != ROLLOUTS_CRD_LIST_SHA256:
                raise AdapterRejected("crds-official-render-hash")
            observations = self.plan["steps"][step]["observations"]
            if step == "controllers" and self.plugin_contract is not None:
                plugin_targets = {
                    ("apps/v1", "Deployment/fresh-control/argo-rollouts"),
                    ("v1", "ConfigMap/fresh-control/argo-rollouts-config"),
                    ("rbac.authorization.k8s.io/v1", "Role/fresh-account/account-policy-reader"),
                    ("rbac.authorization.k8s.io/v1", "RoleBinding/fresh-account/account-policy-reader"),
                }
                observations = [item for item in observations if (item["apiVersion"], item["identity"]) in plugin_targets]
                expected_targets = plugin_targets
                if {(item["apiVersion"], item["identity"]) for item in observations} != plugin_targets:
                    raise AdapterRejected("plugin-controller-observations")
            else:
                expected_targets = {(item["apiVersion"], item["identity"]) for item in observations}
            if self._manifest_identities(manifest_path, observations) != expected_targets:
                raise AdapterRejected("kubectl-manifest-targets")
            return
        raise AdapterRejected("kubectl-operation")

    @staticmethod
    def _manifest_identities(path: Path, observations: list[dict]) -> set[tuple[str, str]]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise AdapterRejected("kubectl-manifest-json") from error
        items = value.get("items") if isinstance(value, dict) and value.get("kind") == "List" else [value]
        if not isinstance(items, list) or not items or any(not isinstance(item, dict) for item in items):
            raise AdapterRejected("kubectl-manifest-objects")
        identities = set()
        resources = {}
        for item in items:
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                raise AdapterRejected("kubectl-manifest-metadata")
            api_version, kind, name = item.get("apiVersion"), item.get("kind"), metadata.get("name")
            namespace = metadata.get("namespace") or "None"
            if not all(isinstance(value, str) and value
                       for value in (api_version, kind, name, namespace)):
                raise AdapterRejected("kubectl-manifest-identity")
            if kind == "CustomResourceDefinition" and "namespace" in metadata:
                raise AdapterRejected("kubectl-crd-must-be-cluster-scoped")
            identity = f"{kind}/{namespace}/{name}"
            target = (api_version, identity)
            if target in identities:
                raise AdapterRejected("kubectl-manifest-duplicate")
            identities.add(target)
            resources[identity] = item
        expected = {(item["apiVersion"], item["identity"]) for item in observations}
        if identities != expected:
            return identities
        for contract in observations:
            resource = resources[contract["identity"]]
            desired = {pointer: _json_pointer(resource, pointer)
                       for pointer in contract["desiredPaths"]}
            if _mapping_digest(desired) != contract["desiredSha256"]:
                raise AdapterRejected("kubectl-manifest-desired-digest:" + contract["identity"])
        return identities

    def _validate_kind_command(self, args, section, step, resources, target, content_digest):
        if step == "preflight":
            if args != ["get", "clusters"] or content_digest is not None:
                raise AdapterRejected("kind-preflight-command")
            return
        if section == "run" and step == "cluster":
            expected = ["create", "cluster", "--name", target["clusterName"],
                        "--kubeconfig", target["kubeconfig"]]
            if (args[:len(expected)] != expected or len(args) != len(expected) + 2
                    or args[-2] != "--image" or "@sha256:" not in args[-1]
                    or content_digest is not None
                    or (self.expected_kind_node_ref is not None
                        and args[-1] != self.expected_kind_node_ref)):
                raise AdapterRejected("kind-create-command")
            return
        if section == "run" and step == "image-import":
            if (len(args) != 5 or args[:2] != ["load", "image-archive"]
                    or not Path(args[2]).is_absolute() or not Path(args[2]).is_file()
                    or content_digest is None or _sha256(Path(args[2])) != content_digest
                    or args[3:] != ["--name", target["clusterName"]]):
                raise AdapterRejected("kind-load-command")
            return
        if section in {"verify", "reconcile"} and step in {"cluster", "empty-node-cache"}:
            if args != ["get", "nodes", "--name", target["clusterName"]]:
                raise AdapterRejected("kind-read-command")
            if content_digest is not None:
                raise AdapterRejected("kind-read-content")
            return
        raise AdapterRejected("kind-operation")

    def _validate_helm_command(self, args, section, step, resources, target, content_digest):
        prefix = ["--kubeconfig", target["kubeconfig"], "--kube-context", target["context"]]
        rest = self._exact_prefix(args, prefix)
        if section != "run" or step not in HELM_RELEASES:
            raise AdapterRejected("helm-operation")
        if len(rest) < 12 or rest[:2] != ["upgrade", "--install"]:
            raise AdapterRejected("helm-command")
        release, chart = rest[2], rest[3]
        tail = ["--atomic", "--wait", "--wait-for-jobs", "--create-namespace"]
        if release == "argo-rollouts":
            tail = ["--set", "installCRDs=false", "--set-string",
                    "controller.image.tag=" + ROLLOUTS_CONTROLLER_TAG, *tail]
        expected_chart = self.expected_helm_charts.get(release)
        if (not Path(chart).is_absolute() or rest[4] != "--namespace"
                or (release, rest[5]) not in HELM_RELEASES[step]
                or rest[6] != "--version" or not rest[7]
                or rest[8:] != tail
                or (release == "argo-rollouts" and rest[7] != ROLLOUTS_CHART_VERSION)
                or content_digest is None or _content_sha256(Path(chart)) != content_digest
                or (expected_chart is not None
                    and (rest[7] != expected_chart["version"]
                         or content_digest != expected_chart["sha256"]))):
            raise AdapterRejected("helm-command")

    def _validate_istioctl_command(self, args, section, step, resources, target, content_digest):
        prefix = ["--kubeconfig", target["kubeconfig"], "--context", target["context"]]
        rest = self._exact_prefix(args, prefix)
        if (section != "run" or step != "controllers" or len(rest) != 4
                or rest[:2] != ["install", "--filename"]
                or not Path(rest[2]).is_absolute() or not Path(rest[2]).is_file()
                or content_digest is None or _sha256(Path(rest[2])) != content_digest
                or rest[3] != "--skip-confirmation"):
            raise AdapterRejected("istioctl-command")

    def _validate_argocd_command(self, args, section, step, resources, target, content_digest):
        if content_digest is not None:
            raise AdapterRejected("argocd-content")
        rest = self._exact_prefix(args, ["--core", "--kube-context", target["context"]])
        if section in {"verify", "reconcile"}:
            if len(rest) != 5 or rest[:2] != ["app", "get"] or rest[3:] != ["--output", "json"]:
                raise AdapterRejected("argocd-read-command")
            target = ("argoproj.io/v1alpha1", f"Application/fresh-control/{rest[2]}")
            expected_targets = {(item["apiVersion"], item["identity"])
                                for item in self.plan["steps"][step]["observations"]}
            if target not in expected_targets:
                raise AdapterRejected("argocd-read-target")
            return
        if section == "run" and step in GITOPS_STEPS:
            expected = ["app", "sync", "fresh-account-release"]
            resources = ARGO_SYNC_RESOURCES[step]
            if step == "application" and self.plugin_contract is not None:
                resources = ["argoproj.io:AnalysisTemplate:account-api-policy", *resources]
            for resource in resources:
                expected.extend(["--resource", resource])
            expected.append("--prune=false")
            if rest != expected:
                raise AdapterRejected("argocd-run-command")
            target = ("argoproj.io/v1alpha1",
                      "Application/fresh-control/fresh-account-release")
            expected_targets = {(item["apiVersion"], item["identity"])
                                for item in self.plan["steps"][step]["observations"]}
            if target not in expected_targets:
                raise AdapterRejected("argocd-run-target")
            return
        raise AdapterRejected("argocd-operation")

    def _validate_rollouts_command(self, args, section, step, resources, target, content_digest):
        if content_digest is not None:
            raise AdapterRejected("rollouts-content")
        rest = self._exact_prefix(args, ["--kubeconfig", target["kubeconfig"],
                                         "--context", target["context"]])
        exact_target = "Rollout/fresh-account/account-api"
        expected_targets = {(item["apiVersion"], item["identity"])
                            for item in self.plan["steps"][step]["observations"]}
        if ("argoproj.io/v1alpha1", exact_target) not in expected_targets:
            raise AdapterRejected("rollouts-target")
        if section in {"verify", "reconcile"}:
            if rest != ["get", "rollout", "account-api", "--namespace", "fresh-account",
                        "--output", "json"]:
                raise AdapterRejected("rollouts-read-command")
            return
        if section == "run" and step == "acceptance":
            if rest not in (["restart", "rollout", "account-api", "--namespace", "fresh-account"],
                            ["promote", "rollout", "account-api", "--namespace", "fresh-account"],
                            ["abort", "rollout", "account-api", "--namespace", "fresh-account"],
                            ["retry", "rollout", "account-api", "--namespace", "fresh-account"]):
                raise AdapterRejected("rollouts-run-command")
            return
        raise AdapterRejected("rollouts-operation")

    @staticmethod
    def _require_pair(args: list[str], flag: str, expected: str):
        if args.count(flag) != 1 or any(arg.startswith(flag + "=") for arg in args):
            raise AdapterRejected("target-flag:" + flag)
        index = args.index(flag)
        if index + 1 >= len(args) or args[index + 1] != expected:
            raise AdapterRejected("target-value:" + flag)

    @staticmethod
    def _reject_environment_override(target: dict):
        kubeconfig = os.environ.get("KUBECONFIG")
        if kubeconfig and Path(kubeconfig).resolve() != Path(target["kubeconfig"]).resolve():
            raise AdapterRejected("environment-target-override:KUBECONFIG")
        for name in ("KUBERNETES_MASTER", "KUBE_API_SERVER", "HELM_KUBECONTEXT",
                     "HELM_KUBEAPISERVER", "ARGOCD_SERVER", "ARGOCD_AUTH_TOKEN",
                     "ARGOCD_OPTS", "ARGOCD_CONFIG_DIR", "ARGOCD_SERVER_NAME"):
            if os.environ.get(name):
                raise AdapterRejected("environment-target-override:" + name)

    def _command_environment(self):
        allowed = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "SSL_CERT_FILE",
                   "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
        environment = {name: os.environ[name] for name in allowed if name in os.environ}
        environment["KUBECONFIG"] = self.plan["target"]["kubeconfig"]
        return environment

    @staticmethod
    def _validate_observation(observation: dict, step: str):
        expected = {"apiVersion", "identity", "desiredPaths", "desiredSha256", "generationPolicy",
                    "healthPolicy", "requiredConditions", "requiredImageDigests"}
        if set(observation) != expected:
            raise AdapterRejected("observation-fields:" + step)
        identity = observation["identity"]
        api_version = observation["apiVersion"]
        if not isinstance(api_version, str) or not api_version or "/" not in api_version and api_version != "v1":
            raise AdapterRejected("observation-api-version:" + step)
        if (not isinstance(identity, str) or len(identity.split("/")) != 3
                or any(not part for part in identity.split("/"))):
            raise AdapterRejected("observation-identity:" + step)
        paths = observation["desiredPaths"]
        if (not isinstance(paths, list) or not paths or len(paths) != len(set(paths))
                or any(not isinstance(path, str) or not path.startswith("/")
                       or path.startswith(("/status", "/metadata/uid", "/metadata/resourceVersion",
                                           "/metadata/creationTimestamp", "/metadata/managedFields"))
                       for path in paths)):
            raise AdapterRejected("observation-paths:" + step)
        if identity.startswith("Secret/") and any(path.startswith(("/data", "/stringData")) for path in paths):
            raise AdapterRejected("observation-secret-data:" + step)
        digest = observation["desiredSha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise AdapterRejected("observation-digest:" + step)
        if observation["generationPolicy"] not in {"none", "observed-current"}:
            raise AdapterRejected("observation-generation:" + step)
        if observation["healthPolicy"] not in {"none", "conditions", "workload-ready", "job-complete",
                                                 "pvc-bound", "statefulset-ready", "rollout-ready",
                                                 "argocd-operation-succeeded",
                                                 "argocd-application-ready", "node-cache-empty",
                                                 "node-images-present"}:
            raise AdapterRejected("observation-health:" + step)
        conditions = observation["requiredConditions"]
        if (not isinstance(conditions, dict)
                or any(not isinstance(key, str) or not key or value not in {"True", "False", "Unknown"}
                       for key, value in conditions.items())):
            raise AdapterRejected("observation-conditions:" + step)
        image_digests = observation["requiredImageDigests"]
        if (not isinstance(image_digests, list) or len(image_digests) != len(set(image_digests))
                or any(not isinstance(item, str) or not item.startswith("sha256:")
                       or len(item) != 71 for item in image_digests)):
            raise AdapterRejected("observation-image-digests:" + step)
        if observation["healthPolicy"] == "conditions" and not conditions:
            raise AdapterRejected("observation-conditions:" + step)
        if observation["healthPolicy"] == "workload-ready" and observation["generationPolicy"] != "observed-current":
            raise AdapterRejected("observation-workload-generation:" + step)
        kind = identity.split("/", 1)[0]
        if observation["healthPolicy"] == "workload-ready" and kind not in {
                "Deployment", "StatefulSet", "Rollout"}:
            raise AdapterRejected("observation-workload-kind:" + step)
        if observation["healthPolicy"] == "job-complete" and kind != "Job":
            raise AdapterRejected("observation-job-kind:" + step)
        if observation["healthPolicy"] == "pvc-bound" and kind != "PersistentVolumeClaim":
            raise AdapterRejected("observation-pvc-kind:" + step)
        if observation["healthPolicy"] == "statefulset-ready" and kind != "StatefulSet":
            raise AdapterRejected("observation-statefulset-kind:" + step)
        if observation["healthPolicy"] == "rollout-ready" and kind != "Rollout":
            raise AdapterRejected("observation-rollout-kind:" + step)
        if observation["healthPolicy"].startswith("argocd-") and kind != "Application":
            raise AdapterRejected("observation-argocd-kind:" + step)
        if observation["healthPolicy"] in {"node-cache-empty", "node-images-present"}:
            if kind != "Node" or not image_digests:
                raise AdapterRejected("observation-node-image-kind:" + step)
        elif image_digests:
            raise AdapterRejected("observation-unexpected-image-digests:" + step)

    def _argv(self, command):
        return [self.plan["tools"][command["tool"]]["path"], *command["args"]]

    def _run_commands(self, commands, incomplete_exit_codes=frozenset(),
                      empty_stdout_is_incomplete=False):
        results = []
        for command in commands:
            self._verify_command_content(command)
            result = self.transport.run(self._argv(command), dict(self.command_env))
            results.append(result)
            if result.returncode in incomplete_exit_codes:
                return results, False
            if result.returncode != 0:
                raise AdapterRejected(f"command-exit:{result.returncode}")
            if empty_stdout_is_incomplete and not result.stdout.strip():
                return results, False
        return results, True

    @staticmethod
    def _verify_command_content(command):
        expected = command.get("contentSha256")
        if expected is None:
            return
        path = Path(_bound_content_path(command))
        if not path.is_absolute() or _content_sha256(path) != expected:
            raise AdapterRejected("command-content-changed")

    def _host_artifact_evidence(self, step):
        normalized = []
        for artifact in self.plan["steps"][step]["hostArtifacts"]:
            path = Path(artifact["path"])
            if (not path.is_file() or path.stat().st_size != artifact["sizeBytes"]
                    or _sha256(path) != artifact["sha256"]):
                raise AdapterRejected("host-artifact-changed:" + artifact["id"])
            normalized.append({
                "identity": "HostArtifact/None/" + artifact["id"],
                "sha256": artifact["sha256"], "sizeBytes": artifact["sizeBytes"],
                "sourceIdentity": artifact["sourceIdentity"],
            })
        return normalized, _mapping_digest(normalized)

    def _preflight_evidence(self, command):
        results, _ = self._run_commands([command])
        clusters = {line.strip() for line in results[0].stdout.splitlines() if line.strip()}
        target = self.plan["target"]["clusterName"]
        if target in clusters:
            raise AdapterRejected("preflight-target-already-exists")
        normalized = [{"identity": "HostState/None/target-cluster-absent",
                       "targetCluster": target, "initialTargetAbsent": True}]
        return normalized, _mapping_digest(normalized)

    @staticmethod
    def _resource_identity(resource: dict) -> str:
        metadata = resource.get("metadata")
        if not isinstance(metadata, dict):
            raise AdapterRejected("observation-metadata")
        kind = resource.get("kind")
        namespace = metadata.get("namespace") or "None"
        name = metadata.get("name")
        if not all(isinstance(item, str) and item for item in (kind, namespace, name)):
            raise AdapterRejected("observation-identity")
        return f"{kind}/{namespace}/{name}"

    def _observe(self, results, observations):
        resources = []
        for result in results:
            try:
                value = json.loads(result.stdout)
            except (TypeError, json.JSONDecodeError) as error:
                raise AdapterRejected("observation-json") from error
            if not isinstance(value, dict):
                raise AdapterRejected("observation-object")
            if value.get("kind") == "List":
                items = value.get("items")
                if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                    raise AdapterRejected("observation-list")
                resources.extend(items)
            else:
                resources.append(value)
        by_identity = {}
        for resource in resources:
            identity = self._resource_identity(resource)
            if identity in by_identity:
                raise AdapterRejected("observation-duplicate")
            by_identity[identity] = resource
        expected = [item["identity"] for item in observations]
        if set(by_identity) != set(expected):
            raise AdapterRejected("observation-resource-set")

        normalized = []
        for contract in observations:
            identity = contract["identity"]
            resource = by_identity[identity]
            if resource.get("apiVersion") != contract["apiVersion"]:
                raise AdapterRejected("observation-api-version:" + identity)
            metadata = resource["metadata"]
            uid = metadata.get("uid")
            if not isinstance(uid, str) or not uid:
                raise AdapterRejected("observation-uid:" + identity)
            desired = {path: _json_pointer(resource, path) for path in contract["desiredPaths"]}
            desired_digest = _mapping_digest(desired)
            if desired_digest != contract["desiredSha256"]:
                raise AdapterRejected("observation-desired-digest:" + identity)
            generation = metadata.get("generation")
            observed_generation = resource.get("status", {}).get("observedGeneration")
            if contract["generationPolicy"] == "observed-current":
                if (not isinstance(generation, int) or generation < 1
                        or not isinstance(observed_generation, int)
                        or observed_generation < generation):
                    raise AdapterRejected("observation-stale-generation:" + identity)
            status = resource.get("status", {})
            if not isinstance(status, dict):
                raise AdapterRejected("observation-status:" + identity)
            conditions = status.get("conditions", [])
            if not isinstance(conditions, list):
                raise AdapterRejected("observation-conditions:" + identity)
            condition_map = {}
            for condition in conditions:
                if isinstance(condition, dict) and isinstance(condition.get("type"), str):
                    condition_map[condition["type"]] = condition.get("status")
            for condition_type, expected_status in contract["requiredConditions"].items():
                if condition_map.get(condition_type) != expected_status:
                    raise AdapterRejected("observation-condition:" + identity + ":" + condition_type)
            health = contract["healthPolicy"]
            if health == "workload-ready":
                replicas = resource.get("spec", {}).get("replicas")
                ready = status.get("readyReplicas")
                available = status.get("availableReplicas")
                if (not isinstance(replicas, int) or replicas < 1
                        or not isinstance(ready, int) or ready < replicas
                        or not isinstance(available, int) or available < replicas):
                    raise AdapterRejected("observation-not-ready:" + identity)
            elif health == "job-complete":
                succeeded = status.get("succeeded")
                if (resource.get("kind") != "Job" or not isinstance(succeeded, int) or succeeded < 1
                        or condition_map.get("Complete") != "True"
                        or not isinstance(status.get("completionTime"), str)):
                    raise AdapterRejected("observation-job-incomplete:" + identity)
            elif health == "pvc-bound":
                capacity = status.get("capacity", {}).get("storage")
                if (resource.get("kind") != "PersistentVolumeClaim"
                        or status.get("phase") != "Bound"
                        or not isinstance(resource.get("spec", {}).get("volumeName"), str)
                        or not resource["spec"]["volumeName"]
                        or not isinstance(capacity, str) or not capacity):
                    raise AdapterRejected("observation-pvc-unbound:" + identity)
            elif health == "statefulset-ready":
                replicas = resource.get("spec", {}).get("replicas")
                revisions = (status.get("currentRevision"), status.get("updateRevision"))
                if (resource.get("kind") != "StatefulSet"
                        or not isinstance(replicas, int) or replicas < 1
                        or any(status.get(field) != replicas for field in
                               ("replicas", "readyReplicas", "currentReplicas", "updatedReplicas"))
                        or not all(isinstance(value, str) and value for value in revisions)
                        or revisions[0] != revisions[1]):
                    raise AdapterRejected("observation-statefulset-unready:" + identity)
            elif health == "rollout-ready":
                replicas = resource.get("spec", {}).get("replicas")
                hashes = (status.get("currentPodHash"), status.get("stableRS"))
                if (resource.get("kind") != "Rollout"
                        or not isinstance(replicas, int) or replicas < 1
                        or any(status.get(field) != replicas for field in
                               ("replicas", "readyReplicas", "availableReplicas", "updatedReplicas"))
                        or not all(isinstance(value, str) and value for value in hashes)
                        or hashes[0] != hashes[1]
                        or condition_map.get("Available") != "True"):
                    raise AdapterRejected("observation-rollout-unready:" + identity)
            elif health in {"argocd-operation-succeeded", "argocd-application-ready"}:
                operation = status.get("operationState")
                sync_result = operation.get("syncResult") if isinstance(operation, dict) else None
                revision = sync_result.get("revision") if isinstance(sync_result, dict) else None
                sources = resource.get("spec", {}).get("sources")
                source = resource.get("spec", {}).get("source")
                revisions = []
                if isinstance(sources, list):
                    revisions.extend(item.get("targetRevision") for item in sources
                                     if isinstance(item, dict))
                if isinstance(source, dict):
                    revisions.append(source.get("targetRevision"))
                if (not isinstance(operation, dict) or operation.get("phase") != "Succeeded"
                        or not isinstance(operation.get("finishedAt"), str)
                        or not operation["finishedAt"] or not isinstance(revision, str)
                        or revision not in revisions):
                    raise AdapterRejected("observation-argocd-operation:" + identity)
                if health == "argocd-application-ready" and (
                        status.get("sync", {}).get("status") != "Synced"
                        or status.get("health", {}).get("status") != "Healthy"):
                    raise AdapterRejected("observation-argocd-not-ready:" + identity)
                condition_map["OperationPhase"] = operation["phase"]
                condition_map["OperationRevision"] = revision
                if health == "argocd-application-ready":
                    condition_map["Sync"] = status["sync"]["status"]
                    condition_map["Health"] = status["health"]["status"]
            elif health in {"node-cache-empty", "node-images-present"}:
                images = status.get("images")
                if not isinstance(images, list):
                    raise AdapterRejected("observation-node-images:" + identity)
                observed_names = {
                    name for image in images if isinstance(image, dict)
                    for name in image.get("names", []) if isinstance(name, str)
                }
                present = {digest for digest in contract["requiredImageDigests"]
                           if any(digest in name for name in observed_names)}
                if health == "node-cache-empty" and present:
                    raise AdapterRejected("observation-node-cache-not-empty:" + identity)
                if health == "node-images-present" and present != set(
                        contract["requiredImageDigests"]):
                    raise AdapterRejected("observation-node-images-missing:" + identity)
                condition_map["RequiredImageDigestSet"] = _mapping_digest(
                    sorted(contract["requiredImageDigests"]))
                condition_map["ObservedRequiredImageCount"] = len(present)
            normalized.append({
                "apiVersion": contract["apiVersion"], "identity": identity, "uid": uid,
                "generation": generation,
                "observedGeneration": observed_generation, "desiredSha256": desired_digest,
                "conditions": {key: condition_map.get(key) for key in sorted(contract["requiredConditions"])},
                "healthPolicy": health,
                "semanticStatus": {key: condition_map[key] for key in
                                   ("OperationPhase", "OperationRevision", "Sync", "Health",
                                    "RequiredImageDigestSet", "ObservedRequiredImageCount")
                                   if key in condition_map},
            })
        return normalized, _mapping_digest(normalized)

    def identity(self, target):
        if target["clusterName"] != self.plan["target"]["clusterName"] or target["context"] != self.plan["target"]["context"]:
            raise AdapterRejected("identity-target")
        # kubectl uses exit 1 when the fresh kubeconfig/context does not exist
        # before the cluster step.  On resume, a missing/unreachable identity
        # becomes ``None`` and the journal's recorded UID rejects the run.
        uid_results, uid_exists = self._run_commands(
            [self.plan["identity"]["clusterUID"]], incomplete_exit_codes={1, 3})
        cluster_uid = uid_results[-1].stdout.strip() if uid_exists else None
        # Context is not read from global kubectl state. Every approved command
        # carries the target context explicitly, so no context switch is needed.
        return {"context": self.plan["target"]["context"],
                "clusterUID": cluster_uid or None}

    def verify(self, step, checkpoint):
        if step == "preflight":
            evidence = checkpoint.get("evidence") if isinstance(checkpoint, dict) else None
            observations = evidence.get("resourceObservations") if isinstance(evidence, dict) else None
            if (not isinstance(observations, list) or len(observations) != 1
                    or observations[0].get("initialTargetAbsent") is not True
                    or observations[0].get("targetCluster") != self.plan["target"]["clusterName"]):
                raise AdapterRejected("preflight-checkpoint")
            digest = _mapping_digest(observations)
            return {"verified": True, "resourceDigest": digest,
                    "resourceObservations": observations}
        if step == "image-source":
            observed, digest = self._host_artifact_evidence(step)
            return {"verified": True, "resourceDigest": digest,
                    "resourceObservations": observed}
        if step == "empty-node-cache":
            evidence = checkpoint.get("evidence") if isinstance(checkpoint, dict) else None
            observations = evidence.get("resourceObservations") if isinstance(evidence, dict) else None
            if (not isinstance(observations, list) or len(observations) != 1
                    or observations[0].get("healthPolicy") != "node-cache-empty"
                    or observations[0].get("semanticStatus", {}).get(
                        "ObservedRequiredImageCount") != 0):
                raise AdapterRejected("empty-node-cache-checkpoint")
            digest = _mapping_digest(observations)
            return {"verified": True, "resourceDigest": digest,
                    "resourceObservations": observations}
        results, _ = self._run_commands(self.plan["steps"][step]["verify"])
        observed, digest = self._observe(results, self.plan["steps"][step]["observations"])
        return {"verified": True, "resourceDigest": digest,
                "resourceObservations": observed}

    def reconcile(self, step, checkpoint):
        if step == "preflight":
            observed, digest = self._preflight_evidence(
                self.plan["steps"][step]["reconcile"][0])
            return {"state": "succeeded", "evidence": {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed}, "resourceDigest": digest}
        if step == "image-source":
            observed, digest = self._host_artifact_evidence(step)
            return {"state": "succeeded", "evidence": {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed}, "resourceDigest": digest}
        results, complete = self._run_commands(
            self.plan["steps"][step]["reconcile"],
            incomplete_exit_codes=(frozenset() if step == "crds" else {3}),
            empty_stdout_is_incomplete=(step == "crds"))
        if not complete:
            return {"state": "incomplete"}
        observed, digest = self._observe(results, self.plan["steps"][step]["observations"])
        evidence = {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed}
        if step == "cluster":
            evidence["clusterUID"] = self.identity(self.plan["target"])["clusterUID"]
        return {"state": "succeeded", "evidence": evidence,
                "resourceDigest": digest}

    def run(self, step):
        if step == "preflight":
            observed, digest = self._preflight_evidence(self.plan["steps"][step]["run"][0])
            return {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed, "resourceDigest": digest,
                    "mutationInputDigests": {}}
        if step == "image-source":
            observed, digest = self._host_artifact_evidence(step)
            return {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed, "resourceDigest": digest,
                    "mutationInputDigests": {item["identity"]: item["sha256"]
                                             for item in observed}}
        if step == "empty-node-cache":
            verified, _ = self._run_commands(self.plan["steps"][step]["run"])
            observed, digest = self._observe(
                verified, self.plan["steps"][step]["observations"])
            return {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed, "resourceDigest": digest,
                    "mutationInputDigests": {}}
        results, _ = self._run_commands(self.plan["steps"][step]["run"])
        # Hash normalized post-mutation verification, not mutation stdout.
        verified, _ = self._run_commands(self.plan["steps"][step]["verify"])
        observed, digest = self._observe(verified, self.plan["steps"][step]["observations"])
        evidence = {"succeeded": True,
                    "resourceIdentities": self.plan["steps"][step]["resourceIdentities"],
                    "resourceObservations": observed,
                    "resourceDigest": digest,
                    "mutationInputDigests": {
                        _bound_content_path(command): command["contentSha256"]
                        for command in self.plan["steps"][step]["run"]
                        if command["contentSha256"] is not None}}
        if step == "cluster":
            cluster_uid = self.identity(self.plan["target"])["clusterUID"]
            if not cluster_uid:
                raise AdapterRejected("cluster-uid-missing")
            evidence["clusterUID"] = cluster_uid
        return evidence


def validate_plan(plan_path: Path, expected_target: dict,
                  expected_versions: dict,
                  expected_image_digests: set[str] | None = None,
                  expected_supply_contract: dict | None = None) -> dict:
    """Validate a plan and its tool bytes without executing any command."""
    return ApprovedLiveAdapter(plan_path, expected_target=expected_target,
                               expected_versions=expected_versions,
                               expected_image_digests=expected_image_digests,
                               expected_supply_contract=expected_supply_contract).plan

