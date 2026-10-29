"""Approval-bound, immutable Git desired contract for a mode transition.

This module only validates and renders; it does not issue cluster writes.
"""
from datetime import datetime, timezone
from pathlib import Path
import copy
import hashlib
import io
import json
import os
import re
import secrets
import subprocess
import tarfile
import tempfile
import yaml
import existing_upgrade as upgrade
import mode_operation as guard

PATHS={(v,m):'learning/fresh-profile/workload'+('-existing' if v=='existing-upgrade' else '')+('-auto' if m=='auto' else '') for v in ('fresh','existing-upgrade') for m in ('manual','auto')}

def validate(plan_path, approval_path, *, now=None):
    plan=upgrade.read_json(plan_path); approval=upgrade.read_json(approval_path)
    require=upgrade.require
    require(set(plan)=={'schemaVersion','target','tools','states','policy','kubeconfigPath','protected'},'mode-plan-fields')
    require(plan['schemaVersion']=='platform.lab/mode-transition/v1','mode-plan-version')
    target=plan['target']
    require(isinstance(target,dict) and set(target)=={'clusterName','context','clusterUID','rootUID','parentUID','childUID','rolloutUID','rootResourceVersion','parentResourceVersion','childResourceVersion','rolloutResourceVersion'} and all(isinstance(v,str) and v for v in target.values()) and target['clusterName'].startswith('learning-fresh-') and target['context']=='kind-'+target['clusterName'],'mode-target')
    tools=plan['tools']
    require(isinstance(tools,dict) and set(tools)=={'git','helm','kubectl'},'mode-tools')
    for item in tools.values():
        require(isinstance(item,dict) and set(item)=={'path','sha256'},'mode-tool-fields')
        p=Path(item['path']); require(p.is_absolute() and p.is_file() and not p.is_symlink() and upgrade.SHA64.fullmatch(item['sha256']) and upgrade.sha(p)==item['sha256'],'mode-tool-identity')
    kubeconfig=Path(plan['kubeconfigPath']);require(kubeconfig.is_absolute() and kubeconfig.is_file() and not kubeconfig.is_symlink(),'mode-kubeconfig')
    states=plan['states']; require(isinstance(states,dict) and set(states)=={'pre','old','new','final'},'mode-states')
    for stage,state in states.items():
        if stage in {'pre','final'} and state is None:continue
        require(isinstance(state,dict) and set(state)=={'platformRevision','chartRevision','workloadRevision','runtimeImage','variant','mode','syncMode','policySha256'},'mode-state-fields')
        require(all(isinstance(state[x],str) and upgrade.SHA40.fullmatch(state[x]) and state[x]!='0'*40 for x in ('platformRevision','chartRevision','workloadRevision')),'mode-revision')
        require(re.fullmatch(r'ghcr\.io/syuhei0519/backend-app@sha256:[0-9a-f]{64}',state['runtimeImage']) is not None and (state['variant'],state['mode']) in PATHS and state['syncMode']==('automated' if stage in {'pre','final'} else 'paused') and upgrade.SHA64.fullmatch(state['policySha256']),'mode-state')
    old,new=states['old'],states['new']
    require(old['mode']!=new['mode'] and all(old[k]==new[k] for k in ('variant','runtimeImage')),'mode-transition-scope')
    pre=states['pre']
    if pre is not None:require(all(pre[k]==old[k] for k in ('variant','mode','workloadRevision','runtimeImage')),'mode-pre-scope')
    final=states['final']
    if final is not None:require(all(final[k]==new[k] for k in ('variant','mode','workloadRevision','runtimeImage')),'mode-final-scope')
    protected=plan['protected'];require(isinstance(protected,dict) and set(protected)==upgrade.PROTECTED and all(isinstance(x,dict) and set(x)=={'uid','resourceVersion'} and all(isinstance(v,str) and v for v in x.values()) for x in protected.values()),'mode-protected-identities')
    policy=plan['policy'];require(isinstance(policy,dict) and set(policy)=={'uid','resourceVersion'} and all(isinstance(v,str) and v for v in policy.values()),'mode-policy-identity')
    require(set(approval)=={'schemaVersion','approved','approvedBy','approvedAt','expiresAt','planSha256'} and approval['schemaVersion']=='platform.lab/mode-transition-approval/v1' and approval['approved'] is True and isinstance(approval['approvedBy'],str) and approval['approvedBy'].strip() and approval['planSha256']==upgrade.sha(plan_path),'mode-approval')
    now=now or datetime.now(timezone.utc);require(now.tzinfo is not None and upgrade.instant(approval['approvedAt'])<=now<upgrade.instant(approval['expiresAt']) and upgrade.instant(approval['approvedAt'])<upgrade.instant(approval['expiresAt']),'mode-approval-expired')
    return plan,upgrade.sha(plan_path),upgrade.sha(approval_path)


