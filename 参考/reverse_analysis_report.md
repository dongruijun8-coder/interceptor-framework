# 双鱼星球 (sybl) APK 逆向分析报告

## 基本信息

| 项目 | 内容 |
|------|------|
| 应用名称 | 双鱼部落 / 双鱼星球 |
| 包名 | `com.sybl.voiceroom` |
| 版本 | 2.47.1 (build 334) |
| APK 大小 | 99MB |
| 平台 | Android |
| 分析日期 | 2026-05-27 / 更新 2026-05-28 |

## Phase 0: 静态分析

### 加固检测

- **加固方案**: 网易易盾 (NetEase NIS Wrapper)
- **Application 类**: `com.netease.nis.wrapper.MyApplication`
- **Native 库**: `libnesec.so` (ARM64) / `libnesec-x86.so` (x86_64)
- **加固原理**: 
  1. 真实代码加密存储在 46MB 的 `classes.dex` 中
  2. NIS Wrapper 读取 `/proc/self/maps` 判断 CPU 架构
  3. 从 assets 释放对应架构的 `libnesec.so` 到 `/templib/`
  4. `System.load()` 加载 native 库
  5. 调用 native 方法 `load()`/`run()` 解密并运行真实 DEX
- **字符串加密**: 自定义 Base64 + XOR key `"NetEase"` (7字节: `0x4e,0x65,0x74,0x65,0x61,0x73,0x65`)
- **反调试**: 包含 `libemulatordetector.so` 模拟器检测库
- **静态分析受限**: apktool 解码后仅有 30 个 wrapper smali 文件,真实代码不可见

### 域名与 Scheme

| 类型 | 值 |
|------|-----|
| 主域名 | `www.shuangyuxingqiu.com` |
| API 服务器 | `ui-api-cn.shuangyuxingqiu.com` |
| Deep Link | `doublefishapp://app/open` |
| RongCloud Navi | `flse.cn.rongnav.com;flse.cn.rongcfg.com` |
| CDN 服务器 | `res-eo.shuangyuxingqiu.com` |
| RongCloud Navi | `flse.cn.rongnav.com;flse.cn.rongcfg.com` |
| RongCloud Config | `dualstack-cloudcontrol.rong-edge.com` |
| RongCloud App Key | `m7ua80gbmdddm` |
| 后端技术栈 | PHP (`PHPSESSID` cookie) |

### 集成 SDK

| SDK | 用途 |
|-----|------|
| 融云 RongCloud IM | 即时通讯 |
| 网易易盾 NIS | 加固/安全 |
| 友盟 Umeng | 数据统计 (APPKEY: `645b9ce07dddcc5bad46d0eb`) |
| 微信支付 | 支付 |
| 支付宝 | 支付 |
| 华为 HMS | 推送 |
| 阿里云号码认证 | 一键登录 (AppKey: `25331768`) |
| 阿里 Zoloz | 人脸识别 |
| 数美 Fenkong | 风控 |
| SudMGP | 游戏平台 |
| 腾讯 Bugly | 崩溃上报 (AppID: `8421cbcd16`) |

### Native 库清单 (lib/arm64-v8a/)

```
libAPSE_7.0.1.so          - 阿里号码认证
libBugly_Native.so        - 腾讯 Bugly
libRongIMLib.so           - 融云 IM
libYTCommonLiveness.so    - 阿里人脸活体检测
libemulatordetector.so    - 模拟器检测
libkyctoolkit.so          - KYC 工具
libnesec.so               - 网易易盾 (ARM64)
libnesec-x86.so           - 网易易盾 (x86_64)
libsecsdk.so              - 安全 SDK
libturingbase.so          - 图灵盾基础
libturingmfa.so           - 图灵盾 MFA
libxcrash.so              - 爱奇艺 xCrash
libyyeva.so               - 语音处理
```

## Phase 1: 环境搭建与数据提取

### 设备环境

- 模拟器: MuMu 5.0 (Android 12, x86_64)
- 已安装: Magisk 24.3, Zygisk-LSPosed, ZygiskFrida, MoveCertificate
- ADB: `127.0.0.1:7555`

### 绕过模拟器检测

