import time

def increment_uptime(settings, log):
    """
    Continuously update the device uptime every second.
    """
    while True:
        time.sleep(1)
        now_ms = int(time.time() * 1000)
        up_since = settings.get("upSince")
        if up_since:
            settings["uptime"] = int((now_ms - up_since) / 1000)
        log.debug(f"Uptime updated: {settings['uptime']}s")
