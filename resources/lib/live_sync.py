import xbmc
import xbmcaddon

from resources.lib import ratings_sync, sync_orchestrator, watched_sync
from resources.lib.mdblist_api import MDBListApiError
from resources.lib.utils import fix_unique_ids, jsonrpc_request


def _bool_setting(setting_id, default=False):
    try:
        return xbmcaddon.Addon().getSettings().getBool(setting_id)
    except Exception:
        return default


def _movie_record(dbid):
    details = jsonrpc_request(
        "VideoLibrary.GetMovieDetails",
        {"movieid": dbid, "properties": ["uniqueid", "playcount", "lastplayed", "userrating"]},
    ).get("moviedetails")
    if not details:
        return None

    ids = fix_unique_ids(details.get("uniqueid", {}), "movie")
    if not ids:
        return None

    return {
        "dbtype": "movie", "ids": ids,
        "playcount": details.get("playcount") or 0,
        "lastplayed": details.get("lastplayed") or None,
        "userrating": details.get("userrating") or 0,
    }


def _episode_record(dbid):
    details = jsonrpc_request(
        "VideoLibrary.GetEpisodeDetails",
        {"episodeid": dbid, "properties": ["season", "episode", "tvshowid", "playcount", "lastplayed", "userrating"]},
    ).get("episodedetails")
    if not details or not details.get("tvshowid"):
        return None

    show = jsonrpc_request(
        "VideoLibrary.GetTVShowDetails",
        {"tvshowid": details["tvshowid"], "properties": ["uniqueid"]},
    ).get("tvshowdetails") or {}
    show_ids = fix_unique_ids(show.get("uniqueid", {}), "show")
    if not show_ids:
        return None

    return {
        "dbtype": "episode", "show_ids": show_ids,
        "season": details.get("season"), "episode": details.get("episode"),
        "playcount": details.get("playcount") or 0,
        "lastplayed": details.get("lastplayed") or None,
        "userrating": details.get("userrating") or 0,
    }


def handle_library_update(dbtype, dbid):
    """Called from MainMonitor.onNotification for VideoLibrary.OnUpdate --
    fires for any change to a library item's playcount/lastplayed/userrating,
    regardless of what caused it (our own scrobble/rating-prompt flow, Kodi's
    native "mark as watched"/rate dialog, another addon). Pushes just that one
    item instead of waiting for the next full sync run."""
    if sync_orchestrator.is_running():
        # Very likely our own pull() applying remote state -- avoid an
        # immediate echo push of what we just pulled.
        xbmc.log("MDBList Sync: live update for {}:{} skipped, a run is in progress".format(dbtype, dbid), level=xbmc.LOGDEBUG)
        return

    watched_enabled = _bool_setting("sync.watched.enabled")
    ratings_enabled = _bool_setting("sync.ratings.enabled")
    if not (watched_enabled or ratings_enabled):
        xbmc.log("MDBList Sync: live update for {}:{} ignored, watched/ratings sync both disabled".format(dbtype, dbid), level=xbmc.LOGDEBUG)
        return

    record = _movie_record(dbid) if dbtype == "movie" else _episode_record(dbid)
    if not record:
        xbmc.log("MDBList Sync: live update for {}:{} - no supported ids, skipping".format(dbtype, dbid), level=xbmc.LOGDEBUG)
        return

    xbmc.log("MDBList Sync: live update for {}:{} record={}".format(dbtype, dbid, record), level=xbmc.LOGDEBUG)

    try:
        if watched_enabled:
            result = watched_sync.push_single(record)
            xbmc.log("MDBList Sync: live watched push_single result={}".format(result), level=xbmc.LOGDEBUG)
        if ratings_enabled:
            result = ratings_sync.push_single(record)
            xbmc.log("MDBList Sync: live ratings push_single result={}".format(result), level=xbmc.LOGDEBUG)
    except MDBListApiError as exception:
        xbmc.log("MDBList Sync: live push failed - {}".format(exception), level=xbmc.LOGDEBUG)
