# User Source Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "user source" dropdown to Dashboard that lets users choose where to pull recipients from (room ranking, global ranking, room online users), with per-app config-driven source definitions.

**Architecture:** New `user_sources` config section in each app's `config.json`. `BaseClient` loads it, dispatches `fetch_users()` by source type/mechanism, and `run_pipeline()` branches on `global` vs `per_room`. Dashboard renders source dropdown dynamically, shows/hides filter rows per source's `filters` array, and switches left panel between room-list and global-stats modes.

**Tech Stack:** Python 3, Flask, vanilla JS (no framework), existing interceptor-framework codebase

---

## File Map

| File | Role | Action |
|------|------|--------|
| `apps/sybl/config.json` | Declare 3 user sources (room ranking, global ranking, room online placeholder) | Modify |
| `apps/wefun/config.json` | Declare 1 user source (room online via HTTP ranking endpoint) | Modify |
| `apps/hifun/config.json` | Declare 1 user source (room ranking) | Modify |
| `apps/piaopiao/config.json` | Declare 1 user source (room ranking) | Modify |
| `framework/core/base_client.py` | Load user_sources; new `fetch_users()`; branch `run_pipeline()`; extend `get_stats()` | Modify |
| `framework/core/dashboard.py` | Handle `user_source` in settings; runtime switch protection (409) | Modify |
| `docs/superpowers/specs/design-mockup.html` | New dropdown; dynamic filter visibility; global-mode left panel; dual-mode progress bar | Modify |

---

### Task 1: Add `user_sources` to all 4 config.json files

**Files:**
- Modify: `apps/sybl/config.json`
- Modify: `apps/wefun/config.json`
- Modify: `apps/hifun/config.json`
- Modify: `apps/piaopiao/config.json`

- [ ] **Step 1: Add `user_sources` to sybl config**

Insert after the `endpoints` block (after the closing `}` of `global_ranking`), before `runtime_config`:

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
},
```

- [ ] **Step 2: Add `user_sources` to wefun config**

Insert after `endpoints` block. wefun uses its `ranking` endpoint which actually maps to `room_online_user`:

```json
"user_sources": {
  "房间在线": {
    "endpoint": "ranking",
    "type": "per_room",
    "filters": []
  }
},
```

Note: wefun config has duplicate `ranking` keys (lines 78 and 109). Second overwrites first in JSON. Both point to `room_online_user` with `output_mapping: {uid: "mid", nick: "name"}`. Fix duplicate keys in a separate task if desired — not blocking this feature.

- [ ] **Step 3: Add `user_sources` to hifun config**

Insert after `endpoints` block:

```json
"user_sources": {
  "房间榜单": {
    "endpoint": "ranking",
    "type": "per_room",
    "filters": ["period"]
  }
},
```

- [ ] **Step 4: Add `user_sources` to piaopiao config**

Insert after `endpoints` block:

```json
"user_sources": {
  "房间榜单": {
    "endpoint": "ranking",
    "type": "per_room",
    "filters": ["period"]
  }
},
```

- [ ] **Step 5: Verify JSON validity**

Run: `python -c "import json; [json.load(open(f'apps/{a}/config.json')) for a in ['sybl','wefun','hifun','piaopiao']]; print('all valid')"`
Expected: `all valid`

---

### Task 2: BaseClient — load user_sources in `__init__`

**Files:**
- Modify: `framework/core/base_client.py:47-61`

- [ ] **Step 1: Add user_source loading in `__init__`**

Replace lines 47-61 (the runtime settings block) with code that loads user_source from runtime.json first, then falls back to config defaults:

```python
        # Runtime settings
        rt = self._load_runtime()
        settings = rt.get("settings", {})
        self._interval = settings.get("send_interval", 3)
        self._templates = rt.get("templates", self.config.get("runtime_config", {}).get("templates", ["{nick} 你好~"]))
        self._data_sources = rt.get("data_sources", self.config.get("runtime_config", {}).get("data_sources", {}))
        self._periods = rt.get("periods", self.config.get("runtime_config", {}).get("periods", {}))
        self._genders = rt.get("genders", self.config.get("runtime_config", {}).get("genders", {}))

        # User source — persisted selection takes priority, then config default
        self._user_sources = self.config.get("user_sources", {})
        src_keys = list(self._user_sources.keys())
        self._user_source = rt.get("user_source", src_keys[0] if src_keys else "")
        self._current_source_cfg = self._user_sources.get(self._user_source, {})

        keys = list(self._data_sources.keys())
        self._data_source = rt.get("data_source", keys[0] if keys else "")
        keys = list(self._periods.keys())
        self._period = rt.get("period", keys[0] if keys else "")
        keys = list(self._genders.keys())
        self._gender = rt.get("gender", keys[0] if keys else "")
