#!/usr/bin/env python3
"""Render the bounded execution proposal offline. No kube client or credentials."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import yaml

ROOT=Path(__file__).resolve().parent
PLATFORM=ROOT.parents[1]
APP='learning-account-release'
CONTROL='learning-progressive-control'
SERVICES='learning-services-20261018'
ISTIO='istio-learning-system'
SERVER='https://kubernetes.default.svc.cluster.local:443'
CACHE_NS=[SERVICES,CONTROL,'observability','learning-istio-demo',ISTIO,APP]
SA=dict(kind='ServiceAccount',name='argocd-application-controller',namespace=CONTROL)

def obj(kind,name,namespace=None,**fields):
    api=('rbac.authorization.k8s.io/v1' if kind in {'Role','RoleBinding','ClusterRole','ClusterRoleBinding'}
         else 'argoproj.io/v1alpha1' if kind in {'Application','AppProject'} else 'v1')
    metadata=dict(name=name)
    if namespace:metadata['namespace']=namespace
    return dict(apiVersion=api,kind=kind,metadata=metadata,**fields)

def rule(group,resources,verbs,names=None):
    r=dict(apiGroups=[group],resources=resources.split(),verbs=verbs.split())
    if names:r['resourceNames']=names
    return r

def bind(name,namespace,subject,cluster=False):
    kind='ClusterRole' if cluster else 'Role'
    return obj(kind+'Binding',name,namespace,roleRef=dict(apiGroup='rbac.authorization.k8s.io',kind=kind,name=name),subjects=[subject])

def protect(doc):
    doc['metadata'].setdefault('annotations',{})['argocd.argoproj.io/sync-options']='Prune=false,Delete=false'
    return doc

def json_input(name):return json.loads((ROOT/'inputs'/name).read_text())

def build(helm,manifest):
    lock=json.loads((PLATFORM/'learning/istio/versions.lock.json').read_text())
    if hashlib.sha256(Path(helm).read_bytes()).hexdigest()!=lock['helm']['binary']['sha256']:
        raise ValueError('existing pinned Helm required')
    def template(chart,release,ns,values):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'values.yaml';p.write_text(yaml.safe_dump(values))
            raw=subprocess.check_output([helm,'template',release,str(chart),'--namespace',ns,'--kube-version','1.36.4','-f',str(p)],text=True)
        return [x for x in yaml.safe_load_all(raw) if x]
    namespaces=json_input('namespace-identities.json')
    crds=json_input('crd-contracts.json')
    if len(namespaces)!=12 or len(crds)!=21:raise ValueError('reviewed baseline changed')
    nsnames=sorted([x['name'] for x in namespaces]+[APP])
    crdnames=sorted(x['name'] for x in crds)
    outputs={}
    # Namespace identities remain a live precondition; UID is never a desired field.
    outputs['namespaces.yaml']=[protect(obj('Namespace',x['name'])) for x in namespaces]
    account_ns=protect(obj('Namespace',APP));account_ns['metadata']['labels']={'istio.io/rev':'learning-1-31-2'}
    outputs['namespaces.yaml'].append(account_ns)
    read=[rule('','pods services serviceaccounts configmaps persistentvolumeclaims','get list watch'),
          rule('apps','deployments statefulsets replicasets','get list watch'),
          rule('argoproj.io','applications appprojects rollouts analysistemplates analysisruns experiments','get list watch'),
          rule('networking.istio.io','virtualservices destinationrules','get list watch'),
          rule('networking.k8s.io','networkpolicies','get list watch'),
          rule('batch','jobs','get list watch'),
          rule('rbac.authorization.k8s.io','roles rolebindings','get list watch')]
    writes={
      SERVICES:[rule('','configmaps services persistentvolumeclaims','create update patch'),rule('apps','deployments statefulsets','create update patch')],
      CONTROL:[rule('argoproj.io','applications appprojects','create update patch'),rule('','configmaps services serviceaccounts','create update patch'),rule('apps','deployments statefulsets','create update patch')],
      'observability':[rule('','configmaps services serviceaccounts','create update patch'),rule('apps','deployments','create update patch')],
      'learning-istio-demo':[rule('','configmaps services serviceaccounts','create update patch'),rule('apps','deployments','create update patch')],
      ISTIO:[rule('','configmaps services serviceaccounts','create update patch'),rule('apps','deployments','create update patch')],
      APP:[rule('','configmaps services serviceaccounts persistentvolumeclaims','create update patch'),rule('apps','deployments statefulsets','create update patch'),rule('argoproj.io','rollouts analysistemplates','create update patch'),rule('networking.istio.io','virtualservices destinationrules','create update patch'),rule('networking.k8s.io','networkpolicies','create update patch'),rule('batch','jobs','create update patch')],
    }
    access=[]
    for ns in CACHE_NS:
        access += [obj('Role','learning-gitops-adoption',ns,rules=deepcopy(read)+writes[ns]),bind('learning-gitops-adoption',ns,SA)]
    webhook_names=dict(mutating=['istio-sidecar-injector-learning-1-31-2-istio-learning-system'],validating=['istio-validator-learning-1-31-2-istio-learning-system'])
    cluster_rules=[rule('','namespaces','get list watch'),rule('','namespaces','update patch',nsnames),
      rule('apiextensions.k8s.io','customresourcedefinitions','get list watch'),rule('apiextensions.k8s.io','customresourcedefinitions','update patch',crdnames),
      rule('admissionregistration.k8s.io','mutatingwebhookconfigurations validatingwebhookconfigurations','get list watch'),
      rule('admissionregistration.k8s.io','mutatingwebhookconfigurations','update patch',webhook_names['mutating']),
      rule('admissionregistration.k8s.io','validatingwebhookconfigurations','update patch',webhook_names['validating']),
      rule('rbac.authorization.k8s.io','clusterroles clusterrolebindings','get list watch')]
    access += [obj('ClusterRole','learning-gitops-foundation',rules=cluster_rules),bind('learning-gitops-foundation',None,SA,True)]
    outputs['argocd-access.yaml']=access
    # All initial security authorities are reviewed bootstrap declarations. No
    # bind/escalate or permission to rewrite RBAC is granted to Argo.
    base_values=yaml.safe_load((PLATFORM/'learning/istio/install/istiod.values.yaml').read_text())
    base_values['meshConfig']['discoverySelectors']=[dict(matchLabels={'kubernetes.io/metadata.name':n}) for n in [APP,ISTIO]]
    base_values['env'].update(PILOT_ENABLE_GATEWAY_API='false',DEFAULT_WORKLOAD_CERT_TTL='1h',MAX_WORKLOAD_CERT_TTL='24h')
    base_values['global'].setdefault('istiod',{})['enableAnalysis']=False
    istio=template(PLATFORM/'learning/istio/vendor/istiod','istiod',ISTIO,base_values)
    istio_sa=dict(kind='ServiceAccount',name='istiod-learning-1-31-2',namespace=ISTIO)
    clean=[]
    for d in istio:
        k=d['kind']
        if k in {'ClusterRole','ClusterRoleBinding'}:continue
        if k in {'MutatingWebhookConfiguration','ValidatingWebhookConfiguration'}:
            for hook in d['webhooks']:
                original=hook.get('namespaceSelector',{})
                original.setdefault('matchLabels',{})['kubernetes.io/metadata.name']=APP
                if k=='ValidatingWebhookConfiguration':
                    original={'matchExpressions':[dict(key='kubernetes.io/metadata.name',operator='In',values=[APP,ISTIO])]}
                hook['namespaceSelector']=original
                hook['failurePolicy']='Fail'
                hook['timeoutSeconds']=10
        if k=='Role':
            # CA state stays only in control namespace, never old DB namespaces.
            for r in d['rules']:
                if 'secrets' in r['resources']:r['verbs']=[v for v in r['verbs'] if v!='delete']
            # Analysis is disabled; do not keep the chart's broad report-CM
            # deletion authority. Namespace CA distribution gets named writes.
            d['rules']=[r for r in d['rules'] if 'configmaps' not in r['resources']]
        clean.append(d)
    pilot_rules=[rule('','pods nodes services namespaces endpoints','get list watch'),
      rule('discovery.k8s.io','endpointslices','get list watch'),rule('apiextensions.k8s.io','customresourcedefinitions','get list watch'),
      rule('','configmaps','get list watch'),rule('networking.k8s.io','ingresses ingressclasses','get list watch'),
      rule('admissionregistration.k8s.io','mutatingwebhookconfigurations validatingwebhookconfigurations','get list watch'),
      rule('admissionregistration.k8s.io','mutatingwebhookconfigurations','update patch',webhook_names['mutating']),
      rule('admissionregistration.k8s.io','validatingwebhookconfigurations','update',webhook_names['validating']),
      rule('authentication.k8s.io','tokenreviews','create'),rule('authorization.k8s.io','subjectaccessreviews','create')]
    for group,kinds in {'networking.istio.io':'destinationrules envoyfilters gateways proxyconfigs serviceentries sidecars virtualservices workloadentries workloadgroups',
                        'security.istio.io':'authorizationpolicies peerauthentications requestauthentications',
                        'telemetry.istio.io':'telemetries','extensions.istio.io':'wasmplugins trafficextensions'}.items():
        pilot_rules.append(rule(group,kinds,'get list watch'))
    clean += [obj('ClusterRole','learning-istiod-discovery',rules=pilot_rules),bind('learning-istiod-discovery',None,istio_sa,True)]
    for ns in [APP,ISTIO]:
        clean += [obj('Role','learning-istiod-public-ca',ns,rules=[rule('','configmaps','create get list watch'),
          rule('','configmaps','update patch delete',['istio-ca-root-cert','istio-ca-crl'])]),bind('learning-istiod-public-ca',ns,istio_sa)]
    outputs['istiod.yaml']=clean
    outputs['istiod-values.yaml']=[base_values]
    # Reuse the current Deployment/SA, preserving namespace/name/selector and
    # storage/security settings. No second controller or Core is rendered.
    dep=json_input('rollouts-deployment.json');pin=json_input('controller-pin.json')
    c=dep['spec']['template']['spec']['containers'][0]
    if c['args']!=['--namespaced','--namespace',SERVICES,'--leader-elect=true']:raise ValueError('unexpected controller scope')
    c['args']=['--namespaced','--namespace',APP,'--leader-elect=true','--istio-api-version=v1']
    c['image']=pin['image'];c.setdefault('env',[]).append(dict(name='ACCOUNT_POLICY_SHA256',value=pin['binary_sha256']))
    rollout_subject=dict(kind='ServiceAccount',name='learning-rollouts',namespace=SERVICES)
    rollout_rules=[rule('argoproj.io','rollouts experiments analysistemplates analysisruns','get list watch'),
      rule('argoproj.io','rollouts rollouts/status rollouts/finalizers','update patch',['account-release']),
      rule('argoproj.io','analysisruns','create update patch delete'),rule('argoproj.io','analysisruns/status analysisruns/finalizers','update patch'),
      rule('apps','replicasets','get list watch create update patch delete'),rule('apps','deployments','get list watch'),
      rule('','services','get list watch'),rule('','pods','get list watch update'),rule('','events','create update patch'),
      rule('','configmaps','get list watch',['argo-rollouts-config','argo-rollouts-notification-configmap']),
      rule('coordination.k8s.io','leases','create get update'),rule('batch','jobs','get list watch'),rule('networking.k8s.io','ingresses','get list watch'),
      rule('networking.istio.io','virtualservices destinationrules','get list watch'),rule('networking.istio.io','virtualservices destinationrules','update',['account-release'])]
    # The config informer follows the explicit --namespace, not Deployment NS.
    plugin=obj('ConfigMap','argo-rollouts-config',APP,data={'metricProviderPlugins':yaml.safe_dump([dict(name='learning-local/account-policy',location='file:///opt/account-policy',sha256=pin['binary_sha256'])],sort_keys=False)})
    outputs['rollouts.yaml']=[dep,plugin,obj('Role','learning-rollouts-account',APP,rules=rollout_rules),bind('learning-rollouts-account',APP,rollout_subject)]
    backstage=[d for d in yaml.safe_load_all((manifest/'learning/account-release/backstage/access-proposal.yaml').read_text()) if d]
    for d in backstage:
        if d['metadata'].get('namespace')=='learning-account-control':d['metadata']['namespace']=CONTROL
    outputs['backstage-access.yaml']=backstage
    db=[d for d in yaml.safe_load_all((manifest/'learning/existing-kind/account-foundation/database.yaml').read_text()) if d]
    pg=next(d for d in db if d['kind']=='StatefulSet')
    vol=next(v for v in pg['spec']['template']['spec']['volumes'] if v['name']=='data')
    if vol.get('persistentVolumeClaim')!={'claimName':'learning-account-data'} or 'emptyDir' in vol:
        raise ValueError('persistent Account database contract required')
    outputs['account-database.yaml']=db
    # Existing data may be disposable; no destructive storage change is needed
    # for this minimal adoption. Record exact identities for any later decision.
    outputs['existing-pv-decision.json']=dict(action='preserve-in-place; no patch/delete required',pvs=json_input('pv-identities.json'))
    cm=json_input('argocd-config.json');inc=yaml.safe_load(cm['data']['resource.inclusions'])
    inc += [dict(apiGroups=[''],kinds=['Namespace','ServiceAccount','ConfigMap','PersistentVolumeClaim'],clusters=[SERVER]),
      dict(apiGroups=['apps'],kinds=['StatefulSet'],clusters=[SERVER]),dict(apiGroups=['argoproj.io'],kinds=['Application','AppProject','Experiment'],clusters=[SERVER]),
      dict(apiGroups=['apiextensions.k8s.io'],kinds=['CustomResourceDefinition'],clusters=[SERVER]),
      dict(apiGroups=['rbac.authorization.k8s.io'],kinds=['Role','RoleBinding','ClusterRole','ClusterRoleBinding'],clusters=[SERVER]),
      dict(apiGroups=['admissionregistration.k8s.io'],kinds=['MutatingWebhookConfiguration','ValidatingWebhookConfiguration'],clusters=[SERVER]),
      dict(apiGroups=['networking.istio.io'],kinds=['VirtualService','DestinationRule'],clusters=[SERVER]),
      dict(apiGroups=['networking.k8s.io'],kinds=['NetworkPolicy'],clusters=[SERVER]),dict(apiGroups=['batch'],kinds=['Job'],clusters=[SERVER])]
    cm['data']['resource.inclusions']=yaml.safe_dump(inc,sort_keys=False)
    outputs['argocd-config.yaml']=[cm]
    return outputs

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--helm',required=True);p.add_argument('--application-manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():raise ValueError('new output directory required')
    outputs=build(a.helm,a.application_manifest);a.output.mkdir(parents=True)
    summary={}
    for name,docs in outputs.items():
        raw=(json.dumps(docs,ensure_ascii=False,indent=2)+'\n') if name.endswith('.json') else yaml.safe_dump_all(docs,sort_keys=False)
        (a.output/name).write_text(raw);summary[name]=dict(sha256=hashlib.sha256(raw.encode()).hexdigest(),bytes=len(raw.encode()))
    (a.output/'checksums.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