- **问题**: app 启动即闪退, `libemulatordetector.so` 检测到模拟器环境
- **解决**: `magisk --denylist add com.sybl.voiceroom && magisk --denylist enable`
- **原理**: Magisk DenyList 对目标进程隐藏 Magisk/Zygisk 修改痕迹, 间接绕过模拟器检测

### 提取数据

从 `/data/data/com.sybl.voiceroom/` 提取:

**Shared Preferences:**
- `ssoconfigs.xml`: 融云 SSO token: `5EF612CFEF9D854DE7C3F963FCC33C8B` → `%ca07b4ff45d3488580d5ae103e9d13a1`
- `AUTH_APP_INFO.xml`: 加密的认证 token + uniqueId
- `AUTH_DEVICEINFO.xml`: 加密的设备 UUID
- `rc_cloud_config_*.xml`: 融云云配置
- `sudmgp_local_file_encrypt_key.xml`: SudMGP AES 日志加密密钥

**数据库:**
- `bugly_db_`: Bugly 崩溃数据
- `kit_user_*`: 融云用户数据
- `monitor.db`: 监控数据

## Phase 2: 流量抓包

### 抓包配置

- 工具: mitmproxy (mitmdump)
- 代理端口: 8080
- 设备代理: `settings put global http_proxy 10.0.2.2:8080`

### API 端点完整清单

#### 主 API 服务器: `https://ui-api-cn.shuangyuxingqiu.com`

**登录前 (冷启动)**:

| 端点 | 方法 | 说明 |
|------|------|------|
| `/UI/App/init` | POST | 应用初始化, 上报设备信息 |
| `/UI/Version/index` | POST | 版本检查 |
| `/UI/User/refreshToken` | POST | Token 刷新 |

**登录**:

| 端点 | 方法 | 说明 |
|------|------|------|
| `/UI/PasswordLoginPage/passwordLogin` | POST | 密码登录, 返回 `PHPSESSID` |

**登录后 (首页 + 房间 + 榜单 + 聊天)**:

| 端点 | 方法 | 分类 | 说明 |
|------|------|------|------|
| `/UI/User/HomePage/h5/index` | POST | 首页 | H5 首页数据 |
| `/UI/User/HomePage/Ranking/index` | POST | 首页 | 首页榜单入口 |
| `/UI/User/HomePage/Ranking/user` | POST | 首页 | 用户榜 |
| `/UI/User/HomePage/Ranking/toutiao` | POST | 首页 | 头条榜 |
| `/UI/User/HomePage/Ranking/soulmate` | POST | 首页 | 灵魂伴侣榜 |
| `/UI/User/RecommendPage/index` | POST | 首页 | 推荐页数据 |
| `/UI/Room/Home/categoryList` | POST | 房间 | 房间分类列表 |
| `/UI/Room/Home/roomList` | POST | 房间 | 房间列表 |
| `/UI/User/joinRoom` | POST | 房间 | 加入房间 |
| `/room/config` | POST | 房间 | 房间配置 (**注意: 不在 /UI/ 前缀下, 可能独立服务**) |
| `/UI/User/Room/sideRoomList` | POST | 房间 | 侧边栏房间列表 |
| `/UI/User/RoomPage/leave` | POST | 房间 | 离开房间 |
| `/UI/Room/UserRank/index` | POST | 榜单 | 房间内榜单入口 |
| `/UI/Room/UserRank/list` | POST | 榜单 | 房间榜单列表 |
| `/UI/User/User/index` | POST | 用户 | 用户首页/个人资料 |
| `/UI/User/unreadNum` | POST | 用户 | 未读消息数 |
| `/UI/User/User/UserConnect/connectSuccess` | POST | 用户 | 连接成功上报 |
| `/UI/User/Chat/followList` | POST | 聊天 | 关注/好友列表 |
| `/UI/User/Chat/userList` | POST | 聊天 | 用户会话列表 |
| `/UI/User/Chat/systemIdList` | POST | 聊天 | 系统会话 ID 列表 |
| `/UI/User/Chat/Info/emoji` | POST | 聊天 | 表情包列表 |
| `/UI/User/Chat/Info/userInfo` | POST | 聊天 | 指定用户信息 |

#### CDN 服务器: `https://res-eo.shuangyuxingqiu.com`

