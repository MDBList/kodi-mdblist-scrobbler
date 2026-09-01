import xbmc
import xbmcaddon

from resources.lib import oauth, sync_orchestrator
from resources.lib.player_monitor import PlayerMonitor
from resources.lib.timer import Timer


MIN_SYNC_INTERVAL_MINUTES = 5
DEFAULT_SYNC_INTERVAL_MINUTES = 360


class MainMonitor(xbmc.Monitor):
    def __init__(self):
        super().__init__()

        self.player_monitor = PlayerMonitor()
        self.sync_timer = None

        try:
            status = "Connected" if oauth.get_access_token() else "Not connected"
            xbmcaddon.Addon().setSettingString("oauth_status", status)
        except Exception:
            pass

        self.start_sync_timer()
        # Catch-up sync shortly after the service starts, in addition to the
        # periodic timer and the library-scan hooks below.
        sync_orchestrator.run_async()

    def _bool_setting(self, setting_id, default=False):
        try:
            return xbmcaddon.Addon().getSettings().getBool(setting_id)
        except Exception:
            return default

    def _int_setting(self, setting_id, default=0):
        try:
            return xbmcaddon.Addon().getSettings().getInt(setting_id)
        except Exception:
            return default

    def start_sync_timer(self):
        self.stop_sync_timer()
        interval_minutes = max(
            self._int_setting("sync.interval", DEFAULT_SYNC_INTERVAL_MINUTES),
            MIN_SYNC_INTERVAL_MINUTES,
        )
        self.sync_timer = Timer(interval_minutes * 60, self.on_sync_timer)
        self.sync_timer.start()

    def stop_sync_timer(self):
        if self.sync_timer and self.sync_timer.is_alive():
            self.sync_timer.stop()

    def on_sync_timer(self):
        sync_orchestrator.run_async()

    def onScanFinished(self, library):
        if library == "video" and self._bool_setting("sync.on_library_scan", True):
            sync_orchestrator.run_async()

    def onCleanFinished(self, library):
        if library == "video" and self._bool_setting("sync.on_library_scan", True):
            sync_orchestrator.run_async()

    def onSettingsChanged(self):
        self.player_monitor.load_settings()
        self.start_sync_timer()
