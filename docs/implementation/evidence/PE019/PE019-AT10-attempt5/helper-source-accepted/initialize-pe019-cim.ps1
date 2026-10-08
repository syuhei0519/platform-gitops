function Initialize-PE019Cim([string]$AuditPath) {
 $dependency=Get-Content './output/core-platform-v1-phase1-phase2-20261002/evidence/PE019-CIM-dependency-metadata.json' -Raw|ConvertFrom-Json
 $dll=(Resolve-Path (Join-Path $PSHOME 'Microsoft.Management.Infrastructure.CimCmdlets.dll')).Path
 $hash=(Get-FileHash -LiteralPath $dll -Algorithm SHA256).Hash.ToLowerInvariant()
 if($hash -ne $dependency.bundledDependencySHA256){throw 'Pinned bundled CIM dependency changed'}
 $initial=Get-Command Get-CimInstance -ListImported -ErrorAction SilentlyContinue
 if(-not $initial){Import-Module -Name $dll -Global -ErrorAction Stop}
 $command=Get-Command Get-CimInstance -ListImported -ErrorAction Stop
 if($command.CommandType -ne 'Cmdlet' -or $command.ModuleName -ne 'CimCmdlets' -or $command.Module.Path -ne $dll){throw 'Exact bundled native CIM cmdlet required'}
 @{utc=[DateTime]::UtcNow.ToString('o');processId=$PID;powershellVersion=$PSVersionTable.PSVersion.ToString();initialCimCommandImported=[bool]$initial;explicitImportUsed=(-not [bool]$initial);moduleSha256=$hash;dependencyInitialized=$true;originalFailureCauseProven=$false}|ConvertTo-Json|Set-Content -LiteralPath $AuditPath -Encoding utf8
}
