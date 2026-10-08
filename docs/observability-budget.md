# PE-009 観測境界と暫定予算

Namespaceはbootstrap/namespaces.yamlだけが生成する。lab-observabilityはrootのAppProject、ServiceAccount/RBACはobservability-foundationだけが生成する。PrometheusとKSMのchartはSA/RBAC生成を無効にする。ClusterRole/Bindingはkindのnodes/proxy例外をPrometheus SAだけへ付与する。これはnodeへの強い権限であり、アプリ・KSM・Collectorへ渡さない。KSMはaccountのPod/Deployment/ReplicaSet/StatefulSetのget/list/watchだけ。Secret・他namespace・nodesへアクセスさせない。

platform chartのobservabilityEnabled/prometheusEnabledは既定false。localはtrueで、foundation wave -20→Prometheus wave 0になる。親/root/Namespace bootstrapは別の所有者のため、main統合前にbootstrap Namespaceを適用し、AppProject/末端状態を明示確認する。AppProjectのallowlistにはwildcard、Secret、Namespace、PV、CRDを含めない。ClusterRoleを生成できるprojectは信頼済みplatform mainレビューで管理する。現在の9 child topologyは既存7にfoundation/Prometheusを追加したもの。

固定版・Linux amd64 digest・chart package checksumはbootstrap/versions.lock.yamlと実valuesに記録する。Prometheus chart 29.35.0、本体3.15.0、KSM chart8.6.0/app2.20.0、reload0.94.1を使う。Argo/Redis/Runner管理imageも同じ版のdigestへ固定する。RunnerはRecreateで更新時の2manager/2つのconcurrent=1を避ける。実行中Jobの完了を待つため、manager更新中のCI待ちを許容する。helperは既存固定digestのまま。

Collector/Grafanaは後続用版台帳と予算だけ。Deployment/ConfigMap/exporter/OTel SDK/New Relicキー/Grafana管理者Secretは今回追加しない。New Relic地域/認証とAPI/registry/Package分離資格情報は未設定として契約表に残す。Grafanaチャートの公式移行先はgrafana-community。実装時は固定版で互換性を受け入れる。

| 対象 | CPU request / limit | memory request / limit | 一時/永続容量 |
|---|---|---|---|
| Prometheus | 200m / 1 | 512Mi / 1Gi | ephemeral64Mi/512Mi、PVC5Gi、retention3d/2GB |
| reload sidecar | 10m / 100m | 16Mi / 64Mi | ephemeral16Mi/64Mi |
| KSM | 50m / 200m | 64Mi / 128Mi | ephemeral32Mi/128Mi |
| Argo controller | 200m / 1 | 256Mi / 1Gi | ephemeral64Mi/512Mi |
| Argo repo-server（copyutil initも同profile） | 100m / 500m | 256Mi / 512Mi | ephemeral64Mi/512Mi |
| Argo server | 100m / 500m | 128Mi / 256Mi | ephemeral64Mi/512Mi |
| Redis | 50m / 250m | 64Mi / 128Mi | ephemeral16Mi/128Mi |
| Argo Redis init Job（起動時だけ）/ApplicationSet（replicas0） | 50m / 200m | 64Mi / 128Mi | ephemeral16Mi/128Mi |
| Runner manager | 100m / 500m | 128Mi / 256Mi | ephemeral16Mi/128Mi |
| CI build / helper（concurrent1） | 500m/2 + 100m/500m | 512Mi/2Gi + 128Mi/256Mi | build request2Gi/limit14Gi、helper64Mi/512Mi、repo emptyDir2Gi、BuildKit10Gi |
| 既存frontend / backend / PostgreSQL | 実manifest固定版の50m/250m、100m/500m、100m/500m | 64Mi/128Mi、64Mi/256Mi、256Mi/512Mi | DB PVC2Gi |
| Collector（未配備） | 100m / 500m | 128Mi / 256Mi | bounded memory queue、ephemeral予算は実装時固定 |
| Grafana（未配備） | 100m / 500m | 128Mi / 256Mi | Git dashboard、sidecar/追加initの予算は実装時固定 |

全platform render container/initの実resource行はCI artifact observability-budget.json。replicas0やinitを常駐Podへ二重計上しない。Pod schedulingはinitの最大値とappコンテナの合計の大きい方を使う。Go policyはimage digest、CPU/memory/ephemeralのrequests/limits、CA・認証・explicit3 scrape、account-only KSM、PVC削除確認を検証する。CI build/helperはRunnerが動的生成するため、manager ConfigMapの固定profileも予算へ含める。

2026-10-02の現状: ホスト約31.8GiB/20CPU、Docker/kind割当15.53GiB/20CPU。既存kindは約2.5GiB使用、隔離試験kindは約1.4GiB。Metrics APIが未導入なのでPod実使用は未測定で、Dockerのnode全体の値として記録する。既存Argoのruntime requests/limitsは新bootstrap values適用前であり、render予算と混同しない。kind基礎Pod/OS/etcd/logに2GiBを暫定予約すると、既存app・Argo・Runner・CI・今回監視のmemory limit合計の参考上限は約8.44GiB、将来Collector/Grafana込み約8.94GiB。これは実同時最大使用を保証する値ではない。15.53GiB割当内で評価し、元設計8GiBは現状の割当値として扱わない。concurrentは1のまま。

ホストC:空き約543GB、kind/containerd filesystem空き約932GiB/1007GiBで、空き20%以上。CIのOCI展開・検査DB・Package/SBOMをまだ導入しないが、Phase2の一時disk増加は現在のbuild14Gi上限内に収まることをPE-014/019で実測する。TSDB retention.sizeはblockだけの厳密PVC全体上限とは扱わず、WAL/headを含む使用と3dの実保持をPE-010で記録する。

PE-009は暫定budgetで受け入れ、最終CIとの同時負荷はPE-014→PE-019 AT-10へ引き継ぐ。今回AT-10全体をPASSにはしない。PE-010はup、account CPU/memory/Ready/Pending、同UIDのrestart増分と別UIDのreplacement、TLS/認証negative、再起動後TSDB/WAL/PVC保護を受け入れる。強いnode権限はGKEへ持ち込まず再設計する。

参照: [公式Prometheus chart](https://github.com/prometheus-community/helm-charts/tree/main/charts/prometheus)、[Prometheus TLS/config](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)、[公式Grafana chart](https://grafana-community.github.io/helm-charts/)、[PE-009](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/9)、[PE-010](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/10)。
