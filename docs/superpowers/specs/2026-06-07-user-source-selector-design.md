# User Source Selector — 用户来源选择器

**Date:** 2026-06-07  
**Status:** design  
**Scope:** interceptor-framework backend + Dashboard UI

## Problem

当前 Dashboard 详情页的"数据源"下拉框概念混乱——它实际是排行类型（逍遥榜/财富榜），而非"从哪里拉用户"。不同 App 获取用户的方式完全不同：

- **sybl**: 房间榜单 API、首页总榜 API
- **wefun**: 仅房间在线用户（WebSocket + Frida RPC）
- **hifun/漂漂**: 仅房间榜单 API

现有 3 个下拉框（数据源/时段/性别）所有 App 一视同仁，无法表达"wefun 无排行类型"或"首页总榜不遍历房间"。需要一个新的**用户来源**抽象层。

## Design

### 1. config.json 新增 `user_sources`

每个 App 在 config.json 顶层新增 `user_sources`，声明可用的用户来源。Dashboard 据此渲染。

```json
"user_sources": {
  "<显示名称>": {
    "endpoint": "<endpoints key 或 null>",
    "type": "per_room | global",
    "mechanism": "http | ws_room_users (可选，默认 http)",
    "filters": ["data_source", "period", "gender"],
    "note": "可选描述"
  }
}
```

**字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `endpoint` | string\|null | 对应 `endpoints` 的 key；`null` = 无 HTTP 端点，由 mechanism 决定如何获取用户 |
| `type` | `"per_room"` \| `"global"` | per_room 遍历房间拉用户；global 一次性拉全站不遍历房间 |
| `mechanism` | `"http"` \| `"ws_room_users"` | 可选，默认 http。ws_room_users = 从 Frida WS hook 缓存取用户 |
| `filters` | string[] | 该来源需要的过滤下拉框。可选值：`data_source`、`period`、`gender`。空数组 = 无过滤 |

**各 App 配置：**

sybl（3 个来源，1 个待实现）：
```json
"user_sources": {
  "房间榜单": {
    "endpoint": "ranking",
    "type": "per_room",
    "filters": ["data_source", "period"]
  },
  "首页总榜": {
    "endpoint": "global_ranking",
    "type": "global",
    "filters": ["data_source", "period"]
  },
  "房间在线": {
    "endpoint": null,
    "type": "per_room",
    "filters": [],
    "note": "待实现：sybl 无在线用户接口"
  }
}
```

wefun（1 个来源）：
```json
"user_sources": {
  "房间在线": {
    "endpoint": null,
    "type": "per_room",
    "mechanism": "ws_room_users",
    "filters": []
  }
}
```

hifun / 漂漂（1 个来源）：
```json
"user_sources": {
  "房间榜单": {
    "endpoint": "ranking",
    "type": "per_room",
    "filters": ["period"]
  }
}
```

**过滤器选项仍从 `runtime_config` 读取：**
- `data_source` → `runtime_config.data_sources`（显示名 → 内部 key 映射）
- `period` → `runtime_config.periods`
- `gender` → `runtime_config.genders`

### 2. BaseClient 改动

**`__init__` 加载 user_source（持久化优先）：**
```python
# 加载 user_sources 配置
self._user_sources = self.config.get("user_sources", {})
src_keys = list(self._user_sources.keys())

# 从 runtime.json 读取上次选择，fallback 到配置的第一个
rt = self._load_runtime()
self._user_source = rt.get("user_source", src_keys[0] if src_keys else "")
self._current_source_cfg = self._user_sources.get(self._user_source, {})

# 同样：data_source / period / gender 也优先从 runtime 读（已有逻辑，保持不变）
```

