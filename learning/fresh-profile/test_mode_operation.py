import copy
import unittest
from mode_operation import Rejected, _settled

class ModeGuardTests(unittest.TestCase):
    def setUp(self):
        self.app={'kind':'Application','metadata':{'name':'fresh-account-release','namespace':'fresh-control','uid':'a','resourceVersion':'10'},'spec':{'source':{'path':'learning/fresh-profile/workload'},'syncPolicy':{}},'status':{}}
        self.rollout={'kind':'Rollout','metadata':{'name':'account-api','namespace':'fresh-account','uid':'r','resourceVersion':'20','generation':3},'status':{'phase':'Healthy','observedGeneration':'3','stableRS':'h','currentPodHash':'h'}}
    def test_guard_accepts_only_fixed_paths(self):
        for suffix in ('','-auto','-existing','-existing-auto'):
            self.app['spec']['source']['path']='learning/fresh-profile/workload'+suffix;_settled(self.app,self.rollout)
        self.app['spec']['source']['path']='learning/fresh-profile/other'
        with self.assertRaises(Rejected):_settled(self.app,self.rollout)
    def test_guard_rejects_progress_and_autosync(self):
        changes=[lambda a,r:r['status'].update(phase='Progressing'),lambda a,r:r['status'].update(observedGeneration='2'),lambda a,r:r['status'].update(currentPodHash='other'),lambda a,r:a.update(operation={'sync':{}}),lambda a,r:a['spec'].update(syncPolicy={'automated':{}})]
        for change in changes:
            app=copy.deepcopy(self.app);rollout=copy.deepcopy(self.rollout);change(app,rollout)
            with self.assertRaises(Rejected):_settled(app,rollout)
