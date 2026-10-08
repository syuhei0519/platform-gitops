# GitHub Actionsの運用手順と移行記録

## 2026-10-09の更新

同名のPublic GitHubリポジトリへ、GitLabの旧コミット履歴を含めずソースを移行しました。
初回のGitHub Actions CIは4件とも成功しています。mainのrulesetとEnvironmentを設定し、
CLI認証はWindows資格情報マネージャーに暗号化保存した認証情報を使います。
通常操作で1Passwordへアクセスせず、PATをファイルやGit URLへ保存しません。

Trivy DBの取得先を公式GHCR優先に変更し、24時間以内の鮮度基準を維持しています。
frontendのbrace-expansion/source-map-jsを更新し、npm auditの検出は0件になりました。
release-record検証ツールのgolang.org/x/textはv0.41.0へ更新して既知脆弱性を修正します。
以下の「移行前」「未実施」およびローカル検証結果は2026-10-08の準備時点の記録です。
公開・切り替えの現在の結果はGitHub Actions、Release、manifest PRの実行記録で確認します。
初回GHCR公開後にはpackageのPublic設定と匿名pullを確認し、manifest PRの独立検証を
通してからArgo CDを切り替えます。Environment承認とPR mergeは運用者が明示的に行います。
移行先: https://github.com/syuhei0519/platform-gitops
起点: 2026-10-08に取得したGitLab origin/main。ローカルの元branchは保存しています。
GitHubへ旧履歴を含めずソースを移行済みです。GitHub用checkoutは `C:\work\CorePlatform-github` 配下です。

`.github/workflows/ci.yml` が通常CIです。GitLab入口は `workflow: rules: when: never`
とし、旧include/toolは回帰テスト・履歴資料用に保持しています。
GitLabへこの変更をpushすると旧pipelineも止まるため、移行先準備後に切り替えてください。

CIはGitHub-hosted Ubuntu 24.04を使い、PR・branch push・明示起動を受け付けます。
PR jobへPATやregistry書込権限を渡しません。checkoutは認証情報を永続化しません。
Actionはcommit SHA、scan/Helm/Kubeconform/BuildKitは既存の固定digestを使います。
Go 1.27.1 / Node 24.19.0等の既存バージョンを維持しています。

文書専用差分はdocsチェックとsecret検査へ分岐し、不明な比較元・CIファイル変更・
初回push・workflow_dispatchは通常CIへ倒します。非main branchはmainとの差分全体で判断します。
branch protection/rulesetの必須checkは `ci-result` に設定してください。
mainへの直接pushを制限し、必須CIと最新mainへの追従をPRで確認します。syuhei0519の単独運用のためPRの必須承認数は0、EnvironmentはOwner承認です。
各EnvironmentのDeployment branchesはmainのみ、Required reviewersはOwner、
自己承認と管理者bypassの可否も運用に合わせて制限してください。


bootstrap契約、Helm render/policy、Kubernetes/Argo CRD schema、source/render scanを実行します。
clusterへのapplyを行うjobはなく、kubeconfig・DB password・New Relic license keyを
GitHub Secretsへ登録する必要はありません。
既存GitLab RunnerのChart/設定はcluster構成として検証対象に残しています。
Runner撤去はCIプロバイダー変更とは別のcluster変更なので、この準備では行いません。

## 移行時に用意するもの

1. syuhei0519 配下に同名Public repoを4件作成します。default branchはmainです。
2. 初回push用fine-grained PAT: 作成済み4repoを選び、Contents: Read and write、
   Workflows: Read and write。空repoをUIで作る場合、PATのrepo作成権限は不要です。
   PATはソース、remote URL、CIログ、Actions Secretに初回push用途として埋め込みません。
3. アプリrepoのEnvironment `ghcr-publish` / `manifest-update`、manifest repoの
   `manifest-verification` を作り、main限定とOwner承認を設定します。
