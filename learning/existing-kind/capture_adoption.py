#!/usr/bin/env python3
# 管理を引き継ぐ既存リソースを読み取り、レビュー用の宣言と識別情報を保存します。
"""Read-only public parent declaration capture for this exact existing kind.

No Secret request, apply, token issuance, Pod/RS capture or status declaration.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import yaml

ROOT=Path(__file__).resolve().parent
CONTEXT='kind-learning-20261018-69c763'
UID='bf8f9b86-9624-4ee6-a1e4-509aa63ce318'
KINDS={'Deployment','StatefulSet','Service','ConfigMap','PersistentVolumeClaim','ServiceAccount'}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--kubeconfig',required=True);p.add_argument('--context',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.context!=CONTEXT or a.output.exists():raise ValueError('fixed context and new output required')
    def get(kind,ns=None,name=None):
        args=['kubectl','--kubeconfig',a.kubeconfig,'--context',a.context,'get',kind]
        if name:args.append(name)
        if ns:args += ['-n',ns]
        return json.loads(subprocess.check_output(args+['-o','json'],text=True))
    if get('namespace',name='kube-system')['metadata']['uid']!=UID:raise ValueError('cluster identity mismatch')
    baseline=json.loads((ROOT/'inventory.json').read_text())['resources']
    expected={(x['kind'],x['namespace'],x['name']):x['uid'] for x in baseline}
    groups={'services':'learning-services-20261018','observability':'observability','mesh-fixture':'learning-istio-demo','argocd-core':'learning-progressive-control'}
    result={};identity=[]
    for group,ns in groups.items():
        docs=[]
        for kind in sorted(KINDS):
            for live in get(kind,ns)['items']:
                m=live['metadata'];name=m['name']
                if name in {'default','kube-root-ca.crt','istio-ca-crl'}:continue
                if ns=='learning-progressive-control' and name=='argocd-cm':continue
                if ns=='learning-services-20261018' and (name=='learning-rollouts' or name.startswith('argo-rollouts')):continue
                if (kind,ns,name) not in expected or m['uid']!=expected[kind,ns,name]:raise ValueError('parent identity changed')
                d={k:deepcopy(live[k]) for k in ['apiVersion','kind']}
                d['metadata']={k:deepcopy(m[k]) for k in ['name','namespace','labels'] if k in m}
                annotations={k:v for k,v in m.get('annotations',{}).items() if k not in {'kubectl.kubernetes.io/last-applied-configuration','deployment.kubernetes.io/revision'}}
                annotations['argocd.argoproj.io/sync-options']='Prune=false,Delete=false'
                d['metadata']['annotations']=annotations
                for field in ['spec','data','binaryData','automountServiceAccountToken','imagePullSecrets']:
                    if field in live:d[field]=deepcopy(live[field])
                for container in d.get('spec',{}).get('template',{}).get('spec',{}).get('containers',[]):
                    for env in container.get('env',[]):
                        if re.search('PASSWORD|TOKEN|SECRET|LICENSE_KEY',env['name']) and 'value' in env and not env['value'].startswith('/'):
                            raise ValueError('literal credential-like environment value: '+ns+'/'+name)
                raw=yaml.safe_dump(d)
                if '-----BEGIN PRIVATE KEY-----' in raw or '-----BEGIN RSA PRIVATE KEY-----' in raw:raise ValueError('private key refused')
                docs.append(d);identity.append(dict(kind=kind,namespace=ns,name=name,uid=m['uid'],sha256=hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest()))
        result[group+'.yaml']=docs
    crds=[]
    for live in get('crd')['items']:
        m=live['metadata']
        if m['uid']!=expected['CustomResourceDefinition','',m['name']]:raise ValueError('CRD identity changed')
        crds.append(dict(apiVersion=live['apiVersion'],kind=live['kind'],metadata=dict(name=m['name'],annotations={'argocd.argoproj.io/sync-options':'Prune=false,Delete=false'}),spec=live['spec']))
    result['crds.yaml']=crds
    a.output.mkdir(parents=True)
    for name,docs in result.items():(a.output/name).write_text(yaml.safe_dump_all(docs,sort_keys=False))
    (a.output/'identities.json').write_text(json.dumps(identity,indent=2)+'\n')
    print(json.dumps({name:len(docs) for name,docs in result.items()}))
if __name__=='__main__':main()