```

Note: `data_source`, `period`, `gender` now also load from runtime.json persisted values (previously only used first key of the dict). This is a latent fix — ensures the dropdown reflects the user's last selection.

- [ ] **Step 2: Verify imports and startup**

Run: `python -c "from framework.core.base_client import BaseClient; c=BaseClient('apps/sybl/config.json'); print(c._user_source, c._current_source_cfg)"`
Expected: `房间榜单 {'endpoint': 'ranking', 'type': 'per_room', 'filters': ['data_source', 'period']}`

---

### Task 3: BaseClient — new `fetch_users()` method

**Files:**
- Modify: `framework/core/base_client.py` (insert before `run_pipeline`)

- [ ] **Step 1: Add `fetch_users()` method**

Insert before `run_pipeline()` (before line 527):

```python
    def fetch_users(self, source_name: str, room: dict = None) -> list:
        """根据用户来源拉取用户列表。

        模板变量从 self._data_source / self._period 解析，
        与 fetch_room_ranking 保持一致。
        """
        cfg = self._user_sources.get(source_name)
        if not cfg:
            return []

        ep_name = cfg.get("endpoint")
        if not ep_name:
            return []

        ep = dict(self.config["endpoints"][ep_name])

        # Resolve template variables
        ds_key = self._data_sources.get(self._data_source, "")
        period_key = self._periods.get(self._period, "day")

        if cfg["type"] == "global":
            body = self._fill_template(
                ep.get("body", {}),
                data_source_key=ds_key,
                period_key=period_key,
            )
            items = self._fetch_paginated(ep, body)
            mapping = ep.get("output_mapping", {})
            if mapping:
                items = [self._map_fields(u, mapping) for u in items]
            return items

        elif cfg["type"] == "per_room":
            if room is None:
                return []
            # Reuse existing per-room ranking logic
            return self.fetch_room_ranking(room, self._period)
