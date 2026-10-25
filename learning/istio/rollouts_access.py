#!/usr/bin/env python3
# Rollouts が Istio の経路を扱うために必要な権限を、学習用の範囲に限定して生成します。
"""Render an isolated demo controller and namespace access proposal; never apply."""
import argparse
import copy
import importlib.util
import re
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('progressive_render', ROOT.parent/'progressive-delivery/render.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
NS = 'learning-istio-demo'
NAME = 'mesh-rollouts-controller'


def proposal(control, owner, cache):
    if (not re.fullmatch(r'learning-[a-z0-9]([-a-z0-9]*[a-z0-9])?', control) or
        len(control)>63 or control==NS or not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?',owner) or len(owner)>63):
        raise ValueError('distinct learning control namespace and valid owner required')
    upstream = base.download('rollouts', cache)
    deployment = copy.deepcopy(next(d for d in upstream if d['kind']=='Deployment' and d['metadata']['name']=='argo-rollouts'))
    deployment['metadata'] = dict(name=NAME, namespace=NS)
    deployment['spec']['selector']['matchLabels'] = {'app':NAME}
    deployment['spec']['template']['metadata'] = dict(labels={'app':NAME,'sidecar.istio.io/inject':'false'})
    deployment['spec']['replicas'] = 1
    pod = deployment['spec']['template']['spec']
    pod['serviceAccountName'] = NAME
    container, = pod['containers']
    container['image'] = base.ROLLOUTS_IMAGE
    container['imagePullPolicy'] = 'IfNotPresent'
    container['args'] = ['--namespaced','--namespace',NS,'--leader-elect=true','--istio-api-version=v1']
    container['resources'] = dict(requests=dict(cpu='50m',memory='64Mi',**{'ephemeral-storage':'32Mi'}),
                                  limits=dict(cpu='500m',memory='256Mi',**{'ephemeral-storage':'256Mi'}))
    rule = base.rule
    readonly = [rule('argoproj.io','experiments analysistemplates analysisruns','get list watch'),
        rule('apps','deployments','get list watch'),rule('','services','get list watch'),
        rule('batch','jobs','get list watch'),rule('networking.k8s.io','ingresses','get list watch'),
        rule('','configmaps','get list watch','argo-rollouts-config argo-rollouts-notification-configmap'),
        rule('','secrets','get list watch','argo-rollouts-notification-secret')]
    docs = [base.object_('ServiceAccount',NAME,NS),
        base.object_('Role',NAME,NS,rules=readonly+[
            rule('argoproj.io','rollouts','get list watch'),
            rule('argoproj.io','rollouts rollouts/status rollouts/finalizers','update patch','mesh-rollout'),
            rule('apps','replicasets','get list watch create update patch delete'),
            rule('','pods','get list watch update'),rule('','events','create update patch'),
            rule('coordination.k8s.io','leases','create get update'),
            rule('networking.istio.io','virtualservices destinationrules','get list watch'),
            rule('networking.istio.io','virtualservices destinationrules','update','mesh-rollout')]),
        base.binding(NAME,NS,NAME,NS),deployment,
        base.object_('Role','mesh-argocd',NS,rules=[
            rule('argoproj.io','rollouts','get list watch create update patch delete'),
            rule('','services','get list watch create update patch delete'),
            rule('networking.istio.io','virtualservices destinationrules','get list watch create update patch delete'),
            rule('apps','replicasets deployments','get list watch'),
            rule('argoproj.io','analysistemplates analysisruns','get list watch'),
            rule('','pods events','get list watch')]),
        base.binding('mesh-argocd',NS,'argocd-application-controller',control)]
    for obj in docs:
        obj['metadata'].setdefault('labels',{})['learning.local/owner'] = owner
    return docs


def registration_scope(applications, current_namespaces):
    """A shared cache cannot list a kind in only one of its registered namespaces."""
    if applications.get('kind') not in ('List','ApplicationList') or applications.get('items') != []:
        raise ValueError('demo-only registration requires ZERO existing Core Applications')
    namespaces=current_namespaces.split(',')
    if not namespaces or any(not re.fullmatch(r'learning-[a-z0-9]([-a-z0-9]*[a-z0-9])?',n) for n in namespaces):
        raise ValueError('explicit current learning namespaces required for private restoration')
    return NS


def inclusions_patch(config, control):
    """Only append a cache inclusion; retain health/config and all prior inclusions."""
    if config['kind']!='ConfigMap' or config['metadata']['name']!='argocd-cm' or config['metadata']['namespace']!=control:
        raise ValueError('Core ConfigMap boundary')
    if config['data'].get('resource.respectRBAC','') != '':
        raise ValueError('fixed demo scenario requires default-disabled respectRBAC; do not change existing settings')
    entries = yaml.safe_load(config['data']['resource.inclusions'])
    if not isinstance(entries,list): raise ValueError('explicit existing inclusions required')
    entry = dict(apiGroups=['networking.istio.io'], kinds=['VirtualService','DestinationRule'],
                 clusters=['https://kubernetes.default.svc'])
    if entry not in entries: entries.append(entry)
    return [dict(op='test',path='/metadata/resourceVersion',value=config['metadata']['resourceVersion']),
            dict(op='replace',path='/data/resource.inclusions',value=yaml.safe_dump(entries,sort_keys=False))]


if __name__=='__main__':
    import json
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--control-namespace',required=True)
    p.add_argument('--owner',required=True)
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--applications',type=Path,required=True,help='fresh private Core Application list; must be empty')
    p.add_argument('--registered-namespaces',required=True,help='current non-secret comma-separated registration value; save privately for restoration')
    p.add_argument('--core-config',type=Path,required=True,help='private saved argocd-cm YAML; not a Secret')
    a=p.parse_args()
    a.cache.mkdir(parents=True,exist_ok=True)
    scope=registration_scope(yaml.safe_load(a.applications.read_text()),a.registered_namespaces)
    docs=proposal(a.control_namespace,a.owner,a.cache)
    patch=inclusions_patch(yaml.safe_load(a.core_config.read_text()),a.control_namespace)
    a.output.mkdir(parents=True,exist_ok=False)
    (a.output/'registration-namespaces.txt').write_text(scope+'\n')
    (a.output/'demo-access.yaml').write_text(yaml.safe_dump_all(docs,sort_keys=False))
    (a.output/'core-inclusions-patch.json').write_text(json.dumps(patch,indent=2)+'\n')
