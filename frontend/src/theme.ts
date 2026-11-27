import { createTheme, rem } from "@mantine/core";

/**
 * Codex desktop design language.
 *
 * Neutral, chat-first, quietly rounded: an ink-on-paper monochrome identity
 * (black primary buttons in light mode, white in dark mode), semantic status
 * colours reserved for real state, soft 1px borders instead of rules, and
 * 6-14px radii throughout. Token values (surfaces, screen, semantic colours,
 * radii, overlay recipe) are extracted from the .codexskin design sources:
 * github.com/Wangnov/awesome-codex-skins (SPEC.md + guts-terminal/asuka-eva02
 * theme.json), github.com/xuhuanstudio/codex-styler.
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
    // The accent is ink itself, warmed to the source skins' ink pair:
    // #e8e5da on dark (ink-light), #2a2a28 on light (ink).
    steelblue: [
      "#f5f4ef",
      "#e8e5da",
      "#d5d2c8",
      "#b3b0a5",
      "#8f8c82",
      "#6e6b63",
      "#55524b",
      "#3f3d38",
      "#2a2a28",
      "#1b1b19",
    ],
    // Warm neutral for chrome, borders and secondary text.
    slate: [
      "#f5f4ef",
      "#eae8e1",
      "#d8d5cc",
      "#b9b6ac",
      "#969389",
      "#74716a",
      "#595752",
      "#42413d",
      "#2b2b28",
      "#1a1a18",
    ],
    // diffRemoved anchor: #d64541 (dark) / #c8300e (light).
    red: [
      "#fdecea",
      "#fbd2ce",
      "#f6a9a2",
      "#ec7d74",
      "#d64541",
      "#c8300e",
      "#8c2d2b",
      "#6b2322",
      "#4e1a19",
      "#361211",
    ],
    // amber / amber-deep from the skins' palette.
    yellow: [
      "#fdf4e7",
      "#f9e5c2",
      "#f1cd92",
      "#e8b96a",
      "#e8a33d",
      "#d97e2a",
      "#b5651f",
      "#8f4f19",
      "#6a3a13",
      "#47270d",
    ],
    // diffAdded anchor: #46c077 (dark) / #2f9e63 (light).
    teal: [
      "#e9faf1",
      "#cfeedd",
      "#a4e5c2",
      "#71d49e",
      "#46c077",
      "#2f9e63",
      "#258252",
      "#1e6742",
      "#185134",
      "#113b26",
    ],
    // semanticColors.skill — the amber that marks AI/model capability in Codex.
    skill: [
      "#fdf4e7",
      "#f9e4c3",
      "#f2ce91",
      "#eabb68",
      "#e8a33d",
      "#d97e2a",
      "#b5651f",
      "#8f4f19",
      "#6a3a13",
      "#47270d",
    ],
    // "cyan" from the skins' palette, desaturated — replaces Mantine's loud
    // default blue for connecting/plan states.
    blue: [
      "#edf7f9",
      "#d7edf2",
      "#b0dbe4",
      "#84c3d1",
      "#4fc3d9",
      "#3d97a8",
      "#307685",
      "#255965",
      "#1a4049",
      "#122c33",
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
    Modal: { defaultProps: { radius: 14, centered: true, shadow: "xl" } },
    Menu: { defaultProps: { radius: 10, shadow: "xl" } },
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
