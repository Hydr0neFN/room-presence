param([string]$bin, [string]$log)
$pw = (Select-String -Path C:\cyd-work\secrets.ini -Pattern 'ota_password\s*=\s*(.+)$').Matches[0].Groups[1].Value.Trim()
py -3.13 C:\Users\Ruanyouyi\Rpi4\room-presence\tools\espota_slow.py -i nldeskcyd.local -p 3232 --auth=$pw -d -f $bin *> $log
"EXIT $LASTEXITCODE" | Add-Content $log
