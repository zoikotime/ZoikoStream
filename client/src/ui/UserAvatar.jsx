import { useState } from "react";
import { FiUser } from "react-icons/fi";
import { cx } from "./tokens";
import { avatarSrc, initialsOf } from "../utils/avatar";

// A person's avatar, everywhere one is shown: their profile photo when they have one, else
// their initials, else a generic person icon. One component, so every surface falls back the
// same way.
//
// The caller's `className` sizes and colours the circle exactly as its old initials badge did;
// a photo fills that same box (object-cover: cropped to the circle, never stretched), so
// switching between photo and initials never moves anything. A photo that fails to load falls
// back to the initials silently — never a broken-image icon.
export default function UserAvatar({ name, src, className = "", alt }) {
  const url = avatarSrc(src);
  const [failed, setFailed] = useState(null);
  const initials = initialsOf(name);
  const showImage = Boolean(url) && failed !== url;
  const kind = showImage ? "image" : initials ? "initials" : "icon";

  return (
    <span
      data-avatar={kind}
      className={cx("relative inline-grid shrink-0 select-none place-items-center overflow-hidden rounded-full", className)}
    >
      {showImage ? (
        <img
          src={url}
          alt={alt ?? (name ? `Profile photo of ${name}` : "Profile photo")}
          onError={() => setFailed(url)}
          draggable={false}
          className="absolute inset-0 h-full w-full object-cover"
        />
      ) : initials ? (
        <span aria-hidden="true">{initials}</span>
      ) : (
        <FiUser aria-hidden="true" className="h-1/2 w-1/2" />
      )}
    </span>
  );
}
