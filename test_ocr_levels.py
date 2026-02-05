"""
Script para probar el sistema de 3 niveles de extraccion de texto.
Ejecutar: python test_ocr_levels.py <archivo.pdf o imagen>
"""

import sys
import os
import io

# Agregar el directorio al path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_pdfplumber(file_path: str):
    """Prueba NIVEL 1: pdfplumber"""
    import pdfplumber

    print("\n" + "="*60)
    print("NIVEL 1: pdfplumber (PDF con texto nativo)")
    print("="*60)

    if not file_path.lower().endswith('.pdf'):
        print("[SKIP] No es PDF, saltando pdfplumber")
        return False, None

    try:
        with open(file_path, 'rb') as f:
            file_content = f.read()

        with pdfplumber.open(io.BytesIO(file_content)) as pdf:
            all_text = []
            print(f"[INFO] PDF tiene {len(pdf.pages)} pagina(s)")

            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text() or ""
                all_text.append(page_text)
                print(f"[INFO] Pagina {i+1}: {len(page_text.strip())} caracteres")

            combined_text = "\n\n".join(all_text)
            total_chars = len(combined_text.strip())

            print(f"[INFO] Total: {total_chars} caracteres")

            if total_chars >= 100:
                print("[EXITO] pdfplumber extrajo texto suficiente!")
                print("\n--- Primeros 500 caracteres ---")
                print(combined_text[:500])
                print("--- Fin preview ---\n")
                return True, combined_text
            else:
                print(f"[FALLO] Texto insuficiente ({total_chars} < 100)")
                return False, None

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        return False, None


def test_paddleocr(file_path: str):
    """Prueba NIVEL 2: PaddleOCR"""
    from paddleocr import PaddleOCR
    from PIL import Image
    import numpy as np

    print("\n" + "="*60)
    print("NIVEL 2: PaddleOCR (OCR local)")
    print("="*60)

    try:
        # Si es PDF, convertir a imagen primero
        if file_path.lower().endswith('.pdf'):
            print("[INFO] Convirtiendo PDF a imagen...")
            from pdf2image import convert_from_path
            import platform

            poppler_path = None
            if platform.system() == "Windows":
                poppler_path = r'C:\poppler\poppler-24.08.0\Library\bin'

            images = convert_from_path(file_path, dpi=200, poppler_path=poppler_path)
            print(f"[INFO] PDF convertido a {len(images)} imagen(es)")

            # Usar primera pagina para prueba
            image = images[0]
        else:
            image = Image.open(file_path)

        if image.mode != 'RGB':
            image = image.convert('RGB')

        image_np = np.array(image)
        print(f"[INFO] Imagen: {image.width}x{image.height} px")

        print("[INFO] Inicializando PaddleOCR (puede tardar la primera vez)...")
        ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)

        print("[INFO] Ejecutando OCR...")
        result = ocr.ocr(image_np, cls=True)

        if not result or not result[0]:
            print("[FALLO] No se detecto texto")
            return False, None

        # Extraer texto
        all_text = []
        total_confidence = 0
        num_detections = 0

        for detection in result[0]:
            if detection and len(detection) >= 2:
                text = detection[1][0]
                confidence = detection[1][1]
                all_text.append(text)
                total_confidence += confidence
                num_detections += 1

        combined_text = "\n".join(all_text)
        avg_confidence = total_confidence / num_detections if num_detections > 0 else 0

        print(f"[INFO] Detecciones: {num_detections}")
        print(f"[INFO] Caracteres: {len(combined_text)}")
        print(f"[INFO] Confianza promedio: {avg_confidence:.2%}")

        if len(combined_text) >= 50 and avg_confidence >= 0.85:
            print("[EXITO] PaddleOCR extrajo texto con alta confianza!")
            print("\n--- Primeros 500 caracteres ---")
            print(combined_text[:500])
            print("--- Fin preview ---\n")
            return True, combined_text
        else:
            if avg_confidence < 0.85:
                print(f"[FALLO] Confianza baja ({avg_confidence:.2%} < 85%)")
            else:
                print(f"[FALLO] Texto insuficiente ({len(combined_text)} < 50)")
            return False, None

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return False, None


def test_vision_ready(file_path: str):
    """Verifica que el archivo esta listo para Vision (NIVEL 3)"""
    print("\n" + "="*60)
    print("NIVEL 3: Vision (fallback)")
    print("="*60)
    print("[INFO] Si los niveles anteriores fallaron, se usaria Vision")
    print("[INFO] Esto enviaria la imagen a GPT-4o-mini (fast) o GPT-4o (heavy)")
    print("[INFO] Para probar Vision, usar el endpoint real /expenses/parse-receipt")


def main():
    if len(sys.argv) < 2:
        print("Uso: python test_ocr_levels.py <archivo.pdf o imagen>")
        print("")
        print("Ejemplos:")
        print("  python test_ocr_levels.py factura.pdf")
        print("  python test_ocr_levels.py receipt.jpg")
        print("  python test_ocr_levels.py invoice.png")
        sys.exit(1)

    file_path = sys.argv[1]

    if not os.path.exists(file_path):
        print(f"Error: Archivo no encontrado: {file_path}")
        sys.exit(1)

    print(f"\nProbando archivo: {file_path}")
    print(f"Tamano: {os.path.getsize(file_path) / 1024:.1f} KB")

    # NIVEL 1: pdfplumber
    success, text = test_pdfplumber(file_path)
    if success:
        print("\n" + "="*60)
        print("RESULTADO: NIVEL 1 (pdfplumber) - EXITO")
        print("El archivo se procesaria con texto extraido directamente")
        print("="*60)
        return

    # NIVEL 2: PaddleOCR
    success, text = test_paddleocr(file_path)
    if success:
        print("\n" + "="*60)
        print("RESULTADO: NIVEL 2 (PaddleOCR) - EXITO")
        print("El archivo se procesaria con OCR local")
        print("="*60)
        return

    # NIVEL 3: Vision
    test_vision_ready(file_path)
    print("\n" + "="*60)
    print("RESULTADO: NIVEL 3 (Vision) - FALLBACK")
    print("El archivo se enviaria a GPT Vision")
    print("="*60)


if __name__ == "__main__":
    main()