def render(plan, platform_root, application_root):
    """Render each exact commit, verify policy source and all GitOps layers."""
    require=upgrade.require
    for tool in plan['tools'].values(): require(upgrade.sha(tool['path'])==tool['sha256'],'mode-tool-changed')
    with tempfile.TemporaryDirectory(prefix='mode-render-') as directory:
        env={'PATH':'/usr/bin:/bin','HOME':directory,'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_ATTR_NOSYSTEM':'1','HELM_CACHE_HOME':directory+'/cache','HELM_CONFIG_HOME':directory+'/config','HELM_DATA_HOME':directory+'/data'}
        def snapshot(root, rev, name):
            root=Path(root);require(root.is_absolute() and root.is_dir() and not root.is_symlink(),'mode-git-root')
            cmd=[plan['tools']['git']['path'],'-C',str(root)]
            typ=subprocess.run(cmd+['cat-file','-t',rev],env=env,capture_output=True,timeout=30)
            require(typ.returncode==0 and typ.stdout.strip()==b'commit','mode-git-unknown-revision')
            result=subprocess.run(cmd+['archive','--format=tar',rev],env=env,capture_output=True,timeout=30)
            require(result.returncode==0 and len(result.stdout)<=30_000_000,'mode-git-archive')
            dest=Path(directory)/name;dest.mkdir()
            with tarfile.open(fileobj=io.BytesIO(result.stdout),mode='r:') as archive:
                for member in archive:
                    part=Path(member.name);require(not part.is_absolute() and '..' not in part.parts and (member.isfile() or member.isdir()) and member.size<=10_000_000,'mode-git-archive-path')
                    path=dest/part
                    if member.isdir():path.mkdir(parents=True,exist_ok=True)
                    else:
                        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(archive.extractfile(member).read())
            return dest
        evidence={}
        workloads={}
        for name,state in plan['states'].items():
            if state is None:continue
            platform=snapshot(platform_root,state['platformRevision'],'platform-'+name);app=snapshot(application_root,state['chartRevision'],'chart-'+name)
            # Ensure the child workload pin denotes a real commit as well.
            workload=snapshot(application_root,state['workloadRevision'],'workload-'+name)
            workload_path=workload/PATHS[(state['variant'],state['mode'])]
            require(workload_path.is_dir() and not workload_path.is_symlink(),'mode-workload-path')
            result=subprocess.run([plan['tools']['kubectl']['path'],'kustomize',str(workload_path)],env=env,capture_output=True,timeout=30)
            require(result.returncode==0 and len(result.stdout)<=10_000_000,'mode-workload-render')
            objects=[x for x in yaml.safe_load_all(result.stdout) if x is not None]
            require(all(isinstance(x,dict) and isinstance(x.get('metadata'),dict) and isinstance(x.get('kind'),str) and isinstance(x['metadata'].get('name'),str) for x in objects),'mode-workload-object')
            identities=[(x.get('apiVersion'),x['kind'],x['metadata'].get('namespace'),x['metadata']['name']) for x in objects]
            require(len(set(identities))==len(identities),'mode-workload-duplicate')
            require(not any(x['kind']=='Secret' and x['metadata']['name'] in {'account-db-bootstrap','account-db-migration','account-db-runtime'} for x in objects),'mode-external-secret-rendered')
            workloads[name]=dict(zip(identities,objects))
            docs=list(yaml.safe_load_all((platform/'learning/fresh-profile/security/admission.yaml').read_text()))
            policies=[x for x in docs if isinstance(x,dict) and x.get('kind')=='ValidatingAdmissionPolicy' and x.get('metadata',{}).get('name')=='fresh-application-boundary']
            require(len(policies)==1,'mode-policy-source')
            policy=copy.deepcopy(policies[0]['spec'])
            for v in policy.get('validations',[]):
                if isinstance(v,dict) and isinstance(v.get('expression'),str):v['expression']=v['expression'].replace('__PLATFORM_GITOPS_SHA__',state['platformRevision'])
            expected=upgrade.build_reviewed_policy(state['platformRevision'],state['chartRevision'],state['workloadRevision'],state['runtimeImage'],state['syncMode'],state['variant'],state['mode'])
            require(policy==expected and upgrade.policy_digest(policy)==state['policySha256'],'mode-policy-mismatch')
            roots=[x for x in yaml.safe_load_all((platform/'learning/fresh-profile/root/bootstrap.yaml').read_text()) if isinstance(x,dict) and x.get('kind')=='Application' and x.get('metadata',{}).get('name')=='account-lightweight-root']
            require(len(roots)==1 and roots[0].get('metadata',{}).get('namespace')=='fresh-control','mode-root')
            root=roots[0]['spec'];source=root.get('source',{})
            require(source.get('repoURL')=='https://github.com/syuhei0519/platform-gitops.git' and source.get('path')=='learning/fresh-profile/root/children' and source.get('targetRevision')=='__PLATFORM_GITOPS_SHA__' and 'automated' not in root.get('syncPolicy',{}) and 'Prune=false' in root.get('syncPolicy',{}).get('syncOptions',[]),'mode-root-source')
            source['targetRevision']=state['platformRevision']
            parent=yaml.safe_load((platform/'learning/fresh-profile/root/children/applications.yaml').read_text()); meta=parent.get('metadata',{});spec=parent.get('spec',{});src=spec.get('source',{})
            require(parent.get('kind')=='Application' and meta.get('name')=='fresh-release-definitions' and meta.get('namespace')=='fresh-control' and src.get('repoURL')=='https://github.com/syuhei0519/application-manifest.git' and src.get('path')=='learning/fresh-profile/release-definition' and src.get('targetRevision')==state['chartRevision'] and 'automated' not in spec.get('syncPolicy',{}) and 'Prune=false' in spec.get('syncPolicy',{}).get('syncOptions',[]),'mode-parent-source')
            helm=src.get('helm',{});params=helm.get('parameters');require(set(helm)=={'releaseName','parameters'} and helm['releaseName']=='fresh-account-release-definition' and isinstance(params,list) and all(isinstance(x,dict) and set(x)=={'name','value'} for x in params),'mode-parent-helm')
            values={x['name']:x['value'] for x in params};require(len(values)==len(params) and values=={'workloadRevision':state['workloadRevision'],'accountRuntimeImage':state['runtimeImage'],'promotionMode':state['mode'],'syncMode':state['syncMode'],'workloadVariant':state['variant']},'mode-parent-parameters')
            args=[plan['tools']['helm']['path'],'template','fresh-account-release-definition',str(app/'learning/fresh-profile/release-definition'),'--namespace','fresh-control']
            for k in sorted(values):args.extend(['--set-string',k+'='+values[k]])
            result=subprocess.run(args,env=env,capture_output=True,timeout=30);require(result.returncode==0 and len(result.stdout)<=5_000_000,'mode-helm-render')
            children=[x for x in yaml.safe_load_all(result.stdout) if x is not None];require(len(children)==1 and isinstance(children[0],dict),'mode-child-render')
            child=children[0];cm=child.get('metadata',{});cs=child.get('spec',{});ss=cs.get('source',{})
            require(child.get('kind')=='Application' and cm.get('name')=='fresh-account-release' and cm.get('namespace')=='fresh-control' and ss.get('repoURL')==src['repoURL'] and ss.get('targetRevision')==state['workloadRevision'] and ss.get('path')==PATHS[(state['variant'],state['mode'])] and (('automated' not in cs.get('syncPolicy',{})) if state['syncMode']=='paused' else cs.get('syncPolicy',{}).get('automated')=={'prune':False,'selfHeal':True}) and 'Prune=false' in cs.get('syncPolicy',{}).get('syncOptions',[]),'mode-child-desired')
            evidence[name]={'rootSpec':root,'parentSpec':spec,'childSpec':cs,'policySpec':policy}
        old,new=workloads['old'],workloads['new'];require(old.keys()==new.keys(),'mode-workload-inventory')
        rollouts=[k for k in old if k[1:]==('Rollout','fresh-account','account-api')]
        require(len(rollouts)==1,'mode-workload-rollout')
        key=rollouts[0];manual=old if plan['states']['old']['mode']=='manual' else new;auto=new if manual is old else old
        steps=manual[key].get('spec',{}).get('strategy',{}).get('canary',{}).get('steps')
        require(isinstance(steps,list) and sum(isinstance(x,dict) and 'pause' in x for x in steps)==3,'mode-workload-manual-pauses')
        normalized=copy.deepcopy(manual);normalized[key]['spec']['strategy']['canary']['steps']=[x for x in steps if 'pause' not in x]
        require(normalized==auto,'mode-workload-unexpected-difference')
        if plan['states']['pre'] is not None:require(workloads['pre']==workloads['old'],'mode-pre-workload-drift')
        if plan['states']['final'] is not None:require(workloads['final']==workloads['new'],'mode-final-workload-drift')
        for name in workloads:
            evidence[name]['rolloutSteps']=workloads[name][key]['spec']['strategy']['canary']['steps']
        return evidence

