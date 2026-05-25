# 统一私信截流框架 — 设计方案

**日期**: 2026-05-25
**版本**: v1.0
**范围**: 框架核心 + 漂漂首个 App 实现
**原则**: 去掉 UI 自动化，只做协议脚本。纯 API 或 API + Frida NIM。

---

## 1. 设计决策

| 决策点 | 选择 | 说明 |
|--------|------|------|
| 架构 | B 方案 · 继承模式 | BaseClient 内置 Pipeline，App 继承后只实现 3 个方法 |
| Pipeline | 逐房遍历 | 房间缓存 → 断点恢复 → 拉排行 → 过滤已发 → 逐人私信 |
| 任务 | 多 App 并行 | 浓缩看板（首页）→ 点击进入详情页 |
| 数据源 | 贡献榜 | 各 App config 声明时段/性别选项，Dashboard 动态渲染 |
| 本地状态 | 3 个 JSON | rooms_cache.json / sent_today.json / progress.json |
| 认证 | 双模式 | auto（框架登录）/ manual（手动填 token，逆向困难时兜底） |
| 错误处理 | 统一跳过 | 排行失败 → 下一间；私信失败 → 记录原因 → 下一人 |
| 私信方式 | 3 种 | rest（纯 HTTP）/ frida_nim（Frida 桥）/ none（纯浏览） |
| 发送顺序 | 排名从高到低 | 按贡献值降序 |
| 发送节奏 | Dashboard 可配 | 用户填入间隔秒数 |
| 首个 App | 漂漂 | 纯 REST，最快验证框架 |
| 梦音代码 | 先不动 | 等框架跑通后再重构接入 |

---

## 2. 核心截流 Pipeline

```
1. 获取全部房间（存 rooms_cache.json，一直保留直到手动点"重新扫描"）
        ↓
2. 断点恢复（读 progress.json，找上次中断的房间）
        ↓
3. 逐房循环（全自动，不需要任何确认）
   ┌─────────────────────────────────────────────────┐
   │ 3a. 拉排行榜 → 失败则跳过，下一间              │
   │ 3b. 过滤已发 → 跳过 sent_today.json 中的 uid    │
   │ 3c. 逐人私信 → 按排名从高到低，随机选话术       │
   │     成功后写入 sent_today.json                   │
   │     失败（等级不足/F_BAN/免打扰）→ 记录原因 → 下一人 │
   │ 3d. 更新 progress.json（每发完一人立即写）        │
   └─────────────────────────────────────────────────┘
   ↓ 本间发完 → 下一间 → 直到全部完成
```

---

## 3. 框架代码结构

```
截流框架/
  framework/
    core/
      base_client.py        # Pipeline + HTTP 封装 + 过滤
      state_manager.py      # 每 App 独立 .state/ 目录读写
      task_manager.py       # 多任务调度 · 启动/暂停/状态
      dashboard.py          # Flask 统一面板
    bridge/
      nim_bridge.py         # Frida NIM 桥（可选）
      hook_nim.js           # NIM SDK 注入脚本
  apps/
    piaopiao/
      config.json           # 认证 + 端点 + 数据源 + 筛选
      client.py             # 3 个方法（~60 行）
      .state/               # rooms_cache / sent_today / progress
    mengyin/                # （暂不动）
```

---

## 4. BaseClient 接口

```python
class BaseClient:
    # ═══ App 必须实现 ═══
    def fetch_all_rooms(self) -> list
        # 返回 [{id, name, type, ...meta}]
        # type 用于 fetch_room_ranking 内部判断接口

    def fetch_room_ranking(self, room, period) -> list
        # 返回 [{uid, nick, amount, gender}]
        # 根据 room.type 选择语音厅/视频直播接口

    def send_message(self, uid, text) -> dict
        # 返回 {success: bool, error: str}

    # ═══ 可选重写（默认覆盖大多数情况）═══
    def authenticate(self) -> bool
        # auto: 走登录接口 / manual: 返回 config 中 token
    def build_request(self, path, params)
        # GET with query params 或 POST with JSON body
    def parse_response(self, r)
        # 解密 + code 检查
    def parse_user(self, raw)
        # 统一为 {uid, nick, amount, gender}

    # ═══ 框架内置 ═══
    run_pipeline()          # 从断点开始 → 逐房 run_room() → 完成
    run_room(room)          # 单间房完整流程
    skip_today(uid)         # 查 sent_today.json
    mark_sent(uid, nick, room)  # 写 sent_today.json
    save_progress()         # 更新 progress.json
```

---