```

- [ ] **Step 2: Quick smoke test**

Run Dashboard, start sybl task, verify it still works via `run_pipeline` → `run_room` → `fetch_room_ranking` path (no behavior change until Task 4).

---

### Task 4: BaseClient — branch `run_pipeline()` by source type

**Files:**
- Modify: `framework/core/base_client.py:527-577`

- [ ] **Step 1: Add global-mode branch to `run_pipeline()`**

Replace the method body (lines 527-577) with:

```python
    def run_pipeline(self) -> None:
        self._running = True
        self._pause_event.set()

        if not self._authenticated:
            if not self.authenticate():
                self._notify("error", "认证失败")
                self._running = False
                return
            self._authenticated = True

        cfg = self._current_source_cfg
        if not cfg:
            self._notify("error", "未配置用户来源 (user_sources 为空)")
            self._running = False
            return

        if cfg["type"] == "global":
            self._run_global(cfg)
        else:
            self._run_per_room(cfg)

        self._running = False

    def _run_global(self, cfg: dict) -> None:
        """全站榜单模式：不扫描房间，直接拉取用户列表发送。"""
        self._notify("info", f"全站模式: {self._user_source}")
        try:
            users = self.fetch_users(self._user_source)
        except Exception as e:
            self._notify("error", f"拉取用户失败: {e}")
            return

        with self._lock:
            self._progress["total_users"] = len(users)
            self._progress["sent_total"] = 0
            self._progress["failed_total"] = 0

        self._notify("info", f"拉取完成: {len(users)} 人")

        # Gender filter
        gender_target = self._genders.get(self._gender)
        if gender_target is not None:
            users = [u for u in users if u.get("gender") == gender_target]
            with self._lock:
                self._progress["total_users"] = len(users)

        # Sort by amount descending
        users.sort(key=lambda u: u.get("amount", 0), reverse=True)

        # Store for frontend display
        with self._lock:
            self._ranking_users = [dict(u, status="wait") for u in users]

        # Batch-mark already-sent
        skip_uids = set()
        for user in users:
            uid = str(user.get("uid", ""))
            if uid and self.state.is_sent_today(uid):
                skip_uids.add(uid)
        if skip_uids:
            with self._lock:
                for ru in self._ranking_users:
                    if str(ru.get("uid")) in skip_uids:
                        ru["status"] = "sent"

        # Send loop (no room context)
        for user in users:
            if not self._wait_if_paused():
                break
            self._send_to_user(user, room=None)

        if self._running:
            self._notify("done", "全站发送完成")

    def _run_per_room(self, cfg: dict) -> None:
        """房间遍历模式：现有逻辑不变。"""
        self._rooms = self.state.load_rooms()
        if not self._rooms:
            self._notify("info", "扫描房间...")
            try:
                self._rooms = self.fetch_all_rooms()
            except Exception as e:
                self._notify("error", f"扫描房间失败: {e}")
                self._running = False
                return
            self.state.save_rooms(self._rooms)
            self._notify("info", f"扫描完成: {len(self._rooms)} 间房")

        with self._lock:
            self._progress = self.state.load_progress()
            start_idx = self._progress.get("current_room_index", 0)

        for idx in range(start_idx, len(self._rooms)):
            if not self._wait_if_paused():
                break
            room = self._rooms[idx]
            self._notify("progress", {"current_room_index": idx, "room": room})
            try:
                self.run_room(room, idx)
            except FridaDisconnectedError:
                self._notify("error", "Frida 会话已断开，请重新连接设备后继续")
                self.pause()
                return
            except Exception as e:
                self._notify("error", f"房间 {room.get('name')} 失败: {e}")

        if self._running:
            with self._lock:
                self._progress["current_room_index"] = len(self._rooms)
                self._progress["current_room_name"] = ""
            self.state.save_progress(
                current_room_index=len(self._rooms),
                current_room_name="",
            )
            self._notify("done", "全部房间完成")

    def _send_to_user(self, user: dict, room: dict = None) -> None:
        """发送消息给单个用户。从 run_room 提取为独立方法，供 global 和 per_room 共用。"""
        uid = str(user.get("uid", ""))
        nick = user.get("nick", "")
        room_name = room.get("name", "") if room else ""

        if self.state.is_sent_today(uid):
            return

        template = random.choice(self._templates)
        text = template.replace("{nick}", nick).replace("{room_name}", room_name)

        send_start = time.time()
        with self._lock:
            self._current_user = {
                "uid": uid, "nick": nick, "text": text,
                "room": room_name,
                "time": time.strftime("%H:%M:%S"),
            }
            for ru in self._ranking_users:
                if str(ru.get("uid")) == uid:
                    ru["status"] = "sending"
                    break

        time.sleep(0.6)

        try:
            result = self.send_message(uid, text)
        except Exception as e:
            result = {"success": False, "error": str(e)}

        entry = {
            "uid": uid, "nick": nick,
            "room": room_name,
            "time": time.strftime("%H:%M:%S"),
            "success": result.get("success", False),
            "error": result.get("error", ""),
        }

        if result.get("success"):
            entry["text"] = text
            self.state.mark_sent(uid, nick, room_name)
            with self._lock:
                sent = self._progress.get("sent_total", 0) + 1
                self._progress["sent_total"] = sent
                self.state.save_progress(sent_total=sent)
                self._current_user = {}
                self._recent_sent.insert(0, entry)
                if len(self._recent_sent) > 20:
                    self._recent_sent = self._recent_sent[:20]
                for ru in self._ranking_users:
                    if str(ru.get("uid")) == uid:
                        ru["status"] = "sent"
                        break
            self._notify("sent", {"uid": uid, "nick": nick, "text": text})
        else:
            with self._lock:
                failed = self._progress.get("failed_total", 0) + 1
                self._progress["failed_total"] = failed
                self.state.save_progress(failed_total=failed)
                self._current_user = {}
                self._recent_failed.insert(0, entry)
                if len(self._recent_failed) > 20:
                    self._recent_failed = self._recent_failed[:20]
                for ru in self._ranking_users:
                    if str(ru.get("uid")) == uid:
                        ru["status"] = "failed"
                        break
            self._notify("failed", {
                "uid": uid, "nick": nick,
                "error": result.get("error", "unknown"),
            })

        time.sleep(self._interval)
