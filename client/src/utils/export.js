// Client-side CSV export. The moderator console exports the activity feed, chat log and
// poll results — all three are already in memory, so an export endpoint would only
// re-fetch what the socket already delivered.
// ponytail: no papaparse. Quote-escaping is the whole job.

const cell = (v) => {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

// `columns` is [[header, pick]] so the caller controls order and labels.
export function toCsv(rows, columns) {
  const head = columns.map(([label]) => cell(label)).join(",");
  const body = rows.map((r) => columns.map(([, pick]) => cell(pick(r))).join(","));
  return [head, ...body].join("\n");
}

// Shared blob -> anchor -> revoke plumbing for every download on the client.
function save(filename, blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function downloadCsv(filename, rows, columns) {
  save(filename, new Blob([toCsv(rows, columns)], { type: "text/csv;charset=utf-8" }));
}

// Snapshot export for the admin Command Center: the payload IS the record of what the
// operator was looking at, so it is written verbatim rather than flattened into columns.
export function downloadJson(data, filename) {
  save(
    filename.endsWith(".json") ? filename : `${filename}.json`,
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" })
  );
}
