{{/*
業務 Application の共通設定。同期順序と参照する版は呼び出し元から渡す。
削除時の確認や共有リソースの保護を、Account と Order の両方に同じ形で適用する。
revision は YAML 用の表記で受け取り、既存 Account の main と Order の引用符付き SHA を保つ。
*/}}
{{- define "workloads.application" -}}
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: lab-local-{{ .app.name }}
  namespace: argocd
  annotations: {argocd.argoproj.io/sync-wave: {{ .app.wave | quote }}, argocd.argoproj.io/sync-options: Prune=confirm}
spec:
  project: lab-account
  source:
    repoURL: {{ .repository }}
    targetRevision: {{ .revision }}
    path: charts/{{ .app.name }}
    helm:
      releaseName: {{ .app.name }}
      valueFiles: [../../environments/local/{{ .app.name }}.yaml]
  destination: {server: https://kubernetes.default.svc, namespace: account}
  syncPolicy:
    automated: {prune: true, selfHeal: true, allowEmpty: false}
    syncOptions: [PruneLast=true, FailOnSharedResource=true]
{{- end }}