```

- [ ] **Step 2: Refactor `run_room()` to use `_send_to_user()`**

Replace the send loop in `run_room()` (lines 619-693) — keep the ranking fetch + filter + sort + mark-sent logic, replace the user iteration and send logic with `self._send_to_user(user, room)` calls:

```python
    def run_room(self, room: dict, idx: int) -> None:
        room_name = room.get("name", "")
        with self._lock:
            self._progress["current_room_index"] = idx
            self._progress["current_room_name"] = room_name
            self.state.save_progress(
                current_room_index=idx,
                current_room_name=room_name,
            )

        try:
            users = self.fetch_users(self._user_source, room)
        except Exception as e:
            with self._lock:
                self._ranking_users = []
            self._notify("error", f"排行失败 {room.get('name')}: {e}")
            return

        gender_target = self._genders.get(self._gender)
        if gender_target is not None:
            users = [u for u in users if u.get("gender") == gender_target]

        users.sort(key=lambda u: u.get("amount", 0), reverse=True)

        with self._lock:
            self._ranking_users = [dict(u, status="wait") for u in users]

        skip_uids = set()
        for user in users:
            uid = str(user.get("uid", ""))
            if uid and self.state.is_sent_today(uid):
                skip_uids.add(uid)
        if skip_uids:
            with self._lock:
                for ru in self._ranking_users:
                    if str(ru.get("uid")) in skip_uids:
                        ru["status"] = "sent"

        for user in users:
            if not self._wait_if_paused():
                break
            self._send_to_user(user, room)
```

- [ ] **Step 3: Verify both modes**

```bash
# Start Dashboard
cd framework/core && python dashboard.py
```
- Test sybl with "房间榜单" source → should scan rooms and send per room
- Test sybl with "首页总榜" source (need to update runtime.json user_source first) → should skip room scan, go straight to user list

---

### Task 5: BaseClient — extend `get_stats()` with user_source fields

**Files:**
- Modify: `framework/core/base_client.py:751-789`

- [ ] **Step 1: Add new fields to `get_stats()` return dict**

Add after `"gender": self._gender,` (line 776):

```python
            "user_source": self._user_source,
            "available_user_sources": self._user_sources,
            "current_source_cfg": self._current_source_cfg,
            "total_users": self._progress.get("total_users", 0),
```

- [ ] **Step 2: Verify API response**

Run: `curl http://127.0.0.1:3112/api/app/sybl | python -m json.tool | grep -E "user_source|available_user_sources|current_source_cfg|total_users"`
Expected: All 4 fields present in JSON response.

---

### Task 6: Dashboard API — handle `user_source` in settings + runtime protection

**Files:**
- Modify: `framework/core/dashboard.py:125-168`

- [ ] **Step 1: Add `user_source` handling with runtime guard**

Add after the `"gender"` block (after line 160):

```python
    # User source — only allow switch when task is not running
    if "user_source" in data:
        if task.status == "running":
            return jsonify({
                "success": False,
                "error": "请先停止任务再切换用户来源",
            }), 409
        new_source = data["user_source"]
        if new_source not in task._user_sources:
            return jsonify({
                "success": False,
                "error": f"无效的用户来源: {new_source}",
            }), 400
        task._user_source = new_source
        task._current_source_cfg = task._user_sources[new_source]
        runtime["user_source"] = new_source
```

- [ ] **Step 2: Test runtime protection**

