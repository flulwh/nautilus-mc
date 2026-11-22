import { createTheme, rem } from "@mantine/core";

/**
 * Codex desktop design language.
 *
 * Neutral, chat-first, quietly rounded: an ink-on-paper monochrome identity
 * (black primary buttons in light mode, white in dark mode), semantic status
 * colours reserved for real state, soft 1px borders instead of rules, and
 * 6-12px radii throughout. Mirrors the OpenAI Codex desktop app; references:
 * github.com/Wangnov/awesome-codex-skins, github.com/xuhuanstudio/codex-styler.
 */
export const nautilusTheme = createTheme({
  primaryColor: "steelblue",
  primaryShade: { light: 9, dark: 1 },
  defaultRadius: 8,
  fontFamily:
    '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", "Noto Sans SC", system-ui, sans-serif',
  fontFamilyMonospace: 'ui-monospace, "Cascadia Mono", Consolas, "SFMono-Regular", Menlo, monospace',
  radius: { xs: rem(4), sm: rem(6), md: rem(8), lg: rem(12), xl: rem(16) },
  fontSizes: {
    xs: rem(11.5),
    sm: rem(12.5),
    md: rem(13.5),
    lg: rem(15),
    xl: rem(18),
  },
  lineHeights: {
    xs: "1.45",
    sm: "1.5",
    md: "1.55",
    lg: "1.5",
    xl: "1.4",
  },
  headings: {
    fontFamily: "inherit",
    fontWeight: "600",
    sizes: {
      h1: { fontSize: rem(20), lineHeight: "1.3" },
      h2: { fontSize: rem(16), lineHeight: "1.35" },
      h3: { fontSize: rem(14), lineHeight: "1.4" },
      h4: { fontSize: rem(13), lineHeight: "1.4" },
    },
  },
  colors: {
    // The accent is ink itself: near-white on dark, near-black on light.
    steelblue: [
      "#f7f7f8",
      "#e8e8ea",
      "#d4d4d8",
      "#b4b4bc",
      "#8e8e96",
      "#6f6f77",
      "#55555c",
      "#3f3f45",
      "#28282c",
      "#121214",
    ],
    // Cool neutral for chrome, borders and secondary text.
    slate: [
      "#f7f7f8",
      "#ececee",
      "#d9d9dd",
      "#b9b9c0",
      "#96969e",
      "#75757d",
      "#5c5c64",
      "#45454c",
      "#2e2e33",
      "#1a1a1d",
    ],
    red: [
      "#fdecea",
      "#fbd3cf",
      "#f6a9a1",
      "#ee7d72",
      "#e5534b",
      "#c9372f",
      "#a52a24",
      "#83221d",
      "#651c18",
      "#4a1512",
    ],
    yellow: [
      "#fdf6e7",
      "#f9e9c4",
      "#f0d494",
      "#e3ba5e",
      "#d4a341",
      "#b5852f",
      "#93692a",
      "#745227",
      "#573d1f",
      "#3d2b17",
    ],
    teal: [
      "#eafaf1",
      "#d3f4e0",
      "#a8e8c4",
      "#75d8a5",
      "#48c487",
      "#2fa871",
      "#24865d",
      "#1d6b4c",
      "#17553d",
      "#123f2e",
    ],
    // Chart line for the memory series — muted violet.
    grape: [
      "#f1eefc",
      "#e0d9f8",
      "#c6b8f0",
      "#a691e4",
      "#8a6fd6",
      "#6f52c0",
      "#583f9c",
      "#443179",
      "#31255a",
      "#211940",
    ],
  },
  components: {
    Card: { defaultProps: { withBorder: true, radius: 8 } },
    Paper: { defaultProps: { radius: 8 } },
    Button: { defaultProps: { radius: 6, uppercase: false, fw: 500 } },
    ActionIcon: { defaultProps: { radius: 6, variant: "subtle" } },
    Badge: { defaultProps: { radius: 4, tt: "none", fw: 500 } },
    Progress: { defaultProps: { radius: 999 } },
    Modal: { defaultProps: { radius: 12, centered: true, shadow: "md" } },
    Table: {
      defaultProps: {
        highlightOnHover: true,
        verticalSpacing: 7,
        horizontalSpacing: 10,
        fz: 12.5,
      },
    },
    Tooltip: { defaultProps: { withArrow: false, openDelay: 200, fz: 11.5 } },
  },
});

export const stateColor: Record<string, string> = {
  running: "teal",
  starting: "yellow",
  stopping: "orange",
  crashed: "red",
  stopped: "gray",
};
