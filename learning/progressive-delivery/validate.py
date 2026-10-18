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
    secret_rule = next(r for r in role['rules'] if 'secrets' in r['resources'])
    assert secret_rule['resourceNames'] == ['argo-rollouts-notification-secret']
    assert set(secret_rule['verbs']) == {'get','list','watch'}
    assert not any('*' in r['resources'] or '*' in r['verbs'] for o in all_objects if o['kind']=='Role' for r in o['rules'])
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
    print('PASS fixed upstream checksums; 6 CRDs; sample schema; bounded RBAC; no cluster roles/webhooks; manual Application and AppProject schema; namespace/value negatives; 8 executed Lua health cases. No runtime acceptance.')


if __name__ == '__main__':
    main()
