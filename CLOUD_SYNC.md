# Apple Music 歌单云端同步

`scripts/apple_music_sync.py` 可以直接在 Linux 云端运行，仅依赖 Python 3.10+ 标准库。它读取当前 checkout 的 `recommendations/`，调用 Apple Music API 向已有的 `ChatGPT Daily 5` 歌单补充歌曲，不依赖这台电脑、Docker、音频文件或 localhost 登录服务。

## 云端配置

需要两个与现有 Apple Music 登录对应的值，不能提交到 Git 或发到聊天中：

- `APPLE_MUSIC_DEVELOPER_TOKEN`：developer token。
- `APPLE_MUSIC_USER_TOKEN`：Music User Token。

普通环境变量：

- `APPLE_MUSIC_PLAYLIST_ID`：现有 Apple Music **资料库歌单** ID。固定该 ID 可避免选错歌单；它不是公开分享链接中的 ID。
- `APPLE_MUSIC_API_HOST`：`api.music.apple.com`，或现有账户实际使用的 `amp-api.music.apple.com`。
- `APPLE_MUSIC_SYNC_EXECUTE`：脚本入口使用 `0` 预检、`1` 写入，默认 `0`。
- 可选 `APPLE_MUSIC_STATE_FILE`：仓库外的私有 checkpoint 路径。常驻云主机应持久保存该文件。

令牌失效后需要更新云端密钥；脚本不能在无交互环境中重新登录 Apple ID。现有 developer token 的到期时间已单独记录在本机验证报告中。

## Codex Cloud（当前版本）

1. 在新任务中选择 **Work in > Cloud > Select environment > Create environment**，选择 `ZJFILOVE/music-daily`。也可从 **Settings > Codex Cloud > Environments** 创建。
2. 准备环境时，使用包含这些脚本的代码版本。只需 Python 3.10+，不需安装额外 Python 包。
3. 在环境面板 **Network secrets > Manage** 中声明上述两个 token 的 key。将真实值保存到这个专用环境或限定该环境的 Personal vault，不使用普通环境变量存放真实 token。允许的目标仅设为实际需要的 Apple API 域名。
4. **Environment variables > Manage** 中配置资料库歌单 ID、API host 等非敏感设置。
5. 预检：`python3 scripts/apple_music_sync.py --no-wrapper`。
6. 发布环境并启动同步任务，执行：`python3 scripts/apple_music_sync.py --no-wrapper --execute`。也可使用 `APPLE_MUSIC_SYNC_EXECUTE=1 bash scripts/codex_cloud_sync.sh`。

网络密钥由代理注入：程序读取占位符，HTTPS 请求发往允许的主机时由代理替换为真实 token。真实 token 不需要进入进程环境、代码、磁盘或聊天消息。默认的 Python urllib HTTPS 代理支持必须保留，不能对 Apple API 设置 `--noproxy`。

完成配置后，新的云端任务可以在本机关闭时运行。这份改动提供按需入口，不自动创建定时任务；仍须由用户或另行配置的调度器启动任务。跨任务的源代码 refresh 可能更新 checkout，运行前应确认同步脚本位于当前代码版本。

## Codex Cloud Legacy（旧版）

旧版的 Secrets 仅在 setup script 提供，不能与上面的网络密钥流程混用。只有使用 Legacy 环境时，才配置两个 token 为 Secrets，并将 setup script 设置为：

```bash
bash scripts/codex_cloud_sync.sh
```

旧版在 setup 阶段执行预检或同步；`APPLE_MUSIC_SYNC_EXECUTE=0` 预检、`1` 写入。不会将令牌写入磁盘或 `.bashrc`。旧版 agent 阶段另有联网及 HTTP 方法设置。

## 其他云主机或 CI

由云端 Secret manager 向进程注入两个 token 和资料库歌单 ID，然后运行：

```bash
python3 scripts/apple_music_sync.py --no-wrapper
python3 scripts/apple_music_sync.py --no-wrapper --execute
```

云端需要可访问 `https://api.music.apple.com`；只有在账户使用兼容端点时才需 `https://amp-api.music.apple.com`。每次运行必须先取得最新的推荐仓库。

## 同步行为

- 默认为只读预检。`--execute` 才写入；默认不创建歌单。
- 按 catalog ID 读取远端歌单、排除已有歌曲，分批追加并回读验证。新建临时容器也会查询远端。
- 只追加，不删除、不重建、不重排已有歌曲。推荐清单按日期倒序扫描，但 Apple API 的追加操作会把新歌放在现有歌单末尾。
- 国区不可用的歌曲会单独报告，最终显示 `Partial`。不会自行选取其他版本；`--replace 原ID=替代ID` 用于明确选择的版本。
- 对超时或结果不明确的写入不自动重试。checkpoint 保留待确认歌曲；同一 checkpoint 的下次执行先回读确认，无法确认则停止。
- 如果运行容器及 checkpoint 已丢失，而上次写入未确认，先检查 Apple Music，再启动新的写入任务。临时云环境无法仅凭本地 checkpoint 保证跨容器的未知写入结果。
- 同机执行写入时使用文件锁。跨容器调用应由调度器串行执行，禁止重叠任务。
- 令牌只发送至固定的两个 Apple API 主机；不跟随 HTTP 重定向或跨域分页。
- checkpoint 必须在仓库外，权限为 `0600`，不包含令牌。推荐去重历史 `state/tracks.json` 只读取，不会修改。

## 本机用法

不提供 token 环境变量时，脚本继续使用本机 `127.0.0.1:18080/me` 的现有登录。脚本在仓库中时自动定位推荐目录：

```bash
python3 scripts/apple_music_sync.py
python3 scripts/apple_music_sync.py --execute
```

复制到其他目录时，显式加 `--repo /path/to/music-daily`。本机原有命令仍可使用。

## 验证

```bash
python3 -m unittest discover -s scripts -p 'test_*.py' -v
python3 scripts/validate.py
python3 scripts/check_consistency.py
```

官方文档：[当前 Codex Cloud 环境与网络密钥](https://learn.chatgpt.com/docs/environments/cloud-environments)、[Legacy 环境](https://learn.chatgpt.com/docs/environments/cloud-environment)、[Apple Music 用户令牌](https://developer.apple.com/documentation/applemusicapi/user-authentication-for-musickit)。
