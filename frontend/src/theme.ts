import { createTheme, rem } from "@mantine/core";

/**
 * A plain operations console: flat surfaces, 1px rules, one muted accent,
 * square corners, no shadows and no gradients. The UI is meant to read like a
 * tool you leave open in a tab, not like a product page.
 */
export const nautilusTheme = createTheme({
  primaryColor: "moss",
  primaryShade: { light: 6, dark: 5 },
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
    fontWeight: "600",
    sizes: {
      h1: { fontSize: rem(19), lineHeight: "1.3" },
      h2: { fontSize: rem(16), lineHeight: "1.35" },
      h3: { fontSize: rem(13.5), lineHeight: "1.4" },
      h4: { fontSize: rem(12.5), lineHeight: "1.4" },
    },
  },
  colors: {
    // Muted grass green. One accent for links, primary buttons and active
    // states — no second hue, no gradient partner.
    moss: [
      "#eef2ec",
      "#dbe4d6",
      "#bccdb4",
      "#9ab38f",
      "#7a9a6d",
      "#628253",
      "#4f6b43",
      "#3f5637",
      "#32452c",
      "#273622",
    ],
    // Neutral surfaces for text and borders.
    slate: [
      "#e9ecef",
      "#d6dbe0",
      "#bcc3cb",
      "#9aa3ad",
      "#7a838e",
      "#616a74",
      "#4a525b",
      "#373d45",
      "#262b31",
      "#181c21",
    ],
    // Semantic colours are damped down on purpose: destructive actions and
    // warnings should be readable, not shouty.
    red: [
      "#f4e8e8",
      "#e6cfcf",
      "#d0aaaa",
      "#b68484",
      "#9d6666",
      "#8a5555",
      "#714545",
      "#5a3737",
      "#452a2a",
      "#331f1f",
    ],
    yellow: [
      "#f4f0e4",
      "#e6dfc8",
      "#d0c6a3",
      "#b6a97c",
      "#9d8f5f",
      "#8a7c4e",
      "#71653f",
      "#5a5032",
      "#453d26",
      "#332d1c",
    ],
    teal: [
      "#e8f0ec",
      "#d2e0d8",
      "#b0c9bc",
      "#8bafa0",
      "#6d9585",
      "#567d6d",
      "#456757",
      "#374f43",
      "#2b3e35",
      "#20302a",
    ],
  },
  components: {
    Card: { defaultProps: { withBorder: true, radius: 2 } },
    Paper: { defaultProps: { radius: 2 } },
    Button: { defaultProps: { radius: 2 } },
    ActionIcon: { defaultProps: { radius: 2 } },
    Badge: { defaultProps: { radius: 0 } },
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
