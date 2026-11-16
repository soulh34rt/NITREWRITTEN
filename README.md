# NITREWRITTEN (NWN Mod Manager)

Un gestor de mods profesional para **Neverwinter Nights** (Enhanced Edition y Diamond).

![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)
![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/platform-windows%20%7C%20linux%20%7C%20macos-lightgrey)

## Características

- **Instalación sencilla**: Soporta archivos .zip, .7z, .rar, .hak, .tlk, .mod y más.
- **Integración con The Neverwinter Vault**: Busca e instala mods directamente desde la aplicación.
- **Gestión de Conflictos**: Detecta y muestra archivos duplicados entre mods activos.
- **Perfiles**: Crea perfiles para cambiar rápidamente entre diferentes conjuntos de mods.
- **Copias de Seguridad**: Respalda tus partidas guardadas y archivos importantes con un clic.
- **Multiplataforma**: Funciona nativamente en Windows, Linux y macOS.

## Instalación

### Windows
Puedes descargar el ejecutable `.exe` desde la sección de [Releases](https://github.com/jgbed/NITREWRITTEN/releases).

O ejecutarlo desde el código fuente:
```bash
pip install -r requirements.txt
python nwnmods.py
```

### Linux (AppImage)
Descarga el archivo `.AppImage` desde [Releases](https://github.com/jgbed/NITREWRITTEN/releases), dale permisos de ejecución y lánzalo.

## Uso

La interfaz es intuitiva:
1. **Detectar**: Encuentra automáticamente tus instalaciones de NWN.
2. **Mods**: Instala archivos descargados o explora los instalados.
3. **Vault**: Navega por el catálogo de mods online más grande de NWN.
4. **Perfiles**: Guarda tus configuraciones favoritas.

## Requisitos
- Python 3.8 o superior.
- `tkinter` (incluido en la mayoría de distribuciones de Python).
- `Pillow` (para soporte extendido de imágenes en el catálogo).

## Licencia
Este proyecto está bajo la licencia MIT.