```bash
# Start a task
curl -X POST http://127.0.0.1:3112/api/app/sybl/start
# Try to switch source while running
curl -X POST http://127.0.0.1:3112/api/app/sybl/settings \
  -H "Content-Type: application/json" \
  -d '{"user_source":"首页总榜"}'
```
Expected: `{"success": false, "error": "请先停止任务再切换用户来源"}` with HTTP 409.

- [ ] **Step 3: Test valid switch (task stopped)**

```bash
# Stop task
curl -X POST http://127.0.0.1:3112/api/app/sybl/stop
# Switch source
curl -X POST http://127.0.0.1:3112/api/app/sybl/settings \
  -H "Content-Type: application/json" \
  -d '{"user_source":"首页总榜"}'
```
Expected: `{"success": true}`. Check `apps/sybl/runtime.json` contains `"user_source": "首页总榜"`.

---

### Task 7: Dashboard UI — user source dropdown + filter visibility

**Files:**
- Modify: `docs/superpowers/specs/design-mockup.html:207-211` (send settings section)
- Modify: `docs/superpowers/specs/design-mockup.html:463-486` (updateSettings function)

- [ ] **Step 1: Add user source dropdown to HTML**

Replace the send settings HTML (lines 207-211) with:

```html
      <div class="setting-row" id="rowUserSource"><label>用户来源</label><select id="cfgUserSource" onchange="onSourceChange()"></select></div>
      <div class="setting-row hidden" id="rowDataSource"><label>排行类型</label><select id="cfgSource" onchange="onSettingChange()"></select></div>
      <div class="setting-row hidden" id="rowPeriod"><label>时段</label><select id="cfgPeriod" onchange="onSettingChange()"></select></div>
      <div class="setting-row hidden" id="rowGender"><label>性别</label><select id="cfgGender" onchange="onSettingChange()"></select></div>
```

Note: `rowDataSource`, `rowPeriod`, `rowGender` now start with `class="hidden"` — visibility controlled by JS. The existing `onchange="onSettingChange()"` stays for interval/data_source/period/gender changes.

- [ ] **Step 2: Rewrite `updateSettings()` to populate source dropdown + control filter visibility**

Replace the current `updateSettings()` function (lines 463-486) with:

```js
function updateSettings() {
  document.getElementById('cfgInterval').value = appData.interval || 3;

  // --- User source dropdown ---
  var srcs = appData.available_user_sources || {};
  var srcKeys = Object.keys(srcs);
  var srcEl = document.getElementById('cfgUserSource');
  var prevVal = srcEl.value;
  srcEl.innerHTML = '';
  if (srcKeys.length === 0) {
    var o = document.createElement('option');
    o.value = ''; o.textContent = '(未配置)'; o.disabled = true;
    srcEl.appendChild(o);
  } else {
    srcKeys.forEach(function(k) {
      var cfg = srcs[k];
      var o = document.createElement('option');
      o.value = k;
      o.textContent = k + (!cfg.endpoint ? ' (待实现)' : '');
      o.disabled = false;
      if (k === appData.user_source) o.selected = true;
      srcEl.appendChild(o);
    });
  }
  // Restore selection if still valid
  if (prevVal && srcKeys.indexOf(prevVal) >= 0 && prevVal !== appData.user_source) {
    srcEl.value = prevVal;
  }

  // Runtime disable source switch
  var isRunning = appData.status === 'running';
  srcEl.disabled = isRunning;

  // Dynamic filter visibility
  var cfg = appData.current_source_cfg || {};
  var filters = cfg.filters || [];
  document.getElementById('rowDataSource').classList.toggle('hidden', filters.indexOf('data_source') === -1);
  document.getElementById('rowPeriod').classList.toggle('hidden', filters.indexOf('period') === -1);
  document.getElementById('rowGender').classList.toggle('hidden', filters.indexOf('gender') === -1);

  // --- Existing dropdowns (populate when visible) ---
  if (filters.indexOf('data_source') >= 0) {
    var opts = appData.available_data_sources || {};
    var keys = Object.keys(opts);
    if (!keys.length) keys = [appData.data_source || '—'];
    var dsEl = document.getElementById('cfgSource');
    dsEl.innerHTML = '';
    keys.forEach(function(k) { var o = document.createElement('option'); o.value = k; o.textContent = k; if (k === appData.data_source) o.selected = true; dsEl.appendChild(o); });
  }

  if (filters.indexOf('period') >= 0) {
    var opts = appData.available_periods || {};
    var keys = Object.keys(opts);
    if (!keys.length) keys = [appData.period || '—'];
    var perEl = document.getElementById('cfgPeriod');
    perEl.innerHTML = '';
    keys.forEach(function(k) { var o = document.createElement('option'); o.value = k; o.textContent = k; if (k === appData.period) o.selected = true; perEl.appendChild(o); });
  }

  if (filters.indexOf('gender') >= 0) {
    var opts = appData.available_genders || {};
    var keys = Object.keys(opts);
    if (!keys.length) keys = [appData.gender || '全部'];
    var genEl = document.getElementById('cfgGender');
    genEl.innerHTML = '';
    keys.forEach(function(k) { var o = document.createElement('option'); o.value = k; o.textContent = k; if (k === appData.gender) o.selected = true; genEl.appendChild(o); });
  }
}
```

