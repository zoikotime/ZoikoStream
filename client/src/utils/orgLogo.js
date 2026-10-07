// Organization logos: which one a surface shows, and whether a typed URL is acceptable.
//
// Selection, used by the viewer page's brand bar and every branding preview:
//   dark theme  -> dark logo -> light logo -> ZoikoStream mark
//   light theme -> light logo -> ZoikoStream mark
// The dark logo is never shown on a light background — it was drawn for dark surfaces. An
// organization that only ever set `logo_url` therefore sees it in both themes, unchanged.
//
// Validation mirrors server/app/schemas/organization.py normalize_logo_url, so the field says
// what is wrong before Save instead of the whole section coming back as a 422.
export const LOGO_EXTENSIONS = ["png", "svg", "jpg", "jpeg", "webp", "gif", "avif"];
export const LOGO_MAX_LENGTH = 500;

const clean = (v) => (typeof v === "string" ? v.trim() : "") || null;

// The URLs to try, best first; an empty list means "show the ZoikoStream mark".
export function logoCandidates({ light, dark } = {}, theme = "light") {
  const list = theme === "dark" ? [clean(dark), clean(light)] : [clean(light)];
  return [...new Set(list.filter(Boolean))];
}

// null when the value is acceptable (blank included — blank clears the logo).
export function logoUrlError(value) {
  const v = (value || "").trim();
  if (!v) return null;
  if (v.length > LOGO_MAX_LENGTH) return `Use a link of ${LOGO_MAX_LENGTH} characters or fewer.`;
  if (/\s/.test(v)) return "A logo URL cannot contain spaces.";
  // The regex as well as URL(): URL() repairs "https:/host" into "https://host/", which the
  // server would refuse.
  let url;
  try {
    url = new URL(v);
  } catch {
    url = null;
  }
  if (!/^https:\/\//i.test(v) || !url || url.protocol !== "https:" || !url.hostname) {
    return "Use an https:// link to the image.";
  }
  const last = url.pathname.split("/").pop() || "";
  const ext = last.includes(".") ? last.split(".").pop().toLowerCase() : "";
  if (ext && !LOGO_EXTENSIONS.includes(ext)) {
    return "Link to an image file: PNG, SVG, JPG, WebP, GIF or AVIF.";
  }
  return null;
}
