#!/usr/bin/env python3
"""Generate the Account 10% inline gate queries. No network or cluster access."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def queries():
    result = {}
    for role, count in [('latest', 1), ('stable', 9)]:
        selector = ('job="account-backend",account_namespace="{{args.namespace}}",'
                    'learning_service="{{args.service}}",rollout_revision="{{args.' + role + '-hash}}"')

        def series(name, extra=''):
            return name + '{' + selector + extra + '}'

        def inc(name, extra=''):
            return 'sum by(instance)(increase(' + series(name, extra) + '[5m]))'

        heartbeat = series('pe_go_goroutines')
        active = f'({heartbeat} >= 0) and (time() - timestamp({heartbeat}) >= 0) and (time() - timestamp({heartbeat}) < 90)'
        r = inc('pe_http_server_requests_total')
        c = inc('pe_http_server_request_duration_seconds_count')
        b = inc('pe_http_server_request_duration_seconds_bucket', ',le="0.25"')
        inf = inc('pe_http_server_request_duration_seconds_bucket', ',le="+Inf"')
        per_instance = f'({r} >= 0) and on(instance) ({c} >= 0) and on(instance) ({b} >= 0) and on(instance) ({inf} >= 0)'
        for a, z in [(c, r), (inf, c)]:
            per_instance += f' and on(instance) (abs(({a}) - ({z})) < 0.001)'
        per_instance += f' and on(instance) (({b}) <= ({c})) and on(instance) ({active})'
        # Each raw status/route series needs a baseline and current, fresh point.
        # Sum(increase(...)) handles an individual process counter reset before summing.
        for metric, extra in [('pe_http_server_requests_total', ''),
                              ('pe_http_server_request_duration_seconds_count', ''),
                              ('pe_http_server_request_duration_seconds_bucket', ',le="0.25"'),
                              ('pe_http_server_request_duration_seconds_bucket', ',le="+Inf"')]:
            raw = series(metric, extra)
            per_instance += (f' and on(instance) (min by(instance)(count_over_time({raw}[5m])) >= 2)'
                             f' and on(instance) (min by(instance)(time() - timestamp({raw})) >= 0)'
                             f' and on(instance) (max by(instance)(time() - timestamp({raw})) < 90)')
        up = 'up{job="otel-application",account_namespace="{{args.namespace}}"}'
        gate = (f'(count({up}) == 1) and on() (min({up}) == 1)'
                f' and on() (max(time() - timestamp({up})) < 60)'
                f' and on() (min(time() - timestamp({up})) >= 0)'
                f' and on() (count({heartbeat}) == {count})'
                f' and on() (count(count by(instance)({active})) == {count})'
                f' and on() (count({per_instance}) == {count})'
                f' and on() (count({r}) == {count})'
                f' and on() (sum({r}) >= 20)')
        total = f'sum({r})'
        success = f'sum({inc("pe_http_server_requests_total", ",status_class=~\"2xx|3xx\"")})'
        # Only a missing success category is zero-filled, with the observed denominator.
        availability = f'(({success}) or on() (({total}) * 0)) / ({total})'
        for name, expr in [('requests', total), ('availability', availability),
                           ('latency', f'sum({b}) / sum({c})')]:
            result[role + '-' + name] = f'({expr}) and on() ({gate})'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='refresh the retained query source')
    args = parser.parse_args()
    text = json.dumps(queries(), indent=2) + '\n'
    path = ROOT / 'revision-queries.json'
    if args.write:
        path.write_text(text)
    else:
        assert path.read_text() == text, 'retained queries differ; run revision_queries.py --write'
        print('PASS revision query source matches generator')


if __name__ == '__main__':
    main()
