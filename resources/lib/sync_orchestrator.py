import threading

import xbmc
import xbmcaddon
import xbmcgui

from resources.lib import collection_sync, library_snapshot, ratings_sync, sync_state, watched_sync
from resources.lib.mdblist_api import MDBListApiError, fetch_last_activities

_lock = threading.Lock()

# /sync/last_activities buckets that matter for our pull direction -- see
# check_activity(). collected_at exists too but collection sync is push-only,
# so there's nothing for us to pull in reaction to it changing.
WATCHED_ACTIVITY_KEYS = ("watched_at", "season_watched_at", "episode_watched_at")
RATING_ACTIVITY_KEYS = ("rated_at",)


def is_running():
    """True while a run() or check_activity() is in progress -- used by the
    live VideoLibrary.OnUpdate listener to skip reacting to library writes
    that are pull() applying remote state, not a real local/native-UI change."""
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


def _record_summary(summary):
    sync_state.set_last_sync_summary(summary)
    xbmc.log("MDBList Sync: run complete - {}".format(summary), level=xbmc.LOGDEBUG)
    try:
        _addon().setSettingString("sync_last_run", _summary_text(summary))
    except Exception:
        pass


def run(notify=False):
    """Full run: watched and ratings push then pull, collection push-only.
    Rebuilds the local library snapshot unconditionally, so this is the
    expensive path -- covers pushing local changes (backstop for anything
    the live listener missed, e.g. while Kodi was closed) and acts as a
    periodic full reconciliation. Safe to call from multiple trigger points
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

        _record_summary(summary)
        if notify:
            _notify("Sync complete")

        return summary
    finally:
        _lock.release()


def _bucket_advanced(seen, current, keys):
    return any(current.get(key) and current.get(key) != seen.get(key) for key in keys)


def check_activity(notify=False):
    """Cheap poll for the fast timer: check /sync/last_activities (a single
    lightweight GET -- its own docstring recommends calling it first to
    decide which buckets changed) and only pay for the expensive library
    snapshot rebuild + pull when a relevant bucket actually advanced since
    our last check. Independent of run() -- the slower full run still
    happens on scan/periodic/manual, covering push and acting as a
    reconciliation safety net for anything this misses."""
    if not _lock.acquire(blocking=False):
        xbmc.log("MDBList Sync: activity check skipped, a run is already in progress", level=xbmc.LOGDEBUG)
        return None

    try:
        watched_enabled = _bool_setting("sync.watched.enabled")
        ratings_enabled = _bool_setting("sync.ratings.enabled")
        if not (watched_enabled or ratings_enabled):
            return None

        try:
            activities = fetch_last_activities()
        except MDBListApiError as exception:
            xbmc.log("MDBList Sync: activity check failed - {}".format(exception), level=xbmc.LOGDEBUG)
            return None

        seen = sync_state.get_last_activities_seen()
        watched_changed = watched_enabled and _bucket_advanced(seen, activities, WATCHED_ACTIVITY_KEYS)
        ratings_changed = ratings_enabled and _bucket_advanced(seen, activities, RATING_ACTIVITY_KEYS)

        sync_state.set_last_activities_seen(activities)

        if not (watched_changed or ratings_changed):
            xbmc.log("MDBList Sync: activity check found nothing new", level=xbmc.LOGDEBUG)
            return None

        snapshot = library_snapshot.build_snapshot()
        summary = {}

        try:
            if watched_changed:
                summary["watched_pull"] = watched_sync.pull(snapshot)
            if ratings_changed:
                summary["ratings_pull"] = ratings_sync.pull(snapshot)
        except MDBListApiError as exception:
            xbmc.log("MDBList Sync: activity-triggered pull failed - {}".format(exception), level=xbmc.LOGERROR)
            if notify:
                _notify("Sync failed: {}".format(str(exception)[:60]), error=True)
            return None

        _record_summary(summary)
        if notify:
            _notify("Sync complete")

        return summary
    finally:
        _lock.release()


def _summary_text(summary):
    import datetime
    parts = []
    for category in ("watched", "ratings", "collection"):
        push = summary.get("{}_push".format(category))
        if push and (push.get("pushed_add") or push.get("pushed_remove")):
            parts.append("{} push +{}/-{}".format(category, push.get("pushed_add", 0), push.get("pushed_remove", 0)))
        pull = summary.get("{}_pull".format(category))
        if pull and pull.get("pulled_applied"):
            parts.append("{} pull {}".format(category, pull.get("pulled_applied", 0)))
    return "{} ({})".format(", ".join(parts) or "no changes", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))


def run_async(notify=False):
    thread = threading.Thread(target=run, kwargs={"notify": notify})
    thread.daemon = True
    thread.start()


def check_activity_async(notify=False):
    thread = threading.Thread(target=check_activity, kwargs={"notify": notify})
    thread.daemon = True
    thread.start()