- 静态资源, 无需认证
- 头像: `/image/avatar/avatar{md5}.{png|jpeg}`
- 房间封面: `/image/room/cover/room_cover{md5}.{png|jpeg}`
- 房间背景: `/image/room/background/`
- 等级图标: `/等级/{类型}/{序号}.png` (魅力/财富等)
- 头像框: `/头像框/{系列}/{系列}res-{date}.{webp|svga}` (可爱猫猫/萌新等)
- 贵族图标: `/贵族/{类型}/`
- 靓号/靓字: `/靓号/` `/靓字/`
- 修仙: `/修仙/`
- 挚友图标: `/挚友/{level}/`
- 礼物图标: `/礼物/`
- 花间图标: `/花间/`
- 活动图标: `/activity/icon/{name}.png`
- 榜单图标: `/ranking/{榜单名}/`
- 房间游戏: `/app/roomGame/{游戏名}/`
- 系统通知头像: `/app/system_notify_avatar2.png`
- 默认头像: `/app/user_avatar_default`
- 房间守护: `/room_guard/seat_icon`
- 头条: `/头条/`
- 图片处理: `?imageMogr2/thumbnail/{w}x{h}>/ignore-error/1`

#### RongCloud IM (私信/聊天)

私信功能**不走 HTTP API**，通过融云 IM 私有 TCP 协议实现。

**服务发现**:

| 服务器 | 端点 | 说明 |
|--------|------|------|
| `flse.cn.rongnav.com` | `POST /v2/navi.json` | 服务发现, 获取 IM 连接节点 |
| `dualstack-cloudcontrol.rong-edge.com` | `POST /v1/config` | 云配置下发 |

**Navi 请求参数**: `token` + `appId=m7ua80gbmdddm` + `v=5.36.0` + `p=Android`

**Navi 响应关键字段**:

| 字段 | 值 | 说明 |
|------|-----|------|
| `userId` | `22187615` | 融云用户 ID |
| `dc` | `ALIBJ2_IM` | 数据中心 |
| `serverAddr[0]` | `112.126.70.47:443` | IM TCP 服务器 (protocol 1) |
| `serverAddr[1]` | `rmtp.rong-edge.com:8881` | IM TCP 服务器 (protocol 1) |
| `streamServer` | `dualstack-messageflow.rong-edge.com` | 消息流服务器 (protocol 12) |
| `activeServer` | `stats.rong-edge.com` | 统计上报 |
| `ossConfig` | Qiniu + Aliyun OSS | 文件上传配置 |
| `historyMsg` | `true` | 支持历史消息 |
| `msgModifyMinute` | `1440` | 消息可修改时间 (24小时) |

**IM Token**: navi 请求中携带的 token: `jMqzc99pk3usisXNdzTykohBHMvo1C1UjfLFf1Rckxc=@`

**HTTP API 覆盖范围** (仅元数据, 不含消息收发):

| 端点 | 说明 |
|------|------|
| `Chat/userList` | 会话列表 |
| `Chat/followList` | 好友/关注列表 |
| `Chat/systemIdList` | 系统会话 ID |
| `Chat/Info/userInfo` | 用户信息 |
| `Chat/Info/emoji` | 表情包 |

**私信脚本开发路径**:
1. HTTP 登录 → 获取融云 IM Token (在 passwordLogin 加密响应中)
2. 用 IM Token 连接融云 TCP 服务器
3. 通过融云私有协议收发消息
4. 或通过 app 服务端 HTTP 代理 (如果存在) 间接发消息

#### 第三方 API

| 服务器 | 端点 | 说明 |
|--------|------|------|
| `dypnsapi-dualstack.aliyuncs.com` | `/` | 阿里云号码认证 (QuerySdkConfig, QueryPnsDispatchInfo) |
| `fp-it.fengkongcloud.com` | `/v3/cloudconf`, `/deviceprofile/v4` | 数美风控 |
| `cn-000-mg-sdk.s01.tech` | `/v1/sdk/get_token`, `/v1/sdk/report` | SudMGP 游戏 SDK |
| `fqs.sudden.ltd` | `/a4b47ba18fb6be75dab84fa14fdb3a7d` | Sud 游戏平台 CDN 配置 |
| `android.bugly.qq.com` | `/rqd/async` | Bugly 崩溃上报 |
| `tdid.m.qq.com` | `/?mc=2` | 腾讯设备 ID |
| `report.mumu.nie.netease.com` | `/api/collection` | 网易 MuMu 模拟器上报 |

