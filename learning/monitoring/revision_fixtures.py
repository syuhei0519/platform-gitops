#!/usr/bin/env python3
# 版ごとの計測クエリを試すため、成功・失敗・データ不足の入力を生成します。
"""Materialize deterministic promtool fixtures for the exact retained queries."""
import json
from pathlib import Path

from revision_queries import queries


def fixtures():
    tests = []
    cases = ['good', 'error', 'latency', 'low-sample', 'no-traffic', 'missing-heartbeat',
             'stale-heartbeat', 'missing-instance', 'histogram-mismatch', 'missing-bucket',
             'one-point', 'collector-down', 'wrong-namespace', 'wrong-service', 'wrong-revision',
             'nan', 'negative-bucket', 'bucket-over-count', 'counter-reset', 'extra-instance',
             'all-error', 'missing-up', 'absent-http', 'missing-histogram', 'stale-http', 'stable-counter-reset']
    for case in cases:
        inputs = [{'series': 'up{job="otel-application",account_namespace="learning-fixture"}',
                   'values': ('0' if case == 'collector-down' else '1') + '+0x10'}]
        if case == 'missing-up':
            inputs = []
        for role, instances in [('latest', 1), ('stable', 9)]:
            for i in range(instances + (1 if case == 'extra-instance' and role == 'latest' else 0)):
                labels = (f'job="account-backend",account_namespace="learning-fixture",learning_service="account",'
                          f'rollout_revision="{role}-fixture",instance="{role}-{i}"')
                if case == 'wrong-namespace' and role == 'latest':
                    labels = labels.replace('learning-fixture', 'learning-other')
                if case == 'wrong-service' and role == 'latest':
                    labels = labels.replace('learning_service="account"', 'learning_service="order"')
                if case == 'wrong-revision' and role == 'latest':
                    labels = labels.replace('latest-fixture', 'other-fixture')

                def add(name, values, extra=''):
                    inputs.append(dict(series=name + '{' + labels + extra + '}', values=values))

                if not (case == 'missing-heartbeat' and role == 'latest'):
                    add('pe_go_goroutines', '10+0x6 _ _ _ _' if case == 'stale-heartbeat' and role == 'latest' else '10+0x10')
                if case == 'missing-instance' and role == 'stable' and i == 8:
                    continue
                if case == 'absent-http' and role == 'latest':
                    continue
                values = '0+3x10'
                if role == 'latest':
                    if case == 'low-sample': values = '0+1x10'
                    if case == 'no-traffic': values = '0+0x10'
                    if case == 'one-point': values = '_ _ _ _ _ _ _ _ _ _ 30'
                    if case == 'nan': values = 'NaN+0x10'
                    if case == 'counter-reset': values = '0 3 6 9 0 3 6 9 12 15 18'
                if case == 'stable-counter-reset' and role == 'stable' and i == 0:
                    values = '0 3 6 9 0 3 6 9 12 15 18'
                if case == 'stale-http' and role == 'latest':
                    values = '0+3x6 _ _ _ _'
                add('pe_http_server_requests_total', '0+2x10' if case == 'error' and role == 'latest' else values, ',status_class="4xx"' if case == 'all-error' and role == 'latest' else ',status_class="2xx"')
                if case == 'error' and role == 'latest':
                    add('pe_http_server_requests_total', '0+1x10', ',status_class="4xx"')
                if case == 'missing-histogram' and role == 'latest':
                    continue
                add('pe_http_server_request_duration_seconds_count', '0+4x10' if case == 'histogram-mismatch' and role == 'latest' else values)
                add('pe_http_server_request_duration_seconds_bucket', values, ',le="+Inf"')
                if not (case == 'missing-bucket' and role == 'latest'):
                    bucket = values
                    if role == 'latest':
                        if case == 'latency': bucket = '0+2x10'
                        if case == 'negative-bucket': bucket = '0 -3 -6 -9 -12 -15 -18 -21 -24 -27 -30'
                        if case == 'bucket-over-count': bucket = '0+4x10'
                    add('pe_http_server_request_duration_seconds_bucket', bucket, ',le="0.25"')
        expected = {}
        if case not in ('collector-down', 'missing-up'):
            # Stable baseline remains independently observable during a bad canary.
            if case != 'missing-instance':
                expected.update({'stable-requests':800/3 if case == 'stable-counter-reset' else 270, 'stable-availability':1, 'stable-latency':1})
            if case in ('good', 'missing-instance', 'stable-counter-reset'):
                expected.update({'latest-requests':30, 'latest-availability':1, 'latest-latency':1})
            if case == 'error':
                expected.update({'latest-requests':30, 'latest-availability':2/3, 'latest-latency':1})
            if case == 'latency':
                expected.update({'latest-requests':30, 'latest-availability':1, 'latest-latency':2/3})
            if case == 'all-error':
                expected.update({'latest-requests':30, 'latest-availability':0, 'latest-latency':1})
            if case == 'counter-reset':
                expected.update({'latest-requests':80/3, 'latest-availability':1, 'latest-latency':1})
        tests.append(dict(name=case, interval='30s', input_series=inputs,
                          promql_expr_test=[dict(expr=q.replace('{{args.namespace}}','learning-fixture').replace('{{args.service}}','account')
                                                .replace('{{args.latest-hash}}','latest-fixture').replace('{{args.stable-hash}}','stable-fixture'),
                                                eval_time='5m', exp_samples=[dict(labels='{}',value=expected[name])] if name in expected else [])
                                            for name,q in queries().items()]))
    return dict(rule_files=[], evaluation_interval='30s', tests=tests)


if __name__ == '__main__':
    import sys
    Path(sys.argv[1]).write_text(json.dumps(fixtures(), indent=2) + '\n')
