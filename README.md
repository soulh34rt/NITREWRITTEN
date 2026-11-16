# NITREWRITTEN (NWN Mod Manager)

A professional mod manager for Neverwinter Nights (Enhanced Edition and Diamond).

![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)
![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/platform-windows%20%7C%20linux%20%7C%20macos-lightgrey)

## Features

1. Easy Installation: Supports .zip, .7z, .rar, .hak, .tlk, .mod and more formats.
2. Neverwinter Vault Integration: Search and install mods directly from the application.
3. Conflict Management: Detects and displays duplicate files between active mods.
4. Profiles: Create profiles to quickly switch between different mod sets.
5. Backups: Backup your save games and characters with a single click.
6. Cross-platform: Native support for Windows, Linux, and macOS.

## Installation

### Windows
Download the NITREWRITTEN.exe executable from the Releases section.

Alternatively, run from source:
```bash
pip install -r requirements.txt
python nwnmods.py
```

### Linux (AppImage)
Download the .AppImage file from Releases, grant execution permissions, and run it.

## Usage

The interface is designed to be intuitive:
1. Detect: Automatically finds your NWN installations.
2. Mods: Install downloaded mods or manage existing ones.
3. Vault: Browse the largest online NWN mod catalog.
4. Profiles: Save and load your favorite configurations.

## Requirements
1. Python 3.8 or higher.
2. tkinter (usually included with Python).
3. Pillow (optional, for extended image support in the catalog).

## License
This project is licensed under the MIT License.
