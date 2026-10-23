// Client-side CSV export. The host console exports the activity feed, chat log and
// poll results — all three are already in memory, so an export endpoint would only
// re-fetch what the socket already delivered.
// ponytail: no papaparse. Quote-escaping is the whole job.

import { isNativeRuntime, saveFileNative } from "../native/bridge";

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

// How long a download's blob: URL outlives the click. Revoking it in the same tick can cancel
// the download before the browser has read the blob in some browsers; nothing reads it after.
export const BLOB_URL_TTL_MS = 40_000;

// The browser download for a caller that reports the outcome. Unlike `save` above, the anchor
// is attached while it is clicked (older browsers ignore a click on a detached one) and the
// URL is revoked later rather than immediately.
function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  try {
    a.click();
  } finally {
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), BLOB_URL_TTL_MS);
  }
}

/**
 * Save a generated text file on whichever platform this is, and SAY WHETHER IT HAPPENED, so
 * the caller never announces an export that did not take place.
 *
 *   browser      a real download of the file.
 *   Android app  native/bridge.saveFileNative: written to the app cache and offered through
 *                the share sheet. A blob: download is silently dropped by the WebView, which is
 *                how "Report exported" came to be shown with no file anywhere.
 *
 * Never throws. Resolves to `{ ok: true, via: "download" | "share" }` or `{ ok: false, reason }`:
 * "empty" (nothing to save), "download" (the browser path failed), or saveFileNative's
 * "unsupported" | "write" | "share" | "cancelled".
 */
export async function exportTextFile(filename, text, mimeType) {
  if (typeof text !== "string" || !text.length) return { ok: false, reason: "empty" };
  if (isNativeRuntime()) {
    const result = await saveFileNative({ filename, text, title: filename });
    return result.ok ? { ok: true, via: "share" } : result;
  }
  try {
    downloadBlob(filename, new Blob([text], { type: mimeType }));
    return { ok: true, via: "download" };
  } catch {
    return { ok: false, reason: "download" };
  }
}

// Snapshot export for the admin Command Center: the payload IS the record of what the
// operator was looking at, so it is written verbatim rather than flattened into columns.
export function downloadJson(data, filename) {
  save(
    filename.endsWith(".json") ? filename : `${filename}.json`,
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" })
  );
}
