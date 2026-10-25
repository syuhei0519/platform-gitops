#!/usr/bin/env python3
# 監視 Chart・dashboard・クエリのつながりと、計測対象や閾値の契約を静的に確認します。
"""Small offline schema/render/config/dashboard boundary check. No cluster calls."""
import argparse
import configparser
import copy
import json
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parent
CHART = ROOT / 'chart'
DEFAULTS = yaml.safe_load((CHART / 'values.yaml').read_text())
SCHEMA = json.loads((CHART / 'values.schema.json').read_text())
Draft7Validator.check_schema(SCHEMA)
VALIDATOR = Draft7Validator(SCHEMA)


def sli_queries(namespace):
    """Bind the finite profile input before a query reaches Prometheus."""
    assert namespace and '__' not in namespace
    source = json.loads((ROOT / 'sli-queries.json').read_text())
    assert all('__ACCOUNT_NAMESPACE__' in expr for expr in source.values())
    return {key: expr.replace('__ACCOUNT_NAMESPACE__', namespace)
            for key, expr in source.items()}


def helm(args, ok=True):
    result = subprocess.run(['helm', *args], capture_output=True, text=True)
    assert (result.returncode == 0) == ok, result.stderr
    return result.stdout


def render(values, directory, namespace='observability', release='learning-monitoring', ok=True):
    path = directory / 'values.yaml'
    path.write_text(yaml.safe_dump(values))
    return helm(['template', release, str(CHART), '--namespace', namespace,
                 '--kube-version', '1.36.4', '-f', str(path)], ok)


