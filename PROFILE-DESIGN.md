# Organization Profile Design System

本文件是 **NTHU Physics Student Association // IT Team** 公開 GitHub Organization Profile 的交接與維護規格。

最後一次內容核對日期：**2026-07-28**
主要內容來源：`NTHU-Physics-SA-IT/PastExamWeb_PHY` default branch `main`，核對 commit `c5c29964d014af1482a0de8c538065b2c81c225a`。

## 1. 品牌定位

核心敘述：

> We build and maintain reliable digital systems for the NTHU Physics student community.

中文定位：

> 我們是清大物理系系學會資訊組，負責開發、維護與改善面向物理系學生的數位工具、資訊平台與技術基礎設施。

組織身份固定使用：

- 中文：`清大物理系系學會資訊組`
- 英文：`NTHU Physics Student Association // IT Team`
- 簡短標籤：`NTHU PHYSICS SA // IT TEAM`
- 正確單位屬性：`Physics Student Association IT Team`

不得把團隊描述為國立清華大學官方資訊處、物理系官方 IT Office、學校行政單位或官方校務系統開發單位。

品牌關鍵字：

```text
STUDENT SYSTEMS
OPEN KNOWLEDGE
RELIABLE INFRASTRUCTURE
MAINTAINABLE SOFTWARE
DOCUMENTATION
COLLABORATION
```

## 2. 視覺語言

主題為 **SCIENTIFIC TERMINAL / SYSTEM CONSOLE**。

視覺元素使用終端機啟動畫面、工程圖 title block、module IDs、square ports、90-degree buses、junctions、protocol/data labels、服務拓撲與具名狀態端點。每個圖形都必須表達節點、連線、資料類型或狀態；不使用裝飾性軌道、無標籤波形、漂浮節點或抽象科技線條。座標 grid 只出現在 schematic viewport，不鋪滿 title block、文字區或整張畫布。避免駭客電影、Matrix 字幕雨、遊戲機介面、高頻閃光、過量 neon、第三方統計卡、徽章堆疊與無法驗證的數據。

本設計不得重用參考 Profile 的 SVG、座標、動畫路徑、文案、圖片或配色配置。Header 的原創識別是「組織 boot transcript + student-system schematic」；Contact 則把公開端點畫成同一工程圖系統中的連線模組。

### 工程圖框架與固定路由

- **Title block**：顯示圖面名稱、公開狀態與版本語意，不使用無法驗證的數據。
- **Module IDs**：使用 `Pxx`、`Nxx`、`Jxx`、`Exx`、`Cxx` 等短 ID；文字標籤與節點位置必須在 dark/light 間完全一致。
- **Ports and buses**：連接埠使用 square ports；bus 只使用水平與垂直的 90-degree segments，不使用隨意曲線。
- **Junctions**：只有實際分流或匯流處才畫 junction；交叉但不連接的線不得誤畫接點。
- **Protocol/data labels**：標籤描述公開且一般性的資料路徑，不得放入 IP、credential、token 或私人維運資訊。
- **Grid scope**：grid 僅限 schematic viewport；terminal transcript、title block 與外框保持乾淨。

固定的 Header 路由為：

```text
P01 → N01 → N02 → N03 → J01 → N04
                              └→ N05
```

固定的 Contact 路由為：

```text
P01 → J01 → E01 → C01
        └→ E02 → C01
```

更新幾何時可以重新配置座標，但不得改變上述節點順序、分支語意與 module IDs；desktop/mobile 必須表達相同路由。

## 3. 配色

### Dark mode

| Token | Value |
|---|---|
| Background | `#050A10` |
| Panel | `#09131D` |
| Raised Panel | `#0D1B27` |
| Primary Text | `#F1F6F8` |
| Muted Text | `#91A6B5` |
| Terminal Green | `#65F2AE` |
| Scientific Cyan | `#55D8F5` |
| Signal Amber | `#EBCB72` |
| Border | `#1B3A4B` |

