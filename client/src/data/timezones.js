// Timezone reference for the event scheduler.
//
// CRITICAL: the VALUE of every entry is an IANA identifier and must stay one. It is what the
// API stores (`POST /events` → `timezone`) and what ManagedEvents, UpcomingEvents and
// EventDetails hand to `Intl.DateTimeFormat({ timeZone })`, which throws a RangeError on
// anything else. Only the LABEL is the abbreviation an operator reads — storing "IST" would
// break every schedule readout in the console.
//
// Coverage is one entry per populated UTC offset from -11 through +14, grouped by region, so
// the picker spans the world without shipping all ~420 IANA zones (most of which are aliases
// of the ones below).
//
// Abbreviations are qualified where they genuinely collide: IST is India, Ireland AND Israel;
// CST is both US Central and China Standard; BST is Britain and Bangladesh. An unqualified
// abbreviation in a scheduling UI is how a meeting lands on the wrong continent.
export const TIMEZONE_GROUPS = [
  ["Universal", [
    ["UTC", "UTC", "Coordinated Universal Time"],
  ]],
  ["Americas", [
    ["Pacific/Midway", "SST", "Midway, Samoa"],
    ["Pacific/Honolulu", "HST", "Honolulu, Hawaii"],
    ["America/Anchorage", "AKST/AKDT", "Anchorage, Alaska"],
    ["America/Los_Angeles", "PST/PDT", "Los Angeles, Vancouver, Seattle"],
    ["America/Phoenix", "MST", "Phoenix (no DST)"],
    ["America/Denver", "MST/MDT", "Denver, Calgary"],
    ["America/Chicago", "CST/CDT", "Chicago, Dallas, Winnipeg"],
    ["America/Mexico_City", "CST", "Mexico City"],
    ["America/New_York", "EST/EDT", "New York, Toronto, Miami"],
    ["America/Bogota", "COT", "Bogotá, Lima, Quito"],
    ["America/Caracas", "VET", "Caracas"],
    ["America/Halifax", "AST/ADT", "Halifax"],
    ["America/St_Johns", "NST/NDT", "Newfoundland"],
    ["America/Santiago", "CLT/CLST", "Santiago"],
    ["America/Sao_Paulo", "BRT", "São Paulo, Rio de Janeiro"],
    ["America/Argentina/Buenos_Aires", "ART", "Buenos Aires, Montevideo"],
  ]],
  ["Europe", [
    ["Atlantic/Azores", "AZOT", "Azores"],
    ["Atlantic/Reykjavik", "GMT", "Reykjavík"],
    ["Europe/London", "GMT/BST", "London, Dublin, Edinburgh"],
    ["Europe/Lisbon", "WET/WEST", "Lisbon"],
    ["Europe/Paris", "CET/CEST", "Paris, Madrid, Rome, Brussels"],
    ["Europe/Berlin", "CET/CEST", "Berlin, Amsterdam, Zurich, Vienna"],
    ["Europe/Warsaw", "CET/CEST", "Warsaw, Stockholm, Oslo, Prague"],
    ["Europe/Athens", "EET/EEST", "Athens, Helsinki, Bucharest"],
    ["Europe/Kyiv", "EET/EEST", "Kyiv"],
    ["Europe/Istanbul", "TRT", "Istanbul"],
    ["Europe/Moscow", "MSK", "Moscow, St Petersburg"],
  ]],
  ["Africa", [
    ["Africa/Casablanca", "WET/WEST", "Casablanca"],
    ["Africa/Lagos", "WAT", "Lagos, Kinshasa, Algiers"],
    ["Africa/Cairo", "EET/EEST", "Cairo"],
    ["Africa/Johannesburg", "SAST", "Johannesburg, Harare"],
    ["Africa/Nairobi", "EAT", "Nairobi, Addis Ababa"],
  ]],
  ["Middle East", [
    ["Asia/Jerusalem", "IST/IDT (Israel)", "Jerusalem, Tel Aviv"],
    ["Asia/Riyadh", "AST (Arabia)", "Riyadh, Kuwait, Baghdad"],
    ["Asia/Tehran", "IRST", "Tehran"],
    ["Asia/Dubai", "GST", "Dubai, Abu Dhabi, Muscat"],
  ]],
  ["Asia", [
    ["Asia/Kabul", "AFT", "Kabul"],
    ["Asia/Karachi", "PKT", "Karachi, Islamabad"],
    ["Asia/Tashkent", "UZT", "Tashkent"],
    ["Asia/Kolkata", "IST (India)", "Mumbai, Delhi, Bengaluru, Colombo"],
    ["Asia/Kathmandu", "NPT", "Kathmandu"],
    ["Asia/Dhaka", "BST (Bangladesh)", "Dhaka"],
    ["Asia/Yangon", "MMT", "Yangon"],
    ["Asia/Bangkok", "ICT", "Bangkok, Hanoi, Phnom Penh"],
    ["Asia/Jakarta", "WIB", "Jakarta"],
    ["Asia/Shanghai", "CST (China)", "Beijing, Shanghai"],
    ["Asia/Hong_Kong", "HKT", "Hong Kong"],
    ["Asia/Singapore", "SGT", "Singapore"],
    ["Asia/Kuala_Lumpur", "MYT", "Kuala Lumpur"],
    ["Asia/Manila", "PHT", "Manila"],
    ["Asia/Tokyo", "JST", "Tokyo, Osaka"],
    ["Asia/Seoul", "KST", "Seoul"],
  ]],
  ["Oceania", [
    ["Australia/Perth", "AWST", "Perth"],
    ["Australia/Darwin", "ACST", "Darwin (no DST)"],
    ["Australia/Adelaide", "ACST/ACDT", "Adelaide"],
    ["Australia/Brisbane", "AEST", "Brisbane (no DST)"],
    ["Australia/Sydney", "AEST/AEDT", "Sydney, Melbourne, Canberra"],
    ["Pacific/Guam", "ChST", "Guam, Port Moresby"],
    ["Pacific/Noumea", "NCT", "Nouméa, Solomon Islands"],
    ["Pacific/Auckland", "NZST/NZDT", "Auckland, Wellington"],
    ["Pacific/Fiji", "FJT", "Fiji"],
    ["Pacific/Tongatapu", "TOT", "Nukuʻalofa"],
    ["Pacific/Kiritimati", "LINT", "Kiritimati"],
  ]],
];

