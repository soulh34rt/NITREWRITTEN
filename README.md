![NITREWRITTEN Banner](resources/banner-readme-v2.png)

# NITREWRITTEN
### The definitive Mod Manager for Neverwinter Nights

NITREWRITTEN was created with a single purpose, to give Linux users a reliable, modern mod manager for Neverwinter Nights (Enhanced Edition and Diamond). It runs natively on Linux and is also available for Windows.

Built with a focus on simplicity and reliability, it keeps your game clean while offering advanced features for power users.

---

## 1. KEY FEATURES

1. **Streamlined Installation**: Drop any archive (.zip, .7z, .rar, .tar.gz) or individual game file (.hak, .tlk, .mod) and let the manager handle the directory structure automatically. 7-Zip is bundled — no external tools needed.
2. **Neverwinter Vault Integration**: Browse, search, and download thousands of projects directly within the app, complete with images and detailed descriptions.
3. **Advanced Conflict Engine**: Identify overlapping files between different mods to prevent game instability and ensure the correct content loads.
4. **Safe Clean Up**: Preview and move unmanaged files to a recovery folder (`.nwnmods/trash/`). Nothing is ever deleted without confirmation. Saves and characters are protected by default.
5. **Dark Mode & Modern UI**: A clean, responsive interface with full support for Dark Mode.
6. **Robust Backup System**: One-click character and save-game backups.
7. **Multi-Platform Native Performance**: Native Linux support (AppImage) plus Windows executable.

### Additional features

- **Save manager per mod**: switch, archive and restore save folders without ever deleting them.
- **Dependencies**: declare `requires` between mods; enable/disable checks them (`--force` to override).
- **Adopt existing installs**: register files you installed by hand, snapshot the original game files and restore them later.
- **Mod metadata**: notes, rating, author, version and Vault URL per mod, with search.
- **Groups**: organize mods into groups and enable/disable them together.
- **BIK to WBM conversion**: converts BIK movies for Enhanced Edition using FFmpeg (optional).
- **Steam Workshop**: detect and import EE Workshop subscriptions.
- **Export / import**: share individual mods or whole profiles as files.
- **Folder aliases**: redirect content folders (e.g. `hak`) to custom locations.
- **Per-installation settings**: separate preferences per detected game installation.
- **Character explorer & portraits**: browse `localvault` characters and portraits.
- **Playtime log**: tracks your play time per mod (recovered after a crash).
- **NWN log review**: after launching the game, errors found in the NWN log are reported.
- **Game start screen**: change the NWN:EE main menu background (`gui_pre_bknd3`) from Settings; the previous image is always kept in `.nwnmods/trash/`.
- **App splash screen**: optional image shown while the app starts (configure it in Settings or with the `splash` command).
- **Basic mode**: a minimal Install / Play / Quit launcher (switch to Advanced anytime).

---

## 2. QUICK START

### Linux Users (AppImage)
1. Download `NITREWRITTEN-x86_64.AppImage` from the [Releases](https://github.com/soulh34rt/NITREWRITTEN/releases) page.
2. Make it executable: `chmod +x NITREWRITTEN-x86_64.AppImage`.
3. Run it. No installation required.

### Windows Users
1. Download `NITREWRITTEN.exe` from the [Releases](https://github.com/soulh34rt/NITREWRITTEN/releases) page.
2. Run the executable. No installation required.

---

## 3. DEVELOPER SETUP (Source Code)

If you prefer to run from source or contribute:

1. Clone the repository.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Launch the application:
   ```bash
   python nwnmods.py
   ```

---

## 4. COMMAND LINE INTERFACE

Everything the GUI does is also available from the terminal (`python nwnmods.py <command>` or `python -m nwnmods <command>`):

| Command | Description |
|---------|-------------|
| `gui` | Launch the graphical interface (default) |
| `detect` | Show detected game installations |
| `install <archive>` | Install a mod archive (`--name`, `--force`) |
| `enable` / `disable` / `remove` | Manage installed mods (`--force` bypasses dependency checks) |
| `list` / `conflicts` / `search` | Inspect the library |
| `backup` / `restore` | Backup and restore saves, characters and vaults |
| `launch` | Start the game (records playtime) |
| `adopt` / `snapshot` / `restore-original` / `changed` / `unmanaged` | Manage pre-existing installs and the original snapshot |
| `note` / `rate` / `meta` | Edit mod metadata |
| `group` | Create and manage mod groups |
| `saves` | List, switch, archive or restore save sets per mod |
| `startscreen` | Show, set or restore the game start screen (`status`, `set <image>`, `restore`) |
| `splash` | Configure the app splash screen (`status`, `set <image>`, `ms <n>`, `off`) |
| `chars` / `portraits` | Browse characters and portraits |
| `playtime` | Show playtime report per mod |
| `workshop list` / `workshop import` | Steam Workshop items (EE) |
| `convert-bik` | Convert BIK movies to WBM (requires FFmpeg) |
| `export` / `import` | Export or import a mod as a file |
| `export-profile` / `import-profile` | Export or import a profile |
| `alias` | Manage folder aliases |
| `extmap` | Show or reset the extension map |

Use `--help` on any command for details.

---

## 5. CONTRIBUTING

Contributions are welcome! If you find a bug or have a feature request, please open an issue or submit a pull request.

---

*NITREWRITTEN is an independent project and is not affiliated with Beamdog, Bioware or Wizards of the Coast.*

## Credits & Licenses

- 7-Zip is used for archive extraction (LGPL). See `resources/tools/License.txt`.
- 7-Zip homepage: https://www.7-zip.org/
