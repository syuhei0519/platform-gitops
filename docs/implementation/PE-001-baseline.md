# PE-001 現状・責任境界の基準記録

2026-10-01（Asia/Tokyo）。Issue: [PE-001 #1](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/work_items/1)。設計状態はFROZEN FOR IMPLEMENTATION。PE-001は4repoのMR統合・利用者承認待ちであり、DONEではない。

## 最新mainと正常CI

git ls-remote --symref origin HEADとローカルHEADを今回再確認。全repoのdefault branchはmain、変更開始前のworktreeはclean。下記CIは今回画面を読み取った現在のmainに対応する既存成功結果で、新規ATを実行した結果ではない。

| repository / 所有 | main HEAD | main CI |
|---|---|---|
| frontend-app / React・Nginx既定・CI | 00318cc4646728cb47597e99fb8227433806595b | [2833710532 Passed](https://gitlab.com/syuhei-platform-engineering-lab/frontend-app/-/pipelines/2833710532) |
| backend-app / Go API・SQL・CI | 86f30d2f0dcfde4341bc468cde4536bd4bbec983 | [2833434816 Passed](https://gitlab.com/syuhei-platform-engineering-lab/backend-app/-/pipelines/2833434816) |
| application-manifest / account charts・配備値・DB/PVC | a07522355dc4aa259f47195e4d6e3673e9509c86 | [2835309843 Passed](https://gitlab.com/syuhei-platform-engineering-lab/application-manifest/-/pipelines/2835309843) |
| platform-gitops / bootstrap・Argo・Runner・観測基盤 | 4c4c9d59fbb351285b4e1c0ea1ba03a32359b0d5 | [2833800657 Passed](https://gitlab.com/syuhei-platform-engineering-lab/platform-gitops/-/pipelines/2833800657) |

remoteは全repoとも `https://gitlab.com/syuhei-platform-engineering-lab/<repository>.git`。別系統platformengineeringlab配下のfrontend / backend / application-gitopsは対象外で未変更。

## 所有関係・稼働値

対象contextは `kind-platform-lab`。kubectl get applications -n argocdを読み取り、以下8 ApplicationがSynced / Healthy。末端の同時点のdesired revisionは上記Git HEADと一致する。全Application inventoryに旧系統repoURLはなく、同context内のApplicationによる所有競合は検出しなかった。別contextの実体は未確認。

| Application | source / path | destination |
|---|---|---|
| lab-local-root | platform-gitops / charts/root | argocd |
| lab-local-platform | platform-gitops / charts/platform | argocd |
| lab-local-workloads | platform-gitops / charts/workloads | argocd |
| lab-local-runner-foundation | platform-gitops / charts/runner-foundation | runner-system |
| lab-local-gitlab-runner | gitlab-runner chart 0.92.1 + platform-gitops values（multi-source） | runner-system |
| lab-local-postgresql | application-manifest / charts/postgresql | account |
| lab-local-backend | application-manifest / charts/backend | account |
| lab-local-frontend | application-manifest / charts/frontend | account |

Chart既定workloadsEnabled=false、local値true。Chart既定image/release未設定とlocalの検証済みimage/releaseは異なる。READMEの「現在未設定」「Namespaceを所有しない」という混同を修正した。Namespace定義はbootstrap/namespaces.yaml、適用は運用者、Argo Chartでは生成しない。

## 固定版照合（R14）

正本は本repository相対 `bootstrap/versions.lock.yaml`（4repo横断表記: platform-gitops/bootstrap/versions.lock.yaml）。アプリのci/tool-versions.envは双方とも未作成。DESIGNの追加案を導入済みと扱わず、PE-008/015/016/017で実行値追加時に対応を照合する。

| 項目 | 台帳 / 実設定 | 今回確認 |
|---|---|---|
| kind | v0.33.0 / kindest node v1.36.4 + digest | kind CLI版、稼働kubelet v1.36.4 |
| kubectl / Helm | v1.36.4 / v4.2.4 | ローカル固定toolingを使用、CI Helmも4.2.4 |
| Argo CD | chart10.8.2 / app v3.5.2 | 稼働image v3.5.2 |
| Runner | chart0.92.1 / app19.3.1 / helper固定digest | 稼働manager alpine-v19.3.1、multi-source chart固定 |
| BuildKit | v0.30.0-rootless + digest d76eb1ca… | 両アプリCIに台帳と同じimage/digest |
| crane | v0.21.7 + digest54b27703… | 両アプリ/manifest CI固定。台帳追記の対象はPE-008以降 |
| Node / Go | 24.19.0 / 1.27.1 + digest | CI/Dockerfileで固定、両アプリREADMEと整合 |

R01〜R04はdocs/bootstrap.mdに凍結契約を明記。R01 inventoryはPE-003、R02 run別保存はPE-017、R03正式切替はPE-016〜018、R04生成元識別はPE-011〜013で実装受入する。

## 環境条件と有効化ゲート

| 条件 | 現在値 / 確認状態 | 担当PE / 有効化条件 |
|---|---|---|
| Host容量 | 物理34183176192 bytes / 20 logical CPU、C空き569465794560 bytes（時点値） | PE-009予算に使用。空きは予約量ではない |
| Docker / kind容量 | Docker16679239680 bytes / 20CPU、kind node16288320Ki / 20CPU | PE-009でrequests/limits・同時CI・一時diskを予算化 |
| WSL上限 | .wslconfig不存在、WSL2稼働 | PE-009で実上限・空き・Docker/kind外消費を確認 |
| 現状使用量 | Metrics API not available、kubectl top不可 | PE-009で別の実測方法。観測stack有効化前に暫定予算が必要 |
| GitLab plan / artifact上限 | UIでGitLab Free確認、artifact実上限は未確認（API scope不足） | PE-016のOCI有効化を実上限確認まで止める |
| Branch Protection / token role/scope | UIで4repoともmain protected、merge=Maintainers、push=No one、protected tags=0。token role/scopeと実書込能力は未確認。rulesを認可の証拠と扱わない | PE-004/008 AT-13で実権限確認まで秘密付き新経路を有効化しない |
| Package upload/read / 重複保存設定 | 未確認 | PE-006A/017C、実upload/readは017F/B。Token発行やscope変更は利用者承認 |
| Secret再注入 / rotation | 今回値を読まず、作成・変更・rotationも未実施 | PE-006で契約と承認済み実証を分ける |
| 観測基盤 / PVC・TSDB | 未導入・未試験 | PE-009→010。現在のHealthyをAT-09合格と扱わない |

## 検証証跡・差異

読み取り調査コマンド: git status/log/remote/ls-remote、kubectl（明示context）get applications/nodes/pods、docker info、kind version、Get-CimInstance / Get-PSDrive、GitLab pipeline画面。実行日時と生ログは `docs/implementation/evidence/PE-001-readonly.txt` と `PE-001-environment.txt` に保存する。Secret値を含めない。

設計の歴史HEADは今回再確認した最新HEADと一致。観測component未導入、tool-versions.env不存在、docs/bootstrap.md不存在、READMEの稼働値説明の不一致を確認した。後二者を本変更で修正し、componentやtool実行値は担当PEの変更に残した。

全19親Issue / 32内部作業単位 / AT-01〜14参照を作成。Issueリンク索引は同階層のISSUES.md。PE-001依存の実装は本MR群の承認・main統合後に開始し、未統合状態を完了扱いにしない。今回の変更は文書のみで稼働manifest/CI/権限を変更しない。rollbackは文書commitのGit revert。
