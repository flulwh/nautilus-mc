import { createTheme, rem } from "@mantine/core";

/** Deep-ocean palette: dark glass surfaces, one saturated accent. */
export const nautilusTheme = createTheme({
  primaryColor: "abyss",
  primaryShade: { light: 6, dark: 4 },
  defaultRadius: "md",
  fontFamily:
    '"Inter", "SF Pro Text", -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", sans-serif',
  fontFamilyMonospace: '"JetBrains Mono", "Cascadia Code", "SFMono-Regular", Consolas, monospace',
  headings: {
    fontFamily: '"Inter", "Noto Sans SC", sans-serif',
    fontWeight: "650",
    sizes: {
      h1: { fontSize: rem(30), lineHeight: "1.2" },
      h2: { fontSize: rem(22), lineHeight: "1.25" },
      h3: { fontSize: rem(17), lineHeight: "1.3" },
    },
  },
  colors: {
    abyss: [
      "#e8fbff",
      "#cbf3fb",
      "#9ce8f7",
      "#66d9ef",
      "#35c6e1",
      "#14adc9",
      "#0a8ba4",
      "#0a6f83",
      "#0c5868",
      "#0b4553",
    ],
    ink: [
      "#f4f7fa",
      "#dfe6ee",
      "#c2ccd9",
      "#9aa8b8",
      "#6f7f92",
      "#556475",
      "#3d4a58",
      "#2a343f",
      "#1a222b",
      "#0e141a",
    ],
  },
  components: {
    Card: { defaultProps: { withBorder: true, radius: "lg" } },
    Button: { defaultProps: { radius: "md" } },
    Modal: { defaultProps: { radius: "lg", centered: true } },
    Table: { defaultProps: { highlightOnHover: true, verticalSpacing: "xs", horizontalSpacing: "sm" } },
    Tooltip: { defaultProps: { withArrow: true, openDelay: 300 } },
  },
});

export const stateColor: Record<string, string> = {
  running: "teal",
  starting: "yellow",
  stopping: "orange",
  crashed: "red",
  stopped: "gray",
};
