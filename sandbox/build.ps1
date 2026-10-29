# Build image sandbox (Windows): .\sandbox\build.ps1 [-Tag mep-sandbox:latest]
param([string]$Tag = "mep-sandbox:latest")
$ErrorActionPreference = "Stop"
docker build -f "$PSScriptRoot/image/Dockerfile" -t $Tag "$PSScriptRoot"