**新增 `fetch_users(source_name, room=None)` 方法：**
```python
def fetch_users(self, source_name: str, room: dict = None) -> list:
    """根据用户来源拉取用户列表。模板变量从 self._data_source/self._period 解析。"""
    cfg = self._user_sources.get(source_name)
    if not cfg:
        return []

    mechanism = cfg.get("mechanism", "http")

    if mechanism == "ws_room_users":
        # wefun: 从 Frida WS hook 缓存取房间在线用户
        return self._fetch_users_from_ws(room)

    # HTTP mechanism
    ep_name = cfg["endpoint"]
    if not ep_name:
        return []
    ep = dict(self.config["endpoints"][ep_name])

    # 解析模板变量（与 fetch_room_ranking 一致）
    ds_key = self._data_sources.get(self._data_source, "")
    period_key = self._periods.get(self._period, "day")

    if cfg["type"] == "global":
        body = self._fill_template(ep.get("body", {}),
                                   data_source_key=ds_key, period_key=period_key)
        items = self._fetch_paginated(ep, body)
        return [self._map_fields(u, ep.get("output_mapping", {})) for u in items]

    elif cfg["type"] == "per_room":
        # 复用现有 fetch_room_ranking 逻辑（HTTP 房间榜单）
        return self.fetch_room_ranking(room, self._period)
```

**`_fetch_users_from_ws(room)` 方法：**
```python
def _fetch_users_from_ws(self, room: dict = None) -> list:
    """从 Frida WebSocket hook 缓存获取房间在线用户（wefun 路径）"""
    if not self._frida_session or not self._frida_session.is_connected:
        raise RuntimeError("WS 在线用户需要 Frida 连接")
    rpc = self._frida_session._rpc_second or self._frida_session._rpc
    raw = rpc.getOnlineUsers() if room else rpc.getOnlineUsers()
    if isinstance(raw, str):
        import json as _json
        raw = _json.loads(raw)
    return raw if isinstance(raw, list) else []
```

**任务主循环（`_run`）改动：**
```python
def _run(self):
    cfg = self._current_source_cfg
    if not cfg:
        return  # 无 user_sources 配置，不执行

    if cfg["type"] == "global":
        # 全局模式：不扫描房间
        users = self.fetch_users(self._user_source)
        self._progress["total_users"] = len(users)
        self._progress["sent_total"] = 0
        self._progress["failed_total"] = 0
        self._send_to_users(users)  # 遍历发消息，不关联房间
    else:
        # per_room 模式：先扫房间，再遍历
        rooms = self.fetch_all_rooms()
        self._progress["total_rooms"] = len(rooms)
        for i, room in enumerate(rooms):
            if not self._running:
                break
            self._progress["current_room_index"] = i
            self._progress["current_room_name"] = room.get("name", str(room.get("id", "")))
            users = self.fetch_users(self._user_source, room)
            self._send_to_users(users, room)
```

**`get_stats()` 新增/修改字段：**
```python
"user_source": self._user_source,
"available_user_sources": self._user_sources,   # 完整 {名称: {endpoint, type, mechanism, filters}}
"current_source_cfg": self._current_source_cfg,  # 当前选中来源的配置
"total_users": self._progress.get("total_users", 0),  # global 模式总用户数
```

**`/api/app/<id>/settings` POST 增加 `user_source` 处理：**
```python
if "user_source" in body:
    new_source = body["user_source"]
    if new_source in task._user_sources:
        task._user_source = new_source
        task._current_source_cfg = task._user_sources[new_source]
        task._save_runtime({"user_source": new_source})
```
注：仅任务未运行时允许切换。运行中拒绝并返回 error。

**`/api/app/<id>/settings` 运行时保护：**
```python
if "user_source" in body:
    if task.status == "running":
        return jsonify({"success": False, "error": "请先停止任务再切换用户来源"}), 409
```

### 3. Dashboard UI 改动

**右侧发送设置面板 — 新增「用户来源」下拉框：**

```
用户来源  [房间榜单 ▾]         ← 新增，第一优先级
───────────────────────────
排行类型  [逍遥榜 ▾]           ← filters 含 data_source 时显示
时段      [日榜 ▾]             ← filters 含 period 时显示
性别      [全部 ▾]             ← filters 含 gender 时显示
───────────────────────────
间隔      [3] 秒
```

