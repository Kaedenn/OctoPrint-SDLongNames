# coding=utf-8

"""
This plugin adds an M33 fallback for firmware that advertise
    Cap:LONG_FILENAME
but don't advertise
    Cap:EXTENDED_M20

This should result in proper filenames in the SD Card listing.
"""

from __future__ import absolute_import

import os
import threading
import time
from typing import Any

import octoprint.plugin
from octoprint.events import Events
from octoprint.filemanager import valid_file_type


class SDLongNamesPlugin(
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.EventHandlerPlugin,
    octoprint.plugin.ShutdownPlugin,
):
    """
    Adds an M33 fallback for firmware that advertises LONG_FILENAME but does
    not support EXTENDED_M20.

    OctoPrint already knows how to parse M33 responses and store them in the
    corresponding SDFileData.longname field. This plugin supplies the missing
    piece: issuing M33 once per unresolved short filename.
    """

    CAP_LONG_FILENAME = "LONG_FILENAME"
    CAP_EXTENDED_M20 = "EXTENDED_M20"

    def __init__(self) -> None:
        self._capabilities: dict[str, bool] = {}

        self._comm: Any | None = None
        self._lookup_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._state_lock = threading.RLock()
        self._lookup_complete = threading.Event()

        self._lookup_active = False
        self._lookup_response_seen = False
        self._pending_filename: str | None = None
        self._refresh_generation = 0

    # ----------------------------------------------------------------------
    # SettingsPlugin
    # ----------------------------------------------------------------------

    def get_settings_defaults(self):
        return {
            "enabled": True,
            # Small delay after "End file list" so OctoPrint can finish
            # processing and publishing the original M20 response.
            "start_delay": 0.25,
            # Stop waiting if a firmware response never arrives.
            "lookup_timeout": 10.0,
        }

    # ----------------------------------------------------------------------
    # Capability detection
    # ----------------------------------------------------------------------

    def on_firmware_capability_report(
        self,
        comm_instance,
        firmware_capabilities,
        *args,
        **kwargs,
    ):
        """
        Called after the complete M115 capability report has been received.
        """

        with self._state_lock:
            self._comm = comm_instance
            self._capabilities = dict(firmware_capabilities)

        long_filename = bool(
            firmware_capabilities.get(self.CAP_LONG_FILENAME, False)
        )
        extended_m20 = bool(
            firmware_capabilities.get(self.CAP_EXTENDED_M20, False)
        )

        if long_filename and not extended_m20:
            self._logger.info(
                "Firmware supports LONG_FILENAME but not EXTENDED_M20; "
                "M33 fallback enabled"
            )
        elif extended_m20:
            self._logger.info(
                "Firmware supports EXTENDED_M20; M33 fallback is not needed"
            )
        else:
            self._logger.info(
                "Firmware does not advertise usable long-filename support"
            )

    def _fallback_is_applicable(self) -> bool:
        if not self._settings.get_boolean(["enabled"]):
            return False

        with self._state_lock:
            long_filename = bool(
                self._capabilities.get(self.CAP_LONG_FILENAME, False)
            )
            extended_m20 = bool(
                self._capabilities.get(self.CAP_EXTENDED_M20, False)
            )

        return long_filename and not extended_m20

    # ----------------------------------------------------------------------
    # Received-line hook
    # ----------------------------------------------------------------------

    def on_gcode_received(self, comm_instance, line, *args, **kwargs):
        """
        Observe the end of an M20 listing and completion of M33 requests.

        This hook must return the original line so normal OctoPrint processing
        continues unchanged.
        """

        stripped = line.strip()
        lower = stripped.lower()

        with self._state_lock:
            self._comm = comm_instance

        if lower == "end file list":
            self._schedule_enrichment(comm_instance)

        with self._state_lock:
            if self._lookup_active:
                if self._looks_like_m33_response(stripped):
                    self._lookup_response_seen = True

                elif lower == "ok" and self._lookup_response_seen:
                    # OctoPrint has received both the M33 path and its
                    # acknowledgement. Allow the worker to issue the next M33.
                    self._lookup_complete.set()

        return line

    def _looks_like_m33_response(self, line: str) -> bool:
        """
        Match the same basic condition used by OctoPrint's M33 response parser:
        an absolute path with a recognized machine-code extension.

        The pending-lookup check is performed by the caller, preventing random
        printer messages from being interpreted as M33 responses.
        """

        if not line or line.lower() == "ok":
            return False

        if not line.startswith("/") or "//" in line or "\0" in line:
            return False

        _, extension = os.path.splitext(line.lower())
        return bool(extension and valid_file_type(line.lower(), "machinecode"))

    # ----------------------------------------------------------------------
    # Lookup scheduling and worker
    # ----------------------------------------------------------------------

    def _schedule_enrichment(self, comm_instance) -> None:
        if not self._fallback_is_applicable():
            return

        with self._state_lock:
            self._refresh_generation += 1
            generation = self._refresh_generation

            if self._lookup_thread is not None and self._lookup_thread.is_alive():
                self._logger.debug(
                    "An M33 enrichment pass is already active; "
                    "the newer SD listing will be handled afterward"
                )
                return

            self._stop_event.clear()
            self._lookup_thread = threading.Thread(
                target=self._enrich_after_delay,
                args=(comm_instance, generation),
                name="SDLongNames-M33",
                daemon=True,
            )
            self._lookup_thread.start()

    def _enrich_after_delay(self, comm_instance, generation: int) -> None:
        delay = max(
            0.0,
            float(self._settings.get_float(["start_delay"])),
        )

        if self._stop_event.wait(delay):
            return

        try:
            self._enrich_sd_files(comm_instance, generation)
        except (TimeoutError, ConnectionError, BrokenPipeError, OSError) as exc:
            self._logger.warning("M33 enrichment failed: %s", exc)
        finally:
            with self._state_lock:
                self._lookup_active = False
                self._lookup_response_seen = False
                self._pending_filename = None
                self._lookup_complete.clear()
                self._lookup_thread = None

                newer_listing_exists = generation != self._refresh_generation
                next_generation = self._refresh_generation

            if (
                newer_listing_exists
                and not self._stop_event.is_set()
                and self._fallback_is_applicable()
            ):
                self._schedule_enrichment(comm_instance)

    def _enrich_sd_files(self, comm_instance, generation: int) -> None:
        unresolved = self._get_unresolved_files(comm_instance)

        if not unresolved:
            self._logger.debug("No unresolved SD filenames found")
            return

        self._logger.info(
            "Resolving %d SD filename%s with M33",
            len(unresolved),
            "" if len(unresolved) == 1 else "s",
        )

        timeout = max(
            1.0,
            float(self._settings.get_float(["lookup_timeout"])),
        )

        resolved = 0

        for filename in unresolved:
            if self._stop_event.is_set():
                break

            with self._state_lock:
                if generation != self._refresh_generation:
                    self._logger.debug(
                        "SD listing changed during M33 enrichment; "
                        "abandoning the old lookup pass"
                    )
                    break

                self._pending_filename = filename
                self._lookup_active = True
                self._lookup_response_seen = False
                self._lookup_complete.clear()

            self._logger.debug("Resolving SD filename with M33: %s", filename)

            # Use OctoPrint's command path rather than writing to the serial
            # connection directly. Its existing _gcode_M33_sending handler
            # records the short filename, and its receive parser stores the
            # returned long filename in SDFileData.
            self._printer.commands(
                [f"M33 {filename}"],
                tags={
                    "source:plugin",
                    "plugin:sdlongnames",
                    "trigger:sdlongnames.m33",
                },
            )

            completed = self._lookup_complete.wait(timeout)

            with self._state_lock:
                response_seen = self._lookup_response_seen
                self._lookup_active = False
                self._lookup_response_seen = False
                self._pending_filename = None
                self._lookup_complete.clear()

            if not completed:
                self._logger.warning(
                    "Timed out waiting for M33 response for %s",
                    filename,
                )
                continue

            if response_seen:
                resolved += 1
            else:
                self._logger.warning(
                    "M33 completed without a usable long filename for %s",
                    filename,
                )

        self._logger.info(
            "Resolved %d of %d SD filename%s",
            resolved,
            len(unresolved),
            "" if len(unresolved) == 1 else "s",
        )

        # M33 updates MachineCom._sdFiles internally, but OctoPrint's normal
        # SD-list callback was already sent when M20 completed. Publish the
        # enriched list so the Files panel receives the new long names.
        self._publish_enriched_list(comm_instance)

    def _get_unresolved_files(self, comm_instance) -> list[str]:
        """
        Take a snapshot of OctoPrint's parsed SD file table.

        _sdFiles is a private MachineCom attribute, but it is the same table
        OctoPrint's built-in M33 parser updates. This plugin intentionally
        targets OctoPrint 1.11.x's communication implementation.
        """

        sd_files = getattr(comm_instance, "_sdFiles", None)
        if not isinstance(sd_files, dict):
            self._logger.error(
                "MachineCom._sdFiles is unavailable; "
                "this OctoPrint version is not compatible"
            )
            return []

        unresolved: list[str] = []

        for filename, data in list(sd_files.items()):
            longname = getattr(data, "longname", None)

            if not longname and valid_file_type(filename.lower(), "machinecode"):
                unresolved.append(filename)

        # Stable ordering makes logs and serial behavior predictable.
        unresolved.sort(key=str.casefold)
        return unresolved

    def _publish_enriched_list(self, comm_instance) -> None:
        """
        Re-run OctoPrint's SD-file callback after long names have been added.
        """

        try:
            callback = getattr(comm_instance, "_callback")
            get_sd_files = getattr(comm_instance, "getSdFiles")
            callback.on_comm_sd_files(get_sd_files())
        except (AttributeError, TypeError):
            self._logger.exception(
                "Could not publish the enriched SD file list; "
                "the long names were resolved internally, but the browser "
                "may not show them until its next refresh"
            )

    # ----------------------------------------------------------------------
    # Connection and shutdown cleanup
    # ----------------------------------------------------------------------

    def on_event(self, event, payload):
        if event in {
            Events.DISCONNECTED,
            Events.ERROR,
        }:
            with self._state_lock:
                self._capabilities.clear()
                self._comm = None
                self._refresh_generation += 1
                self._lookup_active = False
                self._lookup_response_seen = False
                self._pending_filename = None
                self._lookup_complete.set()

    def on_shutdown(self):
        self._stop_event.set()
        self._lookup_complete.set()

        thread = self._lookup_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    # ----------------------------------------------------------------------
    # Software Update hook
    # ----------------------------------------------------------------------

    def get_update_information(self):
        return {
            "sdlongnames": {
                "displayName": "SDLongNames Plugin",
                "displayVersion": self._plugin_version,
                "type": "github_release",
                "user": "Kaedenn",
                "repo": "OctoPrint-SDLongNames",
                "current": self._plugin_version,
                "pip": (
                    "https://github.com/Kaedenn/"
                    "OctoPrint-SDLongNames/archive/{target_version}.zip"
                ),
            }
        }


__plugin_name__ = "SDLongNames Plugin"
__plugin_pythoncompat__ = ">=3,<4"

def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = SDLongNamesPlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.comm.protocol.firmware.capability_report": (
            __plugin_implementation__.on_firmware_capability_report
        ),
        "octoprint.comm.protocol.gcode.received": (
            __plugin_implementation__.on_gcode_received
        ),
        "octoprint.plugin.softwareupdate.check_config": (
            __plugin_implementation__.get_update_information
        ),
    }
# vim: set ts=4 sts=4 sw=4
