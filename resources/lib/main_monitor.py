import json

import xbmc
import xbmcaddon

from resources.lib import live_sync, oauth, sync_orchestrator
from resources.lib.player_monitor import PlayerMonitor
from resources.lib.timer import Timer


# Fixed, not user-configurable -- a too-low interval here is a footgun (needless
# load on both the Kodi library JSON-RPC calls and the MDBList API), and there's
# a single correct answer for "how often should this poll" that doesn't benefit
# from being exposed as a setting.
SYNC_INTERVAL_MINUTES = 1440
ACTIVITY_CHECK_INTERVAL_MINUTES = 10


class MainMonitor(xbmc.Monitor):
    def __init__(self):
        super().__init__()

        self.player_monitor = PlayerMonitor()
        self.sync_timer = None
        self.activity_timer = None

        try:
            status = "Connected" if oauth.get_access_token() else "Not connected"
            xbmcaddon.Addon().setSettingString("oauth_status", status)
        except Exception:
            pass

        self.start_sync_timer()
        self.start_activity_timer()
        # Catch-up sync shortly after the service starts, in addition to the
        # periodic timer and the library-scan hooks below.
        sync_orchestrator.run_async()

    def _bool_setting(self, setting_id, default=False):
        try:
            return xbmcaddon.Addon().getSettings().getBool(setting_id)
        except Exception:
            return default

    def start_sync_timer(self):
        self.stop_sync_timer()
        self.sync_timer = Timer(SYNC_INTERVAL_MINUTES * 60, self.on_sync_timer)
        self.sync_timer.start()

    def stop_sync_timer(self):
        if self.sync_timer and self.sync_timer.is_alive():
            self.sync_timer.stop()

    def on_sync_timer(self):
        sync_orchestrator.run_async()

    def start_activity_timer(self):
        self.stop_activity_timer()
        self.activity_timer = Timer(ACTIVITY_CHECK_INTERVAL_MINUTES * 60, self.on_activity_timer)
        self.activity_timer.start()

    def stop_activity_timer(self):
        if self.activity_timer and self.activity_timer.is_alive():
            self.activity_timer.stop()

    def on_activity_timer(self):
        sync_orchestrator.check_activity_async()

    def onScanFinished(self, library):
        if library == "video" and self._bool_setting("sync.on_library_scan", True):
            sync_orchestrator.run_async()

    def onCleanFinished(self, library):
        if library == "video" and self._bool_setting("sync.on_library_scan", True):
            sync_orchestrator.run_async()

    def onNotification(self, sender, method, data):
        if method != "VideoLibrary.OnUpdate":
            return

        try:
            payload = json.loads(data)
        except (ValueError, TypeError):
            return

        item = payload.get("item") or {}
        dbtype = item.get("type")
        dbid = item.get("id")
        if dbtype not in ("movie", "episode") or dbid in (None, -1):
            return

        live_sync.handle_library_update(dbtype, dbid)

    def onSettingsChanged(self):
        self.player_monitor.load_settings()
