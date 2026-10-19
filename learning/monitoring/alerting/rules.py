#!/usr/bin/env python3
"""Generate alert rules from the unchanged single-instance SLI contract."""
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = 'https://github.com/syuhei0519/platform-gitops/blob/96053d7e3d6f31fb66eccf216ba8a37ed46799c2/learning/monitoring/alerting/README.md'


def rules():
    sli = json.loads((ROOT / 'sli-queries.json').read_text())
    return {'groups': [{'name': 'account-availability', 'rules': [
        {'record': 'account:sli_state:5m', 'expr': sli['state']},
        {'record': 'account:availability_burn_rate:5m', 'expr': f"round(((1 - ({sli['availability']})) / 0.01) * 1e9) / 1e9"},
        {'alert': 'AccountAvailabilityBudgetBurn',
         'expr': 'account:availability_burn_rate:5m > 2', 'for': '2m',
         'labels': {'severity': 'warning', 'service': 'backend', 'account_namespace': '__ACCOUNT_NAMESPACE__'},
         'annotations': {
             'summary': 'Account availability budget burn exceeds 2x for 2m',
             'service_url': 'http://backend.__ACCOUNT_NAMESPACE__.svc.cluster.local:8080',
             'query': 'account:availability_burn_rate:5m',
             'state_query': 'account:sli_state:5m',
             'runbook_url': RUNBOOK,
             'recovery': 'Resolved is predicate cleared. Healthy recovery requires state=4 and burn<=2; state=0/1/2/3 is unevaluable.'}}]}]}


if __name__ == '__main__':
    (ROOT / 'chart/files/account-alerts.yaml').write_text(yaml.safe_dump(rules(), sort_keys=False, width=100000))
