// Theme — re-export the existing ThemeContext so the design system exposes one
// import surface. (The provider itself stays in src/theme so app bootstrap is unchanged.)
export { ThemeProvider, useTheme } from "../theme/ThemeContext";
