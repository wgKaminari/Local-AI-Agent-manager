# Phone access (0.8.0)

The phone version is the same web interface the desktop window shows,
served by the PC over the home network. Nothing runs on the phone except
the page: collection, the local AI (Ollama), Gmail and the SQLite database
stay on the PC, so features and data are identical and nothing has to sync.

## How it works

* `phone_access.PhoneAccess` opens a second Waitress listener on the PC's
  private network address (port 8765, or the next free one; the port is kept
  so a home-screen icon keeps working). It is off until switched on and
  reopens after a restart when it was on.
* A small WSGI wrapper marks every request that came through that listener.
  The mark lives in the WSGI environ, so a client cannot fake it with a Host
  header, and `web_api.guard` applies phone rules to marked requests:
  * the client address must be in a home-network range (10/8, 172.16/12,
    192.168/16, link-local, loopback, IPv6 ULA); Python's `is_private` is not
    used because it also accepts documentation ranges;
  * the Host header must be the phone address, and requests from other
    websites are refused;
  * the page is served without the desktop's session token;
  * every API call needs a device token. A phone gets one by sending the
    8-character pairing code shown on the PC (10 minutes; five wrong attempts
    end the code). Tokens are stored only as SHA-256 hashes in
    `phone-access.json` and can be removed on the PC.
* Actions that happen on the PC itself are refused from a phone with an
  explanation: connecting Gmail (Google's sign-in opens in a browser),
  Review & send (the employer's form opens in a browser window), updates,
  desktop shortcuts, the Adzuna key and phone management.
* The phone keeps its own light/dark theme in its browser storage.

## Layout

Below 640 px wide the sidebar becomes a top bar (country and refresh), the
main views become a bottom tab bar with More for the rest, list and detail
show one at a time with a Back button, dialogs fill the screen, inputs use
16 px text (no zoom on iPhone) and buttons are at least 40 px tall. A web app
manifest and icons let the phone add Job Agent to its home screen.

## Limits

* The PC must be on, awake and running Job Agent, on the same network.
* The home-network connection is plain HTTP. Do not turn phone access on in
  public networks.
* Browsers only fully install web apps from HTTPS sites, so on Android the
  home-screen icon may open in a browser tab rather than its own window.
* If the PC's network address changes, pair again with the new code.

## Tests

`tests/test_phone_access.py` checks the phone rules against real requests:
no data before pairing, no desktop token in the page, spoofed Host headers,
addresses outside the home network, wrong and expired codes, removal and the
PC-only actions. `tests/test_phone_ui.py` (with `RUN_BROWSER_TESTS=1`) pairs
a 390 × 844 touch browser through the QR link, opens every view and a job,
checks that nothing scrolls sideways, and checks the pairing screen.
