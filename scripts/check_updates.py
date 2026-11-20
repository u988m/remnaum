"""Compatibility entry point for legacy systemd units during an upgrade.

The app now delivers release announcements itself. Upgrading disables the old
timer, and this entry point remains safe if the previous installer ran first.
"""
