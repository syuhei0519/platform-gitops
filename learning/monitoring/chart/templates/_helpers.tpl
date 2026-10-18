{{- define "monitoring.labels" -}}
app.kubernetes.io/part-of: learning-monitoring
learning.local/owner: {{ .Values.owner | quote }}
{{- end -}}
{{- define "monitoring.collectorConfig" -}}
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
        max_request_body_size: 1048576
processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 192
    spike_limit_mib: 48
  # Legacy SDK identity checks, not sender authentication. Only Account metrics.
  filter/account:
    error_mode: propagate
    metrics:
      metric:
        - 'resource.attributes["service.name"] != "account-backend"'
        - 'resource.attributes["deployment.environment.name"] != "local"'
        - 'resource.attributes["k8s.namespace.name"] != "account"'
  resource/real_namespace:
    attributes:
      - key: k8s.namespace.name
        value: {{ .Values.accountNamespace | quote }}
        action: upsert
  batch:
    timeout: 5s
    send_batch_size: 512
    send_batch_max_size: 1024
exporters:
  prometheus:
    endpoint: 0.0.0.0:8889
    send_timestamps: true
    metric_expiration: 90s
    resource_to_telemetry_conversion: {enabled: false}
    enable_open_metrics: true
  nop: {}
extensions:
  health_check: {endpoint: 0.0.0.0:13133}
service:
  extensions: [health_check]
  telemetry:
    logs: {level: warn, encoding: json}
  pipelines:
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, filter/account, resource/real_namespace, batch]
      exporters: [prometheus]
    # The existing SDK sends traces too. Discard them without storage/export.
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [nop]
{{- end -}}
{{- define "monitoring.prometheusConfig" -}}
global:
  scrape_interval: 30s
  scrape_timeout: 10s
  evaluation_interval: 30s
scrape_configs:
  - job_name: otel-application
    honor_labels: true
    honor_timestamps: true
    static_configs:
      - targets: [otel-collector.observability.svc.cluster.local:8889]
        labels: {account_namespace: {{ .Values.accountNamespace | quote }}}
  - job_name: prometheus
    static_configs:
      - targets: [localhost:9090]
{{- end -}}
{{- define "monitoring.grafanaConfig" -}}
[paths]
data = /var/lib/grafana
logs = /var/lib/grafana/log
plugins = /var/lib/grafana/plugins
provisioning = /etc/grafana/provisioning
[server]
http_port = 3000
[security]
disable_initial_admin_creation = true
[auth]
disable_login = true
disable_login_form = true
[auth.basic]
enabled = false
[auth.anonymous]
enabled = true
org_role = Viewer
[users]
allow_sign_up = false
allow_org_create = false
viewers_can_edit = false
[analytics]
enabled = false
reporting_enabled = false
check_for_updates = false
check_for_plugin_updates = false
[snapshots]
external_enabled = false
[plugins]
plugin_admin_enabled = false
preinstall_disabled = true
preinstall_auto_update = false
{{- end -}}
