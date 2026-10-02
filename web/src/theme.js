/**
 * Mini-Drop 主题令牌。
 *
 * 所有颜色、间距、字号集中管理，组件和页面统一引用此处，
 * 不再在 inline style 中硬编码具体色值或尺寸。
 */

// ── 颜色 ──────────────────────────────────────────────────

export const COLORS = {
  // 品牌
  primary: "#2458d3",
  primaryBg: "#edf2fd",

  // 状态
  success: "#14795a",
  warning: "#a46b10",
  error: "#bf3e4c",
  running: "#2458d3",
  pending: "#d9d9d9",
  offline: "#8c8c8c",

  // 中性色
  border: "#e3e7ef",
  borderSecondary: "#d5dce8",
  background: "#f6f7fa",
  cardBackground: "#ffffff",
  textPrimary: "#18243b",
  textSecondary: "#5d6b82",
  textTertiary: "#65738a", // 辅助文字也在浅色底上保持可读。

  // 特殊
  nlpHighlight: "#a46b10",
  aiTag: "orange",
  codeBackground: "#f0f3f8",
};

// ── 间距 ──────────────────────────────────────────────────

export const SPACING = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
};

// ── 字号 ──────────────────────────────────────────────────

export const FONT_SIZES = {
  sm: 12,
  md: 14,
  lg: 16,
  xl: 20,
  title: 18,
};

// ── 布局 ──────────────────────────────────────────────────

export const LAYOUT = {
  siderWidth: 232,
  contentMaxWidth: 1460,
  headerHeight: 72,
};

// ── 动画 ──────────────────────────────────────────────────

export const ANIMATION = {
  fadeIn: "fadeIn 0.3s ease-in-out",
};

export const APP_THEME = {
  token: {
    colorPrimary: COLORS.primary, colorSuccess: COLORS.success,
    colorWarning: COLORS.warning, colorError: COLORS.error,
    colorInfo: COLORS.primary, colorInfoBg: "#f0f4fe", colorInfoBorder: "#d3def6",
    colorSuccessBg: "#eef7f3", colorSuccessBorder: "#cce5d9",
    colorWarningBg: "#fff8e9", colorWarningBorder: "#e9d8b2",
    colorErrorBg: "#fff2f3", colorErrorBorder: "#edcbd1",
    colorText: COLORS.textPrimary, colorTextSecondary: COLORS.textSecondary,
    colorTextPlaceholder: COLORS.textTertiary,
    colorBorder: COLORS.borderSecondary, colorBorderSecondary: COLORS.border,
    colorBgLayout: COLORS.background, colorBgContainer: COLORS.cardBackground,
    borderRadius: 10, fontSize: 14, controlHeight: 40,
    fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
    motionDurationMid: "0.2s", motionDurationSlow: "0.28s",
  },
  components: {
    Button: { fontWeight: 600, primaryShadow: "0 3px 8px rgba(36, 88, 211, 0.14)" },
    Card: { headerFontSize: 16, headerHeight: 58, paddingLG: 24 },
    Menu: { itemHeight: 46, itemSelectedBg: COLORS.primaryBg, itemSelectedColor: COLORS.primary },
    Segmented: { trackBg: "#eef1f6", itemSelectedBg: "#ffffff" },
    Table: { headerBg: "#f8f9fc", cellPaddingBlock: 16 },
  },
};

// ── 火焰图 ────────────────────────────────────────────────

export const FLAMEGRAPH = {
  defaultHeight: 480,
  minHeight: 300,
  maxHeight: 720,
  cellHeight: 18,
  transitionDuration: 750,
};
