# 生成した Rollouts 用の権限が、必要な操作だけを許可し対象外へ広がらないことを確認します。
"""Effective authorization checks for the unapplied demo proposal."""
import tempfile
import unittest
from pathlib import Path
import yaml
import rollouts_access as access


class Access(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.docs=access.proposal('learning-core-test','test',Path(cls.tmp.name))
        cls.roles={d['metadata']['name']:d['rules'] for d in cls.docs if d['kind']=='Role'}

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def allowed(self, role, group, resource, verb, name=''):
        return any(group in r['apiGroups'] and resource in r['resources'] and verb in r['verbs'] and
                   ('resourceNames' not in r or name in r['resourceNames']) for r in self.roles[role])

    def test_scope_and_controller(self):
        self.assertEqual(len(self.docs),6)
        self.assertTrue(all(d['metadata']['namespace']==access.NS for d in self.docs))
        self.assertFalse(any(d['kind'] in ['Secret','ClusterRole','ClusterRoleBinding'] for d in self.docs))
        deployment=next(d for d in self.docs if d['kind']=='Deployment')
        self.assertEqual(deployment['spec']['template']['metadata']['labels']['sidecar.istio.io/inject'],'false')
        c,=deployment['spec']['template']['spec']['containers']
        self.assertEqual(c['args'],['--namespaced','--namespace',access.NS,'--leader-elect=true','--istio-api-version=v1'])
        self.assertEqual(c['image'],access.base.ROLLOUTS_IMAGE)

    def test_rollouts_minimum_and_denials(self):
        ro=access.NAME
        for resource in ['virtualservices','destinationrules']:
            self.assertTrue(self.allowed(ro,'networking.istio.io',resource,'update','mesh-rollout'))
            self.assertFalse(self.allowed(ro,'networking.istio.io',resource,'update','account'))
            self.assertFalse(self.allowed(ro,'networking.istio.io',resource,'create'))
        self.assertTrue(self.allowed(ro,'apps','replicasets','create'))
        self.assertTrue(self.allowed(ro,'argoproj.io','rollouts/status','patch','mesh-rollout'))
        self.assertFalse(self.allowed(ro,'argoproj.io','rollouts/status','patch','learning-account-canary'))
        for resource in ['secrets','persistentvolumeclaims','services']:
            self.assertFalse(self.allowed(ro,'',resource,'patch','mesh-rollout'))
        self.assertFalse(self.allowed(ro,'','secrets','get','account-db'))
        self.assertTrue(self.allowed(ro,'','secrets','get','argo-rollouts-notification-secret'))

    def test_core_minimum_and_denials(self):
        for group,resource in [('argoproj.io','rollouts'),('','services'),('networking.istio.io','virtualservices'),('networking.istio.io','destinationrules')]:
            self.assertTrue(self.allowed('mesh-argocd',group,resource,'create'))
        for group,resource in [('','secrets'),('','configmaps'),('','persistentvolumeclaims'),('batch','jobs')]:
            for verb in ['get','create','patch','delete']:
                self.assertFalse(self.allowed('mesh-argocd',group,resource,verb))

    def test_registration_gate_and_read_inclusions(self):
        self.assertEqual(access.registration_scope({'kind':'ApplicationList','items':[]},'synthetic-services'),access.NS)
        for namespaces in ['', 'synthetic', 'synthetic-other', 'synthetic-services,default']:
            with self.assertRaises(ValueError):
                access.registration_scope({'kind':'ApplicationList','items':[]},namespaces)
        for items in [[{'metadata':{'name':'account'}}],None]:
            with self.assertRaises(ValueError):
                access.registration_scope({'kind':'ApplicationList','items':items},'synthetic-services')
        for group,resource in [('apps','deployments'),('argoproj.io','analysistemplates'),('argoproj.io','analysisruns')]:
            for verb in ['get','list','watch']:
                self.assertTrue(self.allowed('mesh-argocd',group,resource,verb))
            for verb in ['create','update','patch','delete']:
                self.assertFalse(self.allowed('mesh-argocd',group,resource,verb))

    def test_patch_preserves_existing_health_and_cache(self):
        cm=dict(kind='ConfigMap',metadata=dict(name='argocd-cm',namespace='learning-core-test',resourceVersion='1'),
                data={'resource.inclusions':yaml.safe_dump([dict(apiGroups=['argoproj.io'],kinds=['Rollout'],clusters=['*'])]),'health':'unchanged'})
        original=yaml.safe_dump(cm)
        patch=access.inclusions_patch(cm,'learning-core-test')
        self.assertEqual(yaml.safe_dump(cm),original)
        self.assertEqual([p['path'] for p in patch],['/metadata/resourceVersion','/data/resource.inclusions'])
        entries=yaml.safe_load(patch[1]['value'])
        self.assertEqual(entries[0],yaml.safe_load(cm['data']['resource.inclusions'])[0])
        self.assertEqual(entries[1]['kinds'],['VirtualService','DestinationRule'])
        cm['data']['resource.respectRBAC']='normal'
        with self.assertRaises(ValueError): access.inclusions_patch(cm,'learning-core-test')
        cm['data'].pop('resource.respectRBAC')
        cm['metadata']['namespace']='default'
        with self.assertRaises(ValueError): access.inclusions_patch(cm,'learning-core-test')


if __name__=='__main__': unittest.main()
