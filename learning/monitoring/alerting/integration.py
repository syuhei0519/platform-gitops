#!/usr/bin/env python3
"""Actual Prometheus -> Alertmanager -> local webhook, internal Docker network only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
import yaml

ROOT = Path(__file__).resolve().parents[1]
VALUES = yaml.safe_load((ROOT / 'chart/values.yaml').read_text())


def docker(*args):
    return subprocess.check_output(['docker', *map(str, args)], text=True, stderr=subprocess.PIPE).strip()


def run(config):
    suffix = uuid.uuid4().hex[:8]
    network = f'slo-fixture-{suffix}'
    names = []
    # Use caller UID for repository mounts; every container is disposable.
    user = f'{os.getuid()}:{os.getgid()}'
    with tempfile.TemporaryDirectory(prefix='slo-fixture-') as directory:
        work = Path(directory)
        prom = yaml.safe_load((config / 'prometheus.yml').read_text())
        prom['global'].update(scrape_interval='1s', scrape_timeout='1s', evaluation_interval='1s')
        prom['rule_files'] = ['/config/account-alerts.yaml']
        prom['scrape_configs'] = [prom['scrape_configs'][0]]
        prom['scrape_configs'][0]['static_configs'][0]['targets'] = ['fixture:8889']
        prom['alerting']['alertmanagers'][0]['static_configs'][0]['targets'] = ['alertmanager:9093']
        # Faster scrape/evaluation only; production 5m query and 2m for are unchanged.
        (work / 'prometheus.yml').write_text(yaml.safe_dump(prom))
        (work / 'account-alerts.yaml').write_text((config / 'account-alerts.yaml').read_text())
        (work / 'alertmanager.yml').write_text((config / 'alertmanager.yml').read_text().replace('learning-alert-receiver.learning-slo-alerts.svc.cluster.local', 'receiver'))
        docker('network', 'create', '--internal', network)
        try:
            def start(alias, image, *args, extra=()):
                name = f'{network}-{alias}'
                names.append(name)
                docker('run', '-d', '--name', name, '--network', network, '--network-alias', alias,
                       '--env', 'HTTP_PROXY=', '--env', 'http_proxy=', '--env', 'HTTPS_PROXY=', '--env', 'https_proxy=', '--env', 'NO_PROXY=*', '--env', 'no_proxy=*',
                       '--user', user, '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                       '--mount', f'type=bind,src={work},dst=/config,readonly',
                       '--mount', f'type=bind,src={ROOT},dst=/source,readonly', *extra, image, *args)
                return name
            fixture = start('fixture', VALUES['alerting']['receiverImage'], 'python3', '-B', '/source/alerting/fixture_server.py')
            start('receiver', VALUES['alerting']['receiverImage'], 'python3', '-B', '/source/chart/files/receiver.py')
            start('alertmanager', VALUES['alerting']['alertmanagerImage'], '--config.file=/config/alertmanager.yml', '--storage.path=/data', '--cluster.listen-address=', extra=('--tmpfs', '/data:rw,mode=1777'))
            start('prometheus', VALUES['images']['prometheus'], '--config.file=/config/prometheus.yml', '--storage.tsdb.path=/data', extra=('--tmpfs', '/data:rw,mode=1777'))

            def request(url, method='GET'):
                code = 'import urllib.request; print(urllib.request.urlopen(urllib.request.Request(' + repr(url) + ', method=' + repr(method) + '), timeout=3).read().decode())'
                return docker('exec', fixture, 'python3', '-c', code)

            def wait_for(predicate, seconds, label):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    try:
                        result = predicate()
                        if result:
                            print('PASS ' + label, flush=True)
                            return result
                    except (subprocess.CalledProcessError, ValueError, KeyError):
                        pass
                    time.sleep(2)
                raise AssertionError('timeout: ' + label)

            def events():
                return json.loads(request('http://receiver:8080/events'))

            def query(expression):
                from urllib.parse import quote
                return json.loads(request('http://prometheus:9090/api/v1/query?query=' + quote(expression)))['data']['result']

            wait_for(lambda: query('account:sli_state:5m') and float(query('account:sli_state:5m')[0]['value'][1]) == 4, 30, 'synthetic state=4')
            assert not events(), 'must not notify during pending'
            print('PASS no notification during pending; waiting for unchanged 2m hold', flush=True)
            fired = wait_for(lambda: next((e for e in events() if e['status'] == 'firing'), None), 160, 'firing notification received')
            alert = fired['alerts'][0]
            assert alert['labels']['service'] == 'backend' and alert['labels']['account_namespace'] == 'learning-fixture'
            assert alert['annotations']['service_url'] == 'http://backend.learning-fixture.svc.cluster.local:8080'
            assert alert['annotations']['query'] == 'account:availability_burn_rate:5m'
            assert alert['annotations']['runbook_url'].endswith('/learning/monitoring/alerting/README.md')
            assert '/graph?' in alert['generatorURL']
            request('http://fixture:8889/recover', 'POST')
            wait_for(lambda: any(e['status'] == 'resolved' for e in events()), 60, 'resolved notification received')
            state, burn = query('account:sli_state:5m'), query('account:availability_burn_rate:5m')
            assert float(state[0]['value'][1]) == 4 and float(burn[0]['value'][1]) <= 2
            count = len(events())
            time.sleep(12)
            assert len(events()) == count and not query('ALERTS{alertstate="firing"}')
            print('PASS healthy recovery state=4/burn<=2, no continuing notification; Service/query/runbook links present', flush=True)
        finally:
            for name in reversed(names):
                subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            docker('network', 'rm', network)


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve())