## 5. 认证双模式

```json
// auto — 框架自动登录
{
  "auth_mode": "auto",
  "mobile": "13800138000",
  "sms_code": "123456"
}

// manual — 手动填 token（逆向困难兜底）
{
  "auth_mode": "manual",
  "token": "jFOm3hZAcMy09UW2QiC...",
  "uid": "91769319"
}
```

Dashboard 显示当前认证方式，manual 模式下提示"手动刷新"。

---

## 6. 三个本地状态文件

**rooms_cache.json** — 房间列表快照。手动点"重新扫描"才更新。

```json
{ "app": "piaopiao", "rooms": [{ "id": "1045916", "name": "悦悦", "type": "voice" }, ...192] }
```

**sent_today.json** — 今日已发用户。每天 00:00 自动清空。

```json
{ "date": "2026-05-25", "sent": [{ "uid": "90734574", "nick": "杨间", "room": "悦悦", "time": "14:31" }] }
```

**progress.json** — 断点追踪。每发完一人立即更新。

```json
{ "app": "piaopiao", "total": 192, "current_room": 14, "room_id": "...", "sent_total": 118, "failed": 3 }
```

---

## 7. 数据源 / 筛选配置

每个 App 在 config.json 中声明，Dashboard 动态渲染下拉框：

```json
// 漂漂
{ "data_sources": ["贡献榜"], "periods": ["今日", "本周"], "genders": ["全部", "男", "女"] }

// 梦音
{ "data_sources": ["财富榜", "公会房间榜"], "periods": ["今日", "本周", "昨日", "上周"], "genders": ["全部", "男神", "女神"] }
```

---

## 8. Dashboard 两层结构

### 首页：浓缩看板 `/`

- 所有 App 卡片网格（每个 App 一个卡片）
- 卡片显示：名称、运行状态、进度条、已发/失败数、当前房间、配置标签
- 操作按钮：开始/继续、暂停、停止、详情、设置
- "+" 空位卡片 → 添加新 App

### 详情页：任务面板 `/app/{name}`

- 顶部：返回看板 + App 名称 + 运行状态 + 进度 + 暂停按钮 + 设置
- 左侧：实时进度（最多显示 4 间房）+ 统计 + 查看全部
- 中间：当前房间排行列表（已发灰化标注、失败红色标注）
- 右侧：话术模板（一行一条，{nick} 占位，发送时随机）+ 发送日志 + 今日统计
- 发送设置：间隔秒数、数据源、时段、性别

---

## 9. 发送规则

- 按排名从高到低依次发送
- 每条间隔 N 秒（Dashboard 可配）
- 今日已发用户自动跳过
- 发送失败（等级不足、F_BAN、免打扰）→ 记录原因 → 跳过，不重试
- 拉排行失败 → 跳过该房间，继续下一间
- 全部房间完成 → 弹出"群发完成"

---

## 10. 新 App 接入流程

```
mitmproxy 抓包 → 识别 API/参数 → 写 config.json + client.py（3 个方法）→ Dashboard 重新扫描房间 → 开始群发 → 完成
纯 API App：~15 分钟
API + Frida App：~1-2 小时（含 Gadget 注入）
```

---

## 11. 漂漂实现要点（首个 App）

- **API**: `api.pp.weimipopo.com`，POST + JSON body，明文响应
- **房间**: 语音厅 listByCat (66间) + 视频直播 category/live (126间)
- **排行**: 语音厅 `/room/rank/list/contribute/rank` (unRoomId) + 视频 `/gift/list/contribute/rank` (tid)
- **分页**: offset/limit
- **私信**: preCheck（拿 msgChatId）→ send
- **限制**: richLevel ≥ 6
- **认证**: auto 模式走短信登录，manual 模式用已有 token

---

## 12. 前后对比

| 维度 | 现在 | 框架化之后 |
|------|------|-----------|
| 新增 App 代码 | ~500 行 | ~60 行（3 个方法） |
| 多 App 并行 | — | 浓缩看板 + 详情页 |
| 房间遍历/缓存 | 每次手写 | BaseClient 内置 |
| 断点恢复 | 没有 | 每 App 独立 .state/ |
| 今日已发去重 | 没有 | sent_today.json 自动 |
| 发送节奏 | 硬编码 | Dashboard 可配 |
| 认证 | 手动改代码 | auto / manual |
| 私信方式 | 只 Frida NIM | rest / frida_nim / none |
| 数据源/筛选 | 硬编码 | config 声明，动态渲染 |
| 纯 API 接入 | — | ~15 分钟 |
