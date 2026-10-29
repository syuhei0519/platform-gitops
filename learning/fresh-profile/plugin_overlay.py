"""Offline review overlay; no cluster, image build, or registry operations."""
import copy
import re

PLUGIN = 'learning-local/account-policy'
BASE = 'quay.io/argoproj/argo-rollouts:v1.10.1@sha256:fbcc4cf0a29da25d86230992b1dfdeadf30b6f1baa74ea7c9c98e3e580769396'


def overlay(deployment, image, binary_sha256):
    if not re.fullmatch(r'[a-z0-9][a-z0-9./-]*@sha256:[a-f0-9]{64}', image):
        raise ValueError('immutable-controller-image-required')
    if not re.fullmatch(r'[a-f0-9]{64}', binary_sha256):
        raise ValueError('binary-checksum-required')
    d = copy.deepcopy(deployment)
    meta = d.get('metadata', {})
    if d.get('kind') != 'Deployment' or meta.get('name') != 'argo-rollouts' or meta.get('namespace') != 'fresh-control':
        raise ValueError('controller-identity')
    pod = d['spec']['template']['spec']; containers = pod['containers']
    if len(containers) != 1 or containers[0].get('image') != BASE:
        raise ValueError('pinned-controller-required')
    sa = pod.get('serviceAccountName')
    if not sa: raise ValueError('controller-service-account-required')
    container = containers[0]
    if any(e.get('name') in ('ACCOUNT_POLICY_PROFILE','ACCOUNT_POLICY_SHA256') for e in container.get('env', [])):
        raise ValueError('controller-env-conflict')
    container['image'] = image
    container.setdefault('env', []).extend([{'name':'ACCOUNT_POLICY_PROFILE','value':'fresh-account'}, {'name':'ACCOUNT_POLICY_SHA256','value':binary_sha256}])
    config = {'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'argo-rollouts-config','namespace':'fresh-control'},
              'data':{'metricProviderPlugins':f'- name: {PLUGIN}\n  location: file:///opt/account-policy\n  sha256: {binary_sha256}\n'}}
    role = {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'Role','metadata':{'name':'account-policy-reader','namespace':'fresh-account'},
            'rules':[{'apiGroups':[''], 'resources':['pods'], 'verbs':['list']},
                     {'apiGroups':['apps'], 'resources':['replicasets'], 'verbs':['list']},
                     {'apiGroups':['argoproj.io'], 'resources':['rollouts'], 'verbs':['get'], 'resourceNames':['account-api']},
                     {'apiGroups':['argoproj.io'], 'resources':['analysisruns'], 'verbs':['get']},
                     {'apiGroups':['networking.istio.io'], 'resources':['destinationrules','virtualservices'], 'verbs':['get'], 'resourceNames':['account-api']}]}
    binding = {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'RoleBinding','metadata':{'name':'account-policy-reader','namespace':'fresh-account'},
               'subjects':[{'kind':'ServiceAccount','name':sa,'namespace':'fresh-control'}],
               'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':'account-policy-reader'}}
    return [d, config, role, binding]
