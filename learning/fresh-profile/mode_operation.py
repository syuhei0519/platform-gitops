"""Shared stable Rollout and paused Application guard for mode transitions."""
PATHS = {"manual":"learning/fresh-profile/workload", "auto":"learning/fresh-profile/workload-auto", "existing-manual":"learning/fresh-profile/workload-existing", "existing-auto":"learning/fresh-profile/workload-existing-auto"}

class Rejected(ValueError): pass


def _require(condition, reason):
    if not condition: raise Rejected(reason)


def _identity(obj, kind, name, namespace):
    m=obj.get("metadata", {})
    _require(obj.get("kind")==kind and m.get("name")==name and m.get("namespace")==namespace and bool(m.get("uid")) and bool(m.get("resourceVersion")), "target-identity")
    return (m["uid"],m["resourceVersion"])


def _settled(app, rollout, *, allow_automated=False):
    _identity(app,"Application","fresh-account-release","fresh-control")
    _identity(rollout,"Rollout","account-api","fresh-account")
    status=rollout.get("status",{}); meta=rollout["metadata"]
    _require(status.get("phase")=="Healthy" and status.get("observedGeneration")==str(meta.get("generation")) and bool(status.get("stableRS")) and status.get("stableRS")==status.get("currentPodHash") and not status.get("abort") and not status.get("pauseConditions"),"rollout-not-stable")
    _require(not app.get("operation") and app.get("status",{}).get("operationState",{}).get("phase") not in {"Running","Terminating"},"application-operation-active")
    automated=app.get("spec",{}).get("syncPolicy",{}).get("automated")
    _require(allow_automated or automated is None or automated.get("enabled") is False,"autosync-must-be-disabled")
    _require(app.get("spec",{}).get("source",{}).get("path") in PATHS.values(),"source-path")
