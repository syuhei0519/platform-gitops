#!/usr/bin/env python3
"""Render a proposed, bounded learning install. Never contacts a Kubernetes API."""
import argparse
import copy
import hashlib
import re
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
ARGOCD_COMMIT = 'e258ee23c3e52266d407572f4bcdfe7d9ed36cb5'  # v3.5.2
ROLLOUTS_COMMIT = '356117c8e9a21b74b58c2c01d640aa91994522b5'  # v1.10.1
INPUTS = {
    'argocd': (f'https://raw.githubusercontent.com/argoproj/argo-cd/{ARGOCD_COMMIT}/manifests/core-install.yaml',
               'a30a525206b0c84ab3078958fc5b455bf471d279296046418c004e665912ecc7'),
    'rollouts': (f'https://raw.githubusercontent.com/argoproj/argo-rollouts/{ROLLOUTS_COMMIT}/manifests/install.yaml',
                 'be25242c55bab7650e0b845da11dbce811f7427deae367e224435e0d766ebe5a'),
}
ARGOCD_IMAGE = 'quay.io/argoproj/argocd:v3.5.2@sha256:478701ca1208502315ae55eecb50180292739f6b579d4bbfa9015a43e7c71e68'
ROLLOUTS_IMAGE = 'quay.io/argoproj/argo-rollouts:v1.10.1@sha256:fbcc4cf0a29da25d86230992b1dfdeadf30b6f1baa74ea7c9c98e3e580769396'
REDIS_IMAGE = 'public.ecr.aws/docker/library/redis:8.6.4-alpine@sha256:c64af41b8fc06a2d9b8fde812dd781aa157bed6fcf8ae1656ad4e79f3f9fc9b1'


def download(name, cache):
    url, digest = INPUTS[name]
    path = cache / (name + '.yaml')
    if not path.exists():
        path.write_bytes(urllib.request.urlopen(url, timeout=60).read())
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f'{name}: upstream checksum mismatch')
    return list(yaml.safe_load_all(data))


def object_(kind, name, ns, **fields):
    api = 'rbac.authorization.k8s.io/v1' if kind in ('Role', 'RoleBinding') else 'v1'
    return dict(apiVersion=api, kind=kind, metadata=dict(name=name, namespace=ns), **fields)


def rule(group, resources, verbs, names=None):
    out = dict(apiGroups=[group], resources=resources.split(), verbs=verbs.split())
    if names:
        out['resourceNames'] = names.split()
    return out


def binding(name, ns, sa, sa_ns):
    return object_('RoleBinding', name, ns, roleRef=dict(apiGroup='rbac.authorization.k8s.io', kind='Role', name=name),
                   subjects=[dict(kind='ServiceAccount', name=sa, namespace=sa_ns)])


