# 截流看板 — Design System

**Generated**: 2026-05-26 | **Tool**: UI/UX Pro Max

---

## Pattern
Real-Time / Operations Landing — 监控型 Dashboard，多任务并行状态展示，数据密集但可快速扫描。

## Style: Dark Mode (OLED)
纯深色主题，高对比度，适合长时间监控使用。深蓝黑底 + 状态色语义化。

## Color Tokens

| Role | Hex | CSS Variable | Usage |
|------|-----|-------------|-------|
| Background | `#080C14` | `--bg` | 页面底色 |
| Surface | `#111827` | `--surface` | 卡片、模态框 |
| Surface Elevated | `#1A2332` | `--surface-2` | 悬浮态 |
| Border | `#1E2D3D` | `--border` | 分割线、边框 |
| Primary | `#3B82F6` | `--primary` | 主按钮、链接、进度条 |
| Primary Muted | `#1E3A5F` | `--primary-muted` | 主色背景层 |
| Accent | `#F59E0B` | `--accent` | 暂停状态、警告 |
| Success | `#22C55E` | `--success` | 运行中、已发送 |
| Danger | `#EF4444` | `--danger` | 失败、停止、错误 |
| Text Primary | `#E2E8F0` | `--text` | 正文 |
| Text Secondary | `#94A3B8` | `--text-muted` | 辅助文字 |
| Text Inverse | `#0F172A` | `--text-inverse` | 深色按钮上的文字 |

## Typography

- **Heading**: Fira Code (500-700) — 状态标签、数字、代码
- **Body**: Fira Sans (400-500) — 正文、按钮、标签
- **Chinese Fallback**: `'Microsoft YaHei', 'PingFang SC', 'Noto Sans SC'`
- **Base Size**: 14px body, 11-13px labels, 16-24px headings
- **Line Height**: 1.5 body, 1.25 heading

## Spacing Scale (4dp)

4, 8, 12, 16, 20, 24, 32, 40, 48

## Effects

- Card hover: border-color transition 200ms + subtle glow
- Button press: scale(0.97) 100ms
- Progress bar: width transition 400ms ease-out
- Modal: backdrop blur + scale+fade enter 200ms
- Status pulse: running indicator pulse animation 2s

## Icons

Use inline SVG only. No emojis. Source: Lucide-style (24px, stroke-width 2).

## Status Colors

| Status | Color | Usage |
|--------|-------|-------|
| Running | `--success` `#22C55E` | 运行中卡片边框、状态标签 |
| Paused | `--accent` `#F59E0B` | 暂停卡片边框、状态标签 |
| Idle | `--text-muted` `#94A3B8` | 待开始状态标签 |
| Error/Failed | `--danger` `#EF4444` | 失败数、错误状态 |
