# PE-017C 共通証跡保存契約の受入

Source両サービスの共通schema v2、strict validator、Package client、保持計画とmanifest専用read-only能力試験をmainへ統合した。frontend !9/backend !11/manifest !47はown CI成功、固定source/main鮮度と実merge tree一致を確認した。PE015はplatform !25統合後、実Issue readbackでClosedとなり親15/19・内部24/32。旧PE015 JSONのintegrationPendingは提出時履歴であり、今回のpost-close証拠が現在状態を示す。

## 実GitLab能力受入

Source protected-mainの専用pipeline frontend 2908243077/backend 2908256232は、実CI_JOB_TOKENで予約済み非image digestへ3ファイルを保存し、再取得checksum、同名同値冪等、client別値拒否、server重複PUT HTTP400、拒否後の元bytes一致を確認した。group141770909はGeneric重複禁止・例外空。writerは各projectのevidence-writer resource_groupで直列化する。

manifest 86247034だけを両Source allowlistへdefaultPermissions=false/READ_PACKAGESだけで登録し、別API readbackも照合。新mainの専用pipeline2908297735/job16907884468で両Sourceの全固定URL/checksumをGET照合し、manifest自身の別run名による新規PUTを両方HTTP403で拒否した。既存ファイル重複400をwrite権限拒否と取り違えていない。試験payloadはschema0の非リリースfixtureで、schema2採用は必ず拒否する。全validate/source-scan成功、専用試験からimage delivery/trusted MR検証を除外した。通常push mainのfrontend2908236037/backend2908251571もdelivery成功を再確認した。

## モデル・保持契約

3repoのGo1.27.1全11試験が合格。schema v2はsource project/repo、元buildと現在scanの別ID、固定digest/run/URL/checksum、SBOM種別、DB/例外の現在時刻、unknown/duplicate/null/欠落を検証。元build不明やinput不足は明示した失敗recordだけを保持し、ID/hashを捏造せず採用拒否する。同digestの新しい失敗runを古い成功に置換しない。Draft2020-12メタschemaと正2/負10fixtureも全3repo合格。実API/OCIラベル・完全SBOM内容の受入はF/Bで行う。

Generic自動削除は有効化しない。保持plannerは最新10 OR 最終run90日以内 OR running/rollback登録の集合を保持する。16version fixtureでexact90日、失敗再scanによる延長、稼働/rollback除外、10未満全保持を確認。未列挙run/未来/欠落/重複/保護inventoryなしは計画全体を拒否。DELETE処理はなく、実inventory収集とoperator-reviewed cleanupは後続統合の条件として残す。90日経過の実時間試験を実施したとは主張しない。

## 受入範囲と次工程

Cの共通契約を受入対象とし、証跡main統合後にIssue17のCだけをチェックする。親17/F/B/AT04/12全体は未完了。次は16F→17F→18F完全切替→16B→17B→18B→19。実OCI入力、scanner/SBOM、immutable release writer、build provenanceとPhase2配備gateをこの能力fixtureだけで完了にしない。Source runtime/DB/PVC/NR課金設定は変更なし。秘密値と生job traceは証跡に含めない。

JSON indexのSHA256はGitへ保存するLF bytesを対象とする。公開proofは過去時点の限定された範囲を保持し、上記の実受入と統合receiptを併読する。