### 请求加密分析

所有主 API 请求使用**自定义请求头** + **加密请求体**:

#### 自定义请求头

| 头部 | 值示例 | 说明 |
|------|--------|------|
| `p1` | `c1b10d97f8dc779e417bfca25f876e52` | 32位 hex — 登录前=p2=p3, 登录后≠p2 |
| `p2` | (动态) | 32位 hex — 登录前=p1, 登录后独立变化 |
| `p3` | (动态) | 32位 hex — 登录前=p1, 登录后消失或独立 |
| `clienttype` | `Android` | 客户端类型 |
| `deviceid` | `5f467c6c3f03a8a5` | 设备 ID (固定) |
| `token` | `0dc789d7-4061-4133-9d0c-f66c900f7d42` | 设备会话 Token (UUID, 跨登录不变) |
| `timestamp` | `1779935669` | Unix 时间戳 (秒级) |
| `devicetoken` | `v3:AAAAAZ5sbFZMGkeL...` | 长加密设备 Token (~600+ 字符) |
| `smdeviceid` | `BlC5up1fNVd0lT+za5FWU...` | 数美设备指纹 (Base64) |
| `clientsession` | `13A072D8-31BB-4AA1...` | 客户端会话 UUID (每次启动变化) |
| `isemulator` | `true` | 模拟器检测 — 服务端不拒绝! |
| `isrooted` | `false` | Root 检测 |
| `hasfrida` | `false` | Frida 检测 |
| `hasxposed` | `false` | Xposed 检测 |
| `isrunninginmultiaccount` | `false` | 多账号检测 |
| `isaccessibilityenabled` | `false` | 无障碍检测 |
| `accessibilityservices` | `[]` | 无障碍服务列表 |
| `appversion` | `2.47.1` | 应用版本 |
| `devicetype` | `Samsung SM-S9280` | 设备型号 (模拟器伪装) |
| `build` | `334` | 构建号 |
| `channel` | `oppo` | 渠道 |

#### p1/p2/p3 签名头变化规律

| 时机 | p1 | p2 | p3 | 说明 |
|------|-----|-----|-----|------|
| App init | `c1b10d...` | `c1b10d...` (同 p1) | `c1b10d...` (同 p1) | 冷启动, 三者相同 |
| Version | `a253b2...` | `a253b2...` (同 p1) | `a253b2...` (同 p1) | 与 init 不同值, 但仍三者相同 |
| refreshToken | `21665b...` | `21665b...` (同 p1) | `21665b...` (同 p1) | 三者相同 |
| passwordLogin | `d04b6b...` | `d04b6b...` (同 p1) | `d04b6b...` (同 p1) | 登录前, 三者相同 |
| followList | `a7c6b5...` | `a6943b...` (**≠p1**) | (无) | 登录后, p1 固定, p2 变化 |
| categoryList | `a7c6b5...` | `a6943b...` (**≠p1**) | (无) | 与 followList 相同组合 |
| HomePage/h5 | `a7c6b5...` | `a6943b...` (**≠p1**) | (无) | 与 followList 相同组合 |

**推测**: 登录后服务端返回 auth token, p1=token 衍生值(固定), p2=请求签名(每次变化), p3 不再使用。

#### 登录流程详情

**请求** (`passwordLogin`):
- Body: `D3dXTiTvU9EuXcd6alFUVuenc+GTDxkYnuf98qDWhHRFcbKhnQrG0ueCZRbBoTUb` (64字符, Base64-like)
- 推测包含: 账号 + 密码 (加密后)

**响应**:
- `Set-Cookie: PHPSESSID=j02rsinsl13110au7prnp8feue` → 后端 PHP Session
- Body: 加密 blob (981 bytes, gzip 压缩后)
- 推测包含: 用户信息 + auth token + RongCloud IM token

#### 加密特征

**请求体**: 加密 (Base64-like blob, 64~344 字符)
**响应体**: 加密 (Base64-like blob, gzip 压缩后)
**共同前缀**: 响应体以 `MuLl0IsCQokA` 或 `Afd4/8tjp1I0` 开头 (可能是加密算法的固定头/IV)
**请求体前缀**: `LRCeAyXSi6iZ` 或 `D3dXTiTvU9Eu` 或 `JrOGP3kEFCfD` (不同端点不同前缀)

