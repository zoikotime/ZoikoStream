// Single source of truth for the ZoikoStream brand mark. Renders the icon tile
// plus (by default) the "ZoikoStream" wordmark. Purely presentational — wrap it in
// a <Link> at the call site when a click target is needed.
// Assets live in /public so the same files back the favicon and the email templates.
export default function Logo({
  showText = true,
  textClass = "text-lg text-slate-900 dark:text-white",
  size = "h-9 w-9",
  icon = "/zoiko-icon.png",
  className = "",
  children,
}) {
  return (
    <span className={`flex items-center gap-2.5 ${className}`}>
      <img src={icon} alt="ZoikoStream" className={`${size} shrink-0 object-contain`} />
      {children ?? (showText && (
        <span className={`font-bold tracking-tight ${textClass}`}>ZoikoStream</span>
      ))}
    </span>
  );
}