### Light mode

| Token | Value |
|---|---|
| Background | `#F4F7F8` |
| Panel | `#FFFFFF` |
| Raised Panel | `#EAF0F2` |
| Primary Text | `#0A1720` |
| Muted Text | `#536875` |
| Terminal Green | `#087A4B` |
| Scientific Cyan | `#006D85` |
| Signal Amber | `#8A6500` |
| Border | `#B8C9D1` |

紅色只保留給真實 warning/error；正常狀態不得使用紅色作為主品牌色。

## 4. 字體

所有 SVG 文字使用完全相同的 fallback stack：

```css
font-family:
  "Courier New",
  Courier,
  "Liberation Mono",
  "DejaVu Sans Mono",
  monospace;
```

字重：

- 主標題：`900`
- 副標題與章節標籤：`800`
- 專案卡、終端機指令、狀態與 metadata：至少 `700`

不下載、不提交、不嵌入字型檔案，也不連接 Google Fonts、CDN 或其他外部字型服務。

## 5. SVG 尺寸與命名

| Asset | Desktop viewBox | Mobile viewBox |
|---|---:|---:|
| Header | `0 0 1200 360` | `0 0 720 620` |
| PhysArchive project card | `0 0 1200 320` | `0 0 720 580` |
| Contact console | `0 0 1200 190` | `0 0 720 360` |

### Header / Contact 透明畫布規格

Header 與 Contact 的 SVG 根畫布必須保持**完全透明**。實作上直接省略覆蓋完整
`viewBox` 的背景 `<rect>`，透明區域由 SVG 原生透明畫布呈現，而不是以頁面背景色
模擬。即使矩形目前宣告為透明，也不要保留 full-view rect；CSS class 或 SMIL
animation 可能在未來重新覆寫其 fill / opacity。驗證器因此會拒絕 render tree
中的任何 full-view rect，非渲染用途的 `defs`、`clipPath` 等結構除外。

這項規格只移除最外層畫布底色，不得改動內層主要 panel。內層的 theme panel 填色、
細邊框、圓角、角落標記、title block、文字、Header 動畫及 topology schematic
都必須保留。desktop 與 mobile 的 `viewBox`、SVG 尺寸、panel 座標、內容位置及
版面比例不得因透明化而改變。畫布邊界到內層 panel 之間的留白，應在 GitHub
README 背景上顯示為透明。

命名規則：

```text
profile/assets/header-{theme}.svg
profile/assets/header-mobile-{theme}.svg
profile/assets/contact-{theme}.svg
profile/assets/contact-mobile-{theme}.svg
profile/assets/projects/{project}-{theme}.svg
profile/assets/projects/{project}-mobile-{theme}.svg
```

`{theme}` 只使用 `dark` 或 `light`。檔名全部小寫，使用連字號，不使用空格。

Dark/light 變體必須共享幾何與內容，只替換設計 token。Mobile 是重新排版，不可只把 desktop 等比例縮小。

## 6. Header 動畫

完整循環固定為 **16 秒**：

```text
00.000–05.616s  boot transcript 逐行、逐字輸入
05.616–15.488s  完整內容停留（9.872 秒，循環的 61.7%）
15.488–16.000s  快速重置
```

實作規格：

- 每行文字使用獨立 group 與 `clipPath`。
- clip rect 的 width 使用原生 SVG `<animate>` 與離散步進。
- Cursor 約每 `0.8s` 閃爍一次，不使用高頻閃光。
- 完整靜態文字必須保留為 fallback；SMIL 不執行時仍可閱讀。
- `@media (prefers-reduced-motion: reduce)` 必須停用動畫層並直接顯示完整靜態內容。
- 禁止 JavaScript、`script`、`foreignObject`、外部 CSS 與外部動畫函式庫。

調整文案長度時，需要同步調整 clip rect 的終點寬度與離散步數，並重新檢查 desktop/mobile 是否溢出。

## 7. 更新專案卡