#### isemulator 字段

login 请求中 `isemulator: true` 被服务端正常接受 — 说明服务端**不做模拟器拦截**, 安全仅依赖客户端侧检测。

### 第三方 API 认证

**阿里云号码认证**:
- 使用 STS 临时凭证
- `AccessKeyId: STS.NYJkssQGj5Gnzgew34orxYTJJ`
- 终端信息中包含 app sign: `05b2cf539281c97e7396b5d5ca2202a5`
- AppKey: `25331768`
- SceneCode: `FC220000011185012`

## Phase 3: 加密算法逆向

### 3.1 动态 Hook 环境

- **Frida**: 普通 frida-server spawn 失败 (NIS 反制)
- **hluda-server**: 绕过 NIS 检测, attach 模式成功
- **Hook 策略**: 经 14 轮迭代, 最终用最简 `Body.getData()` hook 成功截获解密响应

### 3.2 p1/p2/p3 签名算法 (破解)

通过 Frida Hook `RequestBuilder.header()` 捕获大量 p1/p2/p3 值, 分析发现:

```
p1 ^ p2 = 固定 4 字节 XOR 密钥 (循环重复)
```

| 密钥 | 适用场景 | p2 vs p3 |
|------|---------|----------|
| `01528e5f` | 读请求 (Ranking, roomList, userList 等) | p2 = p3 |
| `015357de` | 写请求 (joinRoom, room/config, UserRank, sideRoomList 等) | p2 ≠ p3 |

**p2 vs p3**:
- 读请求: `p2 = p3 = p1 XOR 01528e5f01528e5f...`
- 写请求: `p2 = p1 XOR 015357de015357de...`, `p3 = p2 XOR 0001d9810001d981...`

**关键规律**:
- `p1[0] ^ p2[0]` 始终为 `0x01` (验证了 50+ 样本, 100%)
- p1 在同端点连续调用中保持不变 (用于防重放?)
- p1 在调用不同端点时变化

### 3.3 响应体解密 (破解)

通过 Hook `com.sybl.voiceroom.data.core.http.api.Body.getData()` 截获解密后的 JSON 响应:

```json
{
  "user": {"id":22187615, "nickname":"CY.xxx", "avatar":"...", "gender":2, "age":21},
  "wallet": {"diamond":"6.00", "income":"0.00"},
  "follow_info": {"friend_number":0, "follow_number":3, "fans_number":1},
  ...
}
```

**关键类结构** (Kotlin data class):
- `Body`: code, data, message, redirect, version (响应体)
- `HttpRequest`: path, param, method, header, dataType (接口, 实现类由 Retrofit 动态生成)

### 3.4 请求体解密 (已破解)

通过 Hook `HttpClientImp.createCall(req)` 截获加密前的请求参数明文。

**关键发现**: 请求参数是 **Kotlin data class**, `toString()` 返回可读格式 `ClassName(field=value, ...)`。

**createCall 捕获示例**:

```
Path: UI/Room/UserRank/index
  Param: RoomUserRankIndexParam(room_id=135719)
  DataType: RoomUserRankIndexData

Path: UI/User/Chat/userList
  Param: UserStatusListParams(user_ids=23162687,22839886,22600224,...)
  DataType: UserStatusListData

Path: UI/User/joinRoom
  Param: RoomRoomJoinParam(roomId=80193, password=null)
  DataType: RoomRoomJoinData

Path: UI/User/Chat/Info/userInfo
  Param: ChatUserInfoParam(user_id=22839886)
  DataType: UserInfoCpEntity
```

**参数类型汇总**:

