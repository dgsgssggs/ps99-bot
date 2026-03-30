"""
obfuscate.py — Ofusca todos los archivos del bot
=================================================
Ejecuta este script UNA SOLA VEZ antes de subir a GitHub/Railway:
    python obfuscate.py

Genera la carpeta 'ps99-bot-obfuscated/' con todos los archivos
codificados. Railway y Python pueden ejecutarlos normalmente,
pero un humano no puede leer el código fuente.

Técnica: zlib (compresión) + base64 (encoding) + marshal (bytecode)
Nivel: dificulta enormemente la ingeniería inversa casual.
"""

import os
import base64
import zlib
import marshal
import py_compile
import tempfile
import shutil

# Archivos que NO se ofuscan (deben ser texto plano para funcionar)
SKIP_FILES = {
    'requirements.txt',
    '.env.example',
    'README.md',
    'Procfile',
    '.env',
}

# Carpeta de entrada y salida
SRC_DIR = '.'
OUT_DIR = 'ps99-bot-obfuscated'

# Plantilla del loader — ejecuta el código ofuscado
LOADER_TEMPLATE = """import base64,zlib,marshal
exec(marshal.loads(zlib.decompress(base64.b64decode({data!r}))))
"""

def obfuscate_file(src_path: str, dst_path: str):
    """
    Ofusca un archivo Python:
    1. Compila a bytecode (marshal)
    2. Comprime con zlib
    3. Codifica en base64
    4. Envuelve en un loader de una línea
    """
    # Lee el código fuente
    with open(src_path, 'r', encoding='utf-8') as f:
        source = f.read()

    # Compila a código objeto de Python
    code_obj = compile(source, src_path, 'exec')

    # Serializa el bytecode con marshal
    bytecode = marshal.dumps(code_obj)

    # Comprime con zlib (nivel máximo = 9)
    compressed = zlib.compress(bytecode, level=9)

    # Codifica en base64
    encoded = base64.b64encode(compressed).decode('ascii')

    # Genera el loader
    loader = f"import base64,zlib,marshal\nexec(marshal.loads(zlib.decompress(base64.b64decode('{encoded}'))))\n"

    # Escribe el archivo ofuscado
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    with open(dst_path, 'w', encoding='utf-8') as f:
        f.write(loader)

def copy_plain(src_path: str, dst_path: str):
    """Copia un archivo sin modificar (para no-.py o archivos especiales)."""
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    shutil.copy2(src_path, dst_path)

def main():
    # Limpia la carpeta de salida si existe
    if os.path.exists(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR)

    print(f"🔒 Ofuscando archivos en '{SRC_DIR}' → '{OUT_DIR}'\n")

    obfuscated = 0
    copied     = 0
    errors     = 0

    for root, dirs, files in os.walk(SRC_DIR):
        # Ignora __pycache__, .git y la propia carpeta de salida
        dirs[:] = [
            d for d in dirs
            if d not in ('__pycache__', '.git', OUT_DIR, 'ps99-bot-obfuscated')
        ]

        for filename in files:
            src_path = os.path.join(root, filename)
            rel_path = os.path.relpath(src_path, SRC_DIR)
            dst_path = os.path.join(OUT_DIR, rel_path)

            # Archivos a copiar sin modificar
            if filename in SKIP_FILES or not filename.endswith('.py'):
                copy_plain(src_path, dst_path)
                print(f"  📄 Copiado:     {rel_path}")
                copied += 1
                continue

            # Ofusca el archivo Python
            try:
                obfuscate_file(src_path, dst_path)
                print(f"  🔒 Ofuscado:    {rel_path}")
                obfuscated += 1
            except SyntaxError as e:
                print(f"  ❌ Error en {rel_path}: {e}")
                errors += 1
                # En caso de error, copia el original sin ofuscar
                copy_plain(src_path, dst_path)

    print(f"\n{'='*50}")
    print(f"✅ Ofuscados:  {obfuscated} archivos")
    print(f"📄 Copiados:   {copied} archivos")
    if errors:
        print(f"❌ Errores:    {errors} archivos (copiados sin ofuscar)")
    print(f"\n📦 Carpeta lista: '{OUT_DIR}/'")
    print("Sube el contenido de esa carpeta a GitHub/Railway.")

if __name__ == '__main__':
    main()
