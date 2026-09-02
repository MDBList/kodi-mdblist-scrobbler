import contextlib
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
# Removals (unwatch, unrate) do NOT bump the per-bucket timestamps above --
# confirmed against api.mdblist's SyncWatchedRemove/SyncRatingsRemove, which
# only clear the per-item state and separately bump journal_at via
# _bulk_write_journal. Without checking journal_at too, an unwatch/unrate
# never trips this gate and pull() never runs for it.
JOURNAL_ACTIVITY_KEYS = ("journal_at",)


def is_running():
    """True while a run()/check_activity()/check_ratings_local() is in
    progress. Prefer try_lock() over this where the caller goes on to do
    real work afterward -- this alone is a check-then-act race (the state
    can change the instant after it's read); try_lock() holds the lock for
    the actual duration instead."""
    return _lock.locked()


@contextlib.contextmanager
def try_lock():
    """Non-blocking acquire held for the duration of the `with` block, so a
    caller like live_sync.handle_library_update can safely skip when a
    run()/check_activity() is in progress *or* starts mid-operation -- unlike
    a one-shot is_running() check, which only rules out overlap with a sync
    that had already started at the moment of the check (confirmed race)."""
    acquired = _lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _lock.release()


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
    skipped rather than queued or run concurrently.

    Deliberately not gated behind a cheap "did anything change" pre-check:
    every trigger that reaches this is already independently justified to
    need a real snapshot -- a scan/clean finishing needs it for push()
    regardless of remote state, and the 24h timer/manual action are meant to
    be an unconditional reconciliation backstop, not something to skip based
    on a signal that might itself be stale."""
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
        # journal_at covers removals for both categories (it doesn't say which),
        # so an advance there is checked as a possible change for whichever
        # categories are enabled, same as an advance in their own bucket.
        journal_advanced = _bucket_advanced(seen, activities, JOURNAL_ACTIVITY_KEYS)
        watched_changed = watched_enabled and (_bucket_advanced(seen, activities, WATCHED_ACTIVITY_KEYS) or journal_advanced)
        ratings_changed = ratings_enabled and (_bucket_advanced(seen, activities, RATING_ACTIVITY_KEYS) or journal_advanced)

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


def check_ratings_local(notify=False):
    """Frequent local ratings-only poll. Kodi's native "Rate" UI (video info
    dialog) does not reliably announce VideoLibrary.OnUpdate the way marking
    watched does -- confirmed by log inspection, no notification arrives for
    a rating made that way -- so the live listener alone can't catch it.
    Uses library_snapshot.build_ratings_snapshot(), a much lighter query than
    the full snapshot (no playcount/lastplayed/dateadded/file), so polling
    this frequently is cheap. Push-only, same as the live listener; pull is
    still handled by check_activity()."""
    if not _lock.acquire(blocking=False):
        xbmc.log("MDBList Sync: ratings check skipped, a run is already in progress", level=xbmc.LOGDEBUG)
        return None

    try:
        if not _bool_setting("sync.ratings.enabled"):
            return None

        snapshot = library_snapshot.build_ratings_snapshot()

        try:
            result = ratings_sync.push(snapshot)
        except MDBListApiError as exception:
            xbmc.log("MDBList Sync: local ratings push failed - {}".format(exception), level=xbmc.LOGERROR)
            if notify:
                _notify("Sync failed: {}".format(str(exception)[:60]), error=True)
            return None

        if not (result.get("pushed_add") or result.get("pushed_remove")):
            return None

        summary = {"ratings_push": result}
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


def check_ratings_local_async(notify=False):
    thread = threading.Thread(target=check_ratings_local, kwargs={"notify": notify})
    thread.daemon = True
    thread.start()
