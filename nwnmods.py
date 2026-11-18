"""NITREWRITTEN mod manager entry point.

Thin wrapper around the ``nwnmods`` package. All logic lives in the package;
running this script delegates to :func:`nwnmods.cli.main`.
"""

from nwnmods.cli import main

if __name__ == "__main__":
    main()