| 端点 | Param 类 | 字段 |
|------|---------|------|
| Room/UserRank/index | `RoomUserRankIndexParam` | room_id |
| Room/UserRank/list | `RoomUserRankParam` | room_id, mode, rank_type |
| Room/Home/roomList | `RoomHomeRoomListParam` | page, page_size, id |
| User/joinRoom | `RoomRoomJoinParam` | roomId, password |
| User/Room/sideRoomList | `UserRoomSideParam` | room_id, page, page_size |
| User/RoomPage/leave | `RoomRoomLeaveParam` | roomId |
| User/Chat/userList | `UserStatusListParams` | user_ids (逗号分隔) |
| User/Chat/Info/userInfo | `ChatUserInfoParam` | user_id |
| Ranking/user | `RankUserParam` | mode, rank_type |
| Ranking/index | `RankConfigParam` | id, rank_type |
| User/User/index | `Blank` | 无参数 |
| User/unreadNum | `Blank` | 无参数 |
| room/config | `Blank` | 无参数 |
| RecommendPage/refresh | `Blank` | 无参数 |
| Chat/systemIdList | `Blank` | 无参数 |
| Chat/Info/emoji | `Blank` | 无参数 |

**Blank 模式**: 无需参数的请求传入 `Blank` 单例对象。

### 3.5 加密算法完整破解

#### 3.5.1 序列化格式: Gson JSON

Hook `Gson.toJson()` 捕获序列化:

```
RoomUserRankParam → {"mode":"guard","room_id":45308}
RoomHomeRoomListParam → {"id":2,"page":1,"page_size":20}
```

不是 Protobuf — 实际使用 **JSON 序列化**。

#### 3.5.2 加密算法: AES-256-CBC

Hook `javax.crypto.Cipher.doFinal()` + `Cipher.init()` 完整捕获:

| 参数 | 值 |
|------|-----|
| 算法 | `AES/CBC/PKCS7Padding` |
| 密钥长度 | 256 bit (32 bytes) |
| 密钥 (hex) | `596e396a734c5262486b306f3659796b524a38494c6f5664317967716b414d4b` |
| 密钥 (base64) | `WW45anNMUmJIazBvNll5a1JKOElMb1ZkMXlncWtBTUs=` |
| 密钥 (ascii) | `Yn9jsLRbHk0o6YykRJ8ILoVd1ygqkAMK` |
| IV | `FCE3F1A4-5DC3-41` (UUID 格式前 16 字符) |
| 模式 | 确定性 — 相同输入=相同输出 (固定 IV) |

#### 3.5.3 解密验证

使用 Python `pycryptodome` 验证成功:

```
输入: {} (2 bytes) → AES-256-CBC → nyFNSw60IP5ELDnT5AiCEA== ✅
输入: {"id":2,"page":1,"page_size":20} → 54Ae6XhFZENzSi4... ✅
输入: {"mode":"rich","rank_type":"month","room_id":45308} → 29a2ab87f19d... ✅
```

#### 3.5.4 完整加密流程

```
Kotlin data class → Gson.toJson() → JSON bytes
→ AES-256-CBC encrypt (固定 key+IV)
→ Base64 encode → HTTP body (text/plain)
```

#### 3.5.5 密钥来源推测

- Key: 32 字节随机字符串, 每会话固定
- IV: `FCE3F1A4-5DC3-41`, UUID 格式前 16 字符
- 可能从 `devicetoken` 或 `clientsession` 派生
- 服务端通过同样方式计算, 不需要传输

#### 3.5.6 特殊情况

- `/room/config` 端点: body 为明文 `{}`, 不加密
- 请求头 `p1/p2/p3`: 独立 XOR 签名, 非 AES 加密
- `usesCleartextTraffic=true` 但实际使用 HTTPS + 应用层加密

### 3.6 新增端点

| 端点 | 说明 |
|------|------|
| `POST /UI/User/RecommendPage/refresh` | 推荐页刷新 (v15 发现) |

### 3.7 HTTP 层架构

加密流程推测: `JSON → 加密 → Base64 → HTTP body`

通过 `createCall` hook + v5 类枚举发现的关键类:
```
com.sybl.voiceroom.data.core.http.HttpClientImp  — HTTP 客户端实现
com.sybl.voiceroom.data.core.http.api.HttpRequest — 请求接口
com.sybl.voiceroom.data.core.http.api.Body        — 响应体 (Kotlin data class)
com.sybl.voiceroom.data.core.http.api.ErrorCode   — 错误码
com.sybl.voiceroom.data.core.http.api.proto.*     — Protobuf 定义
com.sybl.voiceroom.data.core.business.NetworkBusinessService — 网络业务服务
com.sybl.voiceroom.data.core.application_state_machine.state.* — 请求状态机
```

