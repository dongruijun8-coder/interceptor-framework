# 双鱼部落接入 — 问题文档

**日期**: 2026-05-28 | **状态**: 阻塞 — 待补充逆向数据

---

## 1. 已完成

| 模块 | 文件 | 状态 |
|------|------|------|
| 配置 | `apps/shuangyu/config.json` | 完成，需填账号 |
| 客户端 | `apps/shuangyu/client.py` | 结构完成，登录/房间/排行已实现 |
| AES-256-CBC 加密 | client.py `_encrypt_body/_decrypt_body` | 算法验证通过（`{}`→`nyFNSw60IP5ELDnT5AiCEA==` 与报告一致） |
| p1/p2/p3 签名 | client.py `_make_signature` | 登录前 p1=p2=p3，登录后 XOR 派生 |
| 登录 | `authenticate()` | 已实现，调用 `/UI/PasswordLoginPage/passwordLogin` |
| 房间扫描 | `fetch_all_rooms()` | 已实现，categoryList→roomList 分页 |
| 排行 | `fetch_room_ranking()` | 已实现，`/UI/Room/UserRank/list` |
| 私信 | `send_message()` | **stub** — 融云 TCP 协议待实现 |
| Dashboard 集成 | TaskManager 自动发现 | 正常 |

---

## 2. 已验证通过的组件

### 2.1 AES 加密/解密

```
Key:   Yn9jsLRbHk0o6YykRJ8ILoVd1ygqkAMK (32 bytes)
IV:    FCE3F1A4-5DC3-41 (16 bytes)
算法:  AES-256-CBC / PKCS7Padding

验证:  encrypt("{}") → "nyFNSw60IP5ELDnT5AiCEA=="  与报告 3.5.3 一致
```

### 2.2 网络层

- HTTPS 连接成功（`ui-api-cn.shuangyuxingqiu.com`）
- 服务端返回了可解析的错误响应（非网络错误）
- 加密请求被服务端接收并尝试解密

### 2.3 框架集成

- `TaskManager` 自动发现 `apps/shuangyu/` 目录
- `ShuangyuClient` 正确继承 `BaseClient`
- Dashboard 可加载双鱼部落面板

---

## 3. 阻塞问题

### 3.1 AES Key 会话绑定（核心阻塞）

**现象**：所有请求返回 `120001 密钥获取失败`

**根因**：AES key **不是硬编码**的。服务端通过 `token` 请求头查找对应设备的会话密钥。当前使用的 key（来自 Frida hook）属于逆向时的那次会话，与我们的 `device_token` 不匹配。

**报告依据**（3.5.5 节）：
> "Key: 32 字节随机字符串, 每会话固定"
> "可能从 devicetoken 或 clientsession 派生"
> "服务端通过同样方式计算, 不需要传输"

**影响**：无法通过 App/init 注册设备 → 无法建立会话 → 所有接口不可用。

### 3.2 devicetoken 缺失

**现象**：请求头中缺少有效的 `devicetoken` 值

**说明**：`devicetoken` 格式为 `v3:AAAAAZ5sbFZMGkeL...`（~600+ 字符 Base64）。报告未捕获完整值（被截断），且生成算法未逆向。

**推测用途**：
- 设备指纹容器（版本 `v3`，Base64 编码的设备数据）
- AES key 可能从中派生
- 服务端校验设备合法性

### 3.3 smdeviceid 缺失

**现象**：请求头中缺少 `smdeviceid`

**说明**：数美（Fengkong）设备指纹，Base64 编码。由数美 SDK 在客户端生成。是否为必须字段待确认。

### 3.4 p1 派生逻辑未知（登录后）

**现象**：报告指出登录后 p1 固定不变（推测从 auth token 派生），当前实现用随机值

**影响**：登录成功后，后续请求的签名可能被服务端拒绝

**报告依据**（3.2 节）：
> "登录后服务端返回 auth token, p1=token 衍生值(固定), p2=请求签名(每次变化)"

### 3.5 send_message — 融云 TCP（后续阻塞）

私信功能不走 HTTP API，需实现融云 IM TCP 协议：

1. Navi 服务发现：`POST flse.cn.rongnav.com/v2/navi.json`
2. TCP 连接：`112.126.70.47:443`
3. 融云私有协议认证和消息收发

已有凭据（来自登录响应）：
- `rongCloudToken`: 登录后获取
- `rongCloudId`: 用户 ID
- App Key: `m7ua80gbmdddm`

---

## 4. 请求错误对照

| 条件 | 端点 | 错误码 | 消息 | 含义 |
|------|------|--------|------|------|
| clientsession 为空 | passwordLogin | -8 | 系统繁忙 | 请求格式不合规 |
| clientsession 有效，device_token 来自报告 | passwordLogin | 120001 | 密钥获取失败 | AES key 与 token 不匹配 |
| clientsession 有效，新 device_token | passwordLogin | 120001 | 密钥获取失败 | 设备未注册，无会话 |
| 任意 token | App/init | 120001 | 请求失败1 | 初始化失败（同因） |
| 任意 token | Version/index | 120001 | 密钥获取失败 | 同因 |

---

## 5. 需要补充的数据

要突破阻塞，需要在模拟器上重新运行一次双鱼部落 app，抓取冷启动的完整流量：

### 5.1 优先

| 数据 | 来源 | 用途 |
|------|------|------|
| **完整 devicetoken** | App/init 请求头 | key 派生算法分析 |
| **完整 smdeviceid** | 任意请求头 | 判断是否必需 |
| **App/init 请求体明文** | Frida hook `createCall` | 了解设备注册参数 |
| **App/init 响应体明文** | Frida hook `Body.getData()` | 了解注册返回数据 |

### 5.2 次优先

| 数据 | 用途 |
|------|------|
| passwordLogin 请求体密文 + 对应响应明文 | 对比验证 key 派生 |
| 登录后任意业务请求的 p1/p2/p3 | 验证登录后签名规则 |
| clientsession 生成逻辑 | 确认是否自定义算法 |

---

## 6. 后续步骤

```
阻塞解除后:
1. 实现 key 派生函数，替换硬编码 AES_KEY
2. 实现 App/init → 获取/注册会话
3. 测试 passwordLogin → 房间扫描 → 排行
4. 实现融云 TCP 私信发送
5. 端到端测试完整 pipeline
```
