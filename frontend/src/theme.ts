import { createTheme, rem } from "@mantine/core";

/**
 * Industrial control console, light scheme.
 *
 * A workshop-instrument look: paper-grey surfaces, near-black ink, thin rules,
 * one utilitarian blue accent, square corners, tabular numerals. No shadows,
 * no gradients, no rounded toy buttons — it should read like a rack-mount
 * controller you leave open on a second monitor, not a SaaS landing page.
 */
export const nautilusTheme = createTheme({
  primaryColor: "steelblue",
  primaryShade: { light: 7, dark: 5 },
  defaultRadius: 2,
  fontFamily:
    '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", "Noto Sans SC", system-ui, sans-serif',
  fontFamilyMonospace: 'ui-monospace, "Cascadia Mono", Consolas, "SFMono-Regular", Menlo, monospace',
  fontSizes: {
    xs: rem(11),
    sm: rem(12),
    md: rem(13),
    lg: rem(15),
    xl: rem(17),
  },
  lineHeights: {
    xs: "1.4",
    sm: "1.45",
    md: "1.5",
    lg: "1.5",
    xl: "1.4",
  },
  headings: {
    fontFamily: "inherit",
    fontWeight: "700",
    sizes: {
      h1: { fontSize: rem(19), lineHeight: "1.3" },
      h2: { fontSize: rem(16), lineHeight: "1.35" },
      h3: { fontSize: rem(13.5), lineHeight: "1.4" },
      h4: { fontSize: rem(12.5), lineHeight: "1.4" },
    },
  },
  colors: {
    // One working accent: instrument-panel blue, usable on light surfaces.
    steelblue: [
      "#e8eef4",
      "#d3e0ec",
      "#aac3da",
      "#7ea2c4",
      "#5783ad",
      "#3f6b96",
      "#2f567d",
      "#244361",
      "#1b3349",
      "#132435",
    ],
    // Cool neutral used for chrome, borders and secondary text.
    slate: [
      "#f2f4f6",
      "#e2e6ea",
      "#cad0d6",
      "#aeb6bf",
      "#8e98a2",
      "#6f7a85",
      "#566069",
      "#3f474e",
      "#2b3137",
      "#191d21",
    ],
    // Damped status colours: legible without turning the page into a traffic light.
    red: [
      "#f6eaea",
      "#e9d1d1",
      "#d7aeae",
      "#bd8686",
      "#a46464",
      "#8f5151",
      "#763f3f",
      "#5d3232",
      "#442626",
      "#2f1b1b",
    ],
    yellow: [
      "#f6f1e4",
      "#eae0c9",
      "#d8c7a1",
      "#bea974",
      "#a48a52",
      "#8d7440",
      "#755e34",
      "#5c4a2a",
      "#433720",
      "#2f2718",
    ],
    teal: [
      "#e7f1ee",
      "#cfe2db",
      "#abcabd",
      "#82ad9c",
      "#608f7e",
      "#487567",
      "#385d52",
      "#2c4941",
      "#213830",
      "#172723",
    ],
  },
  components: {
    Card: { defaultProps: { withBorder: true, radius: 2 } },
    Paper: { defaultProps: { radius: 2 } },
    Button: { defaultProps: { radius: 2, uppercase: true, fw: 600 } },
    ActionIcon: { defaultProps: { radius: 2, variant: "subtle" } },
    Badge: { defaultProps: { radius: 0, tt: "uppercase", fw: 600 } },
    Progress: { defaultProps: { radius: 0 } },
    Modal: { defaultProps: { radius: 2, centered: true, shadow: "none" } },
    Table: {
      defaultProps: {
        highlightOnHover: true,
        verticalSpacing: 5,
        horizontalSpacing: 8,
        fz: 12,
      },
    },
    Tooltip: { defaultProps: { withArrow: false, openDelay: 200, fz: 11 } },
  },
});

export const stateColor: Record<string, string> = {
  running: "teal",
  starting: "yellow",
  stopping: "orange",
  crashed: "red",
  stopped: "gray",
};
