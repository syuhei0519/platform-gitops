#!/usr/bin/env python3
"""Issue8 opt-in and existing Issue85 contracts + pinned Istio CRD spec schema. No cluster access."""
import argparse
import copy
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parent
NS = "learning-istio-demo"
CONTROL = "istio-learning-system"
REV = "learning-1-31-2"
HOST = "mesh-demo.learning-istio-demo.svc.cluster.local"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    return [d for d in yaml.safe_load_all(Path(path).read_text()) if d]


def strict_schema(schema):
    """Reject unknown CRD object keys; retain upstream free-form maps explicitly."""
    schema = copy.deepcopy(schema)
    if isinstance(schema, dict):
        if schema.get("type") == "object" and "properties" in schema and not schema.get("x-kubernetes-preserve-unknown-fields"):
            schema.setdefault("additionalProperties", False)
        return {k: strict_schema(v) for k, v in schema.items()}
    if isinstance(schema, list):
        return [strict_schema(v) for v in schema]
    return schema


def contract(docs, mode, schemas, lock):
    routing = mode not in ["plain", "issue8"]
    by = {(d["kind"], d["metadata"]["name"]): d for d in docs}
    require(len(by) == len(docs), "duplicate resource")
    expected = {("Namespace", CONTROL), ("Namespace", NS), ("Service", "mesh-demo"),
                ("ServiceAccount", "mesh-demo"), ("ConfigMap", "mesh-demo-code"),
                ("Deployment", "mesh-demo-client"), ("Deployment", "mesh-demo-v1")}
    if routing:
        expected |= {("Deployment", "mesh-demo-v2"), ("VirtualService", "mesh-demo"), ("DestinationRule", "mesh-demo")}
    require(set(by) == expected, "unexpected/missing resources")
    for d in docs:
        if d["kind"] == "Namespace":
            labels = d["metadata"].get("labels", {})
            wanted = {"istio.io/rev": REV} if mode != "plain" and d["metadata"]["name"] == NS else {}
            require(labels == wanted, "namespace injection label conflict")
        else:
            require(d["metadata"]["namespace"] == NS, "namespace escape")
        if d["kind"] in schemas:
            require(d["apiVersion"] == "networking.istio.io/v1", "CRD version")
            jsonschema.Draft4Validator(schemas[d["kind"]]).validate(d["spec"])
    svc = by[("Service", "mesh-demo")]["spec"]
    require(svc["type"] == "ClusterIP" and svc["selector"] == {"app": "mesh-demo"}, "service selector/type")
    require(svc["ports"] == [{"name": "http", "port": 8080, "targetPort": "http", "appProtocol": "http"}], "service port")
    for name in (["client", "v1"] if not routing else ["client", "v1", "v2"]):
        s = by[("Deployment", "mesh-demo-" + name)]["spec"]
        labels = {"app": "mesh-demo-client" if name == "client" else "mesh-demo", "version": name}
        require(s["replicas"] == 1 and s["strategy"] == {"type": "Recreate"}, "replicas/strategy")
        require(s["selector"]["matchLabels"] == s["template"]["metadata"]["labels"] == labels, "deployment selector")
        pod = s["template"]["spec"]
        require(pod["serviceAccountName"] == "mesh-demo" and pod["automountServiceAccountToken"] is False, "app token")
        require(len(pod["containers"]) == 1 and not pod.get("initContainers"), "unexpected container")
        c = pod["containers"][0]
        require(c["image"] == lock["images"]["fixture"]["ref"], "unpinned fixture image")
        require(c["command"] == ["python3", "-B", "/fixture/demo.py"], "fixture command")
        require(c["args"] == (["idle"] if name == "client" else ["serve", name]), "fixture revision")
        require(c["resources"] == {"requests": {"cpu": "25m", "memory": "32Mi"}, "limits": {"cpu": "200m", "memory": "64Mi"}}, "fixture resources")
        if name != "client":
            require(c["ports"] == [{"name": "http", "containerPort": 8080}], "pod port")
    require(by[("ServiceAccount", "mesh-demo")]["automountServiceAccountToken"] is False, "SA token")
    require(by[("ConfigMap", "mesh-demo-code")]["data"] == {"demo.py": (ROOT / "chart/files/demo.py").read_text()}, "fixture source drift")
    if routing:
        dr = by[("DestinationRule", "mesh-demo")]["spec"]
        vs = by[("VirtualService", "mesh-demo")]["spec"]
        require(dr == {"host": HOST, "exportTo": ["."], "subsets": [
            {"name": "v1", "labels": {"version": "v1"}}, {"name": "v2", "labels": {"version": "v2"}}]}, "DR host/subsets")
        require(vs["hosts"] == [HOST] and vs["exportTo"] == ["."] and vs["gateways"] == ["mesh"], "VS host/visibility")
        require(len(vs["http"]) == 1, "one HTTP path")
        route = vs["http"][0]
        require(route["match"] == [{"method": {"exact": "GET"}, "uri": {"exact": "/version"}}], "route match")
        require(route["retries"] == {"attempts": 0}, "no retries")
        weights = [100, 0] if mode == "stable" else [90, 10]
        require(len(route["route"]) == 2, "two routes")
        for i, target in enumerate(route["route"]):
            require(type(target["weight"]) is int and target["weight"] == weights[i], "route weights")
            require(target["destination"] == {"host": HOST, "subset": "v" + str(i + 1), "port": {"number": 8080}}, "route host/port/subset")
        require(sum(t["weight"] for t in route["route"]) == 100, "weight sum")


