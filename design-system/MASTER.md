# 截流看板 — Design System

**Update**: 2026-05-26 v2 | **Style**: Apple Spatial Glass — Liquid Glass + VisionOS

---

## Style: Apple Spatial Glass

Cinematic dark + frosted glass panels + spring-physics animations. Premium, minimalist.
No pure black. No sharp corners. No instant transitions.

## Color Tokens

| Role | Hex | CSS Variable | Usage |
|------|-----|-------------|-------|
| Background Deep | `#020203` | `--bg-deep` | 页底 |
| Background Base | `#050506` | `--bg-base` | 页面 |
| Surface Glass | `rgba(255,255,255,0.06)` | `--glass` | 卡片/面板 |
| Surface Hover | `rgba(255,255,255,0.10)` | `--glass-hover` | 悬浮态 |
| Border Glass | `rgba(255,255,255,0.08)` | `--glass-border` | 边框/分割线 |
| Accent | `#5E6AD2` | `--accent` | 主按钮、链接、进度条 |
| Accent Glow | `rgba(94,106,210,0.2)` | `--accent-glow` | 发光效果 |
| Success | `#34D399` | `--success` | 运行中、已发送 |
| Warning | `#FBBF24` | `--warning` | 暂停状态 |
| Danger | `#F87171` | `--danger` | 失败、停止 |
| Text Primary | `#EDEDEF` | `--text` | 正文 |
| Text Muted | `#8A8F98` | `--text-muted` | 辅助文字 |

## Typography

- **Family**: Inter (300/400/500/600) — single font, weight-driven hierarchy
- **Chinese Fallback**: `'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC'`
- **Heading**: 600 weight, 16-24px
- **Body**: 400 weight, 13-14px
- **Mono**: SF Mono / ui-monospace for UID/data numbers
- **Line Height**: 1.5 (body), 1.25 (heading)

## Spacing

4, 8, 12, 16, 20, 24, 32, 44, 56

## Corners

- Cards/panels: 16px
- Buttons: 10px
- Modals: 20px
- Inputs: 8px

## Glass Effects

- **Card**: `background: rgba(255,255,255,0.05)`, `backdrop-filter: blur(24px)`, `border: 1px solid rgba(255,255,255,0.08)`
- **Modal**: `background: rgba(10,10,12,0.92)`, `backdrop-filter: blur(44px) saturate(180%)`
- **Top bar**: `background: rgba(5,5,6,0.75)`, `backdrop-filter: blur(20px) saturate(180%)`

## Animations (Apple Spring)

- **Easing**: `cubic-bezier(0.16, 1, 0.3, 1)` — Apple's standard deceleration curve
- **Card hover**: `transform: translateY(-2px)` + accent border glow, 250ms
- **Button press**: `scale(0.97)`, 100ms
- **Modal enter**: `scale(0.92)` → `scale(1)` + opacity 0→1, 300ms spring
- **Progress bar**: width transition 500ms cubic-bezier
- **Page load**: staggered card enter (30ms stagger per card)

## Icons

Inline SVG only. Lucide-style (24px, stroke-width 1.5-2). No emojis.
