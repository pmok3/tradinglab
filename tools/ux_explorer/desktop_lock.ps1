[CmdletBinding()]
param(
    [string]$MutexName = "Local\TradingLab.UxExplorer"
)

$ErrorActionPreference = "Stop"
$mutex = [System.Threading.Mutex]::new($false, $MutexName)
$acquired = $false
try {
    try {
        $acquired = $mutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $acquired = $true
    }
    if (-not $acquired) {
        [Console]::Error.WriteLine("TradingLab UX desktop is already locked.")
        exit 2
    }
    [Console]::Out.WriteLine("LOCKED")
    [Console]::Out.Flush()
    [void][Console]::In.ReadLine()
}
finally {
    if ($acquired) {
        try {
            $mutex.ReleaseMutex()
        }
        catch [System.ApplicationException] {
            # The OS also releases ownership if the helper process is torn down.
        }
    }
    $mutex.Dispose()
}
