// Super Admin design system — single import surface.
//   import { Panel, DataTable, StatCard, Button, Badge, type, cx } from "../../components/admin";
export * from "../../ui/tokens";
export * from "./format";

export { ConsoleButton as Button } from "../../ui/Button";
export { default as Badge } from "../../ui/Badge";
export { default as StatCard } from "./StatCard";
export { default as KpiCard } from "./KpiCard";
export { default as DataTable } from "./DataTable";
export { default as Panel } from "./Panel";
export { default as StatRow } from "./StatRow";
export { default as TabStrip } from "./TabStrip";
export { default as DetailField } from "./DetailField";
export { default as HealthDot, healthColor } from "./HealthDot";
export { default as Icon, ICONS } from "./icons";
