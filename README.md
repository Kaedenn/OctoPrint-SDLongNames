# OctoPrint-SDLongNames

Some Marlin firmware (including Creality's Marlin 2.0.8.2) reports
`Cap:LONG_FILENAME:1` but does not implement `Cap:EXTENDED_M20`. OctoPrint
therefore only displays 8.3 DOS filenames for SD card files, even though
long filenames are supported.

SDLongNames performs an ordinary M20 listing, then resolves each unresolved
short filename using M33, updating OctoPrint's file list transparently.

The short filename remains the operational path used for printing and file
operations.

On firmware with `Cap:EXTENDED_M20`, OctoPrint already retrieves long filenames
natively, so this plugin remains inactive.

## Requirements

- OctoPrint 1.11.x
- Python 3.10 or later
- Firmware supporting `M33`
- `Cap:LONG_FILENAME:1`
- No usable `Cap:EXTENDED_M20`

## Configuration

- `enabled`: enables the fallback
- `start_delay`: delay after an SD listing before beginning M33 resolution
- `lookup_timeout`: maximum wait for each M33 response

## Troubleshooting

If the plugin appears to have no effect:

* Verify your firmware reports `Cap:LONG_FILENAME:1` (send `M115` from OctoPrint's Terminal tab if you're unsure).
* Verify it does **not** report `Cap:EXTENDED_M20`.
* Refresh the SD file list after connecting.
* Check `octoprint.log` for `SDLongNames` messages.

Bug reports are welcome! Please include:

* Your `octoprint.log`
* Your printer model
* Your firmware version (the output of `M115`)
* The relevant Terminal output showing the `M20` and `M33` commands and responses

## License

Copyright (C) 2026 Kaedenn A. D. N.

This project is licensed under the GNU Affero General Public License v3.0 or
later (AGPL-3.0-or-later). See the `LICENSE` file for the full text, or visit:

https://www.gnu.org/licenses/agpl-3.0.html
