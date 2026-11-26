"""Wartet auf das Biegbert-Backend und öffnet zwei Firefox-Fenster links/rechts."""

import subprocess
import sys
import time
import urllib.error
import urllib.request

import win32api
import win32con
import win32gui

FIREFOX = r"C:\Program Files\Mozilla Firefox\firefox.exe"
URL_LINKS = "http://localhost:8765/"
URL_RECHTS = "http://localhost:8765/vis/"
SERVER_TIMEOUT = 60  # Sekunden


def warte_auf_server(url, timeout=SERVER_TIMEOUT):
    """Fragt den Server ab, bis er antwortet (oder der Timeout abläuft)."""
    ende = time.time() + timeout
    while time.time() < ende:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except urllib.error.HTTPError:
            return True  # Server läuft, liefert nur einen Fehlercode
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.5)
    return False


def firefox_fenster():
    """Alle sichtbaren Firefox-Hauptfenster (Handles) sammeln."""
    gefunden = set()

    def callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "MozillaWindowClass":
            gefunden.add(hwnd)

    win32gui.EnumWindows(callback, None)
    return gefunden


def oeffnen(url, timeout=15):
    """Neues Firefox-Fenster öffnen und dessen Handle zurückgeben."""
    vorher = firefox_fenster()
    subprocess.Popen([FIREFOX, "-new-window", url])
    ende = time.time() + timeout
    while time.time() < ende:
        neu = firefox_fenster() - vorher
        if neu:
            return neu.pop()
        time.sleep(0.2)
    raise RuntimeError(f"Kein neues Firefox-Fenster für {url} gefunden")


def platzieren(hwnd, links):
    """Fenster auf die linke oder rechte Hälfte des Hauptbildschirms legen."""
    monitor = win32api.MonitorFromPoint((0, 0))
    l, t, r, b = win32api.GetMonitorInfo(monitor)["Work"]
    breite = (r - l) // 2
    rand = 7  # unsichtbarer Fensterrahmen unter Windows 10/11
    x = (l if links else l + breite) - rand
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)  # falls maximiert
    win32gui.MoveWindow(hwnd, x, t, breite + 2 * rand, b - t + rand, True)


def main():
    if not warte_auf_server(URL_LINKS):
        print(f"Backend unter {URL_LINKS} nicht erreichbar.")
        input("Enter zum Beenden ...")
        sys.exit(1)

    links = oeffnen(URL_LINKS)
    time.sleep(1)  # Firefox kurz fertig aufbauen lassen
    platzieren(links, links=True)

    rechts = oeffnen(URL_RECHTS)
    time.sleep(1)
    platzieren(rechts, links=False)


if __name__ == "__main__":
    main()
