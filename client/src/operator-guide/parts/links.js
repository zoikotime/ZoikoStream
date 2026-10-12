// Guide only ever navigates inside the console: a relative path, never another origin.
export function safeHref(href) {
  return typeof href === "string" && href.startsWith("/") && !href.startsWith("//") && !href.includes("\\")
    ? href
    : null;
}