class ModeAdapter:
    """CAS and read-only observation for the reviewed mode transaction."""
    NAMES={'root':'account-lightweight-root','parent':'fresh-release-definitions','child':'fresh-account-release'}
    def __init__(self,plan,evidence,transport):self.plan=plan;self.evidence=evidence;self.transport=transport
    def call(self,args):
        tool=self.plan['tools']['kubectl'];upgrade.require(upgrade.sha(tool['path'])==tool['sha256'],'mode-kubectl-changed')
        cmd=[tool['path'],'--kubeconfig',self.plan['kubeconfigPath'],'--context',self.plan['target']['context']]+args
        return self.transport.run(cmd,dict(os.environ))
    def get(self,kind,name,namespace=None):
        args=['get',kind,name]+(['--namespace',namespace] if namespace else [])+['-o','json'];r=self.call(args)
        upgrade.require(r.returncode==0 and isinstance(r.stdout,str) and len(r.stdout)<1000000,'mode-observation')
        obj=json.loads(r.stdout);upgrade.require(isinstance(obj,dict),'mode-observation');return obj
    def observe(self, *, rollout_stage="old", allow_automated=False, rollout_version=None):
        t=self.plan['target'];ns=self.get('namespace','kube-system');upgrade.require(ns.get('metadata',{}).get('uid')==t['clusterUID'],'mode-cluster-uid')
        apps={layer:self.get('application',name,'fresh-control') for layer,name in self.NAMES.items()}
        for layer,obj in apps.items():
            meta=obj.get('metadata',{});phase=obj.get('status',{}).get('operationState',{}).get('phase')
            upgrade.require(meta.get('uid')==t[layer+'UID'] and isinstance(meta.get('resourceVersion'),str) and meta['resourceVersion'] and obj.get('operation') is None and phase not in {'Running','Pending','Terminating','Progressing','Waiting'},'mode-application-identity-or-operation:'+layer)
        rollout=self.get('rollout','account-api','fresh-account');guard._settled(apps['child'],rollout,allow_automated=allow_automated)
        upgrade.require(rollout['metadata'].get('uid')==t['rolloutUID'] and (rollout['metadata'].get('resourceVersion')==(t['rolloutResourceVersion'] if rollout_stage in {'pre','old'} else rollout_version) if rollout_stage in {'pre','old'} or rollout_version is not None else True) and rollout.get('spec',{}).get('strategy',{}).get('canary',{}).get('steps')==self.evidence[rollout_stage]['rolloutSteps'],'mode-rollout-stale')
        policy=self.get('validatingadmissionpolicy','fresh-application-boundary');meta=policy.get('metadata',{})
        upgrade.require(meta.get('uid')==self.plan['policy']['uid'] and isinstance(meta.get('resourceVersion'),str) and meta['resourceVersion'],'mode-policy-identity')
        status=policy.get('status',{});warnings=status.get('typeChecking',{}).get('expressionWarnings',[]) if isinstance(status,dict) else None
        upgrade.require(isinstance(meta.get('generation'),int) and isinstance(status,dict) and status.get('observedGeneration')==meta['generation'] and isinstance(warnings,list) and not warnings,'mode-policy-not-ready')
        return apps,policy
    def check(self,done,uncertain=None,*,initial=False,rollout_stage="old"):
        apps,policy=self.observe(rollout_stage=rollout_stage)
        for key,obj in [('policy',policy),*apps.items()]:
            before=self.evidence['old']['policySpec' if key=='policy' else key+'Spec'];after=self.evidence['new']['policySpec' if key=='policy' else key+'Spec']
            allowed=[after] if key in done else [before]
            if key==uncertain:allowed=[before,after]
            upgrade.require(obj.get('spec') in allowed,'mode-desired-drift:'+key)
            if initial:
                version=self.plan['policy']['resourceVersion'] if key=='policy' else self.plan['target'][key+'ResourceVersion']
                upgrade.require(obj['metadata']['resourceVersion']==version,'mode-resource-version:'+key)
        return apps,policy
    def apply(self,key,done):
        apps,policy=self.check(done);obj=policy if key=='policy' else apps[key]
        field='policySpec' if key=='policy' else key+'Spec';before=self.evidence['old'][field];after=self.evidence['new'][field]
        if before==after:return
        meta=obj['metadata'];patch=[{'op':'test','path':'/metadata/uid','value':meta['uid']},{'op':'test','path':'/metadata/resourceVersion','value':meta['resourceVersion']},{'op':'test','path':'/spec','value':before},{'op':'replace','path':'/spec','value':after}]
        kind='validatingadmissionpolicy' if key=='policy' else 'application';name='fresh-application-boundary' if key=='policy' else self.NAMES[key]
        args=['patch',kind,name]+([] if key=='policy' else ['--namespace','fresh-control'])+['--type=json','--patch',json.dumps(patch,separators=(',',':'))]
        result=self.call(args)
        if result.returncode!=0:raise upgrade.Rejected('mode-write-outcome-uncertain:'+key)

    def check_pre(self,done,uncertain=None,*,initial=False):
        apps,policy=self.observe(rollout_stage='pre',allow_automated=True)
        self.check_protected()
        sync=apps['child'].get('status',{}).get('sync',{})
        upgrade.require(sync.get('status')=='Synced' and sync.get('revision')==self.plan['states']['old']['workloadRevision'],'mode-pre-not-fully-synced')
        for key,obj in [('policy',policy),*apps.items()]:
            field='policySpec' if key=='policy' else key+'Spec';before=self.evidence['pre'][field];after=self.evidence['old'][field]
            allowed=[after] if key in done else [before]
            if key==uncertain:allowed=[before,after]
            upgrade.require(obj.get('spec') in allowed,'mode-pre-desired-drift:'+key)
            if initial:
                version=self.plan['policy']['resourceVersion'] if key=='policy' else self.plan['target'][key+'ResourceVersion']
                upgrade.require(obj['metadata']['resourceVersion']==version,'mode-resource-version:'+key)
        return apps,policy
    def apply_pre(self,key,done):
        apps,policy=self.check_pre(done);obj=policy if key=='policy' else apps[key]
        field='policySpec' if key=='policy' else key+'Spec';before=self.evidence['pre'][field];after=self.evidence['old'][field]
        if before==after:return
        meta=obj['metadata'];patch=[{'op':'test','path':'/metadata/uid','value':meta['uid']},{'op':'test','path':'/metadata/resourceVersion','value':meta['resourceVersion']},{'op':'test','path':'/spec','value':before},{'op':'replace','path':'/spec','value':after}]
        kind='validatingadmissionpolicy' if key=='policy' else 'application';name='fresh-application-boundary' if key=='policy' else self.NAMES[key]
        args=['patch',kind,name]+([] if key=='policy' else ['--namespace','fresh-control'])+['--type=json','--patch',json.dumps(patch,separators=(',',':'))]
        result=self.call(args)
        if result.returncode!=0:raise upgrade.Rejected('mode-pre-write-outcome-uncertain:'+key)

    def check_protected(self):
        kinds={'Job':'job','ConfigMap':'configmap','PVC':'persistentvolumeclaim','Secret':'secret'}
        for identity,expected in self.plan['protected'].items():
            kind,name=identity.split('/',1)
            result=self.call(['get',kinds[kind],name,'--namespace','fresh-account','-o','jsonpath={.metadata.uid}{" "}{.metadata.resourceVersion}'])
            upgrade.require(result.returncode==0 and isinstance(result.stdout,str) and result.stdout.strip()==expected['uid']+' '+expected['resourceVersion'],'mode-protected-drift:'+identity)
    def check_final(self,done,uncertain,rollout_version):
        apps,policy=self.observe(rollout_stage='new',allow_automated=True,rollout_version=rollout_version)
        self.check_protected()
        sync=apps['child'].get('status',{}).get('sync',{})
        upgrade.require(sync.get('status')=='Synced' and sync.get('revision')==self.plan['states']['new']['workloadRevision'],'mode-final-not-fully-synced')
        for key,obj in [('policy',policy),*apps.items()]:
            field='policySpec' if key=='policy' else key+'Spec';before=self.evidence['new'][field];after=self.evidence['final'][field]
            allowed=[after] if key in done else [before]
            if key==uncertain:allowed=[before,after]
            upgrade.require(obj.get('spec') in allowed,'mode-final-desired-drift:'+key)
        return apps,policy
    def apply_final(self,key,done,rollout_version):
        apps,policy=self.check_final(done,None,rollout_version);obj=policy if key=='policy' else apps[key]
        field='policySpec' if key=='policy' else key+'Spec';before=self.evidence['new'][field];after=self.evidence['final'][field]
        if before==after:return
        meta=obj['metadata'];patch=[{'op':'test','path':'/metadata/uid','value':meta['uid']},{'op':'test','path':'/metadata/resourceVersion','value':meta['resourceVersion']},{'op':'test','path':'/spec','value':before},{'op':'replace','path':'/spec','value':after}]
        kind='validatingadmissionpolicy' if key=='policy' else 'application';name='fresh-application-boundary' if key=='policy' else self.NAMES[key]
        args=['patch',kind,name]+([] if key=='policy' else ['--namespace','fresh-control'])+['--type=json','--patch',json.dumps(patch,separators=(',',':'))]
        result=self.call(args)
        if result.returncode!=0:raise upgrade.Rejected('mode-final-write-outcome-uncertain:'+key)

    def start_sync(self, nonce):
        apps,_=self.check({'policy','root','parent','child'});child=apps['child'];meta=child['metadata'];spec=child['spec']
        operation={'sync':{'revision':self.plan['states']['new']['workloadRevision'],'prune':False,'resources':[{'group':'argoproj.io','kind':'Rollout','name':'account-api','namespace':'fresh-account'}],'syncOptions':['ApplyOutOfSyncOnly=true','Prune=false','RespectIgnoreDifferences=true']},'info':[{'name':'platform.lab/mode-transition','value':nonce}]}
        patch=[{'op':'test','path':'/metadata/uid','value':meta['uid']},{'op':'test','path':'/metadata/resourceVersion','value':meta['resourceVersion']},{'op':'test','path':'/spec','value':spec},{'op':'add','path':'/operation','value':operation}]
        result=self.call(['patch','application',self.NAMES['child'],'--namespace','fresh-control','--type=json','--patch',json.dumps(patch,separators=(',',':'))])
        if result.returncode!=0:raise upgrade.Rejected('mode-sync-outcome-uncertain')
    def sync_succeeded(self,nonce,*,verify_chain=True):
        child=self.get('application',self.NAMES['child'],'fresh-control')
        status=child.get('status',{}).get('operationState',{});operation=status.get('operation',{});sync=operation.get('sync',{});result=status.get('syncResult',{})
        resources=[{'group':'argoproj.io','kind':'Rollout','name':'account-api','namespace':'fresh-account'}]
        if child.get('operation') is not None or status.get('phase')!='Succeeded' or not status.get('finishedAt') or {'name':'platform.lab/mode-transition','value':nonce} not in operation.get('info',[]):return False
        if sync.get('revision')!=self.plan['states']['new']['workloadRevision'] or sync.get('prune',False) is not False or sync.get('resources')!=resources or sync.get('syncOptions')!=['ApplyOutOfSyncOnly=true','Prune=false','RespectIgnoreDifferences=true'] or sync.get('dryRun',False) is not False:return False
        receipts=result.get('resources');expected={**resources[0],'status':'Synced'}
        if result.get('revision')!=sync['revision'] or not isinstance(receipts,list) or len(receipts)!=1 or not isinstance(receipts[0],dict) or any(receipts[0].get(k)!=v for k,v in expected.items()):return False
        if verify_chain:self.check({'policy','root','parent','child'},rollout_stage='new')
        return True


