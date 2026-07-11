# Javis UI 设计知识库
> 来源: shadcn/ui, Tailwind CSS, Ant Design, NextUI, DaisyUI
> 学习日期: 2025-01

---

## 五大黄金法则

### 1. 设计即系统
颜色/间距/字号/圆角/阴影都必须是预定义的有限集合。
- 间距: 4px基准 (4,8,12,16,20,24,32,40,48)
- 圆角: 2,4,6,8,12,16,24,9999px
- 阴影: sm/md/lg/xl 四级
- 字号: 12,14,16,18,20,24,30,36,48

### 2. 层次通过"浮起"表达
底层 → 表层 → 浮层 → 顶层
实现: 背景色深→浅 + 阴影小→大 + backdrop-blur

### 3. 反馈是交互的灵魂
- hover: 颜色变化 150ms
- press: scale(0.97)
- focus: ring-2 发光
- loading: 骨架屏
- result: 通知toast

### 4. 克制配色 = 90%中性色 + 10%语义色
中性色用于背景/边框/文字, 语义色仅用于交互元素

### 5. 动效增强而非分散
- 微交互: 0.15-0.2s ease-out
- 入场: 0.2-0.3s, 位移+透明度
- 绝不超0.5s, 不用bounce

---

## 组件设计模式速查

| 组件 | 关键设计 |
|------|---------|
| Button | 6种变体(primary/secondary/ghost/outline/destructive/link), 3种尺寸 |
| Input | focus ring变色, error红框+提示, label浮动 |
| Card | 圆角8-12px, 微阴影, hover上浮2-4px |
| Modal | 居中, backdrop模糊, 入场scale 0.95→1 |
| Table | 斑马纹, 排序箭头, 行hover, 固定表头 |
| Navbar | Logo左, 菜单中/右, 当前页高亮, 滚动模糊 |
| Toast | 右上角滑入, 3-5s消失, 四色+图标 |
| DarkMode | CSS变量驱动, dark类切换, 200ms过渡 |

---

## 配色参考

### shadcn/ui (极简专业)
- 背景: white/slate-50 → dark: slate-950
- 卡片: white → dark: slate-900
- 主色: 一个品牌色(蓝/紫/绿任选)
- 边框: gray-200 → dark: gray-800

### NextUI (现代活泼)
- 渐变! primary=紫蓝渐变
- 毛玻璃 backdrop-blur
- 彩色阴影 shadow-blue-500/20
- 大圆角 rounded-xl (12px)

### Ant Design (企业稳重)
- 主色 #1677FF 宝蓝
- 纯功能色: 绿/黄/红
- 13级灰阶
- 小圆角 2-6px

### DaisyUI (主题驱动)
- 30+预设主题一键切换
- HSL变量系统
- 语义class: btn-primary, alert-success
- 可混合Tailwind原生class

---

## 实践检查清单

设计任何一个UI组件时，问自己:
- [ ] 间距是否来自预定义系统? (不是随意值)
- [ ] 颜色是否90%中性+10%语义?
- [ ] hover/press/focus/disabled 四个状态是否覆盖?
- [ ] 动画是否 ≤0.3s?
- [ ] 深色模式下是否可读?
- [ ] 无障碍: 是否可通过键盘操作?
