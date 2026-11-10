"""Settings for the Chicago → Iceland tracker. Edit these to change what it looks for."""

ORIGIN = "ORD"
DEST = "KEF"                 # Reykjavik-Keflavik
DEST_NAME = "Reykjavik"
AIRLINE_CODE = "UA"
AIRLINE_NAME = "United"
MAX_STOPS = 1                # nonstop or 1 stop

# Weekdays: 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri 5=Sat 6=Sun
DEPART_DAYS = [2, 3]         # leave ORD Wednesday or Thursday (any time)
RETURN_DAYS = [6, 0]         # leave Iceland Sunday or Monday (any time)

WEEKENDS_AHEAD = 10          # track the next 10 weekends, rolling forward

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
DEAL_MIN_CHECKS = 24         # ...but only once that trip has a day of history
