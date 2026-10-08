# PE-008 Phase 0 CI実受入（2026-10-02）

両サービスのmainへMR !6で統合したCIはvalidateとdeliveryを分離し、言語固有lint/typecheck/test/buildとGo製manifest-helper-testを追跡可能にする。各job/needs/cache/artifact/retryの正本は両サービスの[frontend契約](https://gitlab.com/syuhei-platform-engineering-lab/frontend-app/-/blob/main/docs/ci-contract.md)と[backend契約](https://gitlab.com/syuhei-platform-engineering-lab/backend-app/-/blob/main/docs/ci-contract.md)。cacheは依存入力hash/tool/linux-amd64/保護境界を含みunprotect=false。Registry cacheは入出力とも無効。validate失敗をallow_failureにしない。retryはRunner等の基盤障害に最大1回、script_failureは対象外。build成果物1日、image由来artifact30日。

実通常branch pipelineはfrontend2905315136/backend2905315179成功、両MR !6のCIも成功。いずれもvalidateのみでpublish-image/verify-image/propose-manifest-updateを含まない。別の文書のみの使い捨てbranchでMR !7を開いた後更新し、frontend2905484034/backend2905484079だけがmerge_request_eventで成功。同じ更新SHAのpush pipelineはゼロで重複抑止を実証。両fixture MRは未統合のままClosed。

保護main通常成功時には全validate→publish→verify→proposalが成功し、manifest !29/30を作成した。Ownerだけがpipeline変数を指定できる設定でAT13_FORCE_VALIDATION_FAILURE=trueを指定した実main pipeline frontend2905266795/backend2905271941はmanifest-helper-testがscript_failure、retryゼロ、後続3delivery全てskipped。通常設定はfalseで、この失敗は意図した異常試験。at13-no-pipeline-20261002タグはworkflowでAPI実行要求400、タグpipelineゼロ。

AT-13の未保護CI_JOB_TOKENで使い捨て40桁SHAタグ、main-cache fixtureタグ、Generic Packageに実書込みを試行し、両projectとも全201だった。提案bot用の保護資格情報は未保護fixtureへ配布されていない。GitLab Freeでrulesは認可境界ではなく、Registry/Packageへの技術的拒否を成立したとは扱わない。保護Runner cache分離ci_separated_caches=trueをRegistry cache権限と混同しない。同projectの信頼済みコードだけを実行し、外部MRは禁止、Registry cacheは無効を維持する。未保護コードを信頼できない用途ではこの経路を有効化しない。資格情報は証跡へ含めない。mainは直接push禁止、提案botはDeveloperでmerge不可、管理PATはCIへ渡さない。

[証跡](evidence/PE008-CI-acceptance.json)は通常branch/MR、main期待失敗、tag、MR重複抑止、権限fixtureを含む。これはPhase 0のPE-008/AT-13受入。Phase 2のscan/SBOM/OCI供給網切替と最終AT-13再確認はPE-015〜019へ残し、実装済みとは記載しない。