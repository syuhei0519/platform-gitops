// 基盤の静的検証と読み取り確認を行う Go ツールの module と依存定義です。
module github.com/syuhei0519/platform-gitops/tools/bootstrap

go 1.27.0

require (
	github.com/yuin/gopher-lua v1.1.1
	gopkg.in/yaml.v3 v3.0.1
)
