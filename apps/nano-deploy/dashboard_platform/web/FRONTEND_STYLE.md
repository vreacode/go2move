# CarMove 前端风格基准

这套前端以 `web.zip` 中的控制台视觉为长期基准：浅灰页面底、白色面板、蓝色科技强调色、清晰的分区层级，以及适合户外操作的高对比文字。

## 视觉 tokens

- 页面：`--color-page`，默认使用 slate-100。
- 面板：`--color-panel`，默认使用白色。
- 卡片：`--color-card`，默认使用 slate-50。
- 边框：`--color-base`，默认使用 slate-200。
- 主文字：`--color-primary`，默认使用 slate-800。
- 辅助文字：优先使用 `secondary`、`tertiary`、`muted`，不要直接使用低对比灰色。
- 强调色：默认使用 sky-500/sky-700；危险操作使用明确的红色。

## 组件规则

- 桌面工作区保持左侧 UGV 菜单 + 右侧内容区结构。
- 内容面板统一通过 `Panel`，使用轻边框、`rounded-lg` 和 `p-6`。
- 控制按钮要有明确的 hover、focus 和 disabled 状态。
- 数据类内容使用小标签、短值和等宽字体，避免大段装饰性文案。
- 手机端复用同一套颜色变量，不再另起深色荧光色板；照片和视频区域可以保留黑色媒体底。

## 新页面检查

新增页面或组件时，先使用现有 Tailwind tokens（`bg-page`、`bg-panel`、`bg-card`、`border-base`、`text-primary`、`text-muted`、`accent-*`），只有无法表达的特殊视觉才增加新的 CSS 变量。