// Flat IANA → [abbreviation, places] lookup, for rendering a stored value.
const BY_ZONE = new Map(
  TIMEZONE_GROUPS.flatMap(([, zones]) => zones.map(([zone, abbr, places]) => [zone, [abbr, places]]))
);

// The zone's GMT offset RIGHT NOW, so a label never goes stale across a DST boundary the way
// a hardcoded "(GMT+1)" would. `shortOffset` is ES2022; older engines throw, and then the
// label simply omits the offset rather than failing.
export function tzOffset(zone) {
  try {
    return (
      new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: "shortOffset" })
        .formatToParts(new Date())
        .find((p) => p.type === "timeZoneName")?.value || null
    );
  } catch {
    return null;
  }
}

// Full picker label: "IST (India) — Mumbai, Delhi… (GMT+5:30)".
export function tzLabel(zone, abbr, places) {
  const offset = tzOffset(zone);
  return `${abbr} — ${places}${offset ? ` (${offset})` : ""}`;
}

// Compact label for reading a stored value back: "IST (India) · GMT+5:30". Unknown zones
// (anything minted before this list, or an IANA name not covered above) fall back to the
// raw identifier — never to a guess.
export function tzShort(zone) {
  if (!zone) return null;
  const entry = BY_ZONE.get(zone);
  if (!entry) return zone;
  const offset = tzOffset(zone);
  return offset ? `${entry[0]} · ${offset}` : entry[0];
}
