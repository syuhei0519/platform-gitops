# PE-009/010 隔離kind実受入

2026-10-02 UTC。対象はkind-core-platform-at0102、nodeはcore-platform-at0102-control-plane。既存Labとは別clusterで実施。platform main 7d27662668a479e7de46e44a730c1cf4a99d33d9、Prometheus chart29.35.0のfoundation/serverともSynced/Healthy。

PE-009: Namespace/AppProject/RBACの所有者とrender予算はdocs/observability-budget.md。Prometheus専用SAのnodes/proxy許可、KSMのaccount Pod読取、他SAの拒否など9件を実測。Secret読取は両観測SAで拒否。証跡 [RBAC](evidence/PE009-rbac.json)。暫定予算の受入であり、CI最終同時負荷はPE-014/019へ残す。

PE-010: 自己/KSM/cAdvisorの明示3targetすべてUP。accountのCPU・memory・Readyを実収集。Pending専用PodをunschedulableにしPending=1を取得。専用Prometheus fixtureではCA検証を保ったままserver_name不一致で証明書エラー、認証無しでHTTP拒否を起こし、いずれもup=0。production scrape定義は変更していない。fixtureは試験後削除。[negative/Pending](evidence/PE010-negative-and-pending.json)。

AT-09: 固定fixture containerを異常終了し、同一Pod UID、exit137、restartCount 0→1を実Podとscrapeの両方で確認。続くPod replacementは別UID、Ready=false→true、restartCount=0とkube_pod_infoのUID一致を確認。[AT-09](evidence/PE010-AT09.json)。

TSDB: server Podを再起動し、Pod UID変更・同じPVC UID/PV・再起動前の指定時点のup sample完全一致・3target UP復帰を確認。[before](evidence/PE010-persistence-before.json)、[after](evidence/PE010-persistence-after.json)。PVCは実オブジェクトでもPrune=confirm/Delete=confirm・Helm keep。実削除を試さずdataを保全した。

保持期間は実起動flagsで3d/2GiB。再起動前の39sampleは約19分の実履歴で、3日分を既に保持したと扱わない。WAL使用508KiBを後続時点で測定。retention.sizeはhead/WALを含むPVC全体のhard limitではない。5Giは申請capacityであり、kind local-pathはnode全体のfilesystemを使い、5Giのhard quotaを保証しない。実data容量・空き容量はbefore/afterに記録。

全操作は2026-10-02の利用者による包括承認の範囲で実施。Collector/Grafana/New Relic/Phase2は未配備。3日実経過の保持実測と最終CI負荷は未測定として後続へ引き継ぐ。