4. アプリrepoの初回検査後 `ENABLE_GHCR_PUBLISH=true` とし、初回公開を承認します。
   GHCRのbackend-app/frontend-app packageをPublicにし、匿名pullを確認します。
5. application-manifestのGitHub版Chart/検証workflowを先に反映し、アプリrepoの
   `MANIFEST_WRITE_TOKEN` をEnvironment Secretへ登録し、manifest側の
   `manifest-verification` Environmentに両アプリのActions: Read / Contents: Readを持つ
   専用 `EVIDENCE_READ_TOKEN` を登録後、`ENABLE_MANIFEST_PR=true` にします。
6. Argo CDの参照URL（platform-gitops/application-manifest、AppProject許可URL、bootstrap）
   をGitHub URLへ切り替えます。これは実稼働の切替手順であり、この準備では既存URLを変更しません。
   旧registryのdigestをGHCR URLへ文字列置換せず、初回の検査済みGHCR公開とPRで更新します。
7. ソースのみ移行する場合は準備branchの追跡ファイルをexportし、別directoryで `git init`
   します。`.git`・旧tag・branchをコピーせず、GitHub noreply emailで初回commitします。
   この準備の履歴なしZIPは未commitの変更も含めています。元repoから `git archive HEAD`
   だけを実行すると今回の未commit変更が含まれないため注意してください。

## 旧GitLab専用経路と受入条件

GitLabのgeneric package API、数値project/job ID、MR API、旧protected-variable条件は
GitHubで再利用しません。通常の新規公開/PR/信頼済み検証はGitHub-native経路に置き換えます。
旧GitLab専用のAT12/AT17/AT18受入fixture、registry reuse/rescan/rollbackの明示API jobは
GitHub workflowへ公開しません。保存済みGitLab証跡は旧providerの証跡として扱います。
GHCR版の過去digest再採用・再scan・rollback運用は別途受入してから追加し、
既存タグの異なるdigestへの上書きや、古いscanでの自動配備は許可しません。

GitHub移行前のため、実Actions run、GHCR push/pull、PAT権限、Environment/ruleset、
Release asset公開とtrusted workflowの統合試験は未実施です。
ローカル検証だけで本番切替完了とは扱いません。GitHub上で正常系と拒否系を確認してから
マニフェストPR・Argo CD参照先を切り替えます。

参照: [GitHub Token](https://docs.github.com/en/actions/concepts/security/github_token)、
[Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)、
[PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)

## 2026-10-08のローカル検証結果

5件のworkflowのactionlint、shell構文、Go moduleテスト、アプリのlint/typecheck/test/build、
Helm render/schemaと旧GitLab/native GitHub release annotation境界テスト、
両アプリのrootless BuildKit OCI build・100MiB境界・crane reader・networkなしruntimeが通過しました。
native release helperの8件の境界テスト（重複JSON、鮮度、artifact改変、API redirect、
PR値の保全、実run identity、後続失敗による旧成功の失効等）も通過しました。
移行候補の追跡ファイル＋今回の新規ファイル全体をGitleaksで検査し、検出0件でした。

全security gateの合格は確認できていません。Trivy DBのUpdatedAtは
2026-10-07T07:38:55Zで、2026-10-08T14:38Z時点で約31時間前です。
ミラーと公式GHCRからの再取得でも同じDBだったため、既存の24時間制限で拒否されました。
DB時刻やpolicyを変更せず、CIも同条件で停止します。新しいDBで再検証が必要です。
backendの同一raw scanからSBOMの構造・image config・部品対応を検証する単独試験は通過しましたが、
これをsecurity gate合格や公開許可として扱っていません。

frontendのnpm auditではbrace-expansion/source-map-jsの間接依存にHigh 2件、
Critical 0件、fixAvailable=trueを確認しました。依存の更新は今回のCI移行変更に含めていません。
GitHubの実認証・公開・API/Environment/rulesetの統合受入は移行先準備後に行ってください。
