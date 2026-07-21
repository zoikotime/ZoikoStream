import { cx } from "./tokens";

// Max-width page gutter. One place owns the responsive horizontal padding.
const WIDTHS = { default: "max-w-7xl", narrow: "max-w-3xl", wide: "max-w-screen-2xl", prose: "max-w-2xl" };

export default function Container({ width = "default", className = "", children, as: Tag = "div" }) {
  return <Tag className={cx("mx-auto w-full px-5 sm:px-8", WIDTHS[width], className)}>{children}</Tag>;
}
