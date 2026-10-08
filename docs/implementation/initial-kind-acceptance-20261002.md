# PE-002A/006A 初回末端・Secret再注入受入

2026-10-02 Asia/Tokyo、利用者から隔離kind core-platform-at0102の構築/固定6Secret再注入/試験clusterのDB/PVC/dataと専用kubeconfig削除を明示承認された。既存kind-platform-labは読取だけで、Secret/DB/PVC/data/Token scope/GitLab権限を変更していない。

kind0.33.0/node1.36.4/Argo chart10.8.2/app3.5.2、platform main7303c290f205dee53f5d3f18580338888cd53bfc、manifest main0ca1f0abe015e31c46c4312954befa6c92fd6cc5。Git外一時領域のkubeconfigを利用者のみのACLにし、既存current-contextを変更していない。NamespaceとAppProject、Workload親Applicationだけをbootstrapし、Runner管理processは起動しない。全root/Runner受入はこの試験の範囲に含めない。

既存6Secretの値をメモリ内のkubectl JSON→標準入力で新規create。名前/namespace/type/キー/UID/resourceVersionと全キー一致だけを証跡へ記録し、平文/base64 Secret/Docker config/認証headerをfile/Git/ログへ出していない。2repositoryのGit取得、2private image取得、DB新規初期化とmigration、frontend経由CRUDも成功した。Deploy Token issuer/最小scope/期限の非秘密UI記録はsecret-contractとcredential-metadata.jsonにある。API/registry分離や将来Package credentialは移行契約であり、発行済みとは扱わない。

Workload親のwaveによる実順序:

- PostgreSQL Application作成01:10:29 JST、Healthyを待ってbackendを01:10:54に作成。
- backend全体syncのmigration Sync hookは対象manifest SHAでSucceeded、終了01:11:09。
- backendのHealthyを待ってfrontendを01:11:15に作成。
- 01:11:28に3末端とWorkload親がSynced/Healthy。マージ済みtools/bootstrapのread-only待機も同SHA/hookを受理した。
- frontend HTTP200、API create201/read200/update200/delete204/削除後404。schema_migrationsはversion1の1行。

Lua10 fixtureのPASSと合わせ、PE-002Aを受入済み。再注入契約・実一致・Git/image/DB有効性からPE-006Aを受入済み。親Issue002/006はBが未完了なのでOpenのまま。AT-03全体・DB rotation・AT-10は未受入。既存Labの8ApplicationはSynced/Healthyを維持した。

秘密値なし証跡はdocs/implementation/evidence/initial-kind-20261002/。初回状態履歴、Pod UID/imageID、migration履歴、CRUD status、Secret metadata/一致、issuer/scope/expiry UIの行だけを保存している。試験clusterは後続受入を継続する間だけ維持し、承認済みの名前/context/node照合のうえ試験終了時に片付ける。