def injection(docs, lock, count=3):
    deployments = [d for d in docs if d["kind"] == "Deployment"]
    require(len(deployments) == count, "injected pod count")
    observations = []
    for d in deployments:
        pod = d["spec"]["template"]["spec"]
        require(pod["automountServiceAccountToken"] is False, "app automount")
        init, = pod["initContainers"]
        proxy, = [c for c in pod["containers"] if c["name"] == "istio-proxy"]
        app, = [c for c in pod["containers"] if c["name"] == "fixture"]
        for c in [init, proxy]:
            require(c["image"] == lock["images"]["proxy"]["ref"], "proxy/init digest")
            require(c["resources"] == {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "500m", "memory": "256Mi"}}, "proxy/init budget")
        require(init["securityContext"]["runAsUser"] == 0 and set(init["securityContext"]["capabilities"]["add"]) == {"NET_ADMIN", "NET_RAW"}, "init privileges")
        require(proxy["securityContext"]["runAsUser"] == 1337, "proxy UID")
        require(app["image"] == lock["images"]["fixture"]["ref"], "app digest")
        tokens = [v["projected"] for v in pod["volumes"] if "projected" in v]
        require(any(any(s.get("serviceAccountToken", {}).get("audience") == "istio-ca" for s in v["sources"]) for v in tokens), "proxy CA token projection")
        observations.append({"name": d["metadata"]["name"], "containers": [
            {k: c.get(k) for k in ["name", "image", "resources", "securityContext"]} for c in [app, proxy, init]],
            "projected_token_audience": "istio-ca", "runtime_tested": False})
    return observations


def foundation(base, control, lock):
    for d in base + control:
        if "namespace" in d["metadata"]:
            require(d["metadata"]["namespace"] == CONTROL, "foundation namespace")
        require(d["kind"] != "Secret", "no secret values")
    pilot, = [d for d in control if d["kind"] == "Deployment"]
    require(pilot["spec"]["replicas"] == 1, "pilot replicas")
    require(pilot["spec"]["template"]["spec"]["containers"][0]["image"] == lock["images"]["pilot"]["ref"], "pilot pin")
    require(not any("reader" in d["metadata"]["name"] for d in base + control), "reader RBAC forbidden")
    require(Counter(d["kind"] for d in base + control) == Counter({
        "CustomResourceDefinition": 15, "ClusterRole": 1, "ClusterRoleBinding": 1,
        "ValidatingWebhookConfiguration": 2, "MutatingWebhookConfiguration": 1,
        "ServiceAccount": 1, "Role": 1, "RoleBinding": 1, "ConfigMap": 3,
        "Service": 1, "Deployment": 1}), "foundation resource boundary")
    require(pilot["spec"]["template"]["spec"]["containers"][0]["resources"] == {
        "requests": {"cpu": "500m", "memory": "512Mi"},
        "limits": {"cpu": "1000m", "memory": "1024Mi"}}, "pilot budget")
    injector, = [d for d in control if d["kind"] == "MutatingWebhookConfiguration"]
    require(len(injector["webhooks"]) == 2, "revision-only webhook pairs")
    for webhook in injector["webhooks"]:
        require(webhook["clientConfig"]["service"]["namespace"] == CONTROL, "webhook target")
        require(webhook["failurePolicy"] == "Fail", "webhook fail closed")
        require(any(e["key"] == "istio-injection" and e["operator"] == "DoesNotExist" for e in webhook["namespaceSelector"]["matchExpressions"]), "legacy label excluded")
        selectors = webhook["namespaceSelector"]["matchExpressions"] + webhook["objectSelector"]["matchExpressions"]
        require(any(e == {"key": "istio.io/rev", "operator": "In", "values": [REV]} for e in selectors), "revision opt-in")
    mesh, = [d for d in control if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "istio-" + REV]
    config = yaml.safe_load(mesh["data"]["mesh"])
    require(config["discoverySelectors"] == [{"matchLabels": {"kubernetes.io/metadata.name": NS}}, {"matchLabels": {"kubernetes.io/metadata.name": CONTROL}}], "discovery names")


