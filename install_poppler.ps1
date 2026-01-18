# Script de instalación automática de Poppler para Windows
# Ejecutar como Administrador

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Instalador de Poppler para Windows  " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Verificar si ya está instalado
Write-Host "[1/5] Verificando si Poppler ya está instalado..." -ForegroundColor Yellow

try {
    $pdfToPpmVersion = pdftoppm -v 2>&1
    if ($pdfToPpmVersion -match "poppler") {
        Write-Host "✅ Poppler ya está instalado:" -ForegroundColor Green
        Write-Host $pdfToPpmVersion -ForegroundColor Gray
        Write-Host ""
        $response = Read-Host "¿Desea reinstalar? (s/n)"
        if ($response -ne "s") {
            Write-Host "Instalación cancelada." -ForegroundColor Yellow
            exit
        }
    }
} catch {
    Write-Host "❌ Poppler no está instalado. Continuando con la instalación..." -ForegroundColor Yellow
}

Write-Host ""

# Verificar Chocolatey
Write-Host "[2/5] Verificando Chocolatey..." -ForegroundColor Yellow

try {
    $chocoVersion = choco --version 2>&1
    Write-Host "✅ Chocolatey instalado: $chocoVersion" -ForegroundColor Green
    Write-Host ""
    Write-Host "[3/5] Instalando Poppler con Chocolatey..." -ForegroundColor Yellow
    choco install poppler -y

    Write-Host ""
    Write-Host "✅ Poppler instalado exitosamente!" -ForegroundColor Green

} catch {
    Write-Host "❌ Chocolatey no está instalado." -ForegroundColor Red
    Write-Host ""
    Write-Host "Instalando Poppler manualmente..." -ForegroundColor Yellow
    Write-Host ""

    # Instalación manual
    Write-Host "[3/5] Descargando Poppler..." -ForegroundColor Yellow

    $popplerUrl = "https://github.com/oschwartz10612/poppler-windows/releases/download/v24.08.0-0/Release-24.08.0-0.zip"
    $downloadPath = "$env:TEMP\poppler.zip"
    $extractPath = "C:\poppler"

    try {
        # Descargar
        Invoke-WebRequest -Uri $popplerUrl -OutFile $downloadPath -UseBasicParsing
        Write-Host "✅ Descarga completada" -ForegroundColor Green

        # Extraer
        Write-Host "[4/5] Extrayendo archivos..." -ForegroundColor Yellow
        Expand-Archive -Path $downloadPath -DestinationPath $extractPath -Force
        Write-Host "✅ Archivos extraídos a: $extractPath" -ForegroundColor Green

        # Agregar al PATH
        Write-Host "[5/5] Agregando al PATH del sistema..." -ForegroundColor Yellow
        $popplerBinPath = "$extractPath\poppler-24.08.0\Library\bin"

        # Obtener PATH actual
        $currentPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::Machine)

        # Verificar si ya está en el PATH
        if ($currentPath -notlike "*$popplerBinPath*") {
            $newPath = $currentPath + ";" + $popplerBinPath
            [Environment]::SetEnvironmentVariable("Path", $newPath, [EnvironmentVariableTarget]::Machine)
            Write-Host "✅ PATH actualizado" -ForegroundColor Green
        } else {
            Write-Host "⚠️  La ruta ya está en el PATH" -ForegroundColor Yellow
        }

        # Limpiar archivo temporal
        Remove-Item $downloadPath -Force

        Write-Host ""
        Write-Host "========================================" -ForegroundColor Green
        Write-Host "  ✅ INSTALACIÓN COMPLETADA" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "Ubicación: $popplerBinPath" -ForegroundColor Gray

    } catch {
        Write-Host ""
        Write-Host "❌ Error durante la instalación:" -ForegroundColor Red
        Write-Host $_.Exception.Message -ForegroundColor Red
        Write-Host ""
        Write-Host "Por favor, instale manualmente siguiendo las instrucciones en POPPLER_SETUP.md" -ForegroundColor Yellow
        exit 1
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  VERIFICACIÓN" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Para verificar que Poppler está instalado correctamente:" -ForegroundColor Yellow
Write-Host ""
Write-Host "1. Cierra esta ventana" -ForegroundColor Gray
Write-Host "2. Abre una NUEVA terminal (PowerShell o CMD)" -ForegroundColor Gray
Write-Host "3. Ejecuta: pdftoppm -v" -ForegroundColor Gray
Write-Host ""
Write-Host "Deberías ver la versión de Poppler instalada." -ForegroundColor Gray
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Read-Host "Presiona Enter para salir"
