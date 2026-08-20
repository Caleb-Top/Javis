# Javis L4 Computer Twin 实施计划

**日期：** 2026-08-20  
**目标：** 交付受 capability、用户 grant、TTL、隐私和模型预算治理的环境事实闭环。

## 全局约束

- 实现依赖已提交的 L2 `AccessContext` 与 L3 主体边界；不得用客户端 body 声明 owner。
- `EnvironmentService` 是快照单写者；适配器、模型与 UI 只提交命令或读取快照。
- 原始媒体 TTL 为零，禁止落盘、禁止 EventBus、禁止 LifeJournal。
- 每个任务先写严格合同与失败测试，再接生产入口。
- 不增加新的持续录屏进程或云端媒体默认路径。

## Task 1：冻结 L4 合同

**创建：** `core/environment/contracts.py`、`tests/test_environment_contracts.py`

定义 `EnvironmentObservationV1`、`EnvironmentFact`、`EnvironmentSnapshotV1`、source/privacy/retention 枚举、严格 wire parser、canonical hash 与时间/大小限制。测试未知字段、非法时间、超限 attributes、错误 boot 与倒退 sequence。

## Task 2：扩展 runtime capability

**修改：** `core/runtime_access.py`  
**测试：** `tests/test_runtime_access.py`、`tests/test_environment_runtime_access.py`

增加五个 L4 scope；保持现有 token、origin、loopback、boot binding 与 TTL。证明旧 token 无新权限、scope 不可通配、日志不泄露 bearer。

## Task 3：实现 EnvironmentGrantStore

**创建：** `core/environment/grants.py`、`tests/test_environment_grants.py`

实现服务端 grant、once 消费、session 关闭、persistent 撤销、foreground、model visibility 与 media egress。写入用户数据根，原子更新，禁止模型工具暴露 grant 写入口。

## Task 4：实现最小化与来源适配器合同

**创建：** `core/environment/minimizer.py`、`core/environment/sources.py`、`tests/test_environment_minimizer.py`

为 foreground app、process health、device health、workspace metadata、screen OCR/object 定义 allowlist。输入包含 secret、全文、路径、命令行、像素或生物信息时拒绝或最小化；输出不能引用原始 buffer。

## Task 5：实现 EnvironmentReducer 与 Service

**创建：** `core/environment/state.py`、`core/environment/service.py`、`core/environment/__init__.py`  
**测试：** `tests/test_environment_state.py`、`tests/test_environment_service.py`

实现按 key 归约、TTL、stale/unknown、source health、permission revision、幂等、乱序和 boot 隔离。使用有界内存与可注入时钟；快照不整体持久化。

## Task 6：接入 runtime 与受控事件

**修改：** `core/runtime.py`  
**创建：** `core/environment/event_adapter.py`  
**测试：** `tests/test_environment_runtime_integration.py`

装配唯一 EnvironmentService；EventBus 只发布薄事件 `environment.snapshot.changed`、`environment.permission.changed`、`environment.source.degraded`，不得包含 attributes、标题、路径或媒体。

## Task 7：治理 workspace

**修改：** `main.py`、`core/workspace_manager.py`  
**测试：** `tests/test_workspace_environment_access.py`

所有 workspace 读取同时检查 runtime scope、AccessContext 与 EnvironmentGrant；路径只在用户授权根，拒绝 traversal、junction/symlink 逃逸与源码根隐式授权。读取结果按 purpose 限长，不自动成为环境事实。

## Task 8：治理屏幕与摄像头

**修改：** `app/src-tauri/src/screen_capture.rs`、`app/src-tauri/src/main.rs`、`app/src/panels/ControlDrawer.ts`、`main.py`  
**测试：** Rust capture permit tests、`tests/test_environment_capture_access.py`、App source contract tests

服务端为已授权的一次采集签发短期、boot-bound、source-bound permit；Tauri 命令验证 permit 后采集，完成即消费。取消、失败和超时清零 buffer。摄像头采用同一 permit 合同。

## Task 9：治理 OCR、YOLO 与 VLM egress

**修改：** `perception/service.py`、`perception/adapters/ocr.py`、`perception/adapters/vlm.py`、`memory/session_db.py`  
**测试：** `tests/test_environment_perception_privacy.py`

OCR/YOLO 只生成 10 秒最小事实；SessionEventStore 不再存 OCR 全文。VLM 非 loopback 需要 `media_egress_allowed`，发送前再次最小化并记录无内容审计收据。

## Task 10：模型只读上下文与注意选择

**创建：** `core/environment/context.py`、`core/environment/attention.py`  
**修改：** `core/prompt_builder.py`  
**测试：** `tests/test_environment_model_context.py`、`tests/test_environment_attention.py`

每回合最多 8 项、2048 字节；先按主体/grant/model visibility 过滤。普通事实不进入 L1，只有确定性 safety allowlist 形成受治理 cue。禁止环境写工具扫描。

## Task 11：只读 API 与前端权限面

**创建：** `core/environment/api.py`、`app/src/environment/environmentTypes.ts`、`app/src/environment/EnvironmentBridge.ts`  
**修改：** `main.py`、`app/src/settings/SettingsSurface.ts`  
**测试：** Python API tests、App strict parser/grant UI tests

提供只读 snapshot/source health 和用户显式 grant 管理命令；所有 API 先校验 runtime scope 与主体。前端显示来源、范围、用途、剩余时间和撤销，不展示 raw payload。

## Task 12：恢复、隐私与发布合同

**创建：** `tests/test_environment_release_contract.py`、`docs/verification/JAVIS_L4_D_DRIVE_ACCEPTANCE.md`

扫描数据库、日志、事件、提示词、崩溃恢复和构建产物中的媒体/OCR 标记；验证重启、撤销、时钟漂移、来源故障、工作区逃逸、云 egress 和降级。D 盘记录 Windows、WebView2、显示器、工作区根、grant、撤销延迟和媒体零落盘证据。

## 阶段出口

1. 全部 Python、App、TypeScript、Vite 与可用 Rust/Tauri 门通过。
2. 真实截图、OCR、工作区和设备健康走统一双门。
3. 原始媒体与敏感全文零持久化，stale 不复活。
4. 关闭 L4 后 L0/L1、对话、语音、打断和关闭流程不回归。
5. 精确提交、安装包哈希和 D 盘矩阵可追溯；未执行真机项标记 `NOT EXECUTED`。
