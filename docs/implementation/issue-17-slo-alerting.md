# Handoff

以下はPR95の初回実装履歴。マージ後のService参照不具合と修正は末尾の追記を参照。

## 目的

- 対象: `syuhei0519/platform-gitops` Issue #17（SLO Alerting）、担当B。
- Branch: `feat/17-account-slo-alerting`。
- 開始固定SHA: `efd3a1f5a31eaf904bc787456034e4cebe932f7f`。
- 実装固定SHA: `7e44e6dd9fc0cc8819401eac3d2e0385fb4ce834`（このHandoffのみ後続commit）。
- System正本: `syuhei0519/ai-development-system@42521c0bd2a1220ef01ed009455a9372708f8314` の `agents/implementation.md`、`workflows/single-issue.md`、`templates/handoff.md` を読んだ。
- 既存Account SLI契約を保持し、可用性burn通知1種類、試験通知受信、正常回復、通知からService/query/runbook参照を実装する。
- Cloudは独立fixtureだけ。実kind適用、Account同時計測、main直接Push、マージ、権限/Secret変更、外部通知は行わない。

## やったこと

公開先は [日本語Draft PR #95](https://github.com/syuhei0519/platform-gitops/pull/95)。

変更ファイルと理由:

- `learning/monitoring/chart/values.yaml` / `values.schema.json`: opt-in `alerting`、固定namespace、Alertmanager/Python imageをtag+digestで固定。
- `chart/templates/_helpers.tpl` / `prometheus-config.yaml`: 有効時のみ既存Prometheusへrule/Alertmanager参照を追加。
- `chart/templates/alerting.yaml`: 専用namespace向けConfigMap/Deployment/ClusterIP Serviceを各2個追加。新規RBAC/Secret/Namespace資源なし、SA tokenなし。
- `chart/files/account-alerts.yaml` / `alertmanager.yml` / `receiver.py`: 既存availability/state式の再利用、burn>2を2分保持、warning、ローカル完全一致route、firing/resolved受信。
- `alerting/rules.py` / `validate.py` / `check.sh`: 正本queryとの一致、既存資源不変、22件の差分fixtureと固定ツール実行。
- `alerting/fixture_server.py` / `integration.py`: 合成exporter→実Prometheus→実Alertmanager→実ローカルWebhook。専用internal Docker network、ホストportなし、終了時削除。
- `alerting/README.md` / 親 `README.md`: 日本語runbook、少量実測・通知・回復・差分適用/削除・必要権限。通知のrunbook URLは文書commit `96053d7e3d6f31fb66eccf216ba8a37ed46799c2` へのpermalink。
- `.github/workflows/ci.yml`: 既存monitoring検証stepへ差分検証とschema対象を追加。既存required/security checksを保持し、生成fixture/renderはCI artifactへ保存。

Collector config、SLI/dashboard/revision queries、共通監視chart、A担当Istio/C担当releaseファイルは変更していない。
`checkout/.agents/skills` に関連SKILL.mdはなかった。

## 観察結果

2026-10-19 Cloud自己検証。コマンドはrepo rootから、HelmをPATHへ置いて実行する。

| コマンド | 結果 |
| --- | --- |
| `python3 learning/monitoring/validate.py --output .rendered/learning-monitoring` | PASS: 既定14資源、schema/strict lint、従来境界とquery一致。SLO全受入/実測は繰り返さず |
| `python3 learning/monitoring/alerting/validate.py .rendered/learning-alerting` | PASS: opt-in20資源、新規RBAC/Secretなし、既存Collector/Grafana/RBAC不変、22ケース生成 |
| `sh learning/monitoring/alerting/check.sh .rendered/learning-alerting` | PASS: 固定promtoolでconfig/3rules/22ケース、amtool config、実通知integration |
| `python3 -m py_compile learning/monitoring/alerting/*.py learning/monitoring/chart/files/receiver.py` | PASS |
| 固定kubeconform v0.7.0 `-strict -summary -kubernetes-version 1.36.0 .rendered/learning-alerting/rendered.yaml` | PASS: Valid20 / Invalid0 / Errors0 / Skipped0 |
| 固定shared scan-gate `-check-render-only .rendered/learning-alerting -out .rendered/learning-alerting/structural-check.json` | PASS。CI指定backend SHA `96e5509757fbfb4bdc7e8b47876cdcc8c288142c` からbuild |
| `git diff --check` / CI YAML読み取り | PASS、ci-result依存と既存security gate維持 |

integrationは元の5分query/2分holdを維持し、scrape/evaluationのみ1秒。
pending中通知なし→firing受信→合成正常通信へ戻す→resolved受信→state=4/burn<=2→継続通知なしを確認した。
Service DNS/query/runbook参照もpayload内で確認。実アプリ/DB/Collectorへ接触していない。
integration後にrunbook URLだけ固定文書commitへ変更し、render/22 fixture/structural guardを再確認した。
Cloudの一時コンテナとnetworkは削除済み。実機の通知AC受入は未完了。

失敗・補正履歴（上書きしない）:

1. 初回promtoolの既定UIDではrepo bind mountがpermission denied。実行者UID/GID指定で成功。
2. 閾値ちょうど2のケースが小数誤差で発火。`round(burn*1e9)/1e9` に修正し22ケースPASS。既存SLI式は変更していない。
3. 初回integrationは環境注入HTTP proxyにより内部宛接続が到達不能、state待機timeout。試験コンテナ内だけproxyを空にして全経路PASS。
4. kubeconform初回はCloud CA不足でschema取得TLS失敗。ホストCA bundleをread-only mountし `SSL_CERT_FILE` を指定して20/20 PASS。検証を無効化していない。
5. 一時archiveのGo buildはVCS stampingエラー。`-buildvcs=false` で固定sharedコードをbuildしguard PASS。

実装の不具合補正は閾値丸めとintegration proxyの2回。追加ACや新規権限へscope拡大なし。
CI全体の最終判定・source/render vulnerability/secret scanはPRの現在のchecksをManagerが確認する。
CI artifact期限後も上記コマンドで再生成できる。内部ログ・非公開実行ID・通知payload全文・秘密値はここに含めない。

## 学び

- burn>2は可用性99%に対する失敗率2%超。5分窓・最低20要求と既存鮮度/整合条件を保持する。
- 欠測/無通信/少量を正常に埋めない。resolvedはpredicate解除を意味するため、正常復旧はstate=4かつburn<=2を別に確認する。
- Alertmanager未導入というruntime側の読み取りpreflightと整合する最小構成。専用2PodでRBAC/Secret追加は不要。
- 実kindの適用・少量実測・通知/回復・必要権限は `learning/monitoring/alerting/README.md` に従ってruntime担当が検証する。namespace作成、専用6資源管理、既存Prometheus2資源更新、read/port-forward権限が必要。権限追加が必要なら実行せず報告。
- 独立Reviewは未実施。Managerが独立Reviewと現在CIを確認してマージを判断する。Draft PR公開は成功済みで、公開承認による停止はない。


### 2026-10-19 追記: 実Account Service参照の修正（Refs #17）

目的・固定入力:

- PR95は独立Review/CI成功後にmain `cfc7c80499cfca832c003f223b85ea7d14aec41a` へマージ済みというManager報告を受領。
- local Testは適用前に、通知URLの `backend.learning-services-20261018` と実Service `learning-account` の不一致を発見。適用/負荷/forwardは0、全Pod正常のまま停止。runtime受入は未完了。
- この固定mainから `fix/17-account-service-reference` を作成。実装固定SHA `cd3b4812d7270d1fe964bdf1768b8164399b4cab`（後続は本Handoff追記のみ）。
- 公開先は新しい [日本語Draft PR #96](https://github.com/syuhei0519/platform-gitops/pull/96)。PR本文はRefs #17を使用し、自動closeを指定しない。

やったこと:

- `chart/values.yaml` / `values.schema.json`: `alerting.accountServiceName`（既定 `learning-account`）と `accountServicePort`（既定8080）を追加。namespaceは既存 `accountNamespace` を使う。
- `alerting/rules.py` / `chart/files/account-alerts.yaml` / `chart/templates/prometheus-config.yaml`: 通知 `service_url` を実Service名/namespace/portから生成する。metric/routing label `service=backend` は保持。
- `alerting/validate.py`: 既定の正しいFQDN、別名/別namespace/別port、label独立、式/閾値不変、無効入力5件の拒否を回帰検査。元の誤参照ではこの検査が失敗する。
- `alerting/integration.py`: payloadのURL期待値のみ新valuesへ追従。長時間integration全体は今回ローカルで再実行しない。
- `alerting/README.md`: metric identityとService identity、実Service入力/事前照合を説明。通知runbook URLを更新文書の固定commit `d66d216f6bce200ec0981673a216cb3ce0a17d90` へ更新。
- 本Handoffは過去履歴を消さず追記。Collector/query/threshold/RBAC/Alertmanager routing/CIは変更なし。

観察結果:

| コマンド・確認 | 結果 |
| --- | --- |
| 固定mainのHelm renderからservice_urlを読み、実Service `learning-account` のFQDNを期待 | 修正前FAIL: 実値は `http://backend.learning-fixture.svc.cluster.local:8080`。不具合を再現 |
| `python3 learning/monitoring/alerting/validate.py .rendered/service-reference-fix` | 修正後PASS: Service名/namespace/portの独立入力、metric/routing label保持、無効入力拒否、20資源/既存境界保持 |
| `python3 learning/monitoring/validate.py --output .rendered/monitoring-service-reference-fix` | PASS: 既定14資源、values schema/strict Helm lint/render、従来query一致 |
| 固定promtool `test rules alert-tests.yaml`（上記生成dirをmount） | PASS: 既存22ケースを新annotationで検査 |
| 固定shared scan-gate `-check-render-only .rendered/service-reference-fix -out .rendered/service-reference-fix/structural-check.json` | PASS |
| `python3 -m py_compile learning/monitoring/alerting/rules.py learning/monitoring/alerting/validate.py learning/monitoring/alerting/integration.py` / `git diff --check` | PASS |
| 固定mainの `chart/files/account-alerts.yaml` と新ruleをYAMLとして比較 | `service_url` / `runbook_url` annotationを除いて完全一致。Collector/query/threshold/RBAC/routingのGit差分なし |

以前の「Service/query/runbook参照PASS」は合成payload内の旧期待値を確認した結果であり、実Kubernetes Serviceの存在を照合した結果ではなかった。成功履歴は残すが、実Service参照のruntime受入が未完了であることを明示する。
PR95の2回補正後の追加不具合1件として報告し、今回明示依頼された参照修正だけを行った。

学び・次の担当:

- 実参照は `http://<alerting.accountServiceName>.<accountNamespace>.svc.cluster.local:<alerting.accountServicePort>`。metric `service=backend` とKubernetes名を同一視しない。
- runtime担当は既存Account namespaceと読み取り確認済み `learning-account:8080` をvaluesへ明記してから差分適用を再開する。必要権限は従来runbookの範囲のまま、新規RBAC/Secretなし。
- 今回は参照のみであるため、承認された方針どおり長時間integrationの重複実行、実kind操作、負荷、port-forwardは行っていない。実到達・通知/回復のruntime検証は別担当。
- 今回の独立Reviewは未実施。Managerが現在CIと独立Reviewを確認してマージする。公開は成功し、承認待ち/拒否によるblockerはない。
