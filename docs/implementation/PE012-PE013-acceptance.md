# PE-012/013 実受入

SDK単体と実APIの計数は厳密値、PromQLは300要求に対して300.00444451という推定値として記録する。p95は0.00475秒で既知0.005秒bucket内、無通信量PUTはN/A。実image digest/source SHA、同Pod内再起動のUUID更新、旧新版2instanceの各+10、HTTP→DB親子とlogを実測した。

New Relic US account8572010でtrace.idの公式構文を実行し、旧新版のHTTP2/DB2spanのID・親・source・UUID・PodUIDを照合した。引用付き検索のNoDataは履歴として保存し、検索成功を推測で代替していない。運用例は `FROM Span SELECT * WHERE trace.id = '<trace_id>' SINCE 60 minutes ago`。公式例: https://docs.newrelic.com/docs/apm/distributed-tracing/trace-api/troubleshooting-missing-trace-api-data/

Grafanaの22query、版legend、再起動/再適用とPVC保持は実受入済み。証跡索引JSONは許可された公開属性だけを保存する。PE014で行うCollector実障害/復旧/15分同時予算のAT07は未実施であり、本受入で合格にしない。全19親Issueの最終完了も未達。Secret/PAT/License Keyを含めない。