新增或更新卡片前：

1. 列出 Organization 的公開 repositories。
2. 讀取 default branch 的 README、依賴檔、Compose、workflows 與公開文件。
3. 排除 archived、空白或缺乏可驗證內容的 repository。
4. 不依 repository 名稱猜用途。
5. 不加入使用者數、流量、可用率、貢獻者數或未啟用功能。
6. 同時更新 desktop/mobile、dark/light 四個變體。
7. 更新 README 的 `<picture>` 路徑、alt text 與 cache version。

目前唯一展示專案：

```text
PHYSARCHIVE
NTHU PHYSICS PAST EXAM PLATFORM
https://github.com/NTHU-Physics-SA-IT/PastExamWeb_PHY
https://physarchive.com/
```

若未來沒有第二個成熟公開專案，不得為了版面湊數。

## 8. Cache busting

README 內 SVG 路徑使用 query version，例如：

```html
srcset="./assets/header-dark.svg?v=3"
```

修改任何已發布 SVG 後：

1. 將該組 asset 的所有 dark/light、desktop/mobile 引用更新為同一個新整數，例如 `v=3`。
2. `<img>` fallback 與所有 `<source>` 必須使用同一版本。
3. 不更名 asset；version 只用於清除 GitHub/CDN 的舊快取。
4. 路徑驗證時先移除 query string，再檢查實體檔案。

## 9. 驗證

### Git 與 diff

```bash
git status --short
git diff --check
git diff --name-only
```

### SVG XML

```python
from pathlib import Path
import xml.etree.ElementTree as ET

for path in Path("profile/assets").rglob("*.svg"):
    ET.parse(path)
    print(f"OK: {path}")
```

### 安全與相容性

每個 SVG 都必須確認：

- 有有效 XML、`viewBox`、`title`、`desc`、`role="img"`。
- 沒有 `script`、`foreignObject`、事件處理屬性、外部 href、`file://`、本機絕對路徑、base64、tracking 或 analytics。
- font stack 完整，重要文字字重符合規格。
- 深淺模式對比足夠。
- 動畫失效與 reduced motion 時仍顯示完整文字。
- 沒有 `.env`、字型檔、token、密碼、IP、主機憑證或私人維運資訊。

### README 路徑

解析 `profile/README.md` 的 `src` 與 `srcset`：

1. 移除 query string 與 fragment。
2. 以 `profile/README.md` 的父目錄解析。
3. 拒絕絕對路徑與逃出 repository 的 `..`。
4. 確認檔案存在，並逐段比對實際大小寫。
5. 檢查 `<picture>` 順序為 dark-mobile、light-mobile、dark、light，且 `<img>` fallback 與非空 alt 存在。

### 視覺檢查

至少產生並人工檢查：

- Header：desktop dark/light、mobile dark/light。
- PhysArchive card：desktop dark/light、mobile dark/light。
- Contact：desktop dark/light、mobile dark/light。
- 動畫輸入中畫面、完整停留畫面、重置、cursor 與 reduced motion。
- 文字裁切、重疊、邊界留白、小字可讀性與中文 glyph fallback。

優先使用環境中既有的 Chromium/Edge headless、Playwright、ImageMagick、Inkscape 或 resvg；不得為預覽加入大型 production dependency。

## 10. 維護注意事項

- 所有資訊必須由公開 repository 或 Organization metadata 驗證。
- OAuth、部署切流量、監控等能力只有在正式啟用且有公開證據後才能加入。
- 不從其他 repository 的 branch-relative raw URL 長期載入素材。
- 不加入個人成員的私人 email；聯絡方式只使用 Organization 與團隊系統。
- 不使用校徽、物理系官方 Logo 或其他商標，除非合法使用權已明確確認。
- 不提交外部圖片、字型、秘密、環境檔或第三方動態統計卡。
- GitHub 對 SVG/SMIL 的支援可能演進；每次重大修改後都在實際 Organization Profile 再檢查一次。