- [ ] **Step 3: Add `onSourceChange()` handler**

Add before `onSettingChange()`:

```js
function onSourceChange() {
  var newSource = document.getElementById('cfgUserSource').value;
  if (!newSource || newSource === (appData.user_source || '')) return;
  fetch('/api/app/' + encodeURIComponent(APP_ID) + '/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_source: newSource })
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.error) { alert(d.error); fetchApp(); return; }
    fetchApp();
  });
}
```

- [ ] **Step 4: Test visually**

Start Dashboard, open detail page for sybl. Verify:
- User source dropdown shows 3 options (房间在线 shows "(待实现)")
- Select "房间榜单" → 排行类型 + 时段 visible, 性别 hidden
- Select "首页总榜" → 排行类型 + 时段 visible, 性别 hidden
- Select "房间在线" → all 3 filter rows hidden
- Start task → source dropdown becomes disabled

---

### Task 8: Dashboard UI — global mode left panel + progress bar

**Files:**
- Modify: `docs/superpowers/specs/design-mockup.html:324-338` (updateRooms)
- Modify: `docs/superpowers/specs/design-mockup.html:313-322` (updateProgress)
- Modify: `docs/superpowers/specs/design-mockup.html:343-389` (updateRanking)

- [ ] **Step 1: Rewrite `updateRooms()` with global-mode branch**

Replace `updateRooms()` (lines 324-338):

```js
function updateRooms() {
  var cfg = appData.current_source_cfg || {};
  var isGlobal = cfg.type === 'global';
  var panelHeader = document.querySelector('.panel-left .panel-header');

  if (isGlobal) {
    panelHeader.innerHTML = '全站榜单';
    var roomList = document.getElementById('roomList');
    roomList.innerHTML =
      '<div style="padding:20px;text-align:center">' +
      '<div style="font-size:28px;margin-bottom:8px">&#x1F3C6;</div>' +
      '<div style="font-size:14px;font-weight:600;margin-bottom:4px">' + (appData.user_source || '') + '</div>' +
      '<div style="font-size:12px;color:var(--text-muted);margin-bottom:16px">' +
        (appData.data_source || '') + ' · ' + (appData.period || '') +
      '</div>' +
      '<div style="display:flex;justify-content:space-around;font-size:12px">' +
      '<div><div style="font-size:22px;font-weight:600">' + (appData.total_users || 0) + '</div><div style="color:var(--text-muted)">已拉取</div></div>' +
      '<div><div style="font-size:22px;font-weight:600;color:var(--success)">' + (appData.sent || 0) + '</div><div style="color:var(--text-muted)">已发送</div></div>' +
      '<div><div style="font-size:22px;font-weight:600;color:var(--danger)">' + (appData.failed || 0) + '</div><div style="color:var(--text-muted)">失败</div></div>' +
      '</div></div>';
    document.getElementById('roomCount').textContent = '';
  } else {
    panelHeader.innerHTML = '房间列表 <span class="count" id="roomCount">' + (appData.done_rooms||0) + ' / ' + (appData.total_rooms||0) + '</span>';
    // Existing room list rendering
    var html = '';
    if (appData.total_rooms === 0) {
      html = '<div class="empty-note">等待房间扫描...</div>';
    } else if (appData.done_rooms === appData.total_rooms) {
      html = '<div class="empty-note">全部房间已完成</div>';
    } else {
      html += '<div class="room-item active"><div><div class="ri-name">' + (appData.current_room || '进行中...') + '</div><div class="ri-meta">排名 · 已发 ' + (appData.sent||0) + ' 人</div></div><span class="ri-badge ri-prog">进行中</span></div>';
      var remaining = (appData.total_rooms||0) - (appData.done_rooms||0) - 1;
      if (remaining > 0) html += '<div class="room-item"><div><div class="ri-name">剩余 ' + remaining + ' 间</div><div class="ri-meta">待遍历</div></div><span class="ri-badge ri-wait">等待</span></div>';
    }
    document.getElementById('roomList').innerHTML = html;
    document.getElementById('roomFooter').classList.toggle('hidden', (appData.total_rooms||0) === 0);
  }
}
```

