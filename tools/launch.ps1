# Run with Windows PowerShell 5.1; the generated EXE contains no Python packages.
param([string]$Output)
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
if (!$Output) { $Output = Join-Path $project 'VBuild.exe' }
if (Test-Path -LiteralPath $Output) { throw "输出已存在，请先备份并移动：$Output" }
$parameters = New-Object System.CodeDom.Compiler.CompilerParameters
$parameters.GenerateExecutable = $true
$parameters.GenerateInMemory = $false
$parameters.OutputAssembly = $Output
$parameters.ReferencedAssemblies.AddRange(@('System.dll', 'System.Windows.Forms.dll', 'System.Core.dll', 'System.Drawing.dll', 'System.Web.Extensions.dll'))
$icon = Join-Path $project 'src\vbuild\icons\app.ico'
$parameters.CompilerOptions = '/target:winexe /win32icon:"' + $icon + '"'
Add-Type -Path (Join-Path $PSScriptRoot 'launch.cs') -CompilerParameters $parameters
Get-Item -LiteralPath $Output | Select-Object FullName,Length
