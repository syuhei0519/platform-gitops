#!/usr/bin/env python3
"""Fail-closed offline checks for the reviewed access/admission/storage boundary."""
import argparse
import json
from pathlib import Path
import yaml
from render import APP,CONTROL,ISTIO,ROOT,SERVICES

def main():
    p=argparse.ArgumentParser();p.add_argument('rendered',type=Path);a=p.parse_args()
    def docs(name):return [d for d in yaml.safe_load_all((a.rendered/name).read_text()) if d]
    access=docs('argocd-access.yaml')
    for d in access:
        if d['kind'] not in {'Role','ClusterRole'}:continue
        for r in d['rules']:
            if set(r['verbs'])&{'delete','bind','escalate'} or set(r['resources'])&{'secrets','*'}:
                raise ValueError('unapproved Argo authority')
            if d['kind']=='ClusterRole' and set(r['verbs'])&{'create','update','patch'} and not r.get('resourceNames'):
                raise ValueError('unbounded cluster write')
    crd_names={x['name'] for x in json.loads((ROOT/'inputs/crd-contracts.json').read_text())}
    grants=[r for d in access if d['kind']=='ClusterRole' for r in d['rules'] if r['resources']==['customresourcedefinitions'] and 'patch' in r['verbs']]
    if len(grants)!=1 or set(grants[0]['resourceNames'])!=crd_names:raise ValueError('CRD authority mismatch')
    istio=docs('istiod.yaml');hooks=[d for d in istio if d['kind'].endswith('WebhookConfiguration')]
    if len(hooks)!=2:raise ValueError('unexpected admission footprint')
    for d in hooks:
        for w in d['webhooks']:
            if w['failurePolicy']!='Fail' or w['timeoutSeconds']!=10:raise ValueError('admission policy drift')
            s=w['namespaceSelector']
            if d['kind']=='MutatingWebhookConfiguration' and s.get('matchLabels',{}).get('kubernetes.io/metadata.name')!=APP:
                raise ValueError('injection escapes Account')
            if d['kind']=='ValidatingWebhookConfiguration' and not any(e.get('key')=='kubernetes.io/metadata.name' and e.get('operator')=='In' and set(e.get('values',[]))=={APP,ISTIO} for e in s.get('matchExpressions',[])):
                raise ValueError('validation escapes reviewed namespaces')
    for d in istio:
        if d['kind']=='ClusterRole' and any('secrets' in r['resources'] for r in d['rules']):raise ValueError('cluster Secret grant')
        if d['kind']=='Role' and any('secrets' in r['resources'] for r in d['rules']) and d['metadata']['namespace']!=ISTIO:
            if d['metadata']['namespace']!=APP or d['metadata']['name']!='learning-istiod-credentials' or d['rules']!=[dict(apiGroups=[''],resources=['secrets'],verbs=['list','watch'])]:
                raise ValueError('Secret authority escapes Account/control namespaces')
    pilot=next(d for d in istio if d['kind']=='Deployment')
    env={e['name']:e.get('value') for e in pilot['spec']['template']['spec']['containers'][0]['env']}
    if env.get('ISTIO_WATCH_NAMESPACE')!=APP:raise ValueError('unbounded Istio namespaced informer')
    rollout=next(d for d in docs('rollouts.yaml') if d['kind']=='Deployment')
    old=json.loads((ROOT/'inputs/rollouts-deployment.json').read_text())
    if rollout['metadata']!=old['metadata'] or rollout['spec']['selector']!=old['spec']['selector']:
        raise ValueError('controller replacement or selector change')
    args=rollout['spec']['template']['spec']['containers'][0]['args']
    if args[:3]!=['--namespaced','--namespace',APP] or rollout['metadata']['namespace']!=SERVICES:
        raise ValueError('controller watch/identity mismatch')
    db=docs('account-database.yaml');pvc=next(d for d in db if d['kind']=='PersistentVolumeClaim')
    pg=next(d for d in db if d['kind']=='StatefulSet')
    if pvc['metadata']['namespace']!=APP or pvc['spec']['resources']['requests']['storage']!='1Gi':raise ValueError('storage scope')
    v=next(v for v in pg['spec']['template']['spec']['volumes'] if v['name']=='data')
    if v.get('persistentVolumeClaim',{}).get('claimName')!=pvc['metadata']['name'] or 'emptyDir' in v:raise ValueError('ephemeral DB')
    if 'Prune=false' not in pvc['metadata']['annotations']['argocd.argoproj.io/sync-options']:raise ValueError('unprotected claim')
    for name in ['argocd-access.yaml','istiod.yaml','rollouts.yaml','backstage-access.yaml','account-database.yaml','namespaces.yaml','argocd-config.yaml']:
        if any(d['kind']=='Secret' for d in docs(name)):raise ValueError('credential artifact in proposal')
    print('PASS: bounded cluster writes; no new Argo/cluster Secret API grant; scoped fail-closed admission; same Rollouts identity; persistent Account DB')
if __name__=='__main__':main()
