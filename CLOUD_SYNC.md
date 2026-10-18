# Apple Music 歌单云端同步

`scripts/apple_music_sync.py` 可以直接在 Linux 云端运行，仅依赖 Python 3.10+ 标准库。它读取当前 checkout 的 `recommendations/`，调用 Apple Music API 向已有的 `ChatGPT Daily 5` 歌单补充歌曲，不依赖这台电脑、Docker、音频文件或 localhost 登录服务。

## 云端配置

在云端环境的 **Secrets** 中配置两项，不能提交到 Git 或发到聊天中：

- `APPLE_MUSIC_DEVELOPER_TOKEN`：现有登录对应的 developer token。
- `APPLE_MUSIC_USER_TOKEN`：同一登录对应的 Music User Token。

在普通环境变量中配置：

- `APPLE_MUSIC_PLAYLIST_ID`：现有 Apple Music **资料库歌单** ID。必须固定该 ID，避免选错歌单；它不是公开分享链接中的 ID。
- `APPLE_MUSIC_API_HOST`：`api.music.apple.com`，或现有账户实际使用的 `amp-api.music.apple.com`。
- `APPLE_MUSIC_SYNC_EXECUTE`：先设为 `0` 预检，确认后改成 `1` 写入。
- 可选 `APPLE_MUSIC_STATE_FILE`：仓库外的私有 checkpoint 路径。常驻云主机应持久保存该文件。

令牌失效后需要更新云端 Secrets；脚本不能在无交互环境中重新登录 Apple ID。

## Codex Cloud

1. 给 `ZJFILOVE/music-daily` 创建或选择云端环境，使用包含这些脚本的分支。
2. 配置上述 Secrets 和环境变量。
3. 将环境的 setup script 设为：

   ```bash
   bash scripts/codex_cloud_sync.sh
   ```

4. 创建该仓库的云端任务，setup 阶段将执行预检或同步；任务日志会显示天数、目标歌曲数和回读结果。首次预检通过后，将 `APPLE_MUSIC_SYNC_EXECUTE` 改为 `1` 再启动云端任务。

这里使用 **setup 阶段**，因为 Codex Cloud 的 Secrets 只在 setup script 中提供，进入 agent 阶段前会移除。脚本不会将令牌写入磁盘或 `.bashrc`。Agent 阶段无需持有 Apple Music 令牌。

Setup 阶段可以联网。若在 agent 阶段另外使用受信任的凭据代理执行同步，环境必须允许 Apple API 域名和 `POST`；仅允许 GET 的设置不能更新歌单。

这份改动提供按需执行入口，不会自动建立定时任务。配置成功后，本机关闭不影响云端任务；仍须由用户或另行配置的调度器启动任务。脚本本身不会创建新的 Codex 聊天。

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

官方文档：[Codex Cloud 环境与 Secrets](https://learn.chatgpt.com/docs/environments/cloud-environment)、[联网设置](https://learn.chatgpt.com/docs/cloud/internet-access)、[Apple Music 用户令牌](https://developer.apple.com/documentation/applemusicapi/user-authentication-for-musickit)。
