# PE-005 設定だけの再配置・無関係値・Git復元の実受入

2026-10-02、隔離kind-core-platform-at0102でmanifest MR28を反映し、frontend apiBasePath=/api-pe005とbackend LOG_LEVEL=debug/DB FQDNを変更した。両imageは不変、checksum/configとPodTemplateが変化し、新ReplicaSet revisionとPod UIDを実測。新API pathのJSON・作成/読取/更新/削除とbackend debug起動ログが成功した。ConfigMap volumeだけでNginxがreloadすると仮定せずPod置換を確認した。

MR32でfrontend Service.portだけ80→8088に変更し、新Service port経由のJSON CRUDが成功した。両PodTemplate/checksum/ReplicaSet revision/Pod UIDが不変で、無関係な値による不要再配置なしを実証。古いport-forwardの成功を合格にせず、新Service8088への接続を作り直した。

MR33で元のenvironment YAML設定とService portをGitで復元し、両Applicationを同じmain d782ff546f3bbd1efb22d0846f9be0d36b982b71へ全体明示sync。checksumとimageが元の値へ戻り、試験Podから新Podへ置換された。frontend port80/既定/apiのJSON CRUD成功、backend起動log_level=INFO、debug起動ログなしを確認。SourceLabのaccount3Applicationも同じ復元revisionへ同期した。CI checksumテストの修正は残した。

chartが環境ConfigMapと再配置を所有し、frontend image内Nginx設定は単独起動用既定。React bundleの既定APIは/apiなので試験の/api-pe005とは区別し、復帰後/api CRUDを確認した。Secret値はrender入力/ConfigMap checksumへ含めず、Secret環境変数更新はPE-006BのrestartNonceで扱う。固定main CIの全7設定キー/Service-only/Secret参照静的検証と実runtimeを区別する。

[実証跡](evidence/PE005-config-runtime.json)に変更前後UID/checksum/image/RS、HTTP CRUD、debug/INFO動作、Service-only不変、Git復元を保存。MR28/32/33は各own CIと固定main producer/consumer成功、直前鮮度、誤SHA409、成立tree一致を検証してmain統合した。DB/PVC/dataは削除していない。