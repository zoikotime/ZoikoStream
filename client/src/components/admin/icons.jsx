/* eslint-disable react-refresh/only-export-components -- icon registry (ICONS) + <Icon> component live together by design */
// Explicit icon registry — lets the mock-data modules reference icons by string name
// while keeping the bundle tree-shakeable (no `import * as`). Add a name here when a
// new icon is referenced by data. ponytail: one map, resolved via <Icon name="…" />.
import {
  FiGrid, FiUsers, FiRadio, FiVideo, FiFilm, FiHardDrive, FiWifi, FiDollarSign,
  FiLayers, FiZap, FiDatabase, FiServer, FiCpu, FiCloud, FiLock, FiShield,
  FiAlertTriangle, FiSlash, FiFileText, FiCreditCard, FiFlag, FiHome, FiBarChart2,
  FiSettings, FiActivity, FiKey, FiLifeBuoy, FiGitBranch, FiSearch, FiBell,
  FiChevronDown, FiChevronRight, FiSun, FiMoon, FiPlus, FiGlobe, FiCommand, FiUser,
  FiLogOut, FiSend, FiTool, FiUserPlus, FiArrowUpRight, FiArrowRight, FiClock,
  FiExternalLink, FiCheckCircle, FiXCircle, FiMenu, FiX, FiMapPin, FiTrendingUp,
  FiTrendingDown, FiEye, FiCircle,
} from "react-icons/fi";

export const ICONS = {
  FiGrid, FiUsers, FiRadio, FiVideo, FiFilm, FiHardDrive, FiWifi, FiDollarSign,
  FiLayers, FiZap, FiDatabase, FiServer, FiCpu, FiCloud, FiLock, FiShield,
  FiAlertTriangle, FiSlash, FiFileText, FiCreditCard, FiFlag, FiHome, FiBarChart2,
  FiSettings, FiActivity, FiKey, FiLifeBuoy, FiGitBranch, FiSearch, FiBell,
  FiChevronDown, FiChevronRight, FiSun, FiMoon, FiPlus, FiGlobe, FiCommand, FiUser,
  FiLogOut, FiSend, FiTool, FiUserPlus, FiArrowUpRight, FiArrowRight, FiClock,
  FiExternalLink, FiCheckCircle, FiXCircle, FiMenu, FiX, FiMapPin, FiTrendingUp,
  FiTrendingDown, FiEye,
};

// Resolve a string name to an icon component; falls back to a neutral circle.
export default function Icon({ name, ...props }) {
  const C = ICONS[name] || FiCircle;
  return <C {...props} />;
}