- [ ] **Step 2: Rewrite `updateProgress()` with global-mode branch**

Replace `updateProgress()` (lines 313-322):

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
    var pct = appData.total_rooms ? Math.round((appData.done_rooms||0) / (appData.total_rooms||0) * 100) : 0;
    document.getElementById('pbLabel').textContent = (appData.done_rooms||0) + ' / ' + (appData.total_rooms||0) + ' 间';
    document.getElementById('pbFill').style.width = pct + '%';
    document.getElementById('pbRoom').textContent = appData.current_room ? ('当前: ' + appData.current_room) : '—';
  }

  document.getElementById('pbSent').textContent = appData.sent || 0;
  document.getElementById('pbFailed').textContent = appData.failed || 0;
  document.getElementById('pbTodaySent').textContent = appData.sent_today_total || 0;
}
```

- [ ] **Step 3: Update `updateRanking()` title for global mode**

Replace the title line in `updateRanking()` (line 344):

```js
  var cfg = appData.current_source_cfg || {};
  var isGlobal = cfg.type === 'global';
  var title = (appData.user_source || '榜单') + ' · ' + (appData.data_source || '') + ' · ' + (appData.period || '');
  if (!isGlobal && appData.current_room) title += ' · ' + appData.current_room;
  document.getElementById('rankingTitle').innerHTML = title + ' <span class="count">' + (appData.done_rooms||0) + '/' + (appData.total_rooms||0) + '</span>';
```

- [ ] **Step 4: Visual verification**

Start Dashboard, set sybl to "首页总榜", start task. Verify:
- Left panel shows stats card with 🏆 + 已拉取/已发送/失败 counts
- Progress bar shows "X / Y 人" and "全站模式"
- Center panel title shows "首页总榜 · 逍遥榜 · 日榜" (no room name)
- Switch to "房间榜单", restart → left panel shows room list, progress shows rooms

---

### Task 9: Integration smoke test

- [ ] **Step 1: Test all 4 apps start correctly**

```bash
cd interceptor-framework/framework/core && python dashboard.py
```
Check `http://127.0.0.1:3112/api/apps` — all 4 apps should appear with `user_source` field.

- [ ] **Step 2: Test source persistence**

1. Set sybl source to "首页总榜" via API
2. Restart Dashboard
3. Verify `apps/sybl/runtime.json` has `"user_source": "首页总榜"`
4. Check `http://127.0.0.1:3112/api/app/sybl` shows `"user_source": "首页总榜"`

- [ ] **Step 3: Test backward compat**

1. Temporarily remove `user_sources` from hifun config.json
2. Check hifun detail page — dropdown shows "(未配置)", no crash
3. Restore config

- [ ] **Step 4: Test global mode end-to-end (sybl)**

1. Set sybl source to "首页总榜"
2. Start task
3. Verify: no room scan, users listed in center panel, sends working
4. Stop task

- [ ] **Step 5: Test per_room mode unchanged (all apps)**

1. Set each app to its per_room source
2. Start task
3. Verify room scanning + sending works as before
```
