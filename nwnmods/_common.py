import platform


SYS = platform.system()  # Windows / Linux / Darwin
APP = "nwnmods"
STEAM_APPID_EE = "704450"
UA = "nwnmods/1.0 (personal mod manager)"


class Err(Exception):
    pass


MAX_ENTRIES = 10000
MAX_FILE_BYTES = 512 * 1024 * 1024  # 512 MiB per file
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB per archive
MAX_NAME_LEN = 255