#!/usr/bin/env python3
# 生成したアラートの閾値・対象・通知条件を、実環境へ送信せずに検査します。
"""Validate opt-in rendering and generate minimal alert acceptance fixtures."""
import copy
import json
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import validate as monitoring
from rules import rules


def prepare(output):
    assert yaml.safe_load((ROOT / 'chart/files/account-alerts.yaml').read_text()) == rules()
    values = copy.deepcopy(monitoring.DEFAULTS)
    values.update(owner='synthetic-fixture', accountNamespace='learning-fixture')
    values['alerting']['enabled'] = True
    monitoring.VALIDATOR.validate(values)
    output.mkdir(parents=True, exist_ok=True)
    text = monitoring.render(values, output)
    monitoring.helm(['lint', '--strict', str(monitoring.CHART), '--namespace', 'observability', '-f', str(output / 'values.yaml')])
    objects = list(yaml.safe_load_all(text))
    extra = [o for o in objects if o['metadata']['namespace'] == 'learning-slo-alerts']
    assert len(objects) == 20 and len(extra) == 6
    assert sorted(o['kind'] for o in extra) == ['ConfigMap'] * 2 + ['Deployment'] * 2 + ['Service'] * 2
    assert not any(o['kind'] in ('Secret', 'ClusterRole', 'ClusterRoleBinding', 'Namespace') for o in objects)
    baseline = list(yaml.safe_load_all(monitoring.render(dict(values, alerting=dict(values['alerting'], enabled=False)), output)))
    unchanged = lambda o: o['metadata']['name'] not in ('learning-prometheus', 'learning-prometheus-config') and o['metadata']['namespace'] != 'learning-slo-alerts'
    assert [o for o in objects if unchanged(o)] == [o for o in baseline if unchanged(o)]
    (output / 'values.yaml').write_text(yaml.safe_dump(values))
    for o in extra:
        if o['kind'] == 'Service':
            assert o['spec']['type'] == 'ClusterIP'
        if o['kind'] == 'Deployment':
            pod = o['spec']['template']['spec']
            assert pod['automountServiceAccountToken'] is False
            assert not pod.get('serviceAccountName')
            assert all(v.keys() <= {'name', 'emptyDir', 'configMap'} for v in pod['volumes'])
    configs = {o['metadata']['name']: o['data'] for o in objects if o['kind'] == 'ConfigMap'}
    for name in ('learning-prometheus-config', 'learning-alertmanager', 'learning-alert-receiver'):
        for filename, content in configs[name].items():
            (output / filename).write_text(content)
    (output / 'rendered.yaml').write_text(text)
    fixtures = yaml.safe_load((ROOT / 'sli-fixtures.yaml').read_text())
    alert = yaml.safe_load((output / 'account-alerts.yaml').read_text())['groups'][0]['rules'][-1]
    labels = dict(alert['labels'])
    # Metric routing identity is not the Kubernetes Service identity.
    assert alert['labels']['service'] == 'backend'
    assert alert['annotations']['service_url'] == 'http://learning-account.learning-fixture.svc.cluster.local:8080'
    changed = copy.deepcopy(values)
    changed['accountNamespace'] = 'learning-other'
    changed['alerting'].update(accountServiceName='alternate-account', accountServicePort=18080)
    changed_objects = list(yaml.safe_load_all(monitoring.render(changed, output)))
    changed_cm = next(o for o in changed_objects if o['kind'] == 'ConfigMap' and o['metadata']['name'] == 'learning-prometheus-config')
    changed_rules = yaml.safe_load(changed_cm['data']['account-alerts.yaml'])['groups'][0]['rules']
    changed_alert = changed_rules[-1]
    assert changed_alert['annotations']['service_url'] == 'http://alternate-account.learning-other.svc.cluster.local:18080'
    assert changed_alert['labels']['service'] == 'backend'
    assert changed_alert['labels']['account_namespace'] == 'learning-other'
    baseline_rules = yaml.safe_load((output / 'account-alerts.yaml').read_text())['groups'][0]['rules']
    assert changed_rules[:2] != baseline_rules[:2]
    assert all('account_namespace="learning-other"' in rule['expr'] for rule in changed_rules[:2])
    assert all('account_namespace="learning-fixture"' in rule['expr'] for rule in baseline_rules[:2])
    assert (changed_alert['expr'], changed_alert['for']) == (alert['expr'], alert['for'])
    routing = yaml.safe_load(configs['learning-alertmanager']['alertmanager.yml'])
    assert 'service="backend"' in routing['route']['routes'][0]['matchers']
    for key, invalid in [('accountServiceName', ''), ('accountServiceName', 'http://bad'),
                         ('accountServicePort', 0), ('accountServicePort', 65536), ('accountServicePort', '8080')]:
        bad = copy.deepcopy(values)
        bad['alerting'][key] = invalid
        assert list(monitoring.VALIDATOR.iter_errors(bad))
        monitoring.render(bad, output, ok=False)
    (output / 'values.yaml').write_text(yaml.safe_dump(values))
    print('PASS Service reference: Kubernetes name/namespace/port independent of metric service=backend; invalid inputs rejected')
    tests = []
    for source in fixtures['tests']:
        case = copy.deepcopy(source)
        expected = case.pop('expected')
        burn = (1 - expected.get('availability', 1)) / .01
        case['alert_rule_test'] = [{'eval_time': '10m', 'alertname': alert['alert'],
                                   'exp_alerts': [{'exp_labels': labels, 'exp_annotations': alert['annotations']}] if burn > 2 else []}]
        case['promql_expr_test'] = [{'expr': 'account:sli_state:5m', 'eval_time': '10m', 'exp_samples': [{'labels': 'account:sli_state:5m{}', 'value': expected['state']}]}]
        tests.append(case)
    # Sustained failure; pending first, firing after exactly 2m, then normal recovery.
    series = [
        {'series': 'up{job="otel-application",account_namespace="learning-fixture"}', 'values': '1+0x40'},
        {'series': 'pe_go_goroutines{job="account-backend",account_namespace="learning-fixture"}', 'values': '8+0x40'},
        {'series': 'pe_http_server_requests_total{job="account-backend",account_namespace="learning-fixture",status_class="2xx"}', 'values': '0+27x20 570+30x19'},
        {'series': 'pe_http_server_requests_total{job="account-backend",account_namespace="learning-fixture",status_class="5xx"}', 'values': '0+3x20 60+0x19'},
        {'series': 'pe_http_server_request_duration_seconds_count{job="account-backend",account_namespace="learning-fixture"}', 'values': '0+30x40'},
        {'series': 'pe_http_server_request_duration_seconds_bucket{job="account-backend",account_namespace="learning-fixture",le="0.25"}', 'values': '0+30x40'}]
    tests.append({'name': 'pending firing and healthy recovery', 'interval': '30s', 'input_series': series,
                  'alert_rule_test': [{'eval_time': t, 'alertname': alert['alert'], 'exp_alerts': [{'exp_labels': labels, 'exp_annotations': alert['annotations']}] if fire else []} for t, fire in [('2m', False), ('2m30s', True), ('10m', True), ('15m', False), ('20m', False)]],
                  'promql_expr_test': [{'expr': 'account:sli_state:5m', 'eval_time': '15m', 'exp_samples': [{'labels': 'account:sli_state:5m{}', 'value': 4}]}, {'expr': 'account:availability_burn_rate:5m', 'eval_time': '15m', 'exp_samples': [{'labels': 'account:availability_burn_rate:5m{}', 'value': 0}]}]})
    # Existing normal fixture: exact 2x boundary must not fire (use 1 in 50).
    boundary = copy.deepcopy(tests[0]); boundary['name'] = 'exact 2x boundary does not fire'
    for item in boundary['input_series']:
        series = item['series']
        if 'pe_http_server_requests_total' in series and 'status_class="2xx"' in series:
            item['values'] = '0+49x20'
        elif 'pe_http_server_requests_total' in series and 'status_class="5xx"' in series:
            item['values'] = '0+1x20'
        elif ('pe_http_server_request_duration_seconds_count' in series or
              'pe_http_server_request_duration_seconds_bucket' in series):
            item['values'] = '0+50x20'
    tests.append(boundary)
    # A single 30s evaluation above threshold must remain pending and then clear.
    short = copy.deepcopy(tests[-2])
    short['name'] = 'short breach suppressed'
    short['input_series'][2]['values'] = '0 27 1027+1000x38'
    short['input_series'][3]['values'] = '0 3 3+0x38'
    short['input_series'][4]['values'] = short['input_series'][5]['values'] = '0 30 1030+1000x38'
    short['alert_rule_test'] = [{'eval_time': t, 'alertname': alert['alert'], 'exp_alerts': []} for t in ['30s', '1m', '3m', '10m']]
    short['promql_expr_test'] = []
    tests.append(short)
    (output / 'alert-tests.yaml').write_text(json.dumps({'rule_files': ['account-alerts.yaml'], 'evaluation_interval': '30s', 'fuzzy_compare': True, 'tests': tests}, indent=2))
    print(f'PASS alerting render: 20 resources; no new RBAC/Secret; {len(tests)} alert cases')


if __name__ == '__main__':
    prepare(Path(sys.argv[1]))
