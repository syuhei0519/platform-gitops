#!/usr/bin/env python3
# 学習用の段階配備を管理する Argo CD Application を生成し、固定版と対象範囲を検証します。
"""Create the single manual-sync Account Application proposal, without API calls."""
import argparse
import re
from pathlib import Path

import yaml

REPO = 'https://github.com/syuhei0519/application-manifest.git'


def application(namespace, control, commit, values):
    if any(len(ns) > 63 or not re.fullmatch(r'learning-[a-z0-9]([-a-z0-9]*[a-z0-9])?', ns)
           for ns in (namespace, control)) or namespace == control:
        raise ValueError('distinct learning-* namespaces required')
    if not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('fixed Manifest commit required; branch names are not accepted')
    if set(values) - {'analysis'} != {'owner', 'imageConfigID', 'sourceCommit', 'mode', 'revision', 'telemetry'}:
        raise ValueError('only Account API values accepted; no DB/Secret/migration inputs')
    if values['mode'] != 'rollout' or values['revision'] not in ('good', 'candidate', 'failed'):
        raise ValueError('Rollout mode required; Deployment restoration is explicit local work')
    if values['sourceCommit'] != 'e030275abbf52ecd27b7152ce597b3d89be3f830':
        raise ValueError('reuse the fixed deployed Account source')
    if not re.fullmatch('sha256:[a-f0-9]{64}', values['imageConfigID']):
        raise ValueError('verified image config ID required')
    if not isinstance(values['owner'], str) or len(values['owner']) > 63 or not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?', values['owner']):
        raise ValueError('valid owner required')
    if values['telemetry'] != {'enabled': True}:
        raise ValueError('preserve the enabled Account telemetry contract')
    analysis = values.get('analysis', {'enabled': False, 'case': 'success'})
    if (not isinstance(analysis, dict) or set(analysis) - {'mode'} != {'enabled', 'case'} or
            type(analysis['enabled']) is not bool or analysis['case'] not in
            ('success', 'failure', 'inconclusive', 'missing', 'timeout')):
        raise ValueError('only the bounded Account analysis cases are accepted')
    analysis = dict(analysis, mode=analysis.get('mode', 'fixed'))
    if analysis['mode'] not in ('fixed', 'metrics'):
        raise ValueError('analysis mode must be fixed or metrics')
    if analysis['mode'] == 'metrics' and not analysis['enabled']:
        raise ValueError('metrics mode requires enabled analysis')
    if analysis['enabled'] and values['revision'] == 'failed':
        raise ValueError('analysis requires a Ready revision; failed readiness is a separate test')
    allowed = [dict(group='argoproj.io', kind='Rollout'), dict(group='', kind='Service')]
    if analysis['enabled']:
        allowed += [dict(group='argoproj.io', kind='AnalysisTemplate')]
    project = dict(apiVersion='argoproj.io/v1alpha1', kind='AppProject',
                   metadata=dict(name='learning-account', namespace=control),
                   spec=dict(sourceRepos=[REPO], destinations=[dict(server='https://kubernetes.default.svc', namespace=namespace)],
                             clusterResourceWhitelist=[], namespaceResourceWhitelist=allowed,
                             orphanedResources=dict(warn=False)))
    app = dict(apiVersion='argoproj.io/v1alpha1', kind='Application',
               metadata=dict(name='learning-account', namespace=control),
               spec=dict(project='learning-account', source=dict(repoURL=REPO, targetRevision=commit, path='learning/canary/chart',
                         helm=dict(releaseName='learning-account', valueFiles=['../profiles/' + ('metrics-' if analysis['mode'] == 'metrics' else '') + values['revision'] + '.yaml'],
                                   valuesObject=dict({k:v for k,v in values.items() if k not in ('revision', 'analysis')}, analysis=analysis))),
                         destination=dict(server='https://kubernetes.default.svc', namespace=namespace),
                         syncPolicy=dict(syncOptions=['RespectIgnoreDifferences=true']),
                         ignoreDifferences=[dict(group='', kind='Service', name=name, namespace=namespace,
                         jsonPointers=['/spec/selector/rollouts-pod-template-hash']) for name in ('learning-account-stable','learning-account-canary')]))
    return [project, app]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--namespace', required=True)
    p.add_argument('--control-namespace', required=True)
    p.add_argument('--manifest-commit', required=True)
    p.add_argument('--values', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    docs = application(args.namespace, args.control_namespace, args.manifest_commit, yaml.safe_load(args.values.read_text()))
    with args.output.open('x') as output:
        yaml.safe_dump_all(docs, output, sort_keys=False)


if __name__ == '__main__':
    main()
