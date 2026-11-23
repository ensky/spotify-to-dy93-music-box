# dy93 — 兒歌 SD 卡流水線

把 YouTube 兒歌下載、轉成晶片吃得下的 mp3、放進 SD 卡給玩具音樂機播放。

## 目錄結構

```
dy93/
├── yt.py               # 步驟 1：YouTube → source/yt/（原始影片，檔名含 [video id]）
├── mp3.py              # 步驟 2：source/yt/ → source/mp3/（轉好的 mp3，可剪片頭）
├── sync.py             # 步驟 3：管理 sync/ 並推送到 SD 卡
├── AGENTS.md           # 本文件（CLAUDE.md 是它的 symlink）
├── source/
│   ├── yt/             # YouTube 原始影片（來源真相，不要手動改）
│   └── mp3/            # 轉好的 mp3（以歌名標籤命名，準備上卡的歌）
└── sync/
    ├── sources.tsv     # 帳本：sync/ 下每個檔案對應的 YouTube 來源
    ├── 01/001_xxx.mp3  # SD 卡的完整映射（NN folder / 每個 folder 內從 001 編號）
    └── ...
```

## 晶片限制（硬規則，轉檔與 check 都會驗證）

- FAT32 SD 卡；folder `01`–`99`，**每個 folder 內檔案從 `001` 開始連續編號**：`NN/NNN_標籤.mp3`
- 必須是真的 MP3（MPEG-1 Layer III）；**取樣率 ≤ 44.1 kHz**（48 kHz 晶片讀不了）
- 預設 128 kbps CBR（晶片未驗證過 320 kbps，要換先放一首測試）
- tag 只能是數字值（`001`、`002`…），不可內嵌封面圖
- macOS 會塞幽靈檔（`._*`、`.DS_Store`）讓晶片讀歪——push 完自動清，拔卡前也可手動跑 `clean`

## 常見任務

把某首歌裝到最新號碼（最常用，一句話完成：下載 → 轉檔 → 進 sync/）：

```bash
python3 sync.py add "歌名或 YouTube 網址"
```

指定 folder / 剪片頭 / 先看候選：

```bash
python3 sync.py add "網址" --folder 3          # 裝進 03/（檔號接著編）
python3 sync.py add "網址" --start 10.05      # 剪掉前 10.05 秒片頭
python3 yt.py --list "關鍵字"                  # 列前 5 個候選，不下載
python3 sync.py list                          # 看 sync/ 每首歌的 YouTube 來源
```

SD 卡插上來之後（唯一要做的事）：

```bash
python3 sync.py push        # 卡 ← sync/ 完整鏡射（新增/更新/刪除），並清幽靈檔
python3 sync.py check       # 驗證：編號、音訊格式、無幽靈檔、與 sync/ 一致、帳本有來源
diskutil eject SDCARD       # 拔卡前一定要正常退出
```

## 規則

- root 下的 `01/`、`02/`、`03/` 是舊歌庫封存（2026-08，與 sync/ 無關），**保持原樣不要動**
- `sync/` 是卡上內容的唯一真相；改卡一律走 script，不要直接動卡或手動改 sync/
- `sync/` 下每個檔案都必須在 `sync/sources.tsv` 有來源紀錄（`check` 會驗證）；
  直接以檔案方式 `add` 的歌，來源欄是 `-`，要人工補
- 帳本裡 `(legacy, pre-dy93)` 是移轉進來的舊歌，來源未知
- 依賴：`yt-dlp`（brew）、`ffmpeg`；YouTube 403 時 `yt.py` 會自動 fallback 到 HLS 串流