**动态显隐 + 运行时禁用：**
```js
function updateSettings() {
  // ... existing code for interval, data_source, period, gender ...

  // --- 用户来源下拉框 ---
  var srcs = appData.available_user_sources || {};
  var srcKeys = Object.keys(srcs);
  var srcEl = document.getElementById('cfgUserSource');
  srcEl.innerHTML = '';
  srcKeys.forEach(function(k) {
    var cfg = srcs[k];
    var o = document.createElement('option');
    o.value = k;
    o.textContent = k + (cfg.endpoint ? '' : ' (待实现)');
    o.disabled = !cfg.endpoint && cfg.mechanism !== 'ws_room_users';
    if (k === appData.user_source) o.selected = true;
    srcEl.appendChild(o);
  });

  // 运行时禁止切换来源
  var isRunning = appData.status === 'running';
  srcEl.disabled = isRunning;

  // 动态显隐过滤行
  var cfg = appData.current_source_cfg || {};
  var filters = cfg.filters || [];
  document.getElementById('rowDataSource').classList.toggle('hidden', filters.indexOf('data_source') === -1);
  document.getElementById('rowPeriod').classList.toggle('hidden', filters.indexOf('period') === -1);
  document.getElementById('rowGender').classList.toggle('hidden', filters.indexOf('gender') === -1);

  // ... existing code for templates ...
}
```

切换来源 → `onchange` → POST settings（后端拒绝运行中切换）→ 轮询拿到新 `current_source_cfg` → 重渲染。

**左侧面板 — 根据来源类型切换渲染：**

`updateRooms()` 函数增加类型判断：

```js
function updateRooms() {
  var cfg = appData.current_source_cfg || {};
  var isGlobal = cfg.type === 'global';
  var panelTitle = document.querySelector('.panel-left .panel-header');
  var roomList = document.getElementById('roomList');

  if (isGlobal) {
    // global 模式：统计卡片
    panelTitle.innerHTML = '全站榜单';
    roomList.innerHTML =
      '<div style="padding:16px 20px;text-align:center">' +
      '<div style="font-size:28px;margin-bottom:8px">🏆</div>' +
      '<div style="font-size:14px;font-weight:600;margin-bottom:4px">' + (appData.user_source || '') + '</div>' +
      '<div style="font-size:12px;color:var(--text-muted);margin-bottom:12px">' +
        (appData.data_source || '') + ' · ' + (appData.period || '') +
      '</div>' +
      '<div style="display:flex;justify-content:space-around;font-size:12px">' +
      '<div><div style="font-size:20px;font-weight:600">' + (appData.total_users || 0) + '</div><div style="color:var(--text-muted)">已拉取</div></div>' +
      '<div><div style="font-size:20px;font-weight:600;color:var(--success)">' + (appData.sent || 0) + '</div><div style="color:var(--text-muted)">已发送</div></div>' +
      '<div><div style="font-size:20px;font-weight:600;color:var(--danger)">' + (appData.failed || 0) + '</div><div style="color:var(--text-muted)">失败</div></div>' +
      '</div></div>';
  } else {
    // per_room 模式：现有房间列表逻辑不变
    panelTitle.innerHTML = '房间列表 <span class="count">' + (appData.done_rooms||0) + ' / ' + (appData.total_rooms||0) + '</span>';
    // ... 现有 room list 渲染 ...
  }
}
```

**中间面板标题动态更新：**

`updateRanking()` 中标题逻辑：
```js
var cfg = appData.current_source_cfg || {};
var isGlobal = cfg.type === 'global';
var title = (appData.user_source || '榜单') + ' · ' + (appData.data_source || '') + ' · ' + (appData.period || '');
if (!isGlobal && appData.current_room) {
  title += ' · ' + appData.current_room;
}
document.getElementById('rankingTitle').innerHTML = title + ' <span class="count">' + (appData.done_rooms||0) + '/' + (appData.total_rooms||0) + '</span>';
```

### 4. 进度条适配

- **per_room**: 当前逻辑不变 — `done_rooms / total_rooms` + 已发/失败统计
- **global**: 进度条显示 `sent / total_users`，不显示房间名

