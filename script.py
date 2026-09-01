import os
import sys

import xbmc

# Ensure the addon root is in the Python path when called via RunScript
_addon_root = os.path.dirname(os.path.abspath(__file__))
if _addon_root not in sys.path:
    sys.path.insert(0, _addon_root)

try:
    if "sync_now" in sys.argv:
        from resources.lib import sync_orchestrator
        sync_orchestrator.run(notify=True)
    else:
        from resources.lib import oauth

        if "disconnect" in sys.argv:
            oauth.run_disconnect()
        else:
            oauth.run_connect_flow()
except Exception as e:
    xbmc.log("MDBList Scrobbler: script error - {}".format(e), level=xbmc.LOGERROR)
    import xbmcgui
    xbmcgui.Dialog().notification("MDBList Scrobbler", "Error: {}".format(str(e)[:80]), xbmcgui.NOTIFICATION_ERROR, 4000)
