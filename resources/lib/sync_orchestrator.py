import threading

import xbmc
import xbmcaddon
import xbmcgui

from resources.lib import collection_sync, library_snapshot, ratings_sync, sync_state, watched_sync
from resources.lib.mdblist_api import MDBListApiError

_lock = threading.Lock()


def is_running():
    """True while a full run() is in progress -- used by the live
    VideoLibrary.OnUpdate listener to skip reacting to library writes that
    are pull() applying remote state, not a real local/native-UI change."""
    return _lock.locked()


def _addon():
    return xbmcaddon.Addon()


def _bool_setting(setting_id, default=False):
    try:
        return _addon().getSettings().getBool(setting_id)
    except Exception:
        return default


def _notify(message, error=False):
    icon = xbmcgui.NOTIFICATION_ERROR if error else xbmcgui.NOTIFICATION_INFO
    xbmcgui.Dialog().notification("MDBList Sync", message, icon, 4000)


def run(notify=False):
    """Run every enabled sync category once: watched and ratings push then
    pull, collection push-only. Safe to call from multiple trigger points
    (library scan, periodic timer, manual action) -- overlapping calls are
    skipped rather than queued or run concurrently."""
    if not _lock.acquire(blocking=False):
        xbmc.log("MDBList Sync: run already in progress, skipping", level=xbmc.LOGDEBUG)
        return None

    try:
        watched_enabled = _bool_setting("sync.watched.enabled")
        ratings_enabled = _bool_setting("sync.ratings.enabled")
        collection_enabled = _bool_setting("sync.collection.enabled")

        if not (watched_enabled or ratings_enabled or collection_enabled):
            xbmc.log("MDBList Sync: nothing enabled, skipping run", level=xbmc.LOGDEBUG)
            if notify:
                _notify("Nothing to sync - enable Sync settings first")
            return None

        snapshot = library_snapshot.build_snapshot()
        summary = {}

        try:
            if watched_enabled:
                summary["watched_push"] = watched_sync.push(snapshot)
                summary["watched_pull"] = watched_sync.pull(snapshot)

            if ratings_enabled:
                summary["ratings_push"] = ratings_sync.push(snapshot)
                summary["ratings_pull"] = ratings_sync.pull(snapshot)

            if collection_enabled:
                summary["collection_push"] = collection_sync.push(snapshot)
        except MDBListApiError as exception:
            xbmc.log("MDBList Sync: run failed - {}".format(exception), level=xbmc.LOGERROR)
            if notify:
                _notify("Sync failed: {}".format(str(exception)[:60]), error=True)
            return None

        sync_state.set_last_sync_summary(summary)
        xbmc.log("MDBList Sync: run complete - {}".format(summary), level=xbmc.LOGDEBUG)

        try:
            _addon().setSettingString("sync_last_run", _summary_text(summary))
        except Exception:
            pass

        if notify:
            _notify("Sync complete")

        return summary
    finally:
        _lock.release()


def _summary_text(summary):
    import datetime
    parts = []
    for category, direction in (("watched", "watched"), ("ratings", "ratings"), ("collection", "collection")):
        push = summary.get("{}_push".format(direction))
        if push:
            parts.append("{} +{}/-{}".format(category, push.get("pushed_add", 0), push.get("pushed_remove", 0)))
    return "{} ({})".format(", ".join(parts) or "no changes", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))


def run_async(notify=False):
    thread = threading.Thread(target=run, kwargs={"notify": notify})
    thread.daemon = True
    thread.start()
