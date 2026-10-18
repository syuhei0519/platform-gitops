#!/usr/bin/env python3
"""Focused schema, permission and health tests; does not use kubeconfig."""
import argparse
import copy
import tempfile
from pathlib import Path

import yaml
from jsonschema import Draft7Validator
from lupa import LuaRuntime

from application import application
from render import render


def lua_value(lua, value):
    if isinstance(value, dict):
        return lua.table_from({k:lua_value(lua,v) for k,v in value.items()})
    if isinstance(value, list):
        return lua.table_from([lua_value(lua,v) for v in value])
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        outputs = render('learning-fixture','learning-control','synthetic-fixture',Path(tmp))
        for ns, ctrl in [('default','learning-control'),('learning-fixture','argocd'),('learning-fixture','learning-fixture')]:
            try: render(ns,ctrl,'synthetic-fixture',Path(tmp))
            except ValueError: pass
            else: raise AssertionError('namespace escape accepted')
    assert len(outputs['crds.yaml']) == 6
    assert {o['metadata']['name'] for o in outputs['crds.yaml']} == {
        'applications.argoproj.io','appprojects.argoproj.io','rollouts.argoproj.io',
        'experiments.argoproj.io','analysisruns.argoproj.io','analysistemplates.argoproj.io'}
    all_objects = [o for docs in outputs.values() for o in docs]
    assert not any(o['kind'] == 'Secret' for o in all_objects)
    assert not any(o['kind'] in ('ClusterRole','ClusterRoleBinding','MutatingWebhookConfiguration','ValidatingWebhookConfiguration') for o in all_objects)
    for filename in ('control.yaml','target.yaml','sample.yaml'):
        assert not any(o['kind'] == 'Secret' for o in outputs[filename])
        for o in outputs[filename]:
            assert o['metadata']['namespace'] == ('learning-control' if filename == 'control.yaml' else 'learning-fixture')
            if o['kind'] in ('Deployment','StatefulSet'):
                pod = o['spec']['template']['spec']
                for c in pod['containers']+pod.get('initContainers',[]):
                    assert '@sha256:' in c['image']
                    assert c['resources']['requests'] and c['resources']['limits']
                    assert c['securityContext']['allowPrivilegeEscalation'] is False
                    assert c['securityContext']['readOnlyRootFilesystem'] is True
    role = next(o for o in outputs['target.yaml'] if o['kind']=='Role' and o['metadata']['name']=='learning-rollouts')
    assert any(r['resources']==['leases'] and set(r['verbs'])=={'create','get','update'} for r in role['rules'])
    secret_rule = next(r for r in role['rules'] if 'secrets' in r['resources'])
    assert secret_rule['resourceNames'] == ['argo-rollouts-notification-secret']
    assert set(secret_rule['verbs']) == {'get','list','watch'}
    assert not any('*' in r['resources'] or '*' in r['verbs'] for o in all_objects if o['kind']=='Role' for r in o['rules'])
    # Evaluate effective Role rules, including resourceNames boundaries on provider writes.
    argo = next(o for o in outputs['target.yaml'] if o['kind']=='Role' and o['metadata']['name']=='argocd-learning')
    def permits(role, group, resource, verb, name=''):
        return any(group in r['apiGroups'] and resource in r['resources'] and verb in r['verbs'] and
                   ('resourceNames' not in r or name in r['resourceNames']) for r in role['rules'])
    for verb in ('get','list','watch','create','update','patch','delete'):
        assert permits(role,'argoproj.io','analysisruns',verb)
        assert permits(argo,'argoproj.io','analysistemplates',verb)
    for resource in ('analysisruns/status','analysisruns/finalizers'):
        assert permits(role,'argoproj.io',resource,'update')
        assert permits(role,'argoproj.io',resource,'patch')
    for verb in ('get','list','watch'):
        assert permits(argo,'argoproj.io','analysisruns',verb)
    for group,resource in (('','configmaps'),('apps','deployments')):
        for verb in ('get','list','watch','create','update','patch','delete'):
            assert permits(argo,group,resource,verb,'learning-account-analysis-provider')
        for verb in ('update','patch','delete'):
            assert not permits(argo,group,resource,verb,'learning-account')
            assert not permits(argo,group,resource,verb,'unrelated')
    for sa_role in (role,argo):
        for group,resource in (('argoproj.io','clusteranalysistemplates'),('batch','jobs'),('','secrets'),('','persistentvolumeclaims')):
            for verb in ('create','update','patch','delete'):
                assert not permits(sa_role,group,resource,verb)
    for verb in ('create','update','patch','delete'):
        assert not permits(argo,'argoproj.io','analysisruns',verb)
        assert not permits(role,'argoproj.io','analysistemplates',verb)
    inclusions = yaml.safe_load(next(o for o in outputs['control.yaml'] if o['metadata']['name']=='argocd-cm')['data']['resource.inclusions'])
    assert inclusions == [dict(apiGroups=['argoproj.io'],kinds=['Rollout','AnalysisTemplate','AnalysisRun'],clusters=['*']),
                          dict(apiGroups=[''],kinds=['Service','Pod','ConfigMap'],clusters=['*']),
                          dict(apiGroups=['apps'],kinds=['ReplicaSet','Deployment'],clusters=['*'])]
    crd = next(o for o in outputs['crds.yaml'] if o['metadata']['name']=='rollouts.argoproj.io')
    schema = next(v['schema']['openAPIV3Schema'] for v in crd['spec']['versions'] if v['name']=='v1alpha1')
    Draft7Validator(schema).validate(outputs['sample.yaml'][0])
    values = dict(owner='synthetic-fixture',imageConfigID='sha256:'+'a'*64,sourceCommit='e030275abbf52ecd27b7152ce597b3d89be3f830',mode='rollout',revision='good',telemetry={'enabled':True})
    project, app = application('learning-fixture','learning-control','a'*40,values)
    assert 'automated' not in app['spec']['syncPolicy']
    assert not app['metadata'].get('finalizers')
    assert app['spec']['source']['helm']['valueFiles'] == ['../profiles/good.yaml']
    assert 'revision' not in app['spec']['source']['helm']['valuesObject']
    assert project['spec']['namespaceResourceWhitelist'] == [dict(group='argoproj.io',kind='Rollout'),dict(group='',kind='Service')]
    for kind, obj in [('Application',app),('AppProject',project)]:
        crd = next(o for o in outputs['crds.yaml'] if o['spec']['names']['kind']==kind)
        Draft7Validator(crd['spec']['versions'][0]['schema']['openAPIV3Schema']).validate(obj)
    for change in ({'revision':'unknown'},{'telemetry':{'enabled':False}},{'mode':'deployment'},{'namespace':'default'},{'sourceCommit':'main'}):
        try: application('learning-fixture','learning-control','a'*40,dict(values,**change))
        except ValueError: pass
        else: raise AssertionError('invalid Application input accepted')
    for case in ('success','failure','inconclusive','missing','timeout'):
        analysis_values = dict(values,revision='candidate',analysis=dict(enabled=True,case=case))
        analysis_project, analysis_app = application('learning-fixture','learning-control','a'*40,analysis_values)
        assert analysis_app['spec']['source']['helm']['valuesObject']['analysis'] == analysis_values['analysis']
        assert analysis_app['spec']['source']['helm']['valueFiles'] == ['../profiles/candidate.yaml']
        assert analysis_project['spec']['namespaceResourceWhitelist'] == project['spec']['namespaceResourceWhitelist'] + [
            dict(group='argoproj.io',kind='AnalysisTemplate'), dict(group='',kind='ConfigMap'),dict(group='apps',kind='Deployment')]
        assert 'automated' not in analysis_app['spec']['syncPolicy'] and not analysis_app['metadata'].get('finalizers')
        assert analysis_app['spec']['ignoreDifferences'] == app['spec']['ignoreDifferences']
        for kind,obj in [('Application',analysis_app),('AppProject',analysis_project)]:
            crd = next(o for o in outputs['crds.yaml'] if o['spec']['names']['kind']==kind)
            Draft7Validator(crd['spec']['versions'][0]['schema']['openAPIV3Schema']).validate(obj)
    for change in ({'analysis':{'enabled':True,'case':'unknown'}}, {'analysis':{'enabled':'true','case':'success'}},
                   {'analysis':{'enabled':True,'case':'success','url':'http://other'}}, {'analysis':False},
                   {'analysis':{'enabled':True,'case':'success'},'revision':'failed'}):
        try: application('learning-fixture','learning-control','a'*40,dict(values,**change))
        except ValueError: pass
        else: raise AssertionError('invalid analysis input accepted')
    outputs['analysis-application.yaml'] = [analysis_project,analysis_app]
    cm = next(o for o in outputs['control.yaml'] if o['metadata']['name']=='argocd-cm')
    assert cm['metadata']['labels']['app.kubernetes.io/part-of'] == 'argocd'
    lua = LuaRuntime()
    health = cm['data']['resource.customizations.health.argoproj.io_Rollout']
    cases = [({},'Progressing'), ({'phase':'Progressing'},'Progressing'), ({'phase':'Paused'},'Suspended'),
             ({'phase':'Paused','pauseConditions':[{'reason':'CanaryPauseStep'}]},'Suspended'),
             ({'phase':'Healthy','observedGeneration':'2'},'Healthy'),
             ({'phase':'Degraded'},'Degraded'), ({'abort':True},'Degraded'),
             ({'phase':'Healthy','observedGeneration':'1'},'Progressing')]
    for status, expected in cases:
        lua.globals().obj = lua_value(lua,dict(metadata={'generation':2},status=status))
        assert lua.execute(health)['status'] == expected
    outputs['application.yaml'] = [project,app]
    if args.output:
        args.output.mkdir(parents=True,exist_ok=False)
        for name, docs in outputs.items():
            (args.output/name).write_text(yaml.safe_dump_all(docs,sort_keys=False))
    print('PASS inline analysis permissions: Run management/status/finalizers; template CRUD and Run read; provider name bounds; Job/Secret/PVC/cluster-template writes denied; 5 opt-in Applications and input negatives.')
    print('PASS fixed upstream checksums; 6 CRDs; sample schema; bounded RBAC; no cluster roles/webhooks; manual Application and AppProject schema; namespace/value negatives; 8 executed Lua health cases. No runtime acceptance.')


if __name__ == '__main__':
    main()