def verify(text):
    objects = list(yaml.safe_load_all(text))
    assert Counter(o['kind'] for o in objects) == {
        'Deployment': 3, 'Service': 3, 'ServiceAccount': 3, 'ConfigMap': 3, 'Role': 1, 'RoleBinding': 1}
    by_name = {(o['kind'], o['metadata']['name']): o for o in objects}
    for obj in objects:
        assert obj['metadata']['namespace'] == ('learning-fixture' if obj['kind'] in ('Role', 'RoleBinding') else 'observability')
        assert obj['metadata']['labels'] == {
            'app.kubernetes.io/part-of': 'learning-monitoring',
            'learning.local/owner': 'synthetic-fixture'}
        kind = obj['kind']
        if kind == 'ServiceAccount':
            assert obj['automountServiceAccountToken'] is (obj['metadata']['name'] == 'otel-collector')
        if kind == 'Service':
            assert obj['spec']['type'] == 'ClusterIP'
            deploy = by_name['Deployment', obj['metadata']['name']]
            assert obj['spec']['selector'] == deploy['spec']['selector']['matchLabels']
        if kind != 'Deployment':
            continue
        assert obj['spec']['replicas'] == 1
        template = obj['spec']['template']
        assert template['metadata']['annotations']['checksum/config']
        assert all(template['metadata']['labels'][k] == v
                   for k, v in obj['spec']['selector']['matchLabels'].items())
        spec = template['spec']
        assert spec['automountServiceAccountToken'] is (obj['metadata']['name'] == 'otel-collector')
        assert spec['serviceAccountName'] == obj['metadata']['name']
        assert spec['securityContext']['runAsNonRoot'] is True
        assert not any(spec.get(k) for k in ('hostNetwork', 'hostPID', 'hostIPC'))
        assert len(spec['containers']) == 1 and not spec.get('initContainers')
        assert all(set(v) <= {'name', 'emptyDir', 'configMap'} for v in spec['volumes'])
        c = spec['containers'][0]
        assert c['image'] == DEFAULTS['images'][c['name']]
        assert not c.get('env') and not c.get('envFrom')
        assert c['securityContext'] == {'allowPrivilegeEscalation': False,
                                       'readOnlyRootFilesystem': True,
                                       'capabilities': {'drop': ['ALL']}}
        assert c['resources']['requests']['memory'] == ('128Mi' if c['name'] == 'grafana' else '64Mi')
        assert c['resources']['limits']['memory'] == ('512Mi' if c['name'] == 'grafana' else '256Mi')
        assert all(p in c for p in ('startupProbe', 'readinessProbe', 'livenessProbe'))
    configs = {o['metadata']['name']: o['data'] for o in objects if o['kind'] == 'ConfigMap'}
    collector = yaml.safe_load(configs['otel-collector-config']['config.yaml'])
    assert set(collector['exporters']) == {'prometheus', 'nop'}
    assert collector['exporters']['prometheus']['send_timestamps'] is True
    assert collector['exporters']['prometheus']['metric_expiration'] == '90s'
    assert collector['exporters']['prometheus']['resource_to_telemetry_conversion']['enabled'] is False
    assert len(collector['processors']['filter/account']['metrics']['metric']) == 3
    assert collector['processors']['resource/real_namespace']['attributes'] == [
        {'key': 'k8s.namespace.name', 'value': 'learning-fixture', 'action': 'upsert'}]
    assert collector['service']['pipelines']['metrics']['processors'] == [
        'memory_limiter', 'filter/account', 'resource/real_namespace', 'k8sattributes/account', 'transform/revision', 'batch']
    assert collector['service']['pipelines']['traces']['exporters'] == ['nop']
    role = by_name['Role', 'learning-collector-pods']
    assert role['rules'] == [dict(apiGroups=[''], resources=['pods'], verbs=['get', 'list', 'watch'])]
    binding = by_name['RoleBinding', 'learning-collector-pods']
    assert binding['roleRef'] == dict(apiGroup='rbac.authorization.k8s.io', kind='Role', name='learning-collector-pods')
    assert binding['subjects'] == [dict(kind='ServiceAccount', name='otel-collector', namespace='observability')]
    metadata = collector['processors']['k8sattributes/account']
    assert metadata['filter'] == dict(namespace='learning-fixture')
    assert metadata['pod_association'] == [{'sources': [{'from': 'resource_attribute', 'name': 'k8s.pod.uid'}]}]
    assert metadata['extract']['metadata'] == ['k8s.pod.uid']
    assert metadata['extract']['labels'] == [
        dict(tag_name='rollout_revision', key='rollouts-pod-template-hash', **{'from':'pod'}),
        dict(tag_name='learning_service', key='learning.local/service', **{'from':'pod'})]
    transform = collector['processors']['transform/revision']
    assert transform['error_mode'] == 'propagate'
    statements = transform['metric_statements']
    assert len(statements) == 1 and statements[0]['context'] == 'datapoint'
    assert statements[0]['conditions'] == ['metric.name == "pe_http_server_requests" or metric.name == "pe_http_server_request_duration" or metric.name == "pe_go_goroutines"']
    assert len(statements[0]['statements']) == 4

    prom = yaml.safe_load(configs['learning-prometheus-config']['prometheus.yml'])
    assert [x['job_name'] for x in prom['scrape_configs']] == ['otel-application', 'prometheus']
    app = prom['scrape_configs'][0]
    assert app['honor_labels'] is True and app['honor_timestamps'] is True
    assert app['static_configs'][0]['targets'] == ['otel-collector.observability.svc.cluster.local:8889']
    assert prom['global']['scrape_interval'] == '30s'
    assert not any(k.endswith('_sd_configs') for j in prom['scrape_configs'] for k in j)
    grafana = configs['learning-grafana-config']
    ini = configparser.ConfigParser()
    ini.read_string(grafana['grafana.ini'])
    for section, key, expected in [('security', 'disable_initial_admin_creation', True),
                                   ('auth', 'disable_login', True), ('auth.basic', 'enabled', False),
                                   ('auth.anonymous', 'enabled', True), ('users', 'allow_sign_up', False),
                                   ('analytics', 'reporting_enabled', False),
                                   ('analytics', 'check_for_updates', False),
                                   ('plugins', 'preinstall_disabled', True),
                                   ('plugins', 'preinstall_auto_update', False),
                                   ('snapshots', 'external_enabled', False)]:
        assert ini.getboolean(section, key) is expected
    assert ini['auth.anonymous']['org_role'] == 'Viewer'
    ds = yaml.safe_load(grafana['datasources.yaml'])['datasources']
    assert len(ds) == 1 and ds[0]['uid'] == 'learning-prometheus' and not ds[0]['editable']
    provider = yaml.safe_load(grafana['providers.yaml'])['providers'][0]
    assert not provider['allowUiUpdates'] and not provider['editable']
    dashboard = json.loads(grafana['account-overview.json'])
    assert dashboard['uid'] == 'learning-account-overview' and dashboard['editable'] is False
    assert 'learning-fixture' in dashboard['description']
    assert len(dashboard['panels']) == 13
    queries = {p['targets'][0]['expr'] for p in dashboard['panels'][:6]}
    fixtures = yaml.safe_load((ROOT / 'query-tests.yaml').read_text())
    tested = {e['expr'] for t in fixtures['tests'] for e in t['promql_expr_test']}
    assert queries == tested, 'promtool fixtures must test the exact dashboard expressions'
    sli = sli_queries('learning-fixture')
    assert {p['targets'][0]['expr'] for p in dashboard['panels'][6:]} == set(sli.values())
    assert all('account_namespace="learning-fixture"' in expr for expr in queries | set(sli.values()))
    assert all(p['fieldConfig']['defaults']['noValue'] == 'N/A' for p in dashboard['panels'])
    assert 'or vector(0)' not in dashboard['panels'][1]['targets'][0]['expr'].split(' and on() ')[0]
    return configs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='write synthetic render/configs for promtool/schema checks')
    args = parser.parse_args()
    assert list(VALIDATOR.iter_errors(DEFAULTS)), 'unconfigured defaults must fail'
    from revision_queries import queries
    assert json.loads((ROOT / 'revision-queries.json').read_text()) == queries()
    values = copy.deepcopy(DEFAULTS)
    values.update(owner='synthetic-fixture', accountNamespace='learning-fixture')
    VALIDATOR.validate(values)
    with tempfile.TemporaryDirectory(prefix='monitoring-render-') as tmp:
        directory = Path(tmp)
        text = render(values, directory)
        configs = verify(text)
        helm(['lint', '--strict', str(CHART), '--namespace', 'observability', '-f', str(directory / 'values.yaml')])
        for key, value in [('owner', ''), ('accountNamespace', ''), ('accountNamespace', 'account'),
                           ('accountNamespace', 'default'), ('unexpected', True)]:
            bad = copy.deepcopy(values)
            bad[key] = value
            assert list(VALIDATOR.iter_errors(bad))
            render(bad, directory, ok=False)
        for image in DEFAULTS['images']:
            bad = copy.deepcopy(values)
            bad['images'][image] = 'mutable:latest'
            assert list(VALIDATOR.iter_errors(bad))
            render(bad, directory, ok=False)
        render(values, directory, namespace='default', ok=False)
        render(values, directory, release='wrong', ok=False)
        changed = copy.deepcopy(values)
        changed['accountNamespace'] = 'learning-other'
        changed_text = render(changed, directory)
        assert changed_text != text
        changed_objects = list(yaml.safe_load_all(changed_text))
        changed_grafana = next(o for o in changed_objects
                               if o['kind'] == 'ConfigMap' and o['metadata']['name'] == 'learning-grafana-config')
        changed_dashboard = json.loads(changed_grafana['data']['account-overview.json'])
        changed_queries = {p['targets'][0]['expr'] for p in changed_dashboard['panels']}
        assert changed_queries == set(sli_queries('learning-other').values()) | {
            p['targets'][0]['expr'] for p in changed_dashboard['panels'][:6]}
        assert all('account_namespace="learning-other"' in expr for expr in changed_queries)
        assert all('learning-fixture' not in expr for expr in changed_queries)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'rendered.yaml').write_text(text)
        for data in configs.values():
            for name, content in data.items():
                (args.output / name).write_text(content)
        # Materialize exact dashboard expressions from compact numeric fixtures.
        sli = sli_queries('learning-fixture')
        fixtures = yaml.safe_load((ROOT / 'sli-fixtures.yaml').read_text())
        for case in fixtures['tests']:
            expected = case.pop('expected')
            assert set(expected) <= set(sli)
            case['promql_expr_test'] = [
                {'expr': expr, 'eval_time': '10m', 'exp_samples':
                 [{'labels': '{}', 'value': expected[key]}] if key in expected else []}
                for key, expr in sli.items()]
        # JSON is valid YAML for promtool, without aliases rejected by scan-gate.
        (args.output / 'sli-query-tests.yaml').write_text(json.dumps(fixtures, indent=2) + '\n')
    from revision_fixtures import fixtures as revision_fixtures
    if args.output:
        (args.output / 'revision-query-tests.yaml').write_text(json.dumps(revision_fixtures(), indent=2) + '\n')
    print('PASS monitoring: schema/strict lint/Kubernetes 1.36.4 render, 14-resource / Pod-only RBAC boundary, configs, dashboard/query binding, 10 rejections')
    print('Synthetic render only; no Kubernetes or live acceptance claimed')


if __name__ == '__main__':
    main()
