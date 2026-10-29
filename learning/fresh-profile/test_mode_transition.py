"""Synthetic Git commits exercise reviewed mode source and approval."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import yaml
import mode_transition as mode
import existing_upgrade as upgrade

HERE=Path(__file__).resolve().parent
APP=Path(os.environ.get('ACCOUNT_CANARY_APPLICATION_SOURCE',str(HERE.parents[2]/'account-canary-work')))

class ModeSourceTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        git=shutil.which('git');helm=os.environ.get('HELM_BIN') or shutil.which('helm');kubectl=os.environ.get('KUBECTL_BIN') or shutil.which('kubectl')
        if not git or not helm or not kubectl:self.skipTest('git, helm and kubectl required')
        self.git=str(Path(git).resolve());self.helm=str(Path(helm).resolve());self.kubectl=str(Path(kubectl).resolve());self.app=self.root/'app';self.platform=self.root/'platform'
        for root in (self.app,self.platform):
            root.mkdir();self.cmd(root,'init','-q');self.cmd(root,'config','user.name','fixture');self.cmd(root,'config','user.email','fixture@example.invalid')
        spec=importlib.util.spec_from_file_location('mode_runner_fixture',HERE/'test_runner.py');self.rt=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.rt)
        for repository,root,source in (('platform-gitops',self.platform,HERE.parent.parent),('application-manifest',self.app,APP)):
            for relative in self.rt.runner.REQUIRED_SOURCE_PATHS[repository] | self.rt.runner.PLUGIN_SOURCE_PATHS[repository]:
                src=source/relative;dest=root/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dest)
        shutil.copytree(APP/'learning/fresh-profile/release-definition',self.app/'learning/fresh-profile/release-definition',dirs_exist_ok=True)
        for path in ('workload','workload-auto','workload-existing','workload-existing-auto'):shutil.copytree(APP/'learning/fresh-profile'/path,self.app/'learning/fresh-profile'/path,dirs_exist_ok=True)
        self.commit(self.app,'workload');self.rev=self.cmd(self.app,'rev-parse','HEAD')
        self.image='ghcr.io/syuhei0519/backend-app@sha256:'+'b'*64
    def cmd(self,root,*args):return subprocess.check_output([self.git,'-C',str(root),*args],stderr=subprocess.DEVNULL,text=True).strip()
    def commit(self,root,msg):self.cmd(root,'add','.');self.cmd(root,'commit','--allow-empty','-qm',msg)
    def state(self,variant,promotion,sync="paused"):
        policy=upgrade.build_reviewed_policy('__PLATFORM_GITOPS_SHA__',self.rev,self.rev,self.image,sync,variant,promotion)
        p=self.platform/'learning/fresh-profile/security/admission.yaml';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(yaml.safe_dump({'kind':'ValidatingAdmissionPolicy','metadata':{'name':'fresh-application-boundary'},'spec':policy}))
        p=self.platform/'learning/fresh-profile/root/bootstrap.yaml';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(yaml.safe_dump({'kind':'Application','metadata':{'name':'account-lightweight-root','namespace':'fresh-control'},'spec':{'source':{'repoURL':'https://github.com/syuhei0519/platform-gitops.git','path':'learning/fresh-profile/root/children','targetRevision':'__PLATFORM_GITOPS_SHA__'},'syncPolicy':{'syncOptions':['Prune=false']}}}))
        values={'workloadRevision':self.rev,'accountRuntimeImage':self.image,'promotionMode':promotion,'syncMode':sync,'workloadVariant':variant}
        parent={'kind':'Application','metadata':{'name':'fresh-release-definitions','namespace':'fresh-control'},'spec':{'source':{'repoURL':'https://github.com/syuhei0519/application-manifest.git','path':'learning/fresh-profile/release-definition','targetRevision':self.rev,'helm':{'releaseName':'fresh-account-release-definition','parameters':[{'name':k,'value':v} for k,v in values.items()]}},'syncPolicy':{'syncOptions':['Prune=false']}}}
        p=self.platform/'learning/fresh-profile/root/children/applications.yaml';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(yaml.safe_dump(parent))
        self.commit(self.platform,variant+'-'+promotion);revision=self.cmd(self.platform,'rev-parse','HEAD')
        expected=upgrade.build_reviewed_policy(revision,self.rev,self.rev,self.image,sync,variant,promotion)
        return {'platformRevision':revision,'chartRevision':self.rev,'workloadRevision':self.rev,'runtimeImage':self.image,'variant':variant,'mode':promotion,'syncMode':sync,'policySha256':upgrade.policy_digest(expected)}
    def fixture(self,variant,old,new,restore=False,pre=False,split_revision=False):
        first=self.state(variant,old,'automated') if pre else None;old_state=self.state(variant,old)
        if split_revision:self.commit(self.app,'new reviewed workload revision');self.rev=self.cmd(self.app,'rev-parse','HEAD')
        new_state=self.state(variant,new);states={'pre':first,'old':old_state,'new':new_state,'final':self.state(variant,new,'automated') if restore else None}
        tools={name:{'path':path,'sha256':upgrade.sha(path)} for name,path in (('git',self.git),('helm',self.helm),('kubectl',self.kubectl))}
        plan={'schemaVersion':'platform.lab/mode-transition/v1','target':{'clusterName':'learning-fresh-fixture-sandbox','context':'kind-learning-fresh-fixture-sandbox','clusterUID':'c','rootUID':'r','parentUID':'p','childUID':'a','rolloutUID':'o','rootResourceVersion':'1','parentResourceVersion':'1','childResourceVersion':'1','rolloutResourceVersion':'1'},'tools':tools,'states':states,'policy':{'uid':'policy','resourceVersion':'1'},'protected':{name:{'uid':'uid-'+name,'resourceVersion':'1'} for name in upgrade.PROTECTED},'kubeconfigPath':str(self.root/'kubeconfig')}
        (self.root/'kubeconfig').write_text('fixture')
        path=self.root/'plan.json';path.write_text(json.dumps(plan));approval=self.root/'approval.json';approval.write_text(json.dumps({'schemaVersion':'platform.lab/mode-transition-approval/v1','approved':True,'approvedBy':'fixture','approvedAt':'2020-01-01T00:00:00Z','expiresAt':'2099-01-01T00:00:00Z','planSha256':upgrade.sha(path)}))
        return plan,path,approval
    def test_both_directions_and_variants(self):
        for variant in ('fresh','existing-upgrade'):
            for old,new in (('manual','auto'),('auto','manual')):
                with self.subTest(variant=variant,old=old):
                    plan,p,a=self.fixture(variant,old,new);mode.validate(p,a);e=mode.render(plan,self.platform,self.app)
                    for stage in ('old','new'):self.assertEqual(e[stage]['childSpec']['source']['path'],mode.PATHS[(variant,plan['states'][stage]['mode'])])
    def test_separate_reviewed_workload_revisions(self):
        plan,p,a=self.fixture('existing-upgrade','manual','auto',split_revision=True)
        self.assertNotEqual(plan['states']['old']['workloadRevision'],plan['states']['new']['workloadRevision'])
        mode.validate(p,a);mode.render(plan,self.platform,self.app)
    def test_unapproved_unknown_and_wrong_policy_rejected(self):
        plan,p,a=self.fixture('existing-upgrade','manual','auto');a.write_text(a.read_text().replace('true','false'))
        with self.assertRaisesRegex(ValueError,'mode-approval'):mode.validate(p,a)
        plan['states']['new']['platformRevision']='f'*40
        with self.assertRaisesRegex(ValueError,'unknown-revision'):mode.render(plan,self.platform,self.app)
        plan['states']['new']['platformRevision']=plan['states']['old']['platformRevision']
        with self.assertRaisesRegex(ValueError,'policy-mismatch'):mode.render(plan,self.platform,self.app)
    def test_wrong_chart_path_is_rejected(self):
        chart=self.app/'learning/fresh-profile/release-definition/templates/application.yaml'
        chart.write_text(chart.read_text().replace('path: learning/fresh-profile/workload','path: learning/fresh-profile/unauthorized'))
        self.commit(self.app,'bad chart');self.rev=self.cmd(self.app,'rev-parse','HEAD')
        plan,_,_=self.fixture('existing-upgrade','manual','auto')
        with self.assertRaisesRegex(ValueError,'mode-child-desired'):mode.render(plan,self.platform,self.app)
    def test_journal_cas_guard_and_uncertain_resume(self):
        from types import SimpleNamespace
        plan,p,a=self.fixture('existing-upgrade','manual','auto');evidence=mode.render(plan,self.platform,self.app)
        class Transport:
            def __init__(self):
                self.writes=[];self.uncertain=None;self.conflict=False;self.sync_failed=False;self.policy_delay=False
                self.objects={('namespace','kube-system'):{'metadata':{'uid':'c'}},('rollout','account-api'):{'kind':'Rollout','metadata':{'uid':'o','resourceVersion':'1','generation':1,'name':'account-api','namespace':'fresh-account'},'spec':{'strategy':{'canary':{'steps':copy.deepcopy(evidence['old']['rolloutSteps'])}}},'status':{'phase':'Healthy','observedGeneration':'1','stableRS':'same','currentPodHash':'same'}},('validatingadmissionpolicy','fresh-application-boundary'):{'metadata':{'uid':'policy','resourceVersion':'1','generation':1},'status':{'observedGeneration':1,'typeChecking':{'expressionWarnings':[]}},'spec':copy.deepcopy(evidence['old']['policySpec'])}}
                for layer,name in mode.ModeAdapter.NAMES.items():self.objects[('application',name)]={'kind':'Application','metadata':{'name':name,'namespace':'fresh-control','uid':plan['target'][layer+'UID'],'resourceVersion':'1'},'spec':copy.deepcopy(evidence['old'][layer+'Spec'])}
            def run(self,args,env):
                op=next(x for x in ('get','patch') if x in args);i=args.index(op);key=tuple(args[i+1:i+3]);obj=self.objects[key]
                if op=='get':return SimpleNamespace(returncode=0,stdout=json.dumps(obj),stderr='')
                patch=json.loads(args[args.index('--patch')+1]);self.writes.append((key,patch))
                if self.conflict:return SimpleNamespace(returncode=1,stdout='',stderr='conflict')
                assert patch[0]['value']==obj['metadata']['uid'] and patch[1]['value']==obj['metadata']['resourceVersion'] and patch[2]['value']==obj['spec']
                if patch[3]['path']=='/operation':
                    operation=patch[3]['value'];scope=operation['sync']['resources'][0]
                    obj['status']={'operationState':{'phase':('Failed' if self.sync_failed else 'Succeeded'),'finishedAt':'2026-10-29T00:00:00Z','operation':operation,'syncResult':{'revision':operation['sync']['revision'],'resources':[{**scope,'status':'Synced'}]}}}

                    if not self.sync_failed:self.objects[('rollout','account-api')]['spec']['strategy']['canary']['steps']=copy.deepcopy(evidence['new']['rolloutSteps'])
                else:obj['spec']=copy.deepcopy(patch[3]['value'])
                obj['metadata']['resourceVersion']=str(int(obj['metadata']['resourceVersion'])+1)
                if key[0]=='validatingadmissionpolicy':
                    obj['metadata']['generation']+=1
                    if not getattr(self,'policy_delay',False):obj['status']['observedGeneration']=obj['metadata']['generation']
                if self.uncertain==key:return SimpleNamespace(returncode=1,stdout='',stderr='lost response')
                return SimpleNamespace(returncode=0,stdout='',stderr='')
        t=Transport();adapter=mode.ModeAdapter(plan,evidence,t);journal=self.root/'journal.json'
        result=mode.execute('first',p,a,journal,adapter);self.assertEqual(set(result['steps'].values()),{'succeeded'});self.assertEqual(len(t.writes),5)
        for _,patch in t.writes:self.assertEqual([x['op'] for x in patch],(['test','test','test','add'] if patch[3]['path']=='/operation' else ['test','test','test','replace']))
        self.assertEqual(mode.execute('resume',p,a,journal,adapter),result);self.assertEqual(len(t.writes),5)
        t=Transport();t.uncertain=('application','fresh-release-definitions');adapter=mode.ModeAdapter(plan,evidence,t);journal=self.root/'uncertain.json'
        with self.assertRaisesRegex(ValueError,'outcome-uncertain'):mode.execute('first',p,a,journal,adapter)
        count=len(t.writes);old_approval=json.loads(a.read_text());expired=copy.deepcopy(old_approval);expired['expiresAt']='2021-01-01T00:00:00Z';a.write_text(json.dumps(expired))
        with self.assertRaisesRegex(ValueError,'approval-expired'):mode.execute('resume',p,a,journal,adapter)
        renewed=copy.deepcopy(old_approval);renewed['approvedBy']='renewed-fixture';a.write_text(json.dumps(renewed))
        with self.assertRaisesRegex(ValueError,'renewal-observe-only'):mode.execute('continue',p,a,journal,adapter)
        result=mode.execute('resume',p,a,journal,adapter);self.assertEqual(result['steps']['parent'],'succeeded');self.assertEqual(len(result['approvalRenewals']),1);self.assertEqual(len(t.writes),count)
        wrong=copy.deepcopy(renewed);wrong['planSha256']='f'*64;a.write_text(json.dumps(wrong))
        with self.assertRaisesRegex(ValueError,'mode-approval'):mode.execute('resume',p,a,journal,adapter)
        a.write_text(json.dumps(renewed))
        t.uncertain=None;result=mode.execute('continue',p,a,journal,adapter);self.assertEqual(set(result['steps'].values()),{'succeeded'});self.assertEqual(len(t.writes),count+2)
        t=Transport();t.conflict=True;adapter=mode.ModeAdapter(plan,evidence,t);journal=self.root/'conflict.json'
        with self.assertRaisesRegex(ValueError,'outcome-uncertain'):mode.execute('first',p,a,journal,adapter)
        count=len(t.writes);result=mode.execute('continue',p,a,journal,adapter);self.assertEqual(result['steps']['policy'],'uncertain');self.assertEqual(len(t.writes),count)
        t=Transport();t.objects[('rollout','account-api')]['status']['phase']='Progressing';adapter=mode.ModeAdapter(plan,evidence,t)
        with self.assertRaisesRegex(ValueError,'rollout-not-stable'):mode.execute('first',p,a,self.root/'progressing.json',adapter)
        self.assertEqual(t.writes,[])
        t=Transport();t.policy_delay=True;adapter=mode.ModeAdapter(plan,evidence,t);journal=self.root/'policy-delayed.json'
        with self.assertRaisesRegex(ValueError,'mode-policy-not-ready'):mode.execute('first',p,a,journal,adapter)
        count=len(t.writes)
        with self.assertRaisesRegex(ValueError,'mode-policy-not-ready'):mode.execute('resume',p,a,journal,adapter)
        self.assertEqual(len(t.writes),count);policy=t.objects[('validatingadmissionpolicy','fresh-application-boundary')];policy['status']['observedGeneration']=policy['metadata']['generation']
        result=mode.execute('resume',p,a,journal,adapter);self.assertEqual(result['steps']['policy'],'succeeded');self.assertEqual(len(t.writes),count)
        t=Transport();t.sync_failed=True;adapter=mode.ModeAdapter(plan,evidence,t);journal=self.root/'sync-failed.json'
        result=mode.execute('first',p,a,journal,adapter);self.assertEqual(result['steps']['sync'],'uncertain');count=len(t.writes)
        result=mode.execute('resume',p,a,journal,adapter);self.assertEqual(result['steps']['sync'],'uncertain');self.assertEqual(len(t.writes),count)
        result=mode.execute('continue',p,a,journal,adapter);self.assertEqual(result['steps']['sync'],'uncertain');self.assertEqual(len(t.writes),count)
    def test_normal_cli_real_gate_with_fake_transport(self):
        from unittest import mock
        from types import SimpleNamespace
        import sys,io,contextlib
        plan,p,a=self.fixture('existing-upgrade','manual','auto',restore=True,pre=True,split_revision=True); evidence=mode.render(plan,self.platform,self.app)
        rt=self.rt; fixture=rt.RunnerTests('test_repository_dag_is_the_runner_step_machine');fixture.setUp();self.addCleanup(fixture.doCleanups)
        with mock.patch.object(rt,'PLATFORM',plan['states']['final']['platformRevision']),mock.patch.object(rt,'APPLICATION',self.rev):
            gate,gate_path,materialization,profile,journal,_,_=fixture.fixture(plugin=True)
        doc=json.loads(materialization.read_text())
        for identity in doc['sourceFiles']:
            repository,rest=identity.split('@',1);revision,relative=rest.split(':',1);root=self.platform if repository=='platform-gitops' else self.app
            content=(root/relative).read_bytes();committed=subprocess.check_output([self.git,'-C',str(root),'show',revision+':'+relative]);self.assertEqual(content,committed)
            doc['sourceFiles'][identity]=hashlib.sha256(content).hexdigest()
        materialization.write_text(json.dumps(doc,sort_keys=True));gate['metadata'].update(approvedAt='2026-10-25T00:00:00Z',expiresAt='2026-11-23T00:00:00Z');fixture.reseal(gate,gate_path,materialization,profile)
        class Fake:
            def __init__(self):
                self.writes=[];self.objects={('namespace','kube-system'):{'metadata':{'uid':'c'}},('rollout','account-api'):{'kind':'Rollout','metadata':{'uid':'o','resourceVersion':'1','generation':1,'name':'account-api','namespace':'fresh-account'},'spec':{'strategy':{'canary':{'steps':copy.deepcopy(evidence['old']['rolloutSteps'])}}},'status':{'phase':'Healthy','observedGeneration':'1','stableRS':'same','currentPodHash':'same'}},('validatingadmissionpolicy','fresh-application-boundary'):{'metadata':{'uid':'policy','resourceVersion':'1','generation':1},'status':{'observedGeneration':1,'typeChecking':{'expressionWarnings':[]}},'spec':copy.deepcopy(evidence['pre']['policySpec'])}}
                for layer,name in mode.ModeAdapter.NAMES.items():self.objects[('application',name)]={'kind':'Application','metadata':{'name':name,'namespace':'fresh-control','uid':plan['target'][layer+'UID'],'resourceVersion':'1'},'spec':copy.deepcopy(evidence['pre'][layer+'Spec'])}
                self.objects[('application','fresh-account-release')]['status']={'sync':{'status':'Synced','revision':plan['states']['pre']['workloadRevision']}}
            def run(self,args,env):
                op=next(x for x in ('get','patch') if x in args);i=args.index(op);key=tuple(args[i+1:i+3])
                if 'jsonpath={.metadata.uid}{" "}{.metadata.resourceVersion}' in args:
                    kinds={'job':'Job','configmap':'ConfigMap','persistentvolumeclaim':'PVC','secret':'Secret'};item=plan['protected'][kinds[key[0]]+'/'+key[1]]
                    return SimpleNamespace(returncode=0,stdout=item['uid']+' '+item['resourceVersion'],stderr='')
                obj=self.objects[key]
                if op=='get':return SimpleNamespace(returncode=0,stdout=json.dumps(obj),stderr='')
                patch=json.loads(args[args.index('--patch')+1]);self.writes.append(patch)
                if patch[0]['value']!=obj['metadata']['uid'] or patch[1]['value']!=obj['metadata']['resourceVersion'] or patch[2]['value']!=obj['spec']:return SimpleNamespace(returncode=1,stdout='',stderr='conflict')
                if patch[3]['path']=='/operation':
                    operation=patch[3]['value'];scope=operation['sync']['resources'][0]
                    obj['status']={'operationState':{'phase':'Succeeded','finishedAt':'2026-10-29T00:00:00Z','operation':operation,'syncResult':{'revision':operation['sync']['revision'],'resources':[{**scope,'status':'Synced'}]}}}
                    self.objects[('rollout','account-api')]['spec']['strategy']['canary']['steps']=copy.deepcopy(evidence['new']['rolloutSteps']);obj['status']['sync']={'status':'Synced','revision':operation['sync']['revision']}
                else:obj['spec']=copy.deepcopy(patch[3]['value'])
                obj['metadata']['resourceVersion']=str(int(obj['metadata']['resourceVersion'])+1)
                if key[0]=='validatingadmissionpolicy':
                    obj['metadata']['generation']+=1
                    if not getattr(self,'policy_delay',False):obj['status']['observedGeneration']=obj['metadata']['generation']
                return SimpleNamespace(returncode=0,stdout='',stderr='')
        fake=Fake();loader=rt.runner._load_contract_module
        def load(name):
            module=loader(name)
            if name=='approved_live_adapter':module.SubprocessTransport=lambda:fake
            return module
        argv=['runner.py','--mode','mode-transition','--mode-action','first','--live-plan',str(p),'--approval',str(a),'--journal',str(journal),'--gate-approval',str(gate_path),'--gate-plan',str(materialization.parent/'live-step-plan.json'),'--materialization',str(materialization),'--profile',str(profile),'--platform-source-root',str(self.platform),'--application-source-root',str(self.app)]
        with mock.patch.object(rt.runner,'_load_contract_module',side_effect=load),mock.patch.object(sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()) as output:result=rt.runner.main()
        self.assertEqual(result,0,output.getvalue());self.assertEqual(len(fake.writes),9)
        argv[argv.index('--mode-action')+1]='continue'
        with mock.patch.object(rt.runner,'_load_contract_module',side_effect=load),mock.patch.object(sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()) as output:result=rt.runner.main()
        self.assertEqual(result,0,output.getvalue());self.assertEqual(len(fake.writes),13)
        argv[argv.index('--mode-action')+1]='resume'
        with mock.patch.object(rt.runner,'_load_contract_module',side_effect=load),mock.patch.object(sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()) as output:result=rt.runner.main()
        self.assertEqual(result,0,output.getvalue());self.assertEqual(len(fake.writes),13)
        data=json.loads(a.read_text());data['approved']=False;a.write_text(json.dumps(data))
        with mock.patch.object(rt.runner,'_load_contract_module',side_effect=load),mock.patch.object(sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()) as output:result=rt.runner.main()
        self.assertEqual(result,2);self.assertIn('mode-approval',output.getvalue());self.assertEqual(len(fake.writes),13)
    def test_reviewed_final_autosync_and_protected_drift(self):
        from types import SimpleNamespace
        plan,p,a=self.fixture('existing-upgrade','manual','auto',restore=True);mode.validate(p,a);e=mode.render(plan,self.platform,self.app)
        class Transport:
            def __init__(self):
                self.writes=[];self.drift=False;self.lose_final=False
                self.objects={('namespace','kube-system'):{'metadata':{'uid':'c'}},('rollout','account-api'):{'kind':'Rollout','metadata':{'uid':'o','resourceVersion':'1','generation':1,'name':'account-api','namespace':'fresh-account'},'spec':{'strategy':{'canary':{'steps':copy.deepcopy(e['old']['rolloutSteps'])}}},'status':{'phase':'Healthy','observedGeneration':'1','stableRS':'same','currentPodHash':'same'}},('validatingadmissionpolicy','fresh-application-boundary'):{'metadata':{'uid':'policy','resourceVersion':'1','generation':1},'status':{'observedGeneration':1,'typeChecking':{'expressionWarnings':[]}},'spec':copy.deepcopy(e['old']['policySpec'])}}
                for layer,name in mode.ModeAdapter.NAMES.items():self.objects[('application',name)]={'kind':'Application','metadata':{'name':name,'namespace':'fresh-control','uid':plan['target'][layer+'UID'],'resourceVersion':'1'},'spec':copy.deepcopy(e['old'][layer+'Spec'])}
            def run(self,args,env):
                op=next(x for x in ('get','patch') if x in args);i=args.index(op);key=tuple(args[i+1:i+3])
                if 'jsonpath={.metadata.uid}{" "}{.metadata.resourceVersion}' in args:
                    kinds={'job':'Job','configmap':'ConfigMap','persistentvolumeclaim':'PVC','secret':'Secret'};item=plan['protected'][kinds[key[0]]+'/'+key[1]]
                    return SimpleNamespace(returncode=0,stdout=item['uid']+' '+('changed' if self.drift else item['resourceVersion']),stderr='')
                obj=self.objects[key]
                if op=='get':return SimpleNamespace(returncode=0,stdout=json.dumps(obj),stderr='')
                patch=json.loads(args[args.index('--patch')+1]);self.writes.append((key,patch));assert patch[0]['value']==obj['metadata']['uid'] and patch[1]['value']==obj['metadata']['resourceVersion'] and patch[2]['value']==obj['spec']
                if patch[3]['path']=='/operation':
                    operation=patch[3]['value'];scope=operation['sync']['resources'][0];obj['status']={'operationState':{'phase':'Succeeded','finishedAt':'now','operation':operation,'syncResult':{'revision':operation['sync']['revision'],'resources':[{**scope,'status':'Synced'}]}}};self.objects[('rollout','account-api')]['spec']['strategy']['canary']['steps']=copy.deepcopy(e['new']['rolloutSteps']);obj['status']['sync']={'status':'Synced','revision':operation['sync']['revision']}
                else:obj['spec']=copy.deepcopy(patch[3]['value'])
                obj['metadata']['resourceVersion']=str(int(obj['metadata']['resourceVersion'])+1)
                if key[0]=='validatingadmissionpolicy':
                    obj['metadata']['generation']+=1
                    if not getattr(self,'policy_delay',False):obj['status']['observedGeneration']=obj['metadata']['generation']
                if self.lose_final and key==('application','fresh-account-release') and patch[3]['path']=='/spec' and 'automated' in obj['spec'].get('syncPolicy',{}):return SimpleNamespace(returncode=1,stdout='',stderr='lost')
                return SimpleNamespace(returncode=0,stdout='',stderr='')
        t=Transport();adapter=mode.ModeAdapter(plan,e,t);journal=self.root/'restore.json'
        first=mode.execute('first',p,a,journal,adapter);self.assertEqual(first['steps']['sync'],'succeeded');self.assertEqual(first['steps']['final-policy'],'pending')
        t.drift=True
        with self.assertRaisesRegex(ValueError,'mode-protected-drift'):mode.execute('continue',p,a,journal,adapter)
        self.assertEqual(len(t.writes),5);t.drift=False
        final=mode.execute('continue',p,a,journal,adapter);self.assertEqual(set(final['steps'].values()),{'succeeded'});self.assertEqual(len(t.writes),9)
        self.assertEqual(t.objects[('application','fresh-account-release')]['spec']['syncPolicy']['automated'],{'prune':False,'selfHeal':True})
        self.assertEqual(mode.execute('resume',p,a,journal,adapter),final)
        t=Transport();adapter=mode.ModeAdapter(plan,e,t);journal=self.root/'restore-uncertain.json';mode.execute('first',p,a,journal,adapter);t.lose_final=True
        with self.assertRaisesRegex(ValueError,'mode-final-write-outcome-uncertain'):mode.execute('continue',p,a,journal,adapter)
        count=len(t.writes);resumed=mode.execute('resume',p,a,journal,adapter)
        self.assertEqual(resumed['steps']['final-child'],'succeeded');self.assertEqual(len(t.writes),count)
