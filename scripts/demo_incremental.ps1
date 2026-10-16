<#
.SYNOPSIS
  Demostración de respaldo físico COMPLETO + INCREMENTAL con PostgreSQL 17
  (pg_basebackup --incremental, pg_combinebackup y pg_verifybackup), y
  restauración real levantando el clúster reconstruido en el puerto 5433.

.EXAMPLE
  $env:PGPASSWORD = 'postgres'
  .\scripts\demo_incremental.ps1
#>
param(
    [string]$PgBin   = 'C:\Program Files\PostgreSQL\17\bin',
    [string]$WorkDir = (Join-Path $env:TEMP 'pg_incremental_demo'),
    [string]$PgUser  = 'postgres',
    [int]$RestorePort = 5433
)
$ErrorActionPreference = 'Stop'
$env:PGOPTIONS = '-c client_min_messages=warning'   # oculta avisos NOTICE
$psql = Join-Path $PgBin 'psql.exe'
function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Sql($q, $db = 'postgres', $port = 5432) {
    & $psql -h localhost -p $port -U $PgUser -d $db -v ON_ERROR_STOP=1 -tA -c $q
    if ($LASTEXITCODE) { throw "psql falló: $q" }
}
function Run($exe, [string[]]$argv) {
    & (Join-Path $PgBin $exe) @argv
    if ($LASTEXITCODE) { throw "$exe falló (código $LASTEXITCODE)" }
}

if (Test-Path $WorkDir) { Remove-Item -Recurse -Force $WorkDir }
New-Item -ItemType Directory -Force $WorkDir | Out-Null
$full = Join-Path $WorkDir 'full'; $incr = Join-Path $WorkDir 'incr1'; $combined = Join-Path $WorkDir 'combined'

Step '0. Requisito: summarize_wal = on (permite backups incrementales)'
$prev = Sql 'SHOW summarize_wal'
Sql 'ALTER SYSTEM SET summarize_wal = on' | Out-Null
Sql 'SELECT pg_reload_conf()' | Out-Null
Start-Sleep -Seconds 2

try {
    Step '1. Datos iniciales'
    Sql 'DROP DATABASE IF EXISTS demo_incremental' | Out-Null
    Sql 'CREATE DATABASE demo_incremental' | Out-Null
    Sql 'CREATE TABLE ventas (id serial PRIMARY KEY, monto numeric, fecha timestamptz DEFAULT now()); INSERT INTO ventas (monto) SELECT random()*100 FROM generate_series(1,100000);' 'demo_incremental' | Out-Null
    Write-Host ("Filas en ventas: " + (Sql 'SELECT count(*) FROM ventas' 'demo_incremental'))

    Step '2. Respaldo FÍSICO COMPLETO (pg_basebackup)'
    Run 'pg_basebackup.exe' @('-h','localhost','-U',$PgUser,'-D',$full,'-c','fast','-P')

    Step '3. Cambios después del completo (+50 000 filas)'
    Sql 'INSERT INTO ventas (monto) SELECT random()*100 FROM generate_series(1,50000)' 'demo_incremental' | Out-Null
    $expected = Sql 'SELECT count(*) FROM ventas' 'demo_incremental'
    Write-Host "Filas en ventas: $expected"
    Sql 'SELECT pg_switch_wal()' | Out-Null
    Start-Sleep -Seconds 3

    Step '4. Respaldo INCREMENTAL (solo bloques modificados)'
    Run 'pg_basebackup.exe' @('-h','localhost','-U',$PgUser,'-D',$incr,'-c','fast','-P','--incremental',(Join-Path $full 'backup_manifest'))

    $sizeFull = (Get-ChildItem $full -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
    $sizeIncr = (Get-ChildItem $incr -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
    Write-Host ("Tamaño completo:    {0,8:N1} MB" -f $sizeFull) -ForegroundColor Yellow
    Write-Host ("Tamaño incremental: {0,8:N1} MB" -f $sizeIncr) -ForegroundColor Yellow

    Step '5. Reconstruir con pg_combinebackup (completo + incremental)'
    Run 'pg_combinebackup.exe' @($full, $incr, '-o', $combined)

    Step '6. Verificar integridad con pg_verifybackup'
    Run 'pg_verifybackup.exe' @($combined)

    Step "7. Restaurar: levantar el clúster reconstruido en el puerto $RestorePort"
    # Desactivar la configuración copiada que no aplica a la copia.
    Add-Content (Join-Path $combined 'postgresql.auto.conf') "summarize_wal = off`nport = $RestorePort"
    Run 'pg_ctl.exe' @('-D', $combined, '-l', (Join-Path $WorkDir 'restore.log'), '-w', 'start')
    try {
        $restored = Sql 'SELECT count(*) FROM ventas' 'demo_incremental' $RestorePort
        Write-Host "Filas en la copia restaurada: $restored (esperado: $expected)" -ForegroundColor Green
        if ($restored -ne $expected) { throw 'La restauración no coincide' }
        Write-Host 'RESTAURACIÓN VERIFICADA ✔' -ForegroundColor Green
    } finally {
        Run 'pg_ctl.exe' @('-D', $combined, '-m', 'fast', '-w', 'stop')
    }
} finally {
    Step 'Limpieza: dejar el servidor como estaba'
    Sql "ALTER SYSTEM SET summarize_wal = $prev" | Out-Null
    Sql 'SELECT pg_reload_conf()' | Out-Null
    Sql 'DROP DATABASE IF EXISTS demo_incremental' | Out-Null
    Write-Host "summarize_wal restaurado a '$prev'. Archivos del demo en: $WorkDir"
}