技术栈: **OkHttp 5.3.2 + Retrofit + Gson + Protobuf + Kotlin**

## Phase 4: 认证流程验证

### 4.1 登录流程 (完整截获)

**请求**:
```
AuthLoginParam(phone=13721057968, password=zxc2005, code=null, mobile_token=null)
```

- 密码在加密前为**明文**
- `code` 和 `mobile_token` 未使用 (用于验证码/一键登录)

**响应** (解密后 JSON):

```json
{
  "token": "d2598423-bc55-48f4-a469-315adfa89816",
  "tokenExpiresAt": 1780544730,
  "id": 22187615,
  "displayId": "22187615",
  "nickname": "CY.xxx",
  "avatar": "https://res-eo.shuangyuxingqiu.com/image/avatar/...",
  "gender": 2,
  "rongCloudId": "22187615",
  "rongCloudToken": "jMqzc99pk3usisXNdzTykohBHMvo1C1UIptIUllYpdQ=@flse.cn.rongnav.com;flse.cn.rongcfg.com",
  "clean_start": true,
  "session_expiry": 300,
  "mqttClientId": "22187615",
  "mqttHost": "mqtt-nlb-tx.shuangyuxingqiu.com",
  "mqttPort": 1883,
  "mqttUsername": "22187615",
  "mqttPassword": "d2598423-bc55-48f4-a469-315adfa89816",
  "mqttKeepAlive": 60,
  "needInitUserInfo": 0,
  "childModeStatus": 0,
  "tencent_usersig": "eJyrVgrxCdYrSy1SslIy0jNQ0gHzM1NS80oy0zIhwkaGFuZmhqZQueKU7MSCgswUJStDMwMDQ0tjSwsLiExqRUFmUaqSlZmBiYWBAUSsJDMXKGJobm4JVGhpDBUtzkwHGhzp5lOck1MRFGgcWGUSmmuU65Xq4unjlJTsa*aW4uxdEVJh6pPsX5Jvq1QLANWZMR4_"
}
```

### 4.2 认证架构

app 使用**三通道**实时通信:

| 通道 | 服务器 | 端口 | 认证方式 | 用途 |
|------|--------|------|----------|------|
| HTTP API | `ui-api-cn.shuangyuxingqiu.com` | 443 | `token` header | 业务数据 |
| RongCloud IM | `112.126.70.47` (TCP) | 443 | `rongCloudToken` | 私信/聊天 |
| MQTT | `mqtt-nlb-tx.shuangyuxingqiu.com` | 1883 | `mqttUsername`/`mqttPassword` | 实时推送 |
| Tencent IM | 腾讯云 IM SDK | - | `tencent_usersig` | 音视频通话? |

### 4.3 Token 机制

| 字段 | 格式 | 说明 |
|------|------|------|
| `token` | UUID | HTTP API 认证, 每次请求携带在 header |
| `tokenExpiresAt` | Unix 秒 | 过期时间 |
| `session_expiry` | 秒 | 会话超时 (300s = 5分钟) |
| `rongCloudToken` | `jMqz...=@flse.cn.rongnav.com;flse.cn.rongcfg.com` | 融云 IM Token |
| `mqttPassword` | UUID (与 HTTP token 相同) | MQTT 密码 |
| `tencent_usersig` | Base64 (JWT-like) | 腾讯云 IM UserSig |

### 4.4 登录后数据加载序列

```
1. passwordLogin         → token + IM 凭据
2. Chat/followList       → 好友/关注列表  
3. HomePage/h5/index     → 首页 H5 + banner + 推荐房间
4. RecommendPage/index   → 推荐页
5. Room/Home/categoryList → 房间分类
6. User/User/index       → 用户资料 + 钱包
7. User/unreadNum        → 未读数
8. User/User/UserConnect/connectSuccess → 连接成功 (返回 room_id)
```

### 4.5 验证结论

- ✅ 登录参数格式已确认: `AuthLoginParam(phone, password, code, mobile_token)`
- ✅ 密码明文传输 (在加密层之前)
- ✅ 登录响应包含所有 IM/MQTT 凭据
- ✅ Token 是 UUID 格式
- ✅ 可以构造登录请求 → 获取 token → 调用业务 API
