[CmdletBinding()]
param([string]$Output='output/core-platform-v1-phase1-phase2-20261002/evidence/PE019-resource-baseline.json')
$ErrorActionPreference='Stop'
. './output/core-platform-v1-phase1-phase2-20261002/acceptance/read-pe019-statistics-json.ps1'
$kubectl=(Resolve-Path 'tmp/tooling/kubectl.exe').Path
$targetConfig=(Resolve-Path 'tmp/pe019-private/kubeconfig').Path
$snapshots=[Collections.Generic.List[object]]::new()
function Read-KubernetesJSON([string[]]$Arguments,[string]$Stage){
 $pi=[Diagnostics.ProcessStartInfo]::new($kubectl)
 $pi.UseShellExecute=$false;$pi.CreateNoWindow=$true
 $pi.RedirectStandardOutput=$true;$pi.RedirectStandardError=$true
 $pi.StandardOutputEncoding=[Text.UTF8Encoding]::new($false,$true)
 $pi.StandardErrorEncoding=[Text.UTF8Encoding]::new($false,$true)
 foreach($argument in $Arguments){$pi.ArgumentList.Add($argument)}
 $process=[Diagnostics.Process]::Start($pi)
 try{
  $stdout=$process.StandardOutput.ReadToEndAsync();$stderr=$process.StandardError.ReadToEndAsync()
  if(-not $process.WaitForExit(20000)){$process.Kill($true);throw 'Kubernetes resource metadata timeout'}
  $text=$stdout.GetAwaiter().GetResult();$privateError=$stderr.GetAwaiter().GetResult()
  $code=$process.ExitCode
  $meta=@{stage=$Stage;processId=$process.Id;hasExited=$process.HasExited;processExitCode=$code;stdoutComplete=$stdout.IsCompleted;stdoutUTF8Bytes=[Text.Encoding]::UTF8.GetByteCount($text);stdoutReaderCodePage=$process.StandardOutput.CurrentEncoding.CodePage;hostConsoleCodePage=[Console]::OutputEncoding.CodePage;startsWithJSONObject=$text.TrimStart().StartsWith('{');endsWithJSONObject=$text.TrimEnd().EndsWith('}');emptyOutput=([string]::IsNullOrWhiteSpace($text));stderrCategory='none';rawValuesRecorded=$false}
  if($privateError){$meta.stderrCategory=if($privateError -match 'Unauthorized|Forbidden'){'authorization'}elseif($privateError -match 'deadline|timeout|timed out'){'timeout'}elseif($privateError -match 'connect|refused|resolve|certificate'){'connection-or-certificate'}else{'other-redacted'}}
  $privateError=$null
  $transportDirectory=Join-Path ([IO.Path]::GetDirectoryName($Output)) 'transport'
  $null=New-Item -ItemType Directory -Path $transportDirectory -Force
  $meta|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $transportDirectory ([IO.Path]::GetFileName($Output)+'.'+$Stage+'.transport.json'))
  if($code -ne 0 -or $meta.emptyOutput){throw 'Kubernetes metadata transport failed; inspect safe transport metadata'}
  return ConvertFrom-PE019StatisticsJSON $text
 }finally{$process.Dispose();$text=$null}
}
foreach($ctx in @('kind-platform-lab','kind-core-platform-final-019')){
 $parameters=@('--context',$ctx,'--request-timeout=10s')
 if($ctx -eq 'kind-core-platform-final-019'){$parameters+=@('--kubeconfig',$targetConfig)}
 $nodes=Read-KubernetesJSON ($parameters+@('get','nodes','-o','json')) ($ctx+'-nodes')
 if($nodes.items.Count -ne 1){throw 'Resource snapshot requires both actual single-node clusters'}
 $nodeName=$nodes.items[0].metadata.name
 $stats=Read-KubernetesJSON ($parameters+@('get','--raw',"/api/v1/nodes/$nodeName/proxy/stats/summary")) ($ctx+'-statistics')
 if(-not $stats.node.fs.capacityBytes){throw 'Actual node resource statistics required'}
 $pods=Read-KubernetesJSON ($parameters+@('get','pods','-A','-o','json')) ($ctx+'-pods')
 $snapshots.Add(@{context=$ctx;nodeUID=$nodes.items[0].metadata.uid;nodeName=$nodeName;nodeCpu=$stats.node.cpu;nodeMemory=$stats.node.memory;kindDiskFreePercent=100*$stats.node.fs.availableBytes/$stats.node.fs.capacityBytes;nodeFilesystem=$stats.node.fs;imageFilesystem=$stats.node.runtime.imageFs;pods=@($stats.pods|ForEach-Object {@{namespace=$_.podRef.namespace;name=$_.podRef.name;uid=$_.podRef.uid;cpu=$_.cpu;memory=$_.memory;ephemeral=$_.'ephemeral-storage';containers=@($_.containers|ForEach-Object {@{name=$_.name;cpu=$_.cpu;memory=$_.memory;rootfs=$_.rootfs;logs=$_.logs}})}});states=@($pods.items|ForEach-Object {@{namespace=$_.metadata.namespace;name=$_.metadata.name;uid=$_.metadata.uid;created=$_.metadata.creationTimestamp;phase=$_.status.phase;reason=$_.status.reason;conditions=@($_.status.conditions|ForEach-Object {@{type=$_.type;status=$_.status;transition=$_.lastTransitionTime}});containers=@($_.status.containerStatuses|ForEach-Object {@{name=$_.name;ready=$_.ready;restarts=$_.restartCount;imageID=$_.imageID;lastReason=$_.lastState.terminated.reason;lastFinish=$_.lastState.terminated.finishedAt;currentReason=$_.state.terminated.reason;currentFinish=$_.state.terminated.finishedAt}})}})})
}
$os=Get-CimInstance Win32_OperatingSystem
$mem=Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
$disk=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$lm=@{}
$linux=docker exec core-platform-final-019-control-plane cat /proc/meminfo
if($LASTEXITCODE -ne 0){throw 'WSL memory observation failed'}
foreach($line in $linux){if($line -match '^(MemTotal|MemAvailable):\s+(\d+) kB'){$lm[$matches[1]]=[long]$matches[2]}}
if(-not $lm.MemTotal -or -not $lm.MemAvailable){throw 'Actual WSL memory required'}
$result=@{utc=[DateTime]::UtcNow.ToString('o');hostCpu=(Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'").PercentProcessorTime;hostMemoryTotalKiB=$os.TotalVisibleMemorySize;hostMemoryAvailableKiB=$mem.AvailableKBytes;hostMemoryFreePercent=100*[double]$mem.AvailableKBytes/[double]$os.TotalVisibleMemorySize;hostDiskFreePercent=100*[double]$disk.FreeSpace/[double]$disk.Size;WSLTotalKiB=$lm.MemTotal;WSLAvailableKiB=$lm.MemAvailable;WSLMemoryFreePercent=100*[double]$lm.MemAvailable/[double]$lm.MemTotal;clusters=$snapshots.ToArray();loadAcceptance=$false;secretValuesRecorded=$false}
$result|ConvertTo-Json -Depth 20|Set-Content -LiteralPath $Output -Encoding utf8
$result|Select-Object utc,hostMemoryFreePercent,WSLMemoryFreePercent,hostDiskFreePercent|ConvertTo-Json -Compress
