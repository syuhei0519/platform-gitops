# PE-006A Secretと再注入の契約

Secret値の所有者/保管元はLab運用者のパスワード管理ツール。Gitは参照名/キー/最小権限/手順を所有し、平文・base64のSecret YAML、Docker config、認証headerは保存しない。kindの保存が暗号化されているとは想定しない。

| namespace / Secret | type / keys | 消費者 | 発行主体 / 最小権限 |
|---|---|---|---|
| argocd / repo-platform-gitops | Opaque: type,url,username,password | Argo repo-server | 当該projectのDeploy Token、read_repositoryのみ |
| argocd / repo-application-manifest | Opaque: type,url,username,password | Argo repo-server | 当該projectのDeploy Token、read_repositoryのみ |
| runner-system / gitlab-runner-auth | Opaque: runner-token | Runner管理processのみ | GitLab Runner認証token。Job namespaceへ配布しない |
| account / registry-frontend-pull | kubernetes.io/dockerconfigjson: .dockerconfigjson | kubelet frontend取得 | frontend project Deploy Token、read_registryのみ |
| account / registry-backend-pull | kubernetes.io/dockerconfigjson: .dockerconfigjson | kubelet backend/migration取得 | backend project Deploy Token、read_registryのみ |
| account / account-db | Opaque: admin-password,app-username,app-password | PostgreSQLは全キー、backend/migrationはappキーだけ | Lab運用者。postgres管理roleとアプリroleを分離、アプリはaccount DBの必要DML/DDLだけ |
| observability / grafana-admin（PE-013作成・provisioning確認済み） | Opaque: admin-user,admin-password | Grafana existingSecret | Lab運用者。Grafana管理者のみ |
| observability / newrelic-otlp（PE-011作成・接続受入済み） | Opaque: license-key | Collector SecretKeyRefのみ | Account 8572010 / US のOriginal account license key、APIへ配布しない |

Argo repository Secretはargocd.argoproj.io/secret-type=repositoryラベルを持ち、urlを対象repositoryへ完全一致させる。GitOps tracking annotation/ownerReferenceを付けず、chartから生成しない。CI Job tokenをPodの永続pull資格情報へ転用しない。

| GitLab変数 / 配布先 | 発行主体 / 最小能力 | 消費者 / 制約 |
|---|---|---|
| MANIFEST_UPDATE_TOKEN / frontend・backend project | manifest専用botの期限付きPAT api、対象manifest project Developer相当だけ | 保護mainのmanifest-proposal environment。branch/MR提案のみ、main直接push/merge不可 |
| MANIFEST_VERIFY_API_TOKEN / manifest project（移行契約） | 各ソースprojectの期限付きread_api | 固定SHA検証器を実行する保護main/manual検証だけ。MR変更コードへ渡さない |
| MANIFEST_VERIFY_REGISTRY_USER・MANIFEST_VERIFY_REGISTRY_TOKEN / manifest project（移行契約） | 対象app projectのDeploy Token read_registryのみ | 固定SHA registry検証だけ。API tokenと分離 |
| CI_REGISTRY_USER/PASSWORD / app project | GitLab短期job credential、当該project image push/read | 保護mainの公開/verify。rules自体は権限制限ではない |
| RELEASE_PACKAGE_UPLOAD_TOKEN / app project（PE-015A以降） | 当該Generic Packageへのupload専用資格情報。CI_JOB_TOKENで必要能力が成立するなら優先 | 保護mainの証跡保存jobのみ。MRへ配布しない |
| RELEASE_PACKAGE_READ_TOKEN / trusted verifier（PE-015A以降） | 当該project Deploy Token read_package_registry、upload資格情報と別発行 | 固定origin/project/digest/run URLだけ読取。redirect/userinfo/query/traversal拒否 |

旧MANIFEST_VERIFY_TOKENは現在API/registryで共用されている。上の分離名は移行契約であり、発行/変数設定/既存scope変更をこのMRで実行したとは扱わない。PE-003Bでtrusted verifierへ結合し、PE-004で提案gateを受入する。Package資格情報は今回実装/発行しない。

発行記録は運用者/credential用途/project/role/scope/期限/保管vault参照/最終rotation日を秘密値なしで追跡する。UIマスクだけで漏洩防止を証明しない。MR実コードの信頼とprotected変数/environment条件を維持する。Lab Freeではrole/scopeの組合せで分離が不成立なら具体的な残存能力を記録し、権限が減ったと仮定しない。

## 再注入の実施手順

1. 対象context/namespace/Secret名と必要キー、発行元/期限をこの表と照合する。新規/再注入/rotationの具体的操作を利用者に提示し、承認後に運用者が値を準備する。
2. パスワード管理ツールからアクセス限定のGit外一時directoryへ各キーのfileを出力する。Windowsでは所有者だけのACL、Linuxではumask 077。コマンド履歴へ値を直接入力せず、set -x/debug/verboseを無効にする。
3. Opaqueはkubectl create secret generic NAME --from-file=KEY=ABS_FILE ... --dry-run=client -o jsonをkubectl apply -f -へ直接pipeする。Docker pullは完全なDocker configを--from-file=.dockerconfigjson=ABS_FILE、--type=kubernetes.io/dockerconfigjsonで渡す。repoの非秘密type/url/usernameもfileで統一する。標準出力をtee/file/Gitへ保存しない。
4. Argo repositoryラベルを付ける。名前/キー/type/resourceVersionだけを確認し、値はdecodeしない。起動/repo認証/image取得/DB新規接続の成功を別途確認する。キー存在は資格情報の有効性を証明しない。
5. 一時fileを削除し、保管元のvault参照と結果だけを記録する。Secret checksumをPodTemplateへ入れない。既存PVCのDBは再注入だけでrole passwordが変わらないのでPE-006Bの手順を使う。

isolated kindでもSecret作成/再注入の承認を省略しない。2026-10-02に利用者の明示承認を受け、core-platform-at0102へ既存6Secretを標準入力とメモリ内処理で新規createし、全キーの一致、Argo Git取得、private image取得、DB migration、frontend経由CRUDを確認した。Secret値をfile/Git/ログへ保存していない。Runner管理processは起動せず、既存LabのSecret/DB/dataを変更しない。

既存Deploy Tokenの非秘密メタデータをGitLab UIで読取確認した。platformはargocd-platform-read/read_repository、manifestはargocd-manifest-read/read_repository、backendはplatform-lab-registry-pull/read_registryで、いずれも期限なし。frontendはplatform-lab-registry-pull-20260910/read_registry、UI表記Dec 8, 2026 3:00pm（timezone未確認）。期限なしは既存リスクとして記録し、この作業で発行/期限/scopeを変更しない。API/registry分離とPackage資格情報は未発行の移行契約を維持する。

Rollback: 初期注入失敗は適用前のresourceVersionと保管元を確認し、承認済み旧credentialを復元する。DB既存role変更を伴う場合はDBとSecretを同じ旧状態へ戻し、backend restartNonceでPod置換、DB pgpass/probeと新規接続を確認する。Secret単独revertを復旧完了としない。

参照: [PE-006](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/6)、[初期末端待機](operations/initial-leaf-readiness.md)。
