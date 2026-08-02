# 1.0.2

Fix issue with repeated querying after a printer disconnect and reconnect.

# 1.0.1

Fix quite a few problems not identified during local testing:
1. Marlin returns "/???" for files that don't have a long name.
2. Marlin truncates long filenames which then fail the mimetype check.

Both of these cause the entire lookup to repeat, over and over, and never resolve.

# 1.0.0

Initial release.

- Automatic M33 fallback
- `LONG_FILENAME` support without `EXTENDED_M20`
- Automatic file list refresh
