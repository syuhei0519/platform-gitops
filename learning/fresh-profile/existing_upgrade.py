"""Approval-bound existing-cluster upgrade journal. No cluster creation.

Mutation is deliberately capability-gated until the separately reviewed
GitOps auto-sync pause/resume contract exists. Resume and rerun are read-only.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import io
import subprocess
import tarfile
import yaml
import copy
import secrets

PHASES = ("controller", "account")
PROTECTED = {"Job/account-db-role-bootstrap", "Job/account-migration", "ConfigMap/account-role-bootstrap", "PVC/account-data", "Secret/account-db-bootstrap", "Secret/account-db-migration", "Secret/account-db-runtime"}
EXTERNAL_SECRETS = {name for name in PROTECTED if name.startswith("Secret/")}
DESIRED_PROTECTED = (PROTECTED - EXTERNAL_SECRETS) | {"StatefulSet/account-postgresql", "Service/account-postgresql"}
CONTROLLER_RESOURCES = {
    "Deployment/fresh-control/argo-rollouts": {"spec"},
    "ConfigMap/fresh-control/argo-rollouts-config": {"data"},
    "Role/fresh-account/account-policy-reader": {"rules"},
    "RoleBinding/fresh-account/account-policy-reader": {"roleRef", "subjects"},
}
ACCOUNT_DESIRED_ORDER = ("root", "parent", "child")
CONTROLLER_ORDER = ("Role/fresh-account/account-policy-reader", "RoleBinding/fresh-account/account-policy-reader", "ConfigMap/fresh-control/argo-rollouts-config", "Deployment/fresh-control/argo-rollouts")
SYNC_ALLOWLIST = {"Rollout/account-api", "Service/account-api", "DestinationRule/account-api", "VirtualService/account-api", "AnalysisTemplate/account-api-policy"}
ROLLBACK_SYNC_ALLOWLIST = SYNC_ALLOWLIST - {"AnalysisTemplate/account-api-policy"}
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

class Rejected(ValueError): pass


def require(test, reason):
    if not test: raise Rejected(reason)


def read_json(path):
    path=Path(path)
    require(path.is_absolute() and path.is_file() and not path.is_symlink(), "input-path")
    value=json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value,dict), "input-object")
    return value


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def instant(value):
    try:
        t=datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(t.tzinfo is not None,"approval-time")
        return t.astimezone(timezone.utc)
    except (ValueError, AttributeError): raise Rejected("approval-time")


def validate(plan_path, approval_path, *, now=None):
    plan=read_json(plan_path); approval=read_json(approval_path)
    require(set(plan)=={"schemaVersion","target","tool","source","artifacts","protected","gitDesired","controllerResources","securityPolicy"}, "plan-fields")
    require(plan["schemaVersion"]=="platform.lab/existing-account-upgrade/v1", "plan-version")
    target=plan["target"]
    require(isinstance(target,dict) and set(target)=={"clusterName","context","clusterUID","applicationUID","parentApplicationUID","rootApplicationUID","controllerUID","rolloutUID"},"target-fields")
    require(isinstance(target["clusterName"],str) and target["clusterName"].startswith("learning-fresh-") and target["context"]=="kind-"+target["clusterName"] and all(isinstance(target[k],str) and target[k] for k in ("clusterUID","applicationUID","parentApplicationUID","rootApplicationUID","controllerUID","rolloutUID")),"target-identity")
    tool=plan["tool"]
    require(isinstance(tool,dict) and set(tool)=={"kubectlPath","kubectlSha256","kubeconfigPath"},"tool-fields")
    binary=Path(tool["kubectlPath"]); kubeconfig=Path(tool["kubeconfigPath"])
    require(binary.is_absolute() and binary.is_file() and not binary.is_symlink() and isinstance(tool["kubectlSha256"],str) and SHA64.fullmatch(tool["kubectlSha256"]) and sha(binary)==tool["kubectlSha256"],"tool-identity")
    require(kubeconfig.is_absolute() and kubeconfig.is_file() and not kubeconfig.is_symlink(),"kubeconfig-path")
    source=plan["source"]
    require(isinstance(source,dict) and set(source)=={"platformRevision","oldApplicationRevision","newApplicationRevision"} and all(isinstance(x,str) and SHA40.fullmatch(x) and x!="0"*40 for x in source.values()) and source["oldApplicationRevision"]!=source["newApplicationRevision"],"source-identity")
    artifacts=plan["artifacts"]
    require(isinstance(artifacts,dict) and set(artifacts)=={"oldControllerImageDigest","controllerImageDigest","binarySha256","bundleSha256","bundlePath"} and all(isinstance(artifacts[k],str) and DIGEST.fullmatch(artifacts[k]) and artifacts[k]!="sha256:"+"0"*64 for k in ("oldControllerImageDigest","controllerImageDigest")) and all(isinstance(artifacts[k],str) and SHA64.fullmatch(artifacts[k]) and artifacts[k]!="0"*64 for k in ("binarySha256","bundleSha256")),"artifact-identity")
    bundle=Path(artifacts["bundlePath"])
    require(bundle.is_absolute() and bundle.is_file() and not bundle.is_symlink() and sha(bundle)==artifacts["bundleSha256"],"bundle-content")
    desired=plan["gitDesired"]
    require(isinstance(desired,dict) and set(desired)=={"root", "parent", "child", "protectedDigests", "sourceRender", "limitedSync", "analysisTemplate"}, "git-desired-fields")
    require(desired["analysisTemplate"]=={"state":"absent"},"analysis-template-plan")
    sync=desired["limitedSync"]
    require(isinstance(sync,dict) and set(sync)=={"prune","resources"} and sync["prune"] is False and isinstance(sync["resources"],list) and len(sync["resources"])==len(SYNC_ALLOWLIST) and all(isinstance(x,str) for x in sync["resources"]) and set(sync["resources"])==SYNC_ALLOWLIST,"limited-sync-scope")
    root=desired["root"]
    require(isinstance(root,dict) and set(root)=={"uid","resourceVersion","revision","automated"} and root["uid"]==target["rootApplicationUID"] and isinstance(root["resourceVersion"],str) and root["resourceVersion"] and isinstance(root["revision"],str) and SHA40.fullmatch(root["revision"]) and root["automated"] is None,"git-desired-root")
    for name, uid in (("parent",target["parentApplicationUID"]), ("child",target["applicationUID"])):
        app=desired[name]
        require(isinstance(app,dict) and set(app)=={"uid","resourceVersion","oldRevision","newRevision","automated"}, "git-desired-"+name)
        require(app["uid"]==uid and isinstance(app["resourceVersion"],str) and bool(app["resourceVersion"]) and all(isinstance(app[k],str) and SHA40.fullmatch(app[k]) and app[k]!="0"*40 for k in ("oldRevision","newRevision")), "git-desired-identity:"+name)
        require(app["automated"] is None or isinstance(app["automated"],dict), "git-desired-autosync:"+name)
    require(desired["parent"]["oldRevision"]!=desired["parent"]["newRevision"] and desired["child"]["oldRevision"]==source["oldApplicationRevision"] and desired["child"]["newRevision"]==source["newApplicationRevision"], "git-desired-revisions")
    render=desired["sourceRender"]
    require(isinstance(render,dict) and set(render)=={"platformRevision","chartRevision","workloadRevision","newWorkloadRevision","prePause","transitions","runtimeImage","gitPath","gitSha256","helmPath","helmSha256","kustomizePath","kustomizeSha256"},"git-render-fields")
    require(render["platformRevision"]==source["platformRevision"] and render["workloadRevision"]==desired["child"]["oldRevision"] and render["newWorkloadRevision"]==desired["child"]["newRevision"],"git-render-revisions")
    pre=render["prePause"]
    require(isinstance(pre,dict) and set(pre)=={"platformRevision","chartRevision","workloadRevision"} and all(isinstance(v,str) and SHA40.fullmatch(v) and v!="0"*40 for v in pre.values()) and pre["platformRevision"]==desired["root"]["revision"] and pre["chartRevision"]==desired["parent"]["oldRevision"] and pre["workloadRevision"]==desired["child"]["oldRevision"],"git-render-pre-pause")
    require(isinstance(render["runtimeImage"],str) and re.fullmatch(r"ghcr\.io/syuhei0519/backend-app@sha256:[0-9a-f]{64}",render["runtimeImage"]),"git-render-runtime-image")
    transitions=render["transitions"]
    require(isinstance(transitions,dict) and set(transitions)=={"newPaused","finalAutomated","rollbackOld"},"git-render-transitions")
    for name,state in transitions.items():
        require(isinstance(state,dict) and set(state)=={"platformRevision","chartRevision"} and all(isinstance(v,str) and SHA40.fullmatch(v) and v!="0"*40 for v in state.values()),"git-render-transition:"+name)
    require(transitions["newPaused"]["chartRevision"]==desired["parent"]["newRevision"],"git-render-new-parent-revision")
    require(transitions["rollbackOld"]=={"platformRevision":pre["platformRevision"],"chartRevision":pre["chartRevision"]},"git-render-rollback-not-original")
    for tool_name in ("git","helm","kustomize"):
        path=Path(render[tool_name+"Path"]); digest=render[tool_name+"Sha256"]
        require(path.is_absolute() and path.is_file() and not path.is_symlink() and isinstance(digest,str) and SHA64.fullmatch(digest) and sha(path)==digest,"git-render-tool:"+tool_name)
    digests=desired["protectedDigests"]
    require(isinstance(digests,dict) and set(digests)==DESIRED_PROTECTED, "git-desired-protected-set")
    for name, item in digests.items():
        require(isinstance(item,dict) and set(item)=={"old","new"} and isinstance(item["old"],str) and SHA64.fullmatch(item["old"]) and item["old"]!="0"*64 and item["old"]==item["new"], "git-desired-protected-digest:"+name)
    policy=plan["securityPolicy"]
    require(isinstance(policy,dict) and set(policy)=={"uid","resourceVersion","oldSpec","desiredHashes"} and isinstance(policy["uid"],str) and policy["uid"] and isinstance(policy["resourceVersion"],str) and policy["resourceVersion"] and isinstance(policy["oldSpec"],dict),"security-policy-fields")
    require(isinstance(policy["desiredHashes"],dict) and set(policy["desiredHashes"])=={"initial","newPaused","finalAutomated","rollbackOld"} and all(isinstance(h,str) and SHA64.fullmatch(h) and h!="0"*64 for h in policy["desiredHashes"].values()),"security-policy-hashes")
    require(len(set(policy["desiredHashes"].values()))==4 and policy_digest(policy["oldSpec"])!=policy["desiredHashes"]["initial"],"security-policy-stage-distinct")
    controller=plan["controllerResources"]
    require(isinstance(controller,dict) and set(controller)==set(CONTROLLER_RESOURCES),"controller-resources-set")
    for identity, fields in CONTROLLER_RESOURCES.items():
        item=controller[identity]
        require(isinstance(item,dict) and item.get("state") in {"present","absent"},"controller-resource:"+identity)
        if item["state"]=="absent":
            require(identity.startswith(("Role/","RoleBinding/")) and set(item)=={"state"},"controller-resource-absent:"+identity)
        else:
            require(set(item)=={"state","uid","resourceVersion","rollback"} and isinstance(item["uid"],str) and item["uid"] and isinstance(item["resourceVersion"],str) and item["resourceVersion"] and isinstance(item["rollback"],dict) and set(item["rollback"])==fields,"controller-resource-identity:"+identity)
            for field,value in item["rollback"].items():
                require(isinstance(value,list if field in {"rules","subjects"} else dict),"controller-resource-rollback:"+identity)
            if identity.startswith("Deployment/"): require(item["uid"]==target["controllerUID"],"controller-resource-uid")
    protected=plan["protected"]
    require(isinstance(protected,dict) and set(protected)==PROTECTED,"protected-set")
    for name, item in protected.items():
        require(isinstance(item,dict) and set(item)=={"uid","resourceVersion"} and all(isinstance(v,str) and v for v in item.values()), "protected-identity:"+name)
    require(set(approval)=={"schemaVersion","approved","approvedBy","approvedAt","expiresAt","planSha256"} and approval["schemaVersion"]=="platform.lab/existing-account-upgrade-approval/v1" and approval["approved"] is True and isinstance(approval["approvedBy"],str) and bool(approval["approvedBy"].strip()) and approval["planSha256"]==sha(plan_path),"approval-binding")
    now=now or datetime.now(timezone.utc)
    require(now.tzinfo is not None and instant(approval["approvedAt"])<=now<instant(approval["expiresAt"]) and instant(approval["approvedAt"])<instant(approval["expiresAt"]),"approval-expired")
    return plan, sha(plan_path), sha(approval_path)


def _rewrite_application_policy(spec, platform_revision, chart_revision, workload_revision,
                                runtime_image, sync_mode, variant, promotion_mode="manual"):
    """Rewrite only the reviewed pins in a sealed application-boundary spec."""
    spec=copy.deepcopy(spec)
    require(sync_mode in {"paused","automated"} and variant in {"fresh","existing-upgrade"} and promotion_mode in {"manual","auto"},"security-policy-mode")
    first=spec["validations"][0]["expression"]
    release_marker="(object.metadata.name == 'fresh-release-definitions'"
    observability_marker="(object.metadata.name == 'fresh-observability'"
    account_marker="(object.metadata.name == 'fresh-account-release'"
    require(first.count(release_marker)==first.count(observability_marker)==first.count(account_marker)==1,"security-policy-template-pins")
    prefix,release=first.split(release_marker,1)
    release,tail=release.split(observability_marker,1)
    observability,account=tail.split(account_marker,1)
    release,count=re.subn(r"object\.spec\.source\.targetRevision == '[0-9a-f]{40}'", "object.spec.source.targetRevision == '"+chart_revision+"'", release)
    require(count==1,"security-policy-template-pins")
    account,count=re.subn(r"object\.spec\.source\.targetRevision == '[0-9a-f]{40}'", "object.spec.source.targetRevision == '"+workload_revision+"'", account)
    require(count==1,"security-policy-template-pins")
    account,count=re.subn(r"object\.spec\.source\.path == 'learning/fresh-profile/workload(?:-existing)?(?:-auto)?'", "object.spec.source.path == 'learning/fresh-profile/workload"+("-existing" if variant=="existing-upgrade" else "")+("-auto" if promotion_mode=="auto" else "")+"'", account)
    require(count==1,"security-policy-template-pins")
    first=prefix+release_marker+release+observability_marker+observability+account_marker+account
    first=first.replace("__PLATFORM_GITOPS_SHA__",platform_revision)
    spec["validations"][0]["expression"]=first
    second=spec["validations"][1]["expression"]
    replacements=((r"p\.name == 'workloadRevision' && p\.value == '[0-9a-f]{40}'", "p.name == 'workloadRevision' && p.value == '"+workload_revision+"'",1),
                  (r"p\.name == 'promotionMode' && p\.value == '(?:manual|auto)'", "p.name == 'promotionMode' && p.value == '"+promotion_mode+"'",1),
                  (r"p\.name == 'syncMode' && p\.value == '(?:paused|automated)'", "p.name == 'syncMode' && p.value == '"+sync_mode+"'",1),
                  (r"p\.name == 'workloadVariant' && p\.value == '(?:fresh|existing-upgrade)'", "p.name == 'workloadVariant' && p.value == '"+variant+"'",1),
                  (r"ghcr\.io/syuhei0519/backend-app@sha256:[0-9a-f]{64}",runtime_image,2))
    for pattern,replacement,expected_count in replacements:
        second,count=re.subn(pattern,replacement,second)
        require(count==expected_count,"security-policy-template-pins")
    spec["validations"][1]["expression"]=second
    return spec


def build_reviewed_policy(platform_revision, chart_revision, workload_revision, runtime_image, sync_mode, variant, promotion_mode="manual"):
    """Materialize only reviewed pins in the sealed existing policy template."""
    template=Path(__file__).resolve().parent/"security/admission.yaml"
    try: docs=list(yaml.safe_load_all(template.read_text(encoding="utf-8")))
    except (OSError,yaml.YAMLError) as error: raise Rejected("security-policy-template") from error
    policies=[d for d in docs if isinstance(d,dict) and d.get("kind")=="ValidatingAdmissionPolicy" and d.get("metadata",{}).get("name")=="fresh-application-boundary"]
    require(len(policies)==1,"security-policy-template")
    return _rewrite_application_policy(policies[0]["spec"],platform_revision,chart_revision,workload_revision,runtime_image,sync_mode,variant,promotion_mode)


def policy_digest(spec):
    return hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()


def verify_reviewed_render(plan, platform_root, application_root):
    """Render a stopped child from immutable platform and application Git trees."""
    render=plan["gitDesired"]["sourceRender"]
    for tool in ("git","helm","kustomize"):
        require(sha(render[tool+"Path"])==render[tool+"Sha256"],"git-render-tool-changed:"+tool)
    with tempfile.TemporaryDirectory(prefix="upgrade-render-") as temp:
        env={"PATH":"/usr/bin:/bin", "HOME":temp, "GIT_CONFIG_NOSYSTEM":"1", "GIT_CONFIG_GLOBAL":"/dev/null", "GIT_ATTR_NOSYSTEM":"1", "HELM_CACHE_HOME":temp+"/cache", "HELM_CONFIG_HOME":temp+"/config", "HELM_DATA_HOME":temp+"/data"}
        def snapshot(root, revision, name):
            root=Path(root)
            require(root.is_absolute() and root.is_dir() and not root.is_symlink(),"git-render-root")
            try:
                identity=subprocess.run([render["gitPath"],"-C",str(root),"cat-file","-t",revision],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
                require(identity.returncode==0 and identity.stdout.strip()==b"commit","git-render-unknown-revision")
                result=subprocess.run([render["gitPath"],"-C",str(root),"archive","--format=tar",revision],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
            except (OSError,subprocess.SubprocessError) as error: raise Rejected("git-render-archive") from error
            require(result.returncode==0 and len(result.stdout)<=30_000_000,"git-render-unknown-revision")
            dest=Path(temp)/name;dest.mkdir()
            try:
                with tarfile.open(fileobj=io.BytesIO(result.stdout),mode="r:") as archive:
                    for member in archive:
                        path=Path(member.name)
                        require(not path.is_absolute() and ".." not in path.parts and (member.isfile() or member.isdir()),"git-render-archive-path")
                        target=dest/path
                        if member.isdir(): target.mkdir(parents=True,exist_ok=True)
                        else:
                            require(member.size<=10_000_000,"git-render-archive-size")
                            target.parent.mkdir(parents=True,exist_ok=True)
                            stream=archive.extractfile(member);require(stream is not None,"git-render-archive-file")
                            target.write_bytes(stream.read())
            except (tarfile.TarError,OSError) as error: raise Rejected("git-render-archive") from error
            return dest
        old_workload=snapshot(application_root,render["workloadRevision"],"old-workload")
        new_workload=snapshot(application_root,render["newWorkloadRevision"],"new-workload")
        def render_state(name, platform_revision, chart_revision, workload_revision, sync_mode, variant, *, legacy=False):
            platform=snapshot(platform_root,platform_revision,"platform-"+name)
            app=snapshot(application_root,chart_revision,"chart-"+name)
            policy_file=platform/"learning/fresh-profile/security/admission.yaml"
            try: docs=list(yaml.safe_load_all(policy_file.read_text(encoding="utf-8")))
            except (OSError,yaml.YAMLError) as error: raise Rejected("security-policy-source") from error
            policies=[d for d in docs if isinstance(d,dict) and d.get("kind")=="ValidatingAdmissionPolicy" and d.get("metadata",{}).get("name")=="fresh-application-boundary"]
            require(len(policies)==1,"security-policy-source")
            policy=copy.deepcopy(policies[0]["spec"])
            for validation in policy.get("validations",[]):
                if isinstance(validation,dict) and isinstance(validation.get("expression"),str):
                    validation["expression"]=validation["expression"].replace("__PLATFORM_GITOPS_SHA__",platform_revision)
            expected=plan["securityPolicy"]["oldSpec"] if legacy else build_reviewed_policy(platform_revision,chart_revision,workload_revision,render["runtimeImage"],sync_mode,variant)
            require(policy==expected,"security-policy-source-mismatch:"+name)
            if name!="pre-pause":
                policy_name={"initial":"initial","new-paused":"newPaused","final-automated":"finalAutomated","rollback-old":"rollbackOld"}[name]
                require(policy_digest(policy)==plan["securityPolicy"]["desiredHashes"][policy_name],"security-policy-digest:"+name)
            bootstrap_file=platform/"learning/fresh-profile/root/bootstrap.yaml"
            try: bootstrap=list(yaml.safe_load_all(bootstrap_file.read_text()))
            except (OSError,yaml.YAMLError) as error: raise Rejected("git-render-root") from error
            roots=[obj for obj in bootstrap if isinstance(obj,dict) and obj.get("kind")=="Application" and obj.get("metadata",{}).get("name")=="account-lightweight-root"]
            require(len(roots)==1 and roots[0].get("metadata",{}).get("namespace")=="fresh-control","git-render-root")
            root_spec=roots[0].get("spec",{});root_source=root_spec.get("source",{})
            require(root_source.get("repoURL")=="https://github.com/syuhei0519/platform-gitops.git" and root_source.get("path")=="learning/fresh-profile/root/children" and root_source.get("targetRevision")=="__PLATFORM_GITOPS_SHA__" and "automated" not in root_spec.get("syncPolicy",{}) and "Prune=false" in root_spec.get("syncPolicy",{}).get("syncOptions",[]),"git-render-root-source")
            root_source["targetRevision"]=platform_revision
            parent_file=platform/"learning/fresh-profile/root/children/applications.yaml"
            try: parent=yaml.safe_load(parent_file.read_text())
            except (OSError,yaml.YAMLError) as error: raise Rejected("git-render-parent") from error
            require(isinstance(parent,dict) and parent.get("kind")=="Application" and parent.get("metadata",{}).get("name")=="fresh-release-definitions" and parent.get("metadata",{}).get("namespace")=="fresh-control","git-render-parent")
            spec=parent.get("spec",{}); source=spec.get("source",{})
            require("automated" not in spec.get("syncPolicy",{}) and "Prune=false" in spec.get("syncPolicy",{}).get("syncOptions",[]),"git-render-parent-autosync")
            require(source.get("repoURL")=="https://github.com/syuhei0519/application-manifest.git" and source.get("path")=="learning/fresh-profile/release-definition" and source.get("targetRevision")==chart_revision,"git-render-parent-source")
            helm=source.get("helm",{})
            require(isinstance(helm,dict) and set(helm)=={"releaseName","parameters"} and helm["releaseName"]=="fresh-account-release-definition","git-render-helm-settings")
            parameters=helm["parameters"]
            require(isinstance(parameters,list) and all(isinstance(p,dict) and set(p)=={"name","value"} for p in parameters),"git-render-parameters")
            params={p["name"]:p["value"] for p in parameters}
            keys={"workloadRevision","accountRuntimeImage"} if legacy else {"workloadRevision","accountRuntimeImage","promotionMode","syncMode","workloadVariant"}
            require(len(params)==len(parameters) and set(params)==keys and params["workloadRevision"]==workload_revision and (legacy or (params["promotionMode"]=="manual" and params["syncMode"]==sync_mode and params["workloadVariant"]==variant)) and all(isinstance(v,str) and v for v in params.values()) and params["accountRuntimeImage"]==render["runtimeImage"],"git-render-parameters")
            chart=app/"learning/fresh-profile/release-definition"
            require(chart.is_dir() and not chart.is_symlink(),"git-render-chart-path")
            args=[render["helmPath"],"template","fresh-account-release-definition",str(chart),"--namespace","fresh-control"]
            for key in sorted(params): args.extend(["--set-string",key+"="+params[key]])
            try: result=subprocess.run(args,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
            except (OSError,subprocess.SubprocessError) as error: raise Rejected("git-render-helm") from error
            require(result.returncode==0 and len(result.stdout)<=5_000_000,"git-render-helm")
            try: documents=list(yaml.safe_load_all(result.stdout))
            except yaml.YAMLError as error: raise Rejected("git-render-child") from error
            objects=[d for d in documents if d is not None]
            require(len(objects)==1 and isinstance(objects[0],dict),"git-render-child")
            child=objects[0]; child_spec=child.get("spec",{}); child_source=child_spec.get("source",{})
            require(child.get("kind")=="Application" and child.get("metadata",{}).get("name")=="fresh-account-release" and child.get("metadata",{}).get("namespace")=="fresh-control","git-render-child")
            sync_policy=child_spec.get("syncPolicy",{})
            require(isinstance(sync_policy,dict) and (legacy or "Prune=false" in sync_policy.get("syncOptions",[])),"git-render-child-autosync")
            if sync_mode=="paused": require("automated" not in sync_policy,"git-render-child-autosync")
            else: require(sync_policy.get("automated")=={"prune":False,"selfHeal":True},"git-render-child-autosync")
            if variant=="existing-upgrade":require("ApplyOutOfSyncOnly=true" in sync_policy.get("syncOptions",[]),"git-render-child-apply-scope")
            require(child_source.get("repoURL")==source["repoURL"] and child_source.get("path")==("learning/fresh-profile/workload-existing" if variant=="existing-upgrade" else "learning/fresh-profile/workload") and child_source.get("targetRevision")==workload_revision,"git-render-child-source")
            return {"rootSpec":root_spec,"parentSpec":spec,"childSpec":child_spec,"policySpec":policy}
        pre=render["prePause"]
        pre_pause=render_state("pre-pause",pre["platformRevision"],pre["chartRevision"],pre["workloadRevision"],"automated","fresh",legacy=True)
        initial=render_state("initial",render["platformRevision"],render["chartRevision"],render["workloadRevision"],"paused","fresh")
        def protected_hashes(snapshot_root, path_name):
            path=snapshot_root/"learning/fresh-profile"/path_name
            require(path.is_dir() and not path.is_symlink(),"git-render-workload-path")
            try:
                result=subprocess.run([render["kustomizePath"],"kustomize",str(path)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
            except (OSError,subprocess.SubprocessError) as error: raise Rejected("git-render-kustomize") from error
            require(result.returncode==0 and len(result.stdout)<=10_000_000,"git-render-kustomize")
            try: documents=list(yaml.safe_load_all(result.stdout))
            except yaml.YAMLError as error: raise Rejected("git-render-workload") from error
            found={};sync_found=set();unchanged={}
            groups={"Rollout":"argoproj.io","Service":"","DestinationRule":"networking.istio.io","VirtualService":"networking.istio.io","AnalysisTemplate":"argoproj.io"}
            for item in documents:
                if item is None: continue
                require(isinstance(item,dict),"git-render-workload")
                kind=item.get("kind"); metadata=item.get("metadata",{})
                if not isinstance(metadata,dict): raise Rejected("git-render-workload")
                name=metadata.get("name"); namespace=metadata.get("namespace")
                alias="PVC" if kind=="PersistentVolumeClaim" else kind
                key=str(alias)+"/"+str(name)
                require(key not in EXTERNAL_SECRETS or namespace not in (None,"fresh-account"),"git-render-external-secret-present")
                if path_name=="workload" and key=="Rollout/account-api":
                    def references_retained(value):
                        if isinstance(value,dict):
                            return any((key=="templateName" and child=="account-api-policy") or references_retained(child) for key,child in value.items())
                        if isinstance(value,list):return any(references_retained(child) for child in value)
                        return False
                    require(not references_retained(item.get("spec",{})),"git-render-old-analysis-reference")
                if key in SYNC_ALLOWLIST:
                    version=item.get("apiVersion");group=version.split("/",1)[0] if isinstance(version,str) and "/" in version else ""
                    require(namespace=="fresh-account" and key not in sync_found and group==groups.get(kind),"git-render-sync-identity")
                    if key=="AnalysisTemplate/account-api-policy":
                        require(path_name=="workload-existing","git-render-old-analysis-template")
                        annotations=metadata.get("annotations",{})
                        require(isinstance(annotations,dict) and annotations.get("argocd.argoproj.io/compare-options")=="IgnoreExtraneous" and annotations.get("argocd.argoproj.io/sync-options")=="Prune=false,Delete=false","git-render-analysis-retention")
                    sync_found.add(key)
                if key not in SYNC_ALLOWLIST:
                    version=item.get("apiVersion")
                    require(isinstance(version,str) and version and isinstance(kind,str) and kind and isinstance(name,str) and name and isinstance(namespace,str) and namespace,"git-render-object-identity")
                    full=(version,kind,namespace,name)
                    require(full not in unchanged,"git-render-object-duplicate")
                    unchanged[full]=hashlib.sha256(json.dumps(item,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
                if key not in DESIRED_PROTECTED: continue
                require(namespace=="fresh-account" and key not in found,"git-render-protected-identity")
                encoded=json.dumps(item,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
                found[key]=hashlib.sha256(encoded).hexdigest()
            require(set(found)==DESIRED_PROTECTED,"git-render-protected-missing")
            require(sync_found==(SYNC_ALLOWLIST if path_name=="workload-existing" else ROLLBACK_SYNC_ALLOWLIST),"git-render-sync-missing")
            return found,unchanged
        old_hashes,old_unchanged=protected_hashes(old_workload,"workload");new_hashes,new_unchanged=protected_hashes(new_workload,"workload-existing")
        for key in DESIRED_PROTECTED:
            expected=plan["gitDesired"]["protectedDigests"][key]
            require(old_hashes[key]==new_hashes[key] and old_hashes[key]==expected["old"] and new_hashes[key]==expected["new"],"git-render-protected-mismatch:"+key)
        require(old_unchanged==new_unchanged,"git-render-nonallowlisted-change")
        transitions=render["transitions"]
        new_paused=render_state("new-paused",transitions["newPaused"]["platformRevision"],transitions["newPaused"]["chartRevision"],render["newWorkloadRevision"],"paused","existing-upgrade")
        final_automated=render_state("final-automated",transitions["finalAutomated"]["platformRevision"],transitions["finalAutomated"]["chartRevision"],render["newWorkloadRevision"],"automated","existing-upgrade")
        rollback_old=render_state("rollback-old",transitions["rollbackOld"]["platformRevision"],transitions["rollbackOld"]["chartRevision"],render["workloadRevision"],"automated","fresh",legacy=True)
    return {**initial,"prePause":pre_pause,"transitions":{"newPaused":new_paused,"finalAutomated":final_automated,"rollbackOld":rollback_old}}


def check_observation(plan, obs, *, initial=False):
    require(isinstance(obs,dict) and set(obs)=={"target","applicationRevision","controllerImageDigest","protected","completedJobs"},"observation-fields")
    require(obs["target"]==plan["target"],"observed-target-mismatch")
    require(obs["protected"]==plan["protected"],"protected-drift")
    require(set(obs["completedJobs"])=={"Job/account-db-role-bootstrap","Job/account-migration"} and all(v is True for v in obs["completedJobs"].values()),"job-not-complete")
    allowed={plan["source"]["oldApplicationRevision"], plan["source"]["newApplicationRevision"]}
    require(obs["applicationRevision"] in allowed,"observed-revision-unapproved")
    if initial: require(obs["applicationRevision"]==plan["source"]["oldApplicationRevision"],"initial-revision")
    require(obs["controllerImageDigest"] in {plan["artifacts"]["oldControllerImageDigest"],plan["artifacts"]["controllerImageDigest"]},"observed-controller-digest")
    if initial: require(obs["controllerImageDigest"] == plan["artifacts"]["oldControllerImageDigest"],"initial-controller-digest")


def check_completed(plan, journal, obs, executor):
    if journal["phases"]["controller"]["state"] == "succeeded":
        require(obs["controllerImageDigest"] == plan["artifacts"]["controllerImageDigest"], "completed-controller-drift")
        require(executor.verify_controller_runtime(plan), "completed-controller-runtime-drift")
    if journal["phases"]["account"]["state"] == "succeeded":
        require(obs["applicationRevision"] == plan["source"]["newApplicationRevision"], "completed-application-drift")


def save(path, journal):
    path=Path(path); require(path.is_absolute() and not path.is_symlink(),"journal-path")
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix=".upgrade-journal-",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(journal,f,sort_keys=True,indent=2);f.write("\n");f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def _execute_locked(action, plan_path, approval_path, journal_path, executor, *, now=None):
    require(action in {"observe","first","continue","resume","rerun","pause","policy-initial","policy-new-paused","policy-final-automated","policy-rollback-paused","policy-rollback-old","account-continue","restore-continue","rollback-continue","rollback-restore","prepare-continue"},"action")
    plan, plan_hash, approval_hash=validate(plan_path,approval_path,now=now)
    journal_path=Path(journal_path); require(journal_path.is_absolute() and not journal_path.is_symlink(),"journal-path")
    require(getattr(executor,"upgrade_plan_sha256",None)==plan_hash,"executor-plan-binding")
    policy_actions={"policy-initial":"initial","policy-new-paused":"newPaused","policy-final-automated":"finalAutomated","policy-rollback-paused":"rollbackPaused","policy-rollback-old":"rollbackOld"}
    existing=read_json(journal_path) if journal_path.exists() else None
    nested_policy=(existing.get("policy") if isinstance(existing,dict) and existing.get("schemaVersion")=="platform.lab/existing-upgrade-pause-journal/v1" else None)
    policy_resume=(action=="resume" and isinstance(existing,dict) and (existing.get("schemaVersion")=="platform.lab/existing-upgrade-policy-journal/v1" or (isinstance(nested_policy,dict) and any(x.get("state") in {"running","uncertain"} for x in nested_policy.get("steps",{}).values()))))
    if action in policy_actions or policy_resume:
        require(getattr(executor,"supports_existing_policy_mutations",False) is True,"security-policy-capability-unavailable")
        require(executor.verify_reviewed_render(plan),"git-render-not-verified")
        obs=executor.observe();check_observation(plan,obs)
        names=("initial","newPaused","finalAutomated","rollbackPaused","rollbackOld")
        outer=existing if isinstance(nested_policy,dict) else None
        if outer is not None:
            require(set(outer)=={"schemaVersion","planSha256","approvalSha256","approvalRenewals","state","attempts","policy","prepareSteps","controllerSteps","accountSteps","accountSync","restoreSteps","rollbackSteps","rollbackSync","controllerRollbackSteps","rollbackRestoreSteps","retainedAnalysisUID","rollbackFrom","outcome"} and outer["planSha256"]==plan_hash and outer["state"]=="succeeded" and outer["attempts"]==1 and isinstance(outer["controllerSteps"],dict) and set(outer["controllerSteps"])==set(CONTROLLER_ORDER),"combined-journal-binding")
            account=outer.get("accountSteps")
            require(isinstance(account,dict) and set(account)==set(ACCOUNT_DESIRED_ORDER),"account-journal-steps")
            if outer.get("outcome")=="rolling-back":
                origin=outer.get("rollbackFrom")
                require(origin in {"newPaused","finalAutomated"},"rollback-origin")
                completed={name for name in ACCOUNT_DESIRED_ORDER if outer["rollbackSteps"][name].get("state")=="succeeded"}
                uncertain=[name for name in ACCOUNT_DESIRED_ORDER if outer["rollbackSteps"][name].get("state") in {"running","uncertain"}]
                require(len(uncertain)<=1 and executor.verify_desired_chain(plan,completed,uncertain[0] if uncertain else None,before_stage=origin,after_stage="initial"),"rollback-chain-drift")
            elif outer.get("outcome")=="upgraded":
                require(executor.verify_desired_chain(plan,set(ACCOUNT_DESIRED_ORDER),before_stage="newPaused",after_stage="finalAutomated"),"restore-chain-drift")
            elif any(step.get("state")!="pending" for step in account.values()):
                completed={name for name in ACCOUNT_DESIRED_ORDER if account[name].get("state")=="succeeded"}
                uncertain=[name for name in ACCOUNT_DESIRED_ORDER if account[name].get("state") in {"running","uncertain"}]
                require(len(uncertain)<=1 and executor.verify_desired_chain(plan,completed,uncertain[0] if uncertain else None),"account-transition-chain-drift")
            else: require(executor.observe_child_autosync_paused(plan),"pause-completed-drift")
        def save_policy(value):
            if outer is not None: outer["policy"]=value;save(journal_path,outer)
            else: save(journal_path,value)
        if not journal_path.exists():
            require(action=="policy-initial","policy-journal-required")
            journal={"schemaVersion":"platform.lab/existing-upgrade-policy-journal/v1","planSha256":plan_hash,"approvalSha256":approval_hash,"approvalRenewals":[],"current":"old","steps":{name:{"state":"pending","attempts":0} for name in names}}
            save_policy(journal)
        else:
            require(action!="policy-initial","journal-exists")
            journal=nested_policy if outer is not None else existing
            require(set(journal)=={"schemaVersion","planSha256","approvalSha256","approvalRenewals","current","steps"} and journal["schemaVersion"]=="platform.lab/existing-upgrade-policy-journal/v1" and journal["planSha256"]==plan_hash and isinstance(journal["approvalSha256"],str) and SHA64.fullmatch(journal["approvalSha256"]) and isinstance(journal["approvalRenewals"],list) and all(isinstance(h,str) and SHA64.fullmatch(h) for h in journal["approvalRenewals"]) and journal["current"] in {"old",*names} and isinstance(journal["steps"],dict) and set(journal["steps"])==set(names),"security-policy-journal-binding")
            for name in names:
                step=journal["steps"][name]
                require(isinstance(step,dict) and set(step)=={"state","attempts"} and ((step["state"]=="pending" and step["attempts"]==0) or (step["state"] in {"running","uncertain","succeeded"} and step["attempts"]==1)),"security-policy-journal-step")
        renewed=approval_hash not in [journal["approvalSha256"],*journal["approvalRenewals"]]
        if renewed:
            require(action=="resume","approval-renewal-requires-resume")
            journal["approvalRenewals"].append(approval_hash)
        if action=="resume":
            for name in names:
                step=journal["steps"][name]
                if step["state"] in {"running","uncertain"}:
                    if executor.verify_security_policy(plan,name):step["state"]="succeeded";journal["current"]=name
                    else:step["state"]="uncertain"
            if journal["current"]!="old":require(executor.verify_security_policy(plan,journal["current"]),"security-policy-completed-drift")
            save_policy(journal)
            return journal
        require(all(step["state"] not in {"running","uncertain"} for step in journal["steps"].values()),"security-policy-uncertain-requires-resume")
        stage=policy_actions[action];step=journal["steps"][stage]
        if outer is None and stage!="initial":
            require(getattr(executor,"supports_synthetic_standalone_policy",False) is True,"combined-journal-required")
        if outer is not None:
            if stage=="newPaused":
                require(all(item.get("state")=="succeeded" for item in outer.get("controllerSteps",{}).values()) and len(outer.get("controllerSteps",{}))==len(CONTROLLER_ORDER),"controller-not-complete")
                for identity in CONTROLLER_ORDER:
                    item=outer["controllerSteps"][identity]
                    require(isinstance(item,dict) and set(item)=={"state","attempts","uid"} and item["attempts"]==1 and isinstance(item["uid"],str) and bool(item["uid"]) and executor.verify_controller_resource(identity,plan)==item["uid"],"controller-completed-drift:"+identity)
            if stage=="finalAutomated":
                sync=outer.get("accountSync")
                require(all(outer["accountSteps"][name].get("state")=="succeeded" for name in ACCOUNT_DESIRED_ORDER) and isinstance(sync,dict) and sync.get("state")=="succeeded" and isinstance(sync.get("nonce"),str) and executor.verify_ready_to_restore(plan,sync["nonce"]) and executor.verify_retained_analysis_template(plan,outer["retainedAnalysisUID"]),"account-not-ready-to-restore")
            if stage=="rollbackPaused":
                sync=outer.get("accountSync")
                require(isinstance(sync,dict) and isinstance(sync.get("nonce"),str),"rollback-precondition")
                if outer.get("outcome")=="upgraded":
                    require(sync.get("state")=="succeeded" and executor.verify_account_sync(plan,sync["nonce"]) and executor.verify_full_account_synced(plan) and executor.verify_retained_analysis_template(plan,outer["retainedAnalysisUID"]),"rollback-precondition")
                    outer["rollbackFrom"]="finalAutomated"
                else:
                    require(outer.get("outcome")=="in-progress" and journal["current"]=="newPaused" and sync.get("state") in {"uncertain","succeeded"} and all(outer["accountSteps"][name].get("state")=="succeeded" for name in ACCOUNT_DESIRED_ORDER) and executor.verify_account_sync_terminal(plan,sync["nonce"]),"rollback-precondition")
                    known=outer["retainedAnalysisUID"];observed=executor.retained_analysis_template_uid(plan)
                    if known is not None:require(observed==known,"analysis-template-retention-drift")
                    elif observed is not None:
                        require(executor.verify_terminal_analysis_applied(plan,sync["nonce"]),"analysis-template-ownership-unknown")
                        outer["retainedAnalysisUID"]=observed
                    else:require(executor.verify_analysis_absent(plan),"analysis-template-ownership-unknown")
                    outer["rollbackFrom"]="newPaused"
                outer["outcome"]="rolling-back"
            if stage=="rollbackOld":
                sync=outer.get("rollbackSync")
                require(outer.get("outcome")=="rolling-back" and isinstance(sync,dict) and sync.get("state")=="succeeded" and executor.verify_ready_to_restore(plan,sync["nonce"],stage="initial") and executor.verify_analysis_retention_state(plan,outer["retainedAnalysisUID"]) and all(outer["controllerRollbackSteps"][name].get("state")=="succeeded" and executor.verify_controller_rollback_resource(name,plan,outer["controllerSteps"][name]["uid"]) for name in CONTROLLER_ORDER),"rollback-not-ready-to-restore")
        require(step["state"]=="pending","security-policy-stage-already-attempted")
        allowed_previous={"initial":{"old"},"newPaused":{"initial"},"finalAutomated":{"newPaused"},"rollbackPaused":{"newPaused","finalAutomated"},"rollbackOld":{"rollbackPaused"}}
        require(journal["current"] in allowed_previous[stage],"security-policy-stage-order")
        require(executor.verify_database_prerequisite(),"database-prerequisite")
        if stage=="initial":
            require(executor.verify_pre_pause_chain(plan,initial_versions=True),"pre-pause-chain-drift")
            require(executor.verify_analysis_absent(plan),"analysis-template-not-absent")
        previous=plan["securityPolicy"]["oldSpec"] if journal["current"]=="old" else executor._desired_security_policy(plan,journal["current"])
        step.update(state="running",attempts=1);save_policy(journal)
        try:
            executor.apply_security_policy(plan,stage,previous,initial=journal["current"]=="old")
            require(executor.verify_security_policy(plan,stage),"security-policy-not-observed")
            obs=executor.observe();check_observation(plan,obs)
        except Exception:
            step["state"]="uncertain";save_policy(journal);raise
        step["state"]="succeeded";journal["current"]=stage;save_policy(journal)
        return journal
    prepare_resume=(action=="resume" and isinstance(existing,dict) and existing.get("schemaVersion")=="platform.lab/existing-upgrade-pause-journal/v1" and isinstance(existing.get("prepareSteps"),dict) and any(x.get("state")!="pending" for x in existing["prepareSteps"].values()) and not all(x.get("state")=="succeeded" for x in existing["prepareSteps"].values()))
    if action=="prepare-continue" or prepare_resume:
        require(getattr(executor,"supports_existing_account_mutations",False) is True,"account-mutation-capability-unavailable")
        journal=read_json(journal_path)
        require(journal.get("schemaVersion")=="platform.lab/existing-upgrade-pause-journal/v1" and journal.get("planSha256")==plan_hash and journal.get("state")=="succeeded" and isinstance(journal.get("policy"),dict) and journal["policy"].get("planSha256")==plan_hash and journal["policy"].get("current")=="initial", "prepare-journal-binding")
        steps=journal.get("prepareSteps")
        require(isinstance(steps,dict) and set(steps)==set(ACCOUNT_DESIRED_ORDER) and all(isinstance(x,dict) and set(x)=={"state","attempts"} and ((x["state"]=="pending" and x["attempts"]==0) or (x["state"] in {"running","uncertain","succeeded"} and x["attempts"]==1)) for x in steps.values()),"prepare-journal-steps")
        require(executor.verify_reviewed_render(plan),"git-render-not-verified")
        require(executor.verify_security_policy(plan,"initial"),"security-policy-completed-drift")
        obs=executor.observe();check_observation(plan,obs,initial=True)
        renewed=approval_hash not in [journal["approvalSha256"],*journal["approvalRenewals"]]
        if renewed:
            require(action=="resume","approval-renewal-requires-resume")
            journal["approvalRenewals"].append(approval_hash);policy=journal["policy"]
            if approval_hash not in [policy["approvalSha256"],*policy["approvalRenewals"]]:policy["approvalRenewals"].append(approval_hash)
        if action=="resume":
            for layer in ACCOUNT_DESIRED_ORDER:
                step=steps[layer]
                if step["state"] in {"running","uncertain"}:step["state"]="succeeded" if executor.verify_prepare_transition(plan,layer) else "uncertain"
            completed={name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="succeeded"};uncertain=[name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="uncertain"]
            require(len(uncertain)<=1 and executor.verify_pre_pause_chain(plan,paused=True,completed=completed,uncertain=uncertain[0] if uncertain else None),"prepare-chain-drift")
            save(journal_path,journal);return journal
        require(all(step["state"] not in {"running","uncertain"} for step in steps.values()),"prepare-uncertain-requires-resume")
        completed={name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="succeeded"}
        require(executor.verify_pre_pause_chain(plan,paused=True,completed=completed),"prepare-chain-drift")
        for layer in ACCOUNT_DESIRED_ORDER:
            step=steps[layer]
            if step["state"]=="succeeded":continue
            require(executor.verify_database_prerequisite(),"database-prerequisite")
            step.update(state="running",attempts=1);save(journal_path,journal)
            try:
                executor.apply_prepare_transition(plan,layer)
                require(executor.verify_prepare_transition(plan,layer),"prepare-not-observed:"+layer)
                completed.add(layer);require(executor.verify_pre_pause_chain(plan,paused=True,completed=completed),"prepare-chain-drift")
                obs=executor.observe();check_observation(plan,obs,initial=True)
            except Exception:step["state"]="uncertain";save(journal_path,journal);raise
            step["state"]="succeeded";save(journal_path,journal)
        return journal
    rollback_restore_resume=(action=="resume" and isinstance(existing,dict) and existing.get("schemaVersion")=="platform.lab/existing-upgrade-pause-journal/v1" and isinstance(existing.get("policy"),dict) and existing["policy"].get("current")=="rollbackOld")
    if action=="rollback-restore" or rollback_restore_resume:
        require(getattr(executor,"supports_existing_account_mutations",False) is True and getattr(executor,"supports_existing_controller_mutations",False) is True,"rollback-mutation-capability-unavailable")
        journal=read_json(journal_path)
        require(set(journal)=={"schemaVersion","planSha256","approvalSha256","approvalRenewals","state","attempts","policy","prepareSteps","controllerSteps","accountSteps","accountSync","restoreSteps","rollbackSteps","rollbackSync","controllerRollbackSteps","rollbackRestoreSteps","retainedAnalysisUID","rollbackFrom","outcome"} and journal["schemaVersion"]=="platform.lab/existing-upgrade-pause-journal/v1" and journal["planSha256"]==plan_hash and journal["state"]=="succeeded" and isinstance(journal["policy"],dict) and journal["policy"].get("planSha256")==plan_hash and journal["policy"].get("current")=="rollbackOld" and journal["outcome"] in {"rolling-back","rolled-back"},"rollback-restore-journal-binding")
        steps=journal["rollbackRestoreSteps"];sync=journal["rollbackSync"]
        require(isinstance(steps,dict) and set(steps)==set(ACCOUNT_DESIRED_ORDER) and isinstance(sync,dict) and sync.get("state")=="succeeded" and isinstance(sync.get("nonce"),str),"rollback-restore-journal-steps")
        require(all(isinstance(steps[name],dict) and set(steps[name])=={"state","attempts"} and ((steps[name]["state"]=="pending" and steps[name]["attempts"]==0) or (steps[name]["state"] in {"running","uncertain","succeeded"} and steps[name]["attempts"]==1)) for name in ACCOUNT_DESIRED_ORDER),"rollback-restore-journal-step")
        require(all(journal["rollbackSteps"][name].get("state")=="succeeded" for name in ACCOUNT_DESIRED_ORDER) and all(journal["controllerRollbackSteps"][name].get("state")=="succeeded" for name in CONTROLLER_ORDER),"rollback-incomplete")
        require(executor.verify_reviewed_render(plan),"git-render-not-verified")
        require(executor.verify_security_policy(plan,"rollbackOld"),"security-policy-completed-drift")
        require(executor.verify_analysis_retention_state(plan,journal["retainedAnalysisUID"]),"analysis-template-retention-drift")
        for identity in CONTROLLER_ORDER:
            require(executor.verify_controller_rollback_resource(identity,plan,journal["controllerSteps"][identity]["uid"]),"controller-rollback-completed-drift:"+identity)
        obs=executor.observe();check_observation(plan,obs)
        renewed=approval_hash not in [journal["approvalSha256"],*journal["approvalRenewals"]]
        if renewed:
            require(action=="resume","approval-renewal-requires-resume")
            journal["approvalRenewals"].append(approval_hash);policy=journal["policy"]
            if approval_hash not in [policy["approvalSha256"],*policy["approvalRenewals"]]:policy["approvalRenewals"].append(approval_hash)
        if action=="resume":
            for layer in ACCOUNT_DESIRED_ORDER:
                step=steps[layer]
                if step["state"] in {"running","uncertain"}:step["state"]="succeeded" if executor.verify_desired_transition(plan,layer,stage="rollbackOld") else "uncertain"
            completed={name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="succeeded"}
            uncertain=[name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="uncertain"]
            require(len(uncertain)<=1 and executor.verify_desired_chain(plan,completed,uncertain[0] if uncertain else None,before_stage="initial",after_stage="rollbackOld"),"rollback-restore-chain-drift")
            require(executor.verify_account_sync(plan,sync["nonce"],stage="initial"),"rollback-sync-completed-drift")
            if journal["outcome"]=="rolled-back":require(all(step["state"]=="succeeded" for step in steps.values()) and executor.verify_full_account_synced(plan,stage="initial") and executor.verify_database_prerequisite(),"rollback-restore-completed-drift")
            save(journal_path,journal);return journal
        require(all(step["state"] not in {"running","uncertain"} for step in steps.values()),"rollback-restore-uncertain-requires-resume")
        completed={name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="succeeded"}
        require(executor.verify_desired_chain(plan,completed,before_stage="initial",after_stage="rollbackOld"),"rollback-restore-chain-drift")
        for layer in ACCOUNT_DESIRED_ORDER:
            step=steps[layer]
            if step["state"]=="succeeded":continue
            require(executor.verify_database_prerequisite(),"database-prerequisite")
            require(executor.verify_ready_to_restore(plan,sync["nonce"],stage="initial"),"rollback-not-ready-to-restore")
            require(executor.verify_analysis_retention_state(plan,journal["retainedAnalysisUID"]),"analysis-template-retention-drift")
            step.update(state="running",attempts=1);save(journal_path,journal)
            try:
                executor.apply_desired_transition(plan,layer,before_stage="initial",after_stage="rollbackOld")
                require(executor.verify_desired_transition(plan,layer,stage="rollbackOld"),"rollback-restore-not-observed:"+layer)
                completed.add(layer);require(executor.verify_desired_chain(plan,completed,before_stage="initial",after_stage="rollbackOld"),"rollback-restore-chain-drift")
                obs=executor.observe();check_observation(plan,obs)
            except Exception:step["state"]="uncertain";save(journal_path,journal);raise
            step["state"]="succeeded";save(journal_path,journal)
        require(executor.verify_account_sync(plan,sync["nonce"],stage="initial") and executor.verify_full_account_synced(plan,stage="initial") and executor.verify_analysis_retention_state(plan,journal["retainedAnalysisUID"]) and executor.verify_database_prerequisite(),"rollback-restore-completed-drift")
        journal["outcome"]="rolled-back";save(journal_path,journal)
        return journal
    rollback_resume=(action=="resume" and isinstance(existing,dict) and existing.get("schemaVersion")=="platform.lab/existing-upgrade-pause-journal/v1" and isinstance(existing.get("policy"),dict) and existing["policy"].get("current")=="rollbackPaused")
    if action=="rollback-continue" or rollback_resume:
        require(getattr(executor,"supports_existing_account_mutations",False) is True and getattr(executor,"supports_existing_controller_mutations",False) is True,"rollback-mutation-capability-unavailable")
        journal=read_json(journal_path)
        require(set(journal)=={"schemaVersion","planSha256","approvalSha256","approvalRenewals","state","attempts","policy","prepareSteps","controllerSteps","accountSteps","accountSync","restoreSteps","rollbackSteps","rollbackSync","controllerRollbackSteps","rollbackRestoreSteps","retainedAnalysisUID","rollbackFrom","outcome"} and journal["schemaVersion"]=="platform.lab/existing-upgrade-pause-journal/v1" and journal["planSha256"]==plan_hash and journal["state"]=="succeeded" and isinstance(journal["policy"],dict) and journal["policy"].get("planSha256")==plan_hash and journal["policy"].get("current")=="rollbackPaused" and journal["outcome"] in {"upgraded","rolling-back"} and journal.get("rollbackFrom") in {"newPaused","finalAutomated"},"rollback-journal-binding")
        steps=journal["rollbackSteps"];sync=journal["rollbackSync"];controller=journal["controllerRollbackSteps"]
        require(isinstance(steps,dict) and set(steps)==set(ACCOUNT_DESIRED_ORDER) and isinstance(controller,dict) and set(controller)==set(CONTROLLER_ORDER),"rollback-journal-steps")
        for step in [*steps.values(),*controller.values()]:
            require(isinstance(step,dict) and set(step)=={"state","attempts"} and ((step["state"]=="pending" and step["attempts"]==0) or (step["state"] in {"running","uncertain","succeeded"} and step["attempts"]==1)),"rollback-journal-step")
        require(isinstance(sync,dict) and set(sync)=={"state","attempts","nonce"} and ((sync["state"]=="pending" and sync["attempts"]==0 and sync["nonce"] is None) or (sync["state"] in {"running","uncertain","succeeded"} and sync["attempts"]==1 and isinstance(sync["nonce"],str) and re.fullmatch(r"[0-9a-f]{32}",sync["nonce"]))),"rollback-sync-journal")
        require(executor.verify_reviewed_render(plan),"git-render-not-verified")
        require(executor.verify_security_policy(plan,"rollbackPaused"),"security-policy-completed-drift")
        require(executor.verify_analysis_retention_state(plan,journal["retainedAnalysisUID"]),"analysis-template-retention-drift")
        obs=executor.observe();check_observation(plan,obs)
        renewed=approval_hash not in [journal["approvalSha256"],*journal["approvalRenewals"]]
        if renewed:
            require(action=="resume","approval-renewal-requires-resume")
            journal["approvalRenewals"].append(approval_hash)
            policy=journal["policy"]
            if approval_hash not in [policy["approvalSha256"],*policy["approvalRenewals"]]:policy["approvalRenewals"].append(approval_hash)
        if action=="resume":
            for layer in ACCOUNT_DESIRED_ORDER:
                step=steps[layer]
                if step["state"] in {"running","uncertain"}:step["state"]="succeeded" if executor.verify_desired_transition(plan,layer,stage="initial") else "uncertain"
            if sync["state"] in {"running","uncertain"}:
                sync["state"]="succeeded" if executor.verify_account_sync(plan,sync["nonce"],stage="initial") else "uncertain"
                save(journal_path,journal)
            elif sync["state"]=="succeeded":require(executor.verify_account_sync(plan,sync["nonce"],stage="initial"),"rollback-sync-completed-drift")
            for identity in reversed(CONTROLLER_ORDER):
                step=controller[identity]
                if step["state"] in {"running","uncertain"}:
                    step["state"]="succeeded" if executor.verify_controller_rollback_resource(identity,plan,journal["controllerSteps"][identity]["uid"]) else "uncertain"
                elif step["state"]=="succeeded":require(executor.verify_controller_rollback_resource(identity,plan,journal["controllerSteps"][identity]["uid"]),"controller-rollback-completed-drift:"+identity)
            completed={name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="succeeded"}
            uncertain=[name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="uncertain"]
            require(len(uncertain)<=1 and executor.verify_desired_chain(plan,completed,uncertain[0] if uncertain else None,before_stage=journal["rollbackFrom"],after_stage="initial"),"rollback-chain-drift")
            save(journal_path,journal);return journal
        require(all(step["state"] not in {"running","uncertain"} for step in [*steps.values(),*controller.values()]) and sync["state"] not in {"running","uncertain"},"rollback-uncertain-requires-resume")
        completed={name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="succeeded"}
        require(executor.verify_desired_chain(plan,completed,before_stage=journal["rollbackFrom"],after_stage="initial"),"rollback-chain-drift")
        for layer in ACCOUNT_DESIRED_ORDER:
            step=steps[layer]
            if step["state"]=="succeeded":continue
            require(executor.verify_database_prerequisite(),"database-prerequisite")
            journal["outcome"]="rolling-back";step.update(state="running",attempts=1);save(journal_path,journal)
            try:
                executor.apply_desired_transition(plan,layer,before_stage=journal["rollbackFrom"],after_stage="initial")
                require(executor.verify_desired_transition(plan,layer,stage="initial"),"rollback-desired-not-observed:"+layer)
                completed.add(layer);require(executor.verify_desired_chain(plan,completed,before_stage=journal["rollbackFrom"],after_stage="initial"),"rollback-chain-drift")
                obs=executor.observe();check_observation(plan,obs)
            except Exception:step["state"]="uncertain";save(journal_path,journal);raise
            step["state"]="succeeded";save(journal_path,journal)
        if sync["state"]=="pending":
            require(executor.verify_database_prerequisite(),"database-prerequisite")
            require(executor.verify_analysis_retention_state(plan,journal["retainedAnalysisUID"]),"analysis-template-retention-drift")
            sync.update(state="running",attempts=1,nonce=secrets.token_hex(16));save(journal_path,journal)
            try:
                executor.start_account_sync(plan,sync["nonce"],stage="initial")
                require(executor.verify_account_sync(plan,sync["nonce"],stage="initial"),"rollback-sync-not-observed")
                require(executor.verify_analysis_retention_state(plan,journal["retainedAnalysisUID"]),"analysis-template-retention-drift")
                obs=executor.observe();check_observation(plan,obs)
                require(executor.verify_database_prerequisite(),"database-prerequisite")
            except Exception:sync["state"]="uncertain";save(journal_path,journal);raise
            sync["state"]="succeeded";save(journal_path,journal)
        else:require(executor.verify_account_sync(plan,sync["nonce"],stage="initial"),"rollback-sync-completed-drift")
        for identity in reversed(CONTROLLER_ORDER):
            step=controller[identity];uid=journal["controllerSteps"][identity]["uid"]
            if step["state"]=="succeeded":
                require(executor.verify_controller_rollback_resource(identity,plan,uid),"controller-rollback-completed-drift:"+identity);continue
            require(executor.verify_controller_resource(identity,plan)==uid,"controller-completed-drift:"+identity)
            require(executor.verify_database_prerequisite(),"database-prerequisite")
            step.update(state="running",attempts=1);save(journal_path,journal)
            try:
                executor.rollback_controller_resource(identity,plan,uid)
                require(executor.verify_controller_rollback_resource(identity,plan,uid),"controller-rollback-not-observed:"+identity)
                obs=executor.observe();check_observation(plan,obs)
            except Exception:step["state"]="uncertain";save(journal_path,journal);raise
            step["state"]="succeeded";save(journal_path,journal)
        return journal
    restore_resume=(action=="resume" and isinstance(existing,dict) and existing.get("schemaVersion")=="platform.lab/existing-upgrade-pause-journal/v1" and isinstance(existing.get("policy"),dict) and existing["policy"].get("current")=="finalAutomated")
    if action=="restore-continue" or restore_resume:
        require(getattr(executor,"supports_existing_account_mutations",False) is True,"account-mutation-capability-unavailable")
        journal=read_json(journal_path)
        require(set(journal)=={"schemaVersion","planSha256","approvalSha256","approvalRenewals","state","attempts","policy","prepareSteps","controllerSteps","accountSteps","accountSync","restoreSteps","rollbackSteps","rollbackSync","controllerRollbackSteps","rollbackRestoreSteps","retainedAnalysisUID","rollbackFrom","outcome"} and journal["schemaVersion"]=="platform.lab/existing-upgrade-pause-journal/v1" and journal["planSha256"]==plan_hash and journal["state"]=="succeeded" and isinstance(journal["policy"],dict) and journal["policy"].get("planSha256")==plan_hash and journal["policy"].get("current")=="finalAutomated" and journal["outcome"] in {"in-progress","upgraded"},"restore-journal-binding")
        steps=journal["restoreSteps"];sync=journal["accountSync"]
        require(isinstance(steps,dict) and set(steps)==set(ACCOUNT_DESIRED_ORDER) and isinstance(sync,dict) and sync.get("state")=="succeeded" and isinstance(sync.get("nonce"),str),"restore-journal-steps")
        require(all(isinstance(steps[name],dict) and set(steps[name])=={"state","attempts"} and ((steps[name]["state"]=="pending" and steps[name]["attempts"]==0) or (steps[name]["state"] in {"running","uncertain","succeeded"} and steps[name]["attempts"]==1)) for name in ACCOUNT_DESIRED_ORDER),"restore-journal-step")
        require(all(journal["accountSteps"][name].get("state")=="succeeded" for name in ACCOUNT_DESIRED_ORDER),"account-transition-incomplete")
        require(executor.verify_reviewed_render(plan),"git-render-not-verified")
        require(executor.verify_security_policy(plan,"finalAutomated"),"security-policy-completed-drift")
        require(executor.verify_retained_analysis_template(plan,journal["retainedAnalysisUID"]),"analysis-template-retention-drift")
        for identity in CONTROLLER_ORDER:
            step=journal["controllerSteps"][identity]
            require(step.get("state")=="succeeded" and isinstance(step.get("uid"),str) and executor.verify_controller_resource(identity,plan)==step["uid"],"controller-completed-drift:"+identity)
        obs=executor.observe();check_observation(plan,obs)
        renewed=approval_hash not in [journal["approvalSha256"],*journal["approvalRenewals"]]
        if renewed:
            require(action=="resume","approval-renewal-requires-resume")
            journal["approvalRenewals"].append(approval_hash)
            policy=journal["policy"]
            if approval_hash not in [policy["approvalSha256"],*policy["approvalRenewals"]]:policy["approvalRenewals"].append(approval_hash)
        if action=="resume":
            for layer in ACCOUNT_DESIRED_ORDER:
                step=steps[layer]
                if step["state"] in {"running","uncertain"}:
                    step["state"]="succeeded" if executor.verify_desired_transition(plan,layer,stage="finalAutomated") else "uncertain"
            completed={name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="succeeded"}
            uncertain=[name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="uncertain"]
            require(len(uncertain)<=1 and executor.verify_desired_chain(plan,completed,uncertain[0] if uncertain else None,before_stage="newPaused",after_stage="finalAutomated"),"restore-chain-drift")
            require(executor.verify_account_sync(plan,sync["nonce"]),"account-sync-completed-drift")
            if journal["outcome"]=="upgraded":
                require(all(step["state"]=="succeeded" for step in steps.values()) and executor.verify_full_account_synced(plan) and executor.verify_database_prerequisite(),"restore-completed-drift")
            save(journal_path,journal);return journal
        require(all(step["state"] not in {"running","uncertain"} for step in steps.values()),"restore-uncertain-requires-resume")
        completed={name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="succeeded"}
        require(executor.verify_desired_chain(plan,completed,before_stage="newPaused",after_stage="finalAutomated"),"restore-chain-drift")
        for layer in ACCOUNT_DESIRED_ORDER:
            step=steps[layer]
            if step["state"]=="succeeded":continue
            require(executor.verify_database_prerequisite(),"database-prerequisite")
            require(executor.verify_ready_to_restore(plan,sync["nonce"]),"account-not-ready-to-restore")
            require(executor.verify_retained_analysis_template(plan,journal["retainedAnalysisUID"]),"analysis-template-retention-drift")
            step.update(state="running",attempts=1);save(journal_path,journal)
            try:
                executor.apply_desired_transition(plan,layer,before_stage="newPaused",after_stage="finalAutomated")
                require(executor.verify_desired_transition(plan,layer,stage="finalAutomated"),"restore-not-observed:"+layer)
                completed.add(layer)
                require(executor.verify_desired_chain(plan,completed,before_stage="newPaused",after_stage="finalAutomated"),"restore-chain-drift")
                obs=executor.observe();check_observation(plan,obs)
            except Exception:
                step["state"]="uncertain";save(journal_path,journal);raise
            step["state"]="succeeded";save(journal_path,journal)
        require(executor.verify_account_sync(plan,sync["nonce"]),"account-sync-completed-drift")
        require(executor.verify_full_account_synced(plan),"account-not-fully-synced")
        require(executor.verify_retained_analysis_template(plan,journal["retainedAnalysisUID"]),"analysis-template-retention-drift")
        require(executor.verify_database_prerequisite(),"database-prerequisite")
        journal["outcome"]="upgraded";save(journal_path,journal)
        return journal
    account_resume=(action=="resume" and isinstance(existing,dict) and existing.get("schemaVersion")=="platform.lab/existing-upgrade-pause-journal/v1" and isinstance(existing.get("accountSteps"),dict) and any(step.get("state")!="pending" for step in existing["accountSteps"].values()))
    if action=="account-continue" or account_resume:
        require(getattr(executor,"supports_existing_account_mutations",False) is True,"account-mutation-capability-unavailable")
        journal=read_json(journal_path)
        require(set(journal)=={"schemaVersion","planSha256","approvalSha256","approvalRenewals","state","attempts","policy","prepareSteps","controllerSteps","accountSteps","accountSync","restoreSteps","rollbackSteps","rollbackSync","controllerRollbackSteps","rollbackRestoreSteps","retainedAnalysisUID","rollbackFrom","outcome"} and journal["schemaVersion"]=="platform.lab/existing-upgrade-pause-journal/v1" and journal["planSha256"]==plan_hash and journal["state"]=="succeeded" and isinstance(journal["policy"],dict) and journal["policy"].get("planSha256")==plan_hash and journal["policy"].get("current")=="newPaused" and isinstance(journal["controllerSteps"],dict) and set(journal["controllerSteps"])==set(CONTROLLER_ORDER),"account-journal-binding")
        sync=journal["accountSync"]
        require(isinstance(sync,dict) and set(sync)=={"state","attempts","nonce"} and ((sync["state"]=="pending" and sync["attempts"]==0 and sync["nonce"] is None) or (sync["state"] in {"running","uncertain","succeeded"} and sync["attempts"]==1 and isinstance(sync["nonce"],str) and re.fullmatch(r"[0-9a-f]{32}",sync["nonce"]))),"account-sync-journal")
        steps=journal["accountSteps"]
        require(isinstance(steps,dict) and set(steps)==set(ACCOUNT_DESIRED_ORDER),"account-journal-steps")
        for layer in ACCOUNT_DESIRED_ORDER:
            step=steps[layer]
            require(isinstance(step,dict) and set(step)=={"state","attempts"} and ((step["state"]=="pending" and step["attempts"]==0) or (step["state"] in {"running","uncertain","succeeded"} and step["attempts"]==1)),"account-journal-step")
        require(executor.verify_reviewed_render(plan),"git-render-not-verified")
        require(executor.verify_security_policy(plan,"newPaused"),"security-policy-completed-drift")
        for identity in CONTROLLER_ORDER:
            step=journal["controllerSteps"][identity]
            require(isinstance(step,dict) and step.get("state")=="succeeded" and isinstance(step.get("uid"),str) and bool(step["uid"]) and executor.verify_controller_resource(identity,plan)==step["uid"],"controller-completed-drift:"+identity)
        obs=executor.observe();check_observation(plan,obs)
        renewed=approval_hash not in [journal["approvalSha256"],*journal["approvalRenewals"]]
        if renewed:
            require(action=="resume","approval-renewal-requires-resume")
            journal["approvalRenewals"].append(approval_hash)
            policy=journal["policy"]
            if approval_hash not in [policy["approvalSha256"],*policy["approvalRenewals"]]:policy["approvalRenewals"].append(approval_hash)
        if action=="resume":
            for layer in ACCOUNT_DESIRED_ORDER:
                step=steps[layer]
                if step["state"] in {"running","uncertain"}:
                    if executor.verify_desired_transition(plan,layer):step["state"]="succeeded"
                    else:step["state"]="uncertain"
            if sync["state"] in {"running","uncertain"}:
                if executor.verify_account_sync(plan,sync["nonce"]):
                    uid=executor.retained_analysis_template_uid(plan)
                    if isinstance(uid,str) and uid:sync["state"]="succeeded";journal["retainedAnalysisUID"]=uid
                    else:sync["state"]="uncertain"
                else:sync["state"]="uncertain"
                save(journal_path,journal)
            elif sync["state"]=="succeeded":require(executor.verify_account_sync(plan,sync["nonce"]) and executor.verify_retained_analysis_template(plan,journal["retainedAnalysisUID"]),"account-sync-completed-drift")
            completed={name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="succeeded"}
            uncertain=[name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="uncertain"]
            require(len(uncertain)<=1,"account-journal-order")
            require(executor.verify_desired_chain(plan,completed,uncertain[0] if uncertain else None),"account-transition-chain-drift")
            save(journal_path,journal);return journal
        require(all(step["state"] not in {"running","uncertain"} for step in steps.values()),"account-uncertain-requires-resume")
        require(sync["state"] not in {"running","uncertain"},"account-sync-uncertain-requires-resume")
        completed={name for name in ACCOUNT_DESIRED_ORDER if steps[name]["state"]=="succeeded"}
        require(executor.verify_desired_chain(plan,completed),"account-transition-chain-drift")
        for layer in ACCOUNT_DESIRED_ORDER:
            step=steps[layer]
            if step["state"]=="succeeded":continue
            require(executor.verify_database_prerequisite(),"database-prerequisite")
            step.update(state="running",attempts=1);save(journal_path,journal)
            try:
                executor.apply_desired_transition(plan,layer)
                require(executor.verify_desired_transition(plan,layer),"account-transition-not-observed:"+layer)
                completed.add(layer)
                require(executor.verify_desired_chain(plan,completed),"account-transition-chain-drift")
                obs=executor.observe();check_observation(plan,obs)
            except Exception:
                step["state"]="uncertain";save(journal_path,journal);raise
            step["state"]="succeeded";save(journal_path,journal)
        if sync["state"]=="succeeded":
            require(executor.verify_account_sync(plan,sync["nonce"]) and executor.verify_retained_analysis_template(plan,journal["retainedAnalysisUID"]),"account-sync-completed-drift")
            return journal
        require(executor.verify_database_prerequisite(),"database-prerequisite")
        sync.update(state="running",attempts=1,nonce=secrets.token_hex(16));save(journal_path,journal)
        try:
            executor.start_account_sync(plan,sync["nonce"])
            require(executor.verify_account_sync(plan,sync["nonce"]),"account-sync-not-observed")
            uid=executor.retained_analysis_template_uid(plan)
            require(isinstance(uid,str) and bool(uid),"analysis-template-retention-not-observed")
            journal["retainedAnalysisUID"]=uid
            require(executor.verify_desired_chain(plan,set(ACCOUNT_DESIRED_ORDER)),"account-transition-chain-drift")
            obs=executor.observe();check_observation(plan,obs)
            require(executor.verify_database_prerequisite(),"database-prerequisite")
        except Exception:
            sync["state"]="uncertain";save(journal_path,journal);raise
        sync["state"]="succeeded";save(journal_path,journal)
        return journal
    if action == "pause" or (action in {"resume","continue"} and journal_path.exists() and read_json(journal_path).get("schemaVersion") == "platform.lab/existing-upgrade-pause-journal/v1"):
        require(getattr(executor,"supports_existing_upgrade_pause",False) is True,"upgrade-pause-capability-unavailable")
        if action == "pause":
            prior=None
            if journal_path.exists():
                prior=read_json(journal_path)
                require(prior.get("schemaVersion")!="platform.lab/existing-upgrade-pause-journal/v1","journal-exists")
                require(set(prior)=={"schemaVersion","planSha256","approvalSha256","approvalRenewals","current","steps"} and prior.get("schemaVersion")=="platform.lab/existing-upgrade-policy-journal/v1" and prior.get("planSha256")==plan_hash and isinstance(prior.get("approvalSha256"),str) and SHA64.fullmatch(prior["approvalSha256"]) and isinstance(prior.get("approvalRenewals"),list) and all(isinstance(h,str) and SHA64.fullmatch(h) for h in prior["approvalRenewals"]) and prior.get("current")=="initial" and prior.get("steps")=={"initial":{"state":"succeeded","attempts":1},"newPaused":{"state":"pending","attempts":0},"finalAutomated":{"state":"pending","attempts":0},"rollbackPaused":{"state":"pending","attempts":0},"rollbackOld":{"state":"pending","attempts":0}} and approval_hash in [prior["approvalSha256"],*prior["approvalRenewals"]],"pause-policy-journal-required")
            else:
                require(getattr(executor,"supports_synthetic_pause_without_policy",False) is True,"pause-policy-journal-required")
            obs=executor.observe();check_observation(plan,obs,initial=True)
            require(executor.verify_reviewed_render(plan),"git-render-not-verified")
            if prior is not None: require(executor.verify_security_policy(plan,"initial"),"security-policy-completed-drift")
            if prior is not None:require(executor.verify_pre_pause_chain(plan,initial_versions=True),"pre-pause-chain-drift")
            elif isinstance(getattr(executor,"render_evidence",None),dict) and "prePause" in executor.render_evidence:require(executor.verify_pre_pause_chain(plan,initial_versions=True),"pre-pause-chain-drift")
            else:require(executor.verify_initial_git_desired(plan),"git-desired-not-observed")
            require(executor.verify_database_prerequisite(),"database-prerequisite")
            journal={"schemaVersion":"platform.lab/existing-upgrade-pause-journal/v1", "planSha256":plan_hash,
                     "approvalSha256":approval_hash, "approvalRenewals":[], "state":"running", "attempts":1, "policy":prior,
                     "prepareSteps":{name:{"state":"pending","attempts":0} for name in ACCOUNT_DESIRED_ORDER},
                     "controllerSteps":{name:{"state":"pending","attempts":0,"uid":None} for name in CONTROLLER_ORDER},
                     "accountSteps":{name:{"state":"pending","attempts":0} for name in ACCOUNT_DESIRED_ORDER},
                     "accountSync":{"state":"pending","attempts":0,"nonce":None},
                     "restoreSteps":{name:{"state":"pending","attempts":0} for name in ACCOUNT_DESIRED_ORDER},
                     "rollbackSteps":{name:{"state":"pending","attempts":0} for name in ACCOUNT_DESIRED_ORDER},
                     "rollbackSync":{"state":"pending","attempts":0,"nonce":None},
                     "controllerRollbackSteps":{name:{"state":"pending","attempts":0} for name in reversed(CONTROLLER_ORDER)},
                     "rollbackRestoreSteps":{name:{"state":"pending","attempts":0} for name in ACCOUNT_DESIRED_ORDER},"retainedAnalysisUID":None,"rollbackFrom":None,"outcome":"in-progress"}
            save(journal_path,journal)
            try:
                executor.pause_child_autosync(plan)
                obs=executor.observe();check_observation(plan,obs,initial=True)
                if prior is not None:require(executor.verify_pre_pause_chain(plan,paused=True),"pause-not-observed")
                elif isinstance(getattr(executor,"render_evidence",None),dict) and "prePause" in executor.render_evidence:require(executor.verify_pre_pause_chain(plan,paused=True),"pause-not-observed")
                else:require(executor.observe_child_autosync_paused(plan),"pause-not-observed")
            except Exception:
                journal["state"]="uncertain";save(journal_path,journal);raise
            journal["state"]="succeeded";save(journal_path,journal)
            return journal
        journal=read_json(journal_path)
        require(set(journal)=={"schemaVersion","planSha256","approvalSha256","approvalRenewals","state","attempts","policy","prepareSteps","controllerSteps","accountSteps","accountSync","restoreSteps","rollbackSteps","rollbackSync","controllerRollbackSteps","rollbackRestoreSteps","retainedAnalysisUID","rollbackFrom","outcome"} and journal["planSha256"]==plan_hash and isinstance(journal["approvalSha256"],str) and SHA64.fullmatch(journal["approvalSha256"]) and isinstance(journal["approvalRenewals"],list) and all(isinstance(h,str) and SHA64.fullmatch(h) for h in journal["approvalRenewals"]) and journal["state"] in {"running","uncertain","succeeded"} and journal["attempts"]==1,"pause-journal-binding")
        steps=journal["controllerSteps"]
        require(isinstance(steps,dict) and set(steps)==set(CONTROLLER_ORDER),"controller-journal-steps")
        for identity in CONTROLLER_ORDER:
            step=steps[identity]
            require(isinstance(step,dict) and set(step)=={"state","attempts","uid"} and ((step["state"]=="pending" and step["attempts"]==0 and step["uid"] is None) or (step["state"] in {"running","uncertain"} and step["attempts"]==1 and (step["uid"] is None or (isinstance(step["uid"],str) and bool(step["uid"])))) or (step["state"]=="succeeded" and step["attempts"]==1 and isinstance(step["uid"],str) and bool(step["uid"]))),"controller-journal-step")
        require(executor.verify_reviewed_render(plan),"git-render-not-verified")
        policy=journal["policy"]
        if policy is not None:
            require(isinstance(policy,dict) and policy.get("schemaVersion")=="platform.lab/existing-upgrade-policy-journal/v1" and policy.get("planSha256")==plan_hash and policy.get("current") in {"initial","newPaused","finalAutomated","rollbackPaused","rollbackOld"},"security-policy-journal-binding")
            require(executor.verify_security_policy(plan,policy["current"]),"security-policy-completed-drift")
        obs=executor.observe();check_observation(plan,obs,initial=all(x["state"]=="pending" for x in steps.values()))
        if policy is not None and all(x["state"]=="pending" for x in journal["prepareSteps"].values()):
            observed=executor.verify_pre_pause_chain(plan,paused=True)
        else: observed=executor.observe_child_autosync_paused(plan)
        if journal["state"]=="succeeded": require(observed,"pause-completed-drift")
        else:
            require(action=="resume","pause-uncertain-requires-resume")
            journal["state"]="succeeded" if observed else "uncertain"
        renewed=approval_hash not in [journal["approvalSha256"],*journal["approvalRenewals"]]
        if renewed:
            require(action=="resume","approval-renewal-requires-resume")
            journal["approvalRenewals"].append(approval_hash)
            if policy is not None and approval_hash not in [policy["approvalSha256"],*policy["approvalRenewals"]]:
                policy["approvalRenewals"].append(approval_hash)
        if action=="resume":
            for identity in CONTROLLER_ORDER:
                step=steps[identity]
                if step["state"] in {"running","uncertain","succeeded"}:
                    verified=executor.verify_controller_resource(identity,plan)
                    if step["state"]=="succeeded": require(verified==step["uid"],"controller-completed-drift:"+identity)
                    elif step["uid"] is not None and verified==step["uid"]:
                        step["state"]="succeeded"
                    else: step["state"]="uncertain"
                    obs=executor.observe();check_observation(plan,obs)
            save(journal_path,journal)
            return journal
        require(journal["state"]=="succeeded","pause-not-complete")
        if policy is not None:require(all(step.get("state")=="succeeded" for step in journal["prepareSteps"].values()),"prepare-not-complete")
        require(getattr(executor,"supports_existing_controller_mutations",False) is True,"controller-mutation-capability-unavailable")
        require(all(step["state"] not in {"running","uncertain"} for step in steps.values()),"controller-uncertain-requires-resume")
        require(executor.verify_database_prerequisite(),"database-prerequisite")
        for identity in CONTROLLER_ORDER:
            step=steps[identity]
            if step["state"]=="succeeded":
                require(executor.verify_controller_resource(identity,plan)==step["uid"],"controller-completed-drift:"+identity)
                continue
            old=plan["controllerResources"][identity]
            step.update(state="running",attempts=1,uid=(old["uid"] if old["state"]=="present" else None));save(journal_path,journal)
            try:
                returned=executor.apply_controller_resource(identity,plan)
                require(isinstance(returned,str) and bool(returned),"controller-resource-uid-unavailable:"+identity)
                if step["uid"] is not None: require(returned==step["uid"],"controller-resource-uid-mismatch:"+identity)
                step["uid"]=returned;save(journal_path,journal)
                verified=executor.verify_controller_resource(identity,plan)
                require(verified==step["uid"],"controller-resource-not-observed:"+identity)
                obs=executor.observe();check_observation(plan,obs)
                require(executor.observe_child_autosync_paused(plan),"pause-completed-drift")
            except Exception:
                step["state"]="uncertain";save(journal_path,journal);raise
            step["state"]="succeeded";step["uid"]=verified;save(journal_path,journal)
        return journal
    if action in {"first", "continue"}:
        require(getattr(executor,"supports_existing_upgrade_mutations",False) is True,"upgrade-mutation-capability-unavailable")
    if action == "observe":
        obs=executor.observe();check_observation(plan,obs)
        return {"state":"observed", "planSha256":plan_hash, "observation":obs}
    if not journal_path.exists():
        require(action=="first","journal-required")
        obs=executor.observe();check_observation(plan,obs,initial=True)
        journal={"schemaVersion":"platform.lab/existing-account-upgrade-journal/v1","planSha256":plan_hash,"approvalSha256":approval_hash,"approvalRenewals":[],"target":plan["target"],"source":plan["source"],"artifacts":{k:v for k,v in plan["artifacts"].items() if k!="bundlePath"},"protected":plan["protected"],"phases":{p:{"state":"pending","attempts":0} for p in PHASES}}
        save(journal_path,journal)
    else:
        require(action!="first","journal-exists")
        journal=read_json(journal_path)
        require(set(journal)=={"schemaVersion","planSha256","approvalSha256","approvalRenewals","target","source","artifacts","protected","phases"} and journal["schemaVersion"]=="platform.lab/existing-account-upgrade-journal/v1" and journal["planSha256"]==plan_hash and isinstance(journal["approvalSha256"],str) and SHA64.fullmatch(journal["approvalSha256"]) and isinstance(journal["approvalRenewals"],list) and all(isinstance(x,str) and SHA64.fullmatch(x) for x in journal["approvalRenewals"]) and journal["target"]==plan["target"] and journal["source"]==plan["source"] and journal["protected"]==plan["protected"] and journal["artifacts"]=={k:v for k,v in plan["artifacts"].items() if k!="bundlePath"} and set(journal["phases"])==set(PHASES),"journal-binding")
        renewed = approval_hash not in [journal["approvalSha256"], *journal["approvalRenewals"]]
        if renewed: require(action == "resume", "approval-renewal-requires-resume")
        for phase in PHASES:
            state=journal["phases"][phase]
            require(isinstance(state,dict) and set(state)=={"state","attempts"} and state["state"] in {"pending","running","uncertain","succeeded"} and isinstance(state["attempts"],int) and state["attempts"]>=0,"journal-phase")
        require(not (journal["phases"]["account"]["state"] != "pending" and journal["phases"]["controller"]["state"] != "succeeded"), "journal-phase-order")
        for phase in PHASES:
            state=journal["phases"][phase]
            require((state["state"]=="pending" and state["attempts"]==0) or (state["state"]!="pending" and state["attempts"]==1),"journal-phase-attempts")
        obs=executor.observe();check_observation(plan,obs);check_completed(plan,journal,obs,executor)
        if renewed:
            journal["approvalRenewals"].append(approval_hash)
            save(journal_path,journal)
    # Resume/rerun observe only. A running marker is uncertain after a crash.
    if action in {"resume","rerun"}:
        for phase in PHASES:
            state=journal["phases"][phase]
            if state["state"] in {"running","uncertain"}:
                result=executor.reconcile(phase,plan)
                require(result in {"succeeded","unknown"},"reconcile-result")
                obs=executor.observe();check_observation(plan,obs)
                if result=="succeeded":
                    if phase=="controller":
                        require(obs["controllerImageDigest"]==plan["artifacts"]["controllerImageDigest"],"controller-not-observed")
                        require(obs["applicationRevision"]==plan["source"]["oldApplicationRevision"],"unexpected-application-transition")
                    if phase=="account":
                        require(obs["applicationRevision"]==plan["source"]["newApplicationRevision"],"revision-not-observed")
                        require(obs["controllerImageDigest"]==plan["artifacts"]["controllerImageDigest"],"controller-not-observed")
                    state["state"]="succeeded"
                    check_completed(plan,journal,obs,executor)
                else: state["state"]="uncertain"
                save(journal_path,journal)
        return journal
    require(getattr(executor,"supports_existing_upgrade_mutations",False) is True,"upgrade-mutation-capability-unavailable")
    require(all(journal["phases"][p]["state"] not in {"running","uncertain"} for p in PHASES),"uncertain-requires-observation")
    for phase in PHASES:
        state=journal["phases"][phase]
        if state["state"]=="succeeded": continue
        state["state"]="running";state["attempts"]+=1;save(journal_path,journal)
        try:
            executor.run_upgrade_phase(phase,plan)
            obs=executor.observe();check_observation(plan,obs)
            if phase=="controller":
                require(obs["controllerImageDigest"]==plan["artifacts"]["controllerImageDigest"],"controller-not-observed")
                require(obs["applicationRevision"]==plan["source"]["oldApplicationRevision"],"unexpected-application-transition")
                require(executor.verify_controller_runtime(plan),"controller-runtime-not-converged")
            if phase=="account":
                require(obs["applicationRevision"]==plan["source"]["newApplicationRevision"],"revision-not-observed")
                require(obs["controllerImageDigest"]==plan["artifacts"]["controllerImageDigest"],"controller-not-observed")
        except Exception:
            state["state"]="uncertain";save(journal_path,journal);raise
        state["state"]="succeeded";save(journal_path,journal)
    return journal


def execute(action, plan_path, approval_path, journal_path, executor, *, now=None):
    if action == "observe":
        return _execute_locked(action,plan_path,approval_path,journal_path,executor,now=now)
    path=Path(journal_path)
    require(path.is_absolute() and not path.is_symlink(), "journal-path")
    path.parent.mkdir(parents=True,exist_ok=True)
    lock_path=path.with_name(path.name+".lock")
    flags=os.O_CREAT|os.O_RDWR|getattr(os,"O_NOFOLLOW",0)
    fd=os.open(lock_path,flags,0o600)
    try:
        try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise Rejected("journal-busy")
        return _execute_locked(action,plan_path,approval_path,path,executor,now=now)
    finally:
        os.close(fd)
