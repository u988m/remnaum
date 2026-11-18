"""Prepare the branding bind mount for the non-root application user."""

import os
import stat
from pathlib import Path

APP_UID = 1000
APP_GID = 1000


def prepare_branding_storage(root: Path) -> None:
    data = root / "data"
    data.mkdir(exist_ok=True)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    data_fd = os.open(data, flags)
    try:
        try:
            os.mkdir("branding", mode=0o755, dir_fd=data_fd)
        except FileExistsError:
            pass
        branding_fd = os.open("branding", flags, dir_fd=data_fd)
        try:
            os.fchown(branding_fd, APP_UID, APP_GID)
            try:
                image = os.stat("welcome.jpg", dir_fd=branding_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            if stat.S_ISREG(image.st_mode):
                os.chown("welcome.jpg", APP_UID, APP_GID, dir_fd=branding_fd, follow_symlinks=False)
        finally:
            os.close(branding_fd)
    finally:
        os.close(data_fd)


if __name__ == "__main__":
    prepare_branding_storage(Path(__file__).resolve().parents[1])