def execute(action,plan_path,approval_path,journal_path,adapter,*,now=None):
    """Persist intent before each CAS; uncertain writes are never retransmitted."""
    import fcntl
    plan,plan_hash,approval_hash=validate(plan_path,approval_path,now=now)
    require=upgrade.require;require(plan==adapter.plan,'mode-adapter-plan')
    path=Path(journal_path);require(path.is_absolute() and path.parent.is_dir() and not path.is_symlink(),'mode-journal-path')
    lock=path.with_name(path.name+'.lock');require(not lock.is_symlink(),'mode-journal-lock')
    keys=(('pre-policy','pre-root','pre-parent','pre-child') if plan['states']['pre'] is not None else ()) + ('policy','root','parent','child','sync') + (('final-policy','final-root','final-parent','final-child') if plan['states']['final'] is not None else ())
    with open(lock,'a+') as lock_file:
        fcntl.flock(lock_file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        def save(journal):
            fd,name=tempfile.mkstemp(prefix='.mode-journal-',dir=path.parent)
            try:
                with os.fdopen(fd,'w') as f:json.dump(journal,f,sort_keys=True);f.flush();os.fsync(f.fileno())
                os.replace(name,path);d=os.open(path.parent,os.O_RDONLY);os.fsync(d);os.close(d)
            finally:
                if os.path.exists(name):os.unlink(name)
        require(action in {'first','continue','resume'},'mode-action')
        if action=='first':
            require(not path.exists(),'mode-journal-exists')
            if plan['states']['pre'] is None:adapter.check(set(),initial=True)
            else:adapter.check_pre(set(),initial=True)
            journal={'version':1,'planSha256':plan_hash,'approvalSha256':approval_hash,'approvalRenewals':[],'syncNonce':secrets.token_hex(16),'syncRolloutResourceVersion':None,'steps':{k:'pending' for k in keys}};save(journal)
        else:
            require(path.is_file(),'mode-journal-missing');journal=json.loads(path.read_text());require(set(journal)=={'version','planSha256','approvalSha256','approvalRenewals','syncNonce','syncRolloutResourceVersion','steps'} and journal['version']==1 and journal['planSha256']==plan_hash and isinstance(journal['approvalSha256'],str) and re.fullmatch(r'[0-9a-f]{64}',journal['approvalSha256']) and isinstance(journal['approvalRenewals'],list) and all(isinstance(h,str) and re.fullmatch(r'[0-9a-f]{64}',h) for h in journal['approvalRenewals']) and isinstance(journal['syncNonce'],str) and re.fullmatch(r'[0-9a-f]{32}',journal['syncNonce']) and set(journal['steps'])==set(keys) and all(v in {'pending','uncertain','succeeded'} for v in journal['steps'].values()),'mode-journal-binding')
        if approval_hash not in [journal['approvalSha256'],*journal['approvalRenewals']]:
            require(action=='resume','mode-renewal-observe-only')
            journal['approvalRenewals'].append(approval_hash);save(journal)
        steps=journal['steps'];done={k for k,v in steps.items() if v=='succeeded'};uncertain=[k for k,v in steps.items() if v=='uncertain'];require(len(uncertain)<=1,'mode-journal-uncertain')
        require(all(steps[keys[i]]=='succeeded' for i in range(len(done))) and all(steps[k]=='pending' for k in keys[len(done)+len(uncertain):]),'mode-journal-order')
        if uncertain:
            key=uncertain[0];require(keys[len(done)]==key,'mode-journal-order')
            if key=='sync':
                if adapter.sync_succeeded(journal['syncNonce']):
                    journal['syncRolloutResourceVersion']=adapter.get('rollout','account-api','fresh-account')['metadata']['resourceVersion'];steps[key]='succeeded';save(journal)
                return journal
            if key.startswith('pre-'):
                stage=key[4:];pre_done={x[4:] for x in done if x.startswith('pre-')};apps,policy=adapter.check_pre(pre_done,stage);obj=policy if stage=='policy' else apps[stage];field='policySpec' if stage=='policy' else stage+'Spec';expected=adapter.evidence['old'][field]
            elif key.startswith('final-'):
                stage=key[6:];final_done={x[6:] for x in done if x.startswith('final-')};apps,policy=adapter.check_final(final_done,stage,journal['syncRolloutResourceVersion']);obj=policy if stage=='policy' else apps[stage];field='policySpec' if stage=='policy' else stage+'Spec';expected=adapter.evidence['final'][field]
            else:
                apps,policy=adapter.check(done,uncertain=key);obj=policy if key=='policy' else apps[key];field='policySpec' if key=='policy' else key+'Spec';expected=adapter.evidence['new'][field]
            if obj.get('spec')==expected:steps[key]='succeeded';save(journal)
            return journal
        if 'sync' in done:
            require(adapter.sync_succeeded(journal['syncNonce'],verify_chain=False),'mode-sync-completed-drift')
            if plan['states']['final'] is None:
                adapter.check(done,rollout_stage='new');return journal
            require(isinstance(journal['syncRolloutResourceVersion'],str) and journal['syncRolloutResourceVersion'],'mode-sync-rollout-version')
            adapter.check_final({x[6:] for x in done if x.startswith('final-')},None,journal['syncRolloutResourceVersion'])
        elif plan['states']['pre'] is not None and any(steps['pre-'+k]!='succeeded' for k in ('policy','root','parent','child')):
            adapter.check_pre({x[4:] for x in done if x.startswith('pre-')})
        else:adapter.check(done)
        if action=='resume':return journal
        for key in keys:
            if steps[key]=='succeeded':continue
            steps[key]='uncertain';save(journal)
            if key=='sync':
                adapter.start_sync(journal['syncNonce'])
                if adapter.sync_succeeded(journal['syncNonce']):
                    journal['syncRolloutResourceVersion']=adapter.get('rollout','account-api','fresh-account')['metadata']['resourceVersion'];steps[key]='succeeded';save(journal);done.add(key)
                return journal
            if key.startswith('pre-'):
                stage=key[4:];pre_done={x[4:] for x in done if x.startswith('pre-')};adapter.apply_pre(stage,pre_done);adapter.check_pre(pre_done|{stage})
            elif key.startswith('final-'):
                stage=key[6:];final_done={x[6:] for x in done if x.startswith('final-')};adapter.apply_final(stage,final_done,journal['syncRolloutResourceVersion']);adapter.check_final(final_done|{stage},None,journal['syncRolloutResourceVersion'])
            else:
                adapter.apply(key,done);adapter.check(done|{key})
            steps[key]='succeeded';save(journal);done.add(key)
        return journal
