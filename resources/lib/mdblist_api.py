import requests
import urllib.parse
import xbmc
import xbmcaddon

from resources.lib import oauth


REQUEST_TIMEOUT_SECONDS = 10
DEFAULT_BASE_URL = "https://api.mdblist.com"


class MDBListApiError(Exception):
    pass


def _addon():
    return xbmcaddon.Addon()


def get_string_setting(setting_id: str, default: str = ""):
    try:
        value = _addon().getSettings().getString(setting_id)
        return value or default
    except Exception:
        return default


def base_url():
    return DEFAULT_BASE_URL


def auth_params():
    access_token = oauth.ensure_valid_token()
    apikey = "" if access_token else get_string_setting("apikey")

    if access_token:
        return {"headers": {"Authorization": "Bearer {}".format(access_token)}, "query": ""}
    if apikey:
        return {"headers": None, "query": urllib.parse.urlencode({"apikey": apikey})}

    raise MDBListApiError("Not authenticated. Open addon settings to connect.")


def request(method: str, endpoint: str, params=None, json_data=None):
    auth = auth_params()
    url = "{}{}".format(base_url(), endpoint)

    query = auth["query"]
    if params:
        filtered = {key: value for key, value in params.items() if value not in (None, "")}
        encoded = urllib.parse.urlencode(filtered)
        if encoded:
            query = "{}&{}".format(query, encoded) if query else encoded
    if query:
        url = "{}?{}".format(url, query)

    try:
        response = requests.request(
            method,
            url,
            json=json_data,
            headers=auth["headers"],
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exception:
        raise MDBListApiError(str(exception))

    if response.status_code >= 400:
        xbmc.log(
            "MDBList Scrobbler: API error {} on {} response={}".format(
                response.status_code, endpoint, response.text[:200]
            ),
            level=xbmc.LOGERROR,
        )
        raise MDBListApiError("API Error {}: {}".format(response.status_code, response.text[:80]))

    try:
        return response.json()
    except ValueError:
        # A 200 with an unparseable body is a real failure, not "no data" --
        # callers read the result with .get() and treat a missing key as
        # legitimately absent (no items, no server_time, etc.), so silently
        # returning {} here would be indistinguishable from a genuinely
        # empty-but-valid response.
        raise MDBListApiError("Invalid response from {}".format(endpoint))


def fetch_watchlist(mediatype=None, limit=100):
    endpoint = "/watchlist/items/{}".format(mediatype) if mediatype else "/watchlist/items"
    cursor = None
    movies = []
    shows = []

    while True:
        params = {"limit": limit, "append_to_response": "poster"}
        if cursor:
            params["cursor"] = cursor

        data = request("GET", endpoint, params=params)
        if isinstance(data, list):
            movies.extend([item for item in data if item.get("mediatype") == "movie"])
            shows.extend([item for item in data if item.get("mediatype") == "show"])
        else:
            movies.extend(data.get("movies") or [])
            shows.extend(data.get("shows") or [])

        pagination = data.get("pagination", {}) if isinstance(data, dict) else {}
        cursor = pagination.get("next_cursor")
        if not cursor:
            break

    return {"movies": movies, "shows": shows}


def modify_watchlist(action: str, mediatype: str, ids: dict):
    if mediatype == "movie":
        payload = {"movies": [ids]}
    elif mediatype == "show":
        payload = {"shows": [ids]}
    else:
        raise MDBListApiError("Unsupported watchlist type: {}".format(mediatype))

    return request("POST", "/watchlist/items/{}".format(action), json_data=payload)


def fetch_sync_items(endpoint: str, mediatype=None, since=None, extended="ids_only", limit=1000):
    """Cursor-paginate a /sync/* GET endpoint and merge every page's list-valued
    keys (movies/shows/seasons/episodes/...) into one dict, ignoring pagination."""
    params = {"limit": limit, "extended": extended}
    if mediatype:
        params["mediatype"] = mediatype
    if since:
        params["since"] = since

    cursor = None
    merged = {}

    while True:
        page_params = dict(params)
        if cursor:
            page_params["cursor"] = cursor

        data = request("GET", endpoint, params=page_params)
        if not isinstance(data, dict):
            break

        for key, value in data.items():
            if key == "pagination" or not isinstance(value, list):
                continue
            merged.setdefault(key, []).extend(value)

        cursor = (data.get("pagination") or {}).get("next_cursor")
        if not cursor:
            break

    return merged


def push_sync_items(endpoint: str, payload: dict):
    return request("POST", endpoint, json_data=payload)


def fetch_journal(since=None, limit=1000):
    """Page through /sync/journal starting from `since`. Returns
    {"requires_full_sync": True} if the caller's watermark is outside the
    30-day retention window, otherwise {"requires_full_sync": False,
    "entries": [...], "journal_oldest_at": ...}."""
    cursor = None
    entries = []
    journal_oldest_at = None

    while True:
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        elif since:
            params["since"] = since

        data = request("GET", "/sync/journal", params=params)
        if not isinstance(data, dict):
            break

        if data.get("requires_full_sync"):
            return {"requires_full_sync": True, "entries": []}

        entries.extend(data.get("journal") or [])
        journal_oldest_at = data.get("journal_oldest_at", journal_oldest_at)

        cursor = (data.get("pagination") or {}).get("next_cursor")
        if not cursor:
            break

    return {"requires_full_sync": False, "entries": entries, "journal_oldest_at": journal_oldest_at}


def fetch_last_activities():
    data = request("GET", "/sync/last_activities")
    # server_time is always present in a real response -- every caller uses
    # it as the next sync watermark, so a response that parsed but doesn't
    # have it (wrong shape, unexpected body) needs to abort the pull rather
    # than let watched_sync/ratings_sync silently fall back to the device's
    # own clock.
    if not isinstance(data, dict) or "server_time" not in data:
        raise MDBListApiError("Malformed response from /sync/last_activities")
    return data
