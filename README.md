# YouTube 视频总结 (本地 + Gemini 免费 API)

一条命令总结任意 YouTube 视频，中英对照输出。完全免费（用 Gemini 免费额度）。

## 一次性设置

1. 依赖已装好：`google-genai`、`yt-dlp`（已通过 pip 安装）。
2. 申请免费 Gemini API key：
   - 打开 https://aistudio.google.com/apikey ，登录 Google 账号，点 **Create API key**。
3. 设置环境变量（PowerShell）：
   ```powershell
   setx GEMINI_API_KEY "你的key"
   ```
   然后**重开一个终端**让它生效。

## 使用

```powershell
# 中英对照（默认）
python summarize.py "https://www.youtube.com/shorts/x8lcdDbKZto"

# 仅中文 / 仅英文
python summarize.py "<url>" --lang zh
python summarize.py "<url>" --lang en

# 保存到文件
python summarize.py "<url>" -o summary.txt
```

## 工作原理

- **默认 auto 模式**：先把链接直接交给 Gemini，让它「看」视频做总结（无需下载，对 Shorts 很合适）。
- **自动回退**：万一直传失败（地区限制等），改用 `yt-dlp` 抓字幕文本再总结。
- `--mode direct` 只直传，`--mode transcript` 只用字幕。

## 免费额度说明

Gemini 免费层（gemini-2.5-flash）对个人日常使用足够。视频直传有每日时长配额，
但 Shorts / 普通视频消耗很小。额度详情见 https://ai.google.dev/pricing 。

## 没有字幕又直传失败时

需要本地语音转录：安装 `ffmpeg` 并 `pip install faster-whisper`，可在此基础上扩展。
目前脚本会在这种情况下给出提示。
