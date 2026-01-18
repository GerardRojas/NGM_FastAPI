# Script de instalación automática de Poppler para Windows
# Ejecutar como Administrador

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Instalador de Poppler para Windows  " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Verificar si ya está instalado
Write-Host "[1/4] Verificando si Poppler ya está instalado..." -ForegroundColor Yellow

$popplerInstalled = $false
try {
    $null = pdftoppm -v 2>&1
    $popplerInstalled = $true
    Write-Host "Poppler ya está instalado." -ForegroundColor Green
    Write-Host ""
} catch {
    Write-Host "Poppler no está instalado. Continuando..." -ForegroundColor Yellow
    Write-Host ""
}

if (-not $popplerInstalled) {
    # Instalación manual
    Write-Host "[2/4] Descargando Poppler..." -ForegroundColor Yellow

    $popplerUrl = "https://github.com/oschwartz10612/poppler-windows/releases/download/v24.08.0-0/Release-24.08.0-0.zip"
    $downloadPath = "$env:TEMP\poppler.zip"
    $extractPath = "C:\poppler"

    try {
        # Descargar
        Invoke-WebRequest -Uri $popplerUrl -OutFile $downloadPath -UseBasicParsing
        Write-Host "Descarga completada" -ForegroundColor Green
        Write-Host ""

        # Extraer
        Write-Host "[3/4] Extrayendo archivos..." -ForegroundColor Yellow
        if (Test-Path $extractPath) {
            Remove-Item $extractPath -Recurse -Force
        }
        Expand-Archive -Path $downloadPath -DestinationPath $extractPath -Force
        Write-Host "Archivos extraídos a: $extractPath" -ForegroundColor Green
        Write-Host ""

        # Agregar al PATH
        Write-Host "[4/4] Agregando al PATH del sistema..." -ForegroundColor Yellow
        $popplerBinPath = "$extractPath\poppler-24.08.0\Library\bin"

        # Obtener PATH actual
        $currentPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::Machine)

        # Verificar si ya está en el PATH
        if ($currentPath -notlike "*$popplerBinPath*") {
            $newPath = $currentPath + ";" + $popplerBinPath
            [Environment]::SetEnvironmentVariable("Path", $newPath, [EnvironmentVariableTarget]::Machine)
            Write-Host "PATH actualizado" -ForegroundColor Green
        } else {
            Write-Host "La ruta ya está en el PATH" -ForegroundColor Yellow
        }

        # Limpiar archivo temporal
        Remove-Item $downloadPath -Force

        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  INSTALACION COMPLETADA" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "Ubicacion: $popplerBinPath" -ForegroundColor Gray
        Write-Host ""

    } catch {
        Write-Host ""
        Write-Host "Error durante la instalacion:" -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
        Write-Host ""
        exit 1
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  VERIFICACION" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Cerrando y reabriendo la terminal..." -ForegroundColor Yellow
Write-Host "Luego ejecuta: pdftoppm -v" -ForegroundColor Yellow
Write-Host ""
