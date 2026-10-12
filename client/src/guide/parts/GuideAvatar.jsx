// The Guide's face: the ZoikoStream brand mark (client/public/zoiko-icon.png) in a round
// badge. Decorative — every place it appears already names Guide in text.
const ICON_SRC = "/zoiko-icon.png";

export default function GuideAvatar({ size = "md", online = false }) {
  const cls = size === "md" ? "zg-avatar" : `zg-avatar zg-avatar--${size}`;
  return (
    <span className={cls} aria-hidden="true">
      <img src={ICON_SRC} alt="" width="64" height="64" decoding="async" />
      {online && <span className="zg-online" />}
    </span>
  );
}
