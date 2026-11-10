"""Settings for the Chicago → Iceland tracker. Edit these to change what it looks for."""

ORIGIN = "ORD"
DEST = "KEF"                 # Reykjavik-Keflavik
DEST_NAME = "Reykjavik"
MAX_STOPS = 1                # nonstop or 1 stop

# Airlines to track: code → name as Google Flights shows it.
# Every price on the website and in emails is labeled with its airline.
AIRLINES = {
    "UA": "United",
    "FI": "Icelandair",
}

# Trips to check each week. Days: Mon Tue Wed Thu Fri Sat Sun.
#   leave_after  = outbound must depart ORD at/after this hour (24h clock)
#   leave_before = outbound must depart ORD before this hour
#   land_after   = return must land at ORD at/after this hour
TRIPS = [
    {"out": "Wed", "back": "Sun"},
    {"out": "Wed", "back": "Mon"},
    {"out": "Thu", "back": "Sun"},
    {"out": "Thu", "back": "Mon"},
    # Friday night out, back the following Wednesday (any time):
    {"out": "Fri", "back": "Wed", "leave_after": 18},
]

TRACK_UNTIL = "2027-05-31"   # track every trip departing from now through this date
HOURLY_WEEKENDS = 10         # the next 10 weekends are checked every hour...
FAR_EVERY_HOURS = 3          # ...weekends further out every 3 hours (keeps Google happy)

ADULTS = 1
SEAT = "economy"
EXCLUDE_BASIC_ECONOMY = True # regular Economy only (what Chase Travel sells)
CURRENCY = "USD"
TIMEZONE = "America/Chicago"

# Pause between searches so Google doesn't block the bot (seconds, random range)
DELAY_RANGE = (1.0, 2.5)

# Alerts (GitHub issue → email)
TARGET_PRICE = None          # e.g. 600 = email when any trip drops to $600 or less
DEAL_THRESHOLD = 0.15        # email when a trip is 15%+ below its own typical price
DEAL_MIN_CHECKS = 24         # ...but only once that trip has enough history