def render(namespace, control, owner, cache):
    for ns in (namespace, control):
        if len(ns) > 63 or not re.fullmatch(r'learning-[a-z0-9]([-a-z0-9]*[a-z0-9])?', ns):
            raise ValueError('explicit learning-* namespaces required')
    if namespace == control or not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?', owner) or len(owner) > 63:
        raise ValueError('distinct namespaces and valid owner required')
    cd, ro = download('argocd', cache), download('rollouts', cache)
    crds = [x for x in cd + ro if x['kind'] == 'CustomResourceDefinition' and
            x['metadata']['name'] not in ('applicationsets.argoproj.io', 'clusteranalysistemplates.argoproj.io')]
    control_objects = []
    selected = {
        'ServiceAccount': ['argocd-application-controller', 'argocd-repo-server'],
        'ConfigMap': ['argocd-cm', 'argocd-cmd-params-cm', 'argocd-gpg-keys-cm', 'argocd-rbac-cm',
                      'argocd-ssh-known-hosts-cm', 'argocd-tls-certs-cm'],
        'Service': ['argocd-redis', 'argocd-repo-server'],
        'Deployment': ['argocd-redis', 'argocd-repo-server'],
        'StatefulSet': ['argocd-application-controller'],
    }
    for source in cd:
        if source['metadata']['name'] not in selected.get(source['kind'], []):
            continue
        obj = copy.deepcopy(source)
        obj['metadata']['namespace'] = control
        if obj['kind'] in ('Deployment', 'StatefulSet'):
            obj['spec']['replicas'] = 1
            pod = obj['spec']['template']['spec']
            pod.pop('affinity', None)
            if obj['metadata']['name'] == 'argocd-redis':
                # Explicitly require an owner-provisioned Redis auth Secret after approval.
                pod.pop('initContainers', None)
                pod.pop('serviceAccountName', None)
                pod['automountServiceAccountToken'] = False
            for c in pod['containers'] + pod.get('initContainers', []):
                c['image'] = REDIS_IMAGE if c['name'] == 'redis' else ARGOCD_IMAGE
                c['imagePullPolicy'] = 'IfNotPresent'
                c['resources'] = dict(requests=dict(cpu='50m', memory='64Mi', **{'ephemeral-storage':'32Mi'}),
                                      limits=dict(cpu='500m', memory='256Mi', **{'ephemeral-storage':'256Mi'}))
                if c['name'] == 'argocd-application-controller':
                    c['resources']['requests'] = dict(cpu='100m', memory='256Mi', **{'ephemeral-storage':'32Mi'})
                    c['resources']['limits']['memory'] = '512Mi'
        if obj['metadata']['name'] == 'argocd-cm':
            obj['data'] = {
                'application.resourceTrackingMethod': 'annotation',
                'resource.customizations.health.argoproj.io_Rollout': (ROOT / 'health.lua').read_text(),
                'resource.inclusions': yaml.safe_dump([
                    dict(apiGroups=['argoproj.io'], kinds=['Rollout'], clusters=['*']),
                    dict(apiGroups=[''], kinds=['Service', 'Pod'], clusters=['*']),
                    dict(apiGroups=['apps'], kinds=['ReplicaSet', 'Deployment'], clusters=['*'])]),
            }
        control_objects.append(obj)
    control_objects += [object_('Role', 'argocd-learning', control, rules=[
        rule('', 'configmaps secrets', 'get list watch'),
        rule('argoproj.io', 'applications applications/status applications/finalizers appprojects', 'get list watch update patch'),
        rule('', 'events', 'create list patch')]),
        binding('argocd-learning', control, 'argocd-application-controller', control)]
    target = [object_('Role', 'argocd-learning', namespace, rules=[
        rule('argoproj.io', 'rollouts', 'get list watch create update patch delete'),
        rule('', 'services', 'get list watch create update patch delete'),
        rule('', 'pods', 'get list watch'),
        rule('apps', 'replicasets deployments', 'get list watch'),
        rule('', 'events', 'get list watch create patch')]),
        binding('argocd-learning', namespace, 'argocd-application-controller', control),
        object_('ServiceAccount', 'learning-rollouts', namespace),
        object_('Role', 'learning-rollouts', namespace, rules=[
            rule('argoproj.io', 'rollouts rollouts/status rollouts/finalizers', 'get list watch update patch'),
            rule('argoproj.io', 'analysisruns experiments analysistemplates', 'get list watch'),
            rule('apps', 'replicasets', 'get list watch create update patch delete'),
            rule('apps', 'deployments', 'get list watch'),
            rule('', 'services', 'get list watch'),
            rule('', 'services', 'patch', 'learning-account-stable learning-account-canary'),
            rule('', 'pods', 'get list watch update'),
            rule('', 'events', 'create update patch'),
            rule('', 'configmaps', 'get list watch', 'argo-rollouts-config argo-rollouts-notification-configmap'),
            rule('', 'secrets', 'get list watch', 'argo-rollouts-notification-secret'),
            rule('batch', 'jobs', 'get list watch'),
            rule('networking.k8s.io', 'ingresses', 'get list watch')]),
        binding('learning-rollouts', namespace, 'learning-rollouts', namespace)]
    # Namespace-scoped controller; no workloadRef, AnalysisRun execution or traffic provider.
    dep = next(copy.deepcopy(x) for x in ro if x['kind'] == 'Deployment' and x['metadata']['name'] == 'argo-rollouts')
    dep['metadata']['name'] = 'learning-rollouts'
    dep['metadata']['namespace'] = namespace
    pod = dep['spec']['template']['spec']
    pod['serviceAccountName'] = 'learning-rollouts'
    container = pod['containers'][0]
    container['image'] = ROLLOUTS_IMAGE
    container['imagePullPolicy'] = 'IfNotPresent'
    container['args'] = ['--namespaced', '--namespace', namespace]
    container['resources'] = dict(requests=dict(cpu='50m', memory='64Mi', **{'ephemeral-storage':'32Mi'}),
                                  limits=dict(cpu='500m', memory='256Mi', **{'ephemeral-storage':'256Mi'}))
    target.append(dep)
    sample = dict(apiVersion='argoproj.io/v1alpha1', kind='Rollout', metadata=dict(name='learning-rollouts-sample', namespace=namespace),
                  spec=dict(replicas=1, selector=dict(matchLabels={'learning.local/workload':'learning-rollouts-sample'}),
                            strategy=dict(canary=dict(steps=[dict(setWeight=100), dict(pause={})])),
                            template=dict(metadata=dict(labels={'learning.local/workload':'learning-rollouts-sample'}),
                                          spec=dict(automountServiceAccountToken=False,
                                                    securityContext=dict(runAsNonRoot=True, runAsUser=999, seccompProfile=dict(type='RuntimeDefault')),
                                                    containers=[dict(name='sample', image=REDIS_IMAGE, imagePullPolicy='IfNotPresent',
                                                        args=['--save', '', '--appendonly', 'no'],
                                                        securityContext=dict(allowPrivilegeEscalation=False, readOnlyRootFilesystem=True, capabilities=dict(drop=['ALL'])),
                                                        resources=dict(requests=dict(cpu='10m', memory='16Mi'), limits=dict(cpu='100m', memory='64Mi')))]))))
    outputs = {'crds.yaml':crds, 'control.yaml':control_objects, 'target.yaml':target,
               'sample.yaml':[sample]}
    for objects in outputs.values():
        for obj in objects:
            labels = obj['metadata'].setdefault('labels', {})
            labels['learning.local/owner'] = owner
            labels.setdefault('app.kubernetes.io/part-of', 'learning-only')
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--control-namespace', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    outputs = render(args.namespace, args.control_namespace, args.owner, args.cache)
    args.output.mkdir(parents=True, exist_ok=False)
    for name, objects in outputs.items():
        (args.output / name).write_text(yaml.safe_dump_all(objects, sort_keys=False))
    print('Rendered proposal only; registration/Redis credentials/RBAC/CRDs require local approval before apply')


if __name__ == '__main__':
    main()
