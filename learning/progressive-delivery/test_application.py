#!/usr/bin/env python3
# 段階配備の Application 生成で、許可した対象と固定版の条件を守ることを確認します。
"""Render the Account chart with the generated Application's actual Helm inputs."""
import argparse
import copy
from pathlib import Path
import subprocess
import tempfile

import yaml

from application import application

MANIFEST_COMMIT = 'a817e00f42a659d1dcd8a9766ad2727cc4beee18'


def render_application(app, repository):
    source = app['spec']['source']
    chart = repository / source['path']
    helm = source['helm']
    # The valuesObject must follow valueFiles, matching Argo's Helm precedence.
    with tempfile.TemporaryDirectory() as directory:
        override = Path(directory) / 'values.yaml'
        override.write_text(yaml.safe_dump(helm['valuesObject']))
        command = ['helm', 'template', helm['releaseName'], str(chart),
                   '--namespace', app['spec']['destination']['namespace'], '--kube-version', '1.36.4']
        for path in helm['valueFiles']:
            command += ['-f', str(chart / path)]
        command += ['-f', str(override)]
        return list(yaml.safe_load_all(subprocess.check_output(command, text=True)))


def validate(repository):
    values = dict(owner='synthetic-fixture', imageConfigID='sha256:' + 'a'*64,
                  sourceCommit='e030275abbf52ecd27b7152ce597b3d89be3f830', mode='rollout',
                  telemetry=dict(enabled=True))
    cases = [None, dict(enabled=False, case='success'), dict(enabled=True, case='success'),
             dict(enabled=True, mode='fixed', case='failure'), dict(enabled=True, mode='metrics', case='success')]
    for revision in ('good', 'candidate'):
        for analysis in cases:
            current = dict(values, revision=revision)
            if analysis is not None:
                current['analysis'] = analysis
            original = copy.deepcopy(current)
            project, app = application('learning-fixture', 'learning-control', MANIFEST_COMMIT, current)
            assert current == original, 'generation must not mutate caller inputs'
            helm = app['spec']['source']['helm']
            canonical = dict(enabled=False, case='success', mode='fixed') if analysis is None else dict(analysis, mode=analysis.get('mode', 'fixed'))
            assert helm['valuesObject']['analysis'] == canonical
            assert 'revision' not in helm['valuesObject']
            prefix = 'metrics-' if canonical['mode'] == 'metrics' else ''
            assert helm['valueFiles'] == ['../profiles/' + prefix + revision + '.yaml']
            objects = render_application(app, repository)
            rendered = {(o['apiVersion'].split('/')[0] if '/' in o['apiVersion'] else '', o['kind']) for o in objects}
            allowed = {(r['group'], r['kind']) for r in project['spec']['namespaceResourceWhitelist']}
            assert rendered == allowed, (rendered, allowed)
            assert len(objects) == (5 if canonical['enabled'] else 4)
            rollout = next(o for o in objects if o['kind'] == 'Rollout')
            steps = rollout['spec']['strategy']['canary']['steps']
            assert steps[0] == dict(setWeight=10)
            if canonical['enabled']:
                template = next(o for o in objects if o['kind'] == 'AnalysisTemplate')
                metrics = canonical['mode'] == 'metrics'
                step = 2 if metrics else 1
                if metrics:
                    assert steps[1] == dict(pause={})
                assert steps[step + 1:] == [dict(setWeight=50), dict(pause={}), dict(setWeight=100), dict(pause={})]
                assert steps[step]['analysis']['templates'] == [dict(templateName=template['metadata']['name'])]
                providers = [m['provider'] for m in template['spec']['metrics']]
                assert len(providers) == (6 if metrics else 1)
                assert all(set(p) == ({'prometheus'} if metrics else {'web'}) for p in providers)
            else:
                assert steps[2:] == [dict(setWeight=50), dict(pause={}), dict(setWeight=100), dict(pause={})]
                assert steps[1] == dict(pause={})
                assert not any('analysis' in step for step in steps)
            assert not project['spec']['clusterResourceWhitelist']
            assert 'automated' not in app['spec']['syncPolicy']
            assert not app['metadata'].get('finalizers')
    for analysis in (dict(enabled=False, mode='metrics', case='success'),
                     dict(enabled=True, mode='unknown', case='success'),
                     dict(enabled=True, mode='metrics', case='success', url='http://other')):
        try:
            application('learning-fixture', 'learning-control', MANIFEST_COMMIT, dict(values, revision='candidate', analysis=analysis))
        except ValueError:
            pass
        else:
            raise AssertionError('invalid or disabled metrics input accepted')
    print('PASS generated Application -> actual Helm values/files -> exact AppProject allowlist: 10 manual/fixed/metrics renders; 3 input rejections')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest-repo', type=Path, required=True)
    validate(parser.parse_args().manifest_repo.resolve(strict=True))
