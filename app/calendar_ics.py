"""iCalendar (RFC 5545) for the private appointment feeds, written by hand: a calendar
of VEVENTs is a few lines, and a library would be one more pinned dependency.

Text is escaped (backslash, semicolon, comma, newline) and lines are folded at 75
octets without splitting a UTF-8 character; lines end in CRLF."""

from datetime import UTC


def escape(text):
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def fold(line, limit=75):
    data = line.encode()
    if len(data) <= limit:
        return line
    parts, start, first = [], 0, True
    while start < len(data):
        size = limit if first else limit - 1          # continuation lines start with a space
        end = min(start + size, len(data))
        while end < len(data) and (data[end] & 0xC0) == 0x80:   # don't cut a UTF-8 sequence
            end -= 1
        parts.append(data[start:end].decode())
        start, first = end, False
    return "\r\n ".join(parts)


def stamp(dt):
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def calendar(name, events, now):
    """events: dicts with uid, start, end, summary, and optional location, description,
    url."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ArcadeTech Tracker//shop-hub//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{escape(name)}",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H"]
    for e in events:
        lines += ["BEGIN:VEVENT", f"UID:{e['uid']}", f"DTSTAMP:{stamp(now)}",
                  f"DTSTART:{stamp(e['start'])}", f"DTEND:{stamp(e['end'])}",
                  f"SUMMARY:{escape(e['summary'])}"]
        if e.get("location"):
            lines.append(f"LOCATION:{escape(e['location'])}")
        if e.get("description"):
            lines.append(f"DESCRIPTION:{escape(e['description'])}")
        if e.get("url"):
            lines.append(f"URL:{e['url']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines)
