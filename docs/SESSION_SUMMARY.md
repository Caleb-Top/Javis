# Javis 对话总结 (2026-07-10/11)

## 项目状态
Javis = Windows 桌面智能助手。FastAPI + WebSocket Web 界面，DeepSeek API (deepseek-v4-pro) 驱动。

## 关键修复清单

### 1. API Key 加密灾难
- XOR + 机器特征加密 → VM与本机密钥不同 → Key 损坏 → 401
- **方案**: 换成纯 base64 编码，跨环境一致

### 2. DeepSeek 不支持多模态
- `image_url` → 400 错误 `unknown variant 'image_url'`
- **方案**: 放弃视觉注入，走控件操作路径 (read_ui_window → click_element/set_value)

### 3. focus_window("QQ音乐") 永远失败
- QQ 音乐窗口标题 = "违背的青春 - 薛之谦"，不含"QQ音乐"
- **方案**: `_score_match` 打分制匹配 + APP_ALIASES 别名表 + 5s 缓存 + 前台窗口优先

### 4. 打开微信弹出微软商店
- `system_execute start 微信` 或 `open_app` fallback 导致
- **方案**: `_is_store_lnk()` 拦截 + `open_app` 不再 fallback

### 5. 前端废话太多
- wait/keyboard_press/keyboard_type/list_windows 全部显示
- **方案**: 前端过滤掉这些，只显示有意义操作

### 6. 提示词爆炸
- 多层嵌套的 SYSTEM_PROMPT + 额外混杂，`"""` 提前结束导致 SyntaxError
- **方案**: 精简提示词，统一控件操作优先策略

### 7. 上下文太短
- `self.state.messages[-16:]` → "继续"丢失上文
- **方案**: 扩大到 30 条

### 8. agent.py 多行重复
- `vision` / `_pending_image` / `last_screenshot` 残留代码
- **方案**: 全部清除

## 已实现功能 (v1.6 → v2.0)
- 技能系统: 7 个独立技能，可切换，可编译 .exe
- 统一智能匹配: `_smart_match_window` 打分制
- 窗口缓存: 5s TTL，psutil 延迟加载
- 商店 lnk 拦截: `_is_store_lnk` PowerShell 解析
- Codex 兼容: `scroll_element` / `set_value` / `perform_secondary_action`
- 别名搜索: `_alias_search_names` / `APP_ALIASES` / `_APP_NAME_ALIASES`
- 对话记忆: `D:\Javis\memory\` 文件系统，10000 条 / 30 天

## 用户偏好
- 中文，讨厌弹商店/重新填 Key
- 控件操作 > 截图视觉
- 智能一次完成，不要分步
- 快速优先，缓存加速

## 未完成
- DeepSeek 不支持 vision → 换 Claude/GPT-4o 才有截图视觉
- QQ音乐/Chrome 自绘界面 → read_ui_window 读不到控件
- 唤醒词 / 系统托盘 / 用户中断检测(Escape)