def inventory(docs):
    entries = []
    for d in docs:
        item = {"apiVersion": d["apiVersion"], "kind": d["kind"], "name": d["metadata"]["name"],
                "namespace": d["metadata"].get("namespace"), "scope": "namespace" if d["metadata"].get("namespace") else "cluster"}
        for key in ["rules", "roleRef", "subjects", "webhooks"]:
            if key in d:
                item[key] = d[key]
        entries.append(item)
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rendered", type=Path, default=ROOT / "rendered")
    parser.add_argument("--helm", help="also prove invalid chart inputs fail")
    parser.add_argument("--write-inventory", action="store_true")
    args = parser.parse_args()
    lock = json.loads((ROOT / "versions.lock.json").read_text())
    from render import pinned
    for chart in lock["official_charts"].values():
        metadata = yaml.safe_load((ROOT / chart['path'] / 'Chart.yaml').read_text())
        require(metadata['version'] == chart['version'], 'chart version mismatch')
    for image in lock["images"].values():
        require(image["platform"] == "linux/amd64", "platform pin")
        require("@sha256:" in image["ref"], "image digest required")
    base, control = load(args.rendered / "base.yaml"), load(args.rendered / "istiod.yaml")
    schemas = {}
    for d in base:
        if d["kind"] == "CustomResourceDefinition" and d["spec"]["names"]["kind"] in ["VirtualService", "DestinationRule"]:
            v1, = [v for v in d["spec"]["versions"] if v["name"] == "v1"]
            schemas[d["spec"]["names"]["kind"]] = strict_schema(v1["schema"]["openAPIV3Schema"]["properties"]["spec"])
    require(len(schemas) == 2, "both official CRD schemas required")
    foundation(base, control, lock)
    cases = json.loads((ROOT / "fixtures/cases.json").read_text())
    for case in cases:
        nsfile = ROOT / ("bootstrap/plain.yaml" if case["mode"] == "plain" else "bootstrap/namespaces.yaml")
        docs = load(nsfile) + load(args.rendered / (case["mode"] + ".yaml"))
        if "kind" in case:
            target, = [d for d in docs if d["kind"] == case["kind"] and ("name_filter" not in case or d["metadata"]["name"] == case["name_filter"])]
            parts = case["path"].split(".")
            for part in parts[:-1]:
                target = target[int(part)] if isinstance(target, list) else target[part]
            target[int(parts[-1]) if isinstance(target, list) else parts[-1]] = case["value"]
        valid = True
        try:
            contract(docs, case["mode"], schemas, lock)
        except (ValueError, KeyError, jsonschema.ValidationError):
            valid = False
        require(valid == case["valid"], "fixture failed: " + case["name"])
    issue8 = load(args.rendered / "namespaces.yaml") + load(args.rendered / "issue8.yaml")
    contract(issue8, "issue8", schemas, lock)
    require(load(args.rendered / "issue8.yaml") == load(args.rendered / "plain.yaml"), "Issue8 reuses plain fixture")
    require(load(args.rendered / "namespaces.yaml") == load(ROOT / "bootstrap/namespaces.yaml"), "namespace source drift")
    require(load(args.rendered / "plain-namespaces.yaml") == load(ROOT / "bootstrap/plain.yaml"), "plain namespace source drift")
    require(len(base + control + issue8) == 35, "Issue8 35 resource boundary")
    issue8_injected = injection(load(args.rendered / "issue8-injected.yaml"), lock, count=2)
    injected = injection(load(args.rendered / "injected.yaml"), lock)
    if args.helm:
        pinned(args.helm, lock["helm"]["binary"])
        for flags in [["--set", "mode=unknown"], ["--set", "phase=plain", "--set", "mode=split"], ["--set", "image=fixture:latest"], ["--namespace", "default"]]:
            result = subprocess.run([args.helm, "template", "mesh-demo", str(ROOT / "chart"), "--namespace", NS, *flags], capture_output=True)
            require(result.returncode != 0, "invalid chart input accepted")
    if args.write_inventory:
        evidence = args.rendered
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "resource-inventory.json").write_text(json.dumps(inventory(base + control + load(ROOT / "bootstrap/namespaces.yaml") + load(args.rendered / "stable.yaml")), indent=2) + "\n")
        (evidence / "issue8-resource-inventory.json").write_text(json.dumps(inventory(base + control + issue8), indent=2) + "\n")
        (evidence / "issue8-podspec-inventory.json").write_text(json.dumps(issue8_injected, indent=2) + "\n")
        (evidence / "podspec-inventory.json").write_text(json.dumps(injected, indent=2) + "\n")
    print(f"PASS: {len(cases)} contract fixtures; official VS/DR spec schemas; foundation/webhooks; Issue8 35 resources + 2 injected PodSpecs; legacy 3 injected PodSpecs" + ("; 4 invalid Helm inputs rejected" if args.helm else ""))


if __name__ == "__main__":
    main()
