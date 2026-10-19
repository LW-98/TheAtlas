# Deploys the single-page frontend to S3 with the Google Maps key injected from
# a local, gitignored file (maps-key.txt), so the key never lives in the repo.
# The committed index.html keeps GOOGLE_MAPS_KEY = ""; only the uploaded copy
# gets the real key. Then it invalidates the CloudFront cache.
#
# Usage:  pwsh scripts/deploy-frontend.ps1
$ErrorActionPreference = "Stop"

$root    = Split-Path $PSScriptRoot -Parent
$html    = Join-Path $root "frontend\index.html"
$keyFile = Join-Path $root "maps-key.txt"
$bucket  = "daily-timeguess-781729905947"
$dist    = "E3HVOXBADYCPDV"
$region  = "eu-west-2"

if (-not (Test-Path $keyFile)) { throw "maps-key.txt not found - create it with your Google Maps key." }
$key = (Get-Content -Raw $keyFile).Trim()

$content = (Get-Content -Raw $html).Replace('const GOOGLE_MAPS_KEY = "";', "const GOOGLE_MAPS_KEY = `"$key`";")
if ($content -notmatch [regex]::Escape($key)) { throw "Key injection failed - the placeholder line was not found in index.html." }

$tmp = Join-Path $env:TEMP "atlas-index.html"
[IO.File]::WriteAllText($tmp, $content, (New-Object System.Text.UTF8Encoding($false)))

aws s3 cp $tmp "s3://$bucket/web/index.html" --content-type "text/html; charset=utf-8" --cache-control "no-cache" --region $region
aws cloudfront create-invalidation --distribution-id $dist --paths "/" "/index.html" --query "Invalidation.Status" --output text
Remove-Item $tmp -ErrorAction SilentlyContinue
Write-Host "Frontend deployed with the Maps key injected (key kept out of the repo)."