```js
function updateProgress() {
  var cfg = appData.current_source_cfg || {};
  var isGlobal = cfg.type === 'global';
  if (isGlobal) {
    var total = appData.total_users || 0;
    var sent = appData.sent || 0;
    var pct = total ? Math.round(sent / total * 100) : 0;
    document.getElementById('pbLabel').textContent = sent + ' / ' + total + ' 人';
    document.getElementById('pbFill').style.width = pct + '%';
    document.getElementById('pbRoom').textContent = '全站模式';
  } else {
    // 现有逻辑不变
    var pct = appData.total_rooms ? Math.round(appData.done_rooms / appData.total_rooms * 100) : 0;
    document.getElementById('pbLabel').textContent = (appData.done_rooms||0) + ' / ' + (appData.total_rooms||0) + ' 间';
    document.getElementById('pbFill').style.width = pct + '%';
    document.getElementById('pbRoom').textContent = appData.current_room ? ('当前: ' + appData.current_room) : '—';
  }
  // sent/failed/today 统计不变
  document.getElementById('pbSent').textContent = appData.sent || 0;
  document.getElementById('pbFailed').textContent = appData.failed || 0;
  document.getElementById('pbTodaySent').textContent = appData.sent_today_total || 0;
}
```

### 5. 数据流

```
config.json                      runtime.json                Dashboard
┌──────────────────┐            ┌──────────────┐            ┌──────────────┐
│ user_sources:    │            │ user_source: │            │ [用户来源 ▾] │
│   房间榜单: {...} │            │   "首页总榜"   │←──保存────│  首页总榜     │
│   首页总榜: {...} │            │ data_source: │            │              │
│   房间在线: {...} │            │   逍遥榜      │            │ [排行类型 ▾] │
│                  │            │ period: 日榜  │            │ [时段 ▾]    │
│ runtime_config:  │            │ gender: 全部  │            │              │
│   data_sources:{}│            └──────────────┘            └──────────────┘
│   periods: {}    │
│   genders: {}    │
└──────────────────┘

初始化:
  __init__ → _load_runtime() → 读 user_source（有→用，无→取 user_sources 第一个 key）
  → self._user_source = "首页总榜"
  → self._current_source_cfg = {type:"global", filters:["data_source","period"]}

用户切换来源（任务停止时）:
  → POST /api/app/sybl/settings {user_source: "房间榜单"}
  → 后端检查 status != "running" → 更新 _user_source + _current_source_cfg
  → _save_runtime({"user_source": "房间榜单"})
  → Dashboard 轮询 → 渲染新的过滤行

用户切换来源（任务运行中）:
  → POST /api/app/sybl/settings {user_source: "房间榜单"}
  → 后端返回 409: {"error": "请先停止任务再切换用户来源"}
  → 前端下拉框保持 disabled 态

任务启动:
  → type=global → 跳过房间扫描 → fetch_users("首页总榜")
    → 读 self._data_sources[self._data_source] → ds_key
    → 读 self._periods[self._period] → period_key
    → _fill_template(body, data_source_key=ds_key, period_key=period_key)
    → 分页拉取 → 设置 total_users → 遍历发消息
  → type=per_room → 扫描房间 → for room: fetch_users("房间榜单", room) → 发消息
```

## Files Changed

| 文件 | 改动 |
|------|------|
| `apps/*/config.json` ×4 | 新增 `user_sources` 配置节 |
| `framework/core/base_client.py` | `__init__` 持久化加载 user_source；新增 `fetch_users()`、`_fetch_users_from_ws()`；`get_stats()` 返回 source + total_users 字段；`_run()` 按 type 分支；settings 处理 user_source 保存 |
| `framework/core/dashboard.py` | `/api/app/<id>/settings` 增加运行时切换保护（409），处理 `user_source` |
| `docs/superpowers/specs/design-mockup.html` | 新增用户来源下拉框；动态显隐过滤行；运行时禁用来源切换；左侧面板 global 统计卡片；中间面板标题适配；进度条双模式 |

## Backward Compatibility

- `user_sources` 缺省 → 空对象 `{}` → 下拉框空、`cfg` 为空 → `_run()` 直接 return，不执行任务
- 旧 config.json 不加此字段：任务不启动（因为无 user_source），Dashboard 显示空下拉框
- `get_stats()` 新增字段是增量，前端做 `\|\| {}` 兜底
- 现有 per_room 排名逻辑完整保留，只是多了一层 `fetch_users` 分发
