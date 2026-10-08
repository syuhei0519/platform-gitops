# PE018F frontend全面切替の実受入

2026-10-04、frontendの検査済みOCI経路を既定に切り替え、実公開、同SHA再取得、現在policyによる歴史候補再検査、実rollbackと復帰を確認した。受入監査は成功済み。この証跡のmain統合とIssue18のFチェックで内部単位を完了する。親18とbackendは未完了である。

## 新公開と同SHA再検査

frontend保護main `ce6ec1d94cf1042a824b745f4a5f6367b1c0f845` の通常pipeline2910339772は全15job成功。build16918864597、scan16918864600、verify16918864603、Source CI提案16918864604を実行し、digest `sha256:68aa3ca3b850bea5b47004877067197c14c670a8cd34b57cf58acc33c03fda78` を公開した。record SHAは `c049f1daece26abaae441b7b41d3f558b41747443d19a4a7f27d4c537e405c13`。自動提案manifest !67はown CIと固定検証2910351024、直前SHA、CAS409、実tree一致でmain75b39d88へ統合した。

同SHAのreadonly再取得pipeline2910363180は全14job成功し、新build・再pushなしで同digestを最新DBで再検査した。scan16918980625、record SHA `ab17a7862ac31365e277f4483b41ccdf3ad21d8a89f7029447d5958d2aada518` は新runを指す。自動提案 !68は固定検証2910370268とown CI成功、CAS409、実tree一致でmain9f48b913へ統合した。両runのwriter/export、Generic三ファイルのraw checksum、SBOM、同archiveのformat/runtime検査、source・build・scan・job権限結合を実consumerが確認した。

公開後Pod UID b4bff18f-b02d-48a9-8cb3-dca9f9843e05、再検査配備後UID629acbbf-88f4-4833-b5c5-beaf08e0f882について全12注釈、digest、UID10001、HTTP200を確認。各Podへの専用forwardでCRUD各7操作を実行し、synthetic行239/240の削除後GET404を確認した。forwardは終了済み。

## 最新policyでの最終AT14と復帰

登録済み歴史候補source `6f7e8c047c3d6f648585509a9750423ef78fdb27`、digest `sha256:ce9caabe905b350e96f3fce4a92694f8e9a9486191a3b3fe072605f5aedc7d76` を、現在policy ce6ec1d9のpipeline2910392838で再検査した。全11job成功、scan16919122100、元build2909565747/16914810209を保持し、build/publish/proposalジョブは存在しない。record SHA `4d266d61280dac399ff76374a727c6444709a458c9c17951680fc7ab1c8bc090` とSBOMの完全結合を確認。manifestの実readonly reader2910405510も成功した。このreaderだけで配備を許可しない。

この検査とreaderの成功後、明示理由付きoperator rollback !69を作成。固定検証2910411401とown CI、CAS409、実tree一致でmaindeeb3205へ統合した。実Pod UID897a10cf-dbab-4eda-865a-442a044aadfdは歴史digestと今回runの全注釈、UID10001、HTTP200を満たし、CRUD7操作、行241削除後GET404を確認した。これを最終AT14成功とする。

続くoperator復帰 !70は検査済みce6ec1d9/digest68aaの再検査runを採用。固定検証2910426623とown CI、CAS409、実tree一致でmanifest main `397deed081ec2070ec3b7cc29214bf43649e742d` へ統合した。実Pod UID6da97e91-4aca-4271-b313-4be4245090dcで全注釈・UID10001・HTTP200、CRUD7操作、行242削除後GET404を確認。専用forward終了済み。全四回の配備でbackend Pod、DB PVC UID f7774170-a00e-4d90-9caa-e692f1e1555a、schema1、既存行9を保持し、DB schema変更・data削除は行っていない。

## 拒否条件と残る運用制約

現在保護mainのnegative pipeline2910373204はmanifest-helper-testの意図した失敗を確認し、build/scan/format/runtime/publish/verify/proposalは全skip。検証失敗時の公開・提案停止を実確認した。AT04/12の異なる実DB二回検査、第二failed不変保存、両run実readerとPUT403、差替え拒否はPE017Fのmain da34d595受入を維持し、今回の完全run結合へ接続している。

AT13について、既存PE008のunprotected実probeはSHA-tag/main-cache/evidence-packageへのPUTがいずれも201だった。今回の現設定監査とDAG停止を技術的なregistry書込禁止へ置き換えない。private project、保護main、force-push制限、分離cache、REGISTRY_CACHE_OUTPUT=disabled、保護env資格、実job/main認可、完全tupleと不変証跡を運用制約として記録する。信頼した同project MRを対象とし外部MRを実行しない。旧成功probeや資格setupを再作成していない。

旧未検査 `ci/includes/delivery.yml` と `ci/build-image.sh` は撤去済み。既定OCI_DELIVERY/registry retrieval・inspection/writer/delivery Phase2は有効、失敗後の旧成功fallbackなし。新buildの安全なformat/runtime JSONを既存Reporter readerで読めるようartifact accessを調整した。private OCI build artifactはmaintainerのまま。先行三つのverify失敗pipeline2910097350/2910275893/2910309383は失敗として保持し、retry・同SHA再buildをしていない。

## 証跡と未完了

ローカル正本はoutput/core-platform-v1-phase1-phase2-20261002/evidenceのPE018F-acceptance-audit.json、safe-inspection-reader-main/reinspection/validation-stop-state、current-AT13-boundary-audit、final-historical-record-selector-native-inspection-state、final-historical-inspected-reader-state、published/reinspected/final-rollback/final-restore-phase2-runtime-proof。秘密値、jobtrace、private OCI binaryを保存していない。先行host DB cache削除拒否と残存は未解決のまま保持する。

親Issue進捗15/19。F証跡統合後の内部単位28/32。次の凍結DAGはPE016B→PE017B→PE018B→PE019。backend全面切替、全19親Issue Closed、最終AT01〜14の横断監査は未完了。このF完了を親18完了へ置き換えない。

[frontend main](https://gitlab.com/syuhei-platform-engineering-lab/frontend-app/-/commit/ce6ec1d94cf1042a824b745f4a5f6367b1c0f845)
[最終manifest復帰](https://gitlab.com/syuhei-platform-engineering-lab/application-manifest/-/merge_requests/70)
[前段PE017F受入](PE017F-acceptance-progress.md)
