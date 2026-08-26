# 検出パターン

追記で育てるファイル。1パターン＝「判定」「分級」「実績」の3要素。設計の経緯は書かない。

## P1. ベンダーが旧インストーラを溜め込むフォルダ

最も効く。tempでもキャッシュでもなく、正規のデータフォルダに過去のインストーラが世代ごと残る。自動では消えず、更新のたびに積もる。パス名からは判別できない。

**判定**: 同名または同系統の `.exe` `.msi` `.7z` が複数世代あり、サイズが揃っていて、日付が更新ごとに1つずつ増えている。
**分級**: A（アプリ本体は別の場所にあるので消しても動く）

| パス | 実績 |
|---|---|
| `C:\ProgramData\LGHUB\depots` | 11.3GB / 700MB × 17世代 |
| `C:\ProgramData\NVIDIA Corporation\NVIDIA App\UpdateFramework` | 6.5GB / 旧ドライバの `.exe` |
| `%LOCALAPPDATA%\*-updater` | 3.1GB / electron-updater 系14本の `installer.exe` |
| `C:\ProgramData\Emurasoft\EmEditor\updates` | 0.6GB / `.msi` が世代ごと |
| `C:\ProgramData\Intel Package Cache {GUID}` | 0.4GB / 数年前の `Setup.exe` |

`%LOCALAPPDATA%` 配下は `Where-Object {$_.Name -match 'updater$'}` で一括して拾える。

## P2. アンインストール済みアプリの残置データ

アンインストールは実行ファイルしか消さない。データフォルダは残る。

**判定**: `%APPDATA%` / `%LOCALAPPDATA%` / `%LOCALAPPDATA%Low` のフォルダ名を、レジストリのアンインストール一覧・`Get-AppxPackage`・`Program Files`・スタートメニューのショートカットと突き合わせ、どこにも無いもの。
**分級**: A（ログイン状態やセーブが入るものはB）

| 実績 | MB |
|---|---|
| 旧版が使っていたブラウザプロファイル | 9,610 |
| ローカルLLMアプリ3チャンネル（本体は未インストール） | 6,110 |
| Steamライブラリに無いゲームの `LocalLow` | 1,548 |

## P3. アプリの識別子変更で取り残されたデータ

自作アプリやベータ版で起きる。Tauri / Electron は識別子ごとにデータ先を作る。

**判定**: 同じアプリの識別子違いが複数ある。現行は `tauri.conf.json` / `package.json` の `identifier`。中身が `EBWebView` だけならWebViewキャッシュで、保存データは入っていない。
**分級**: A（`Local Storage` と `IndexedDB` が1MB未満であることを確認してから）

素の製品名フォルダ（`%LOCALAPPDATA%\<ProductName>`）は実行ファイルの置き場所であることがある。ショートカットの参照先を確認してから触る。

## P4. 開発ツールの重複世代

**判定**: 同じツールのバージョン違いが複数。要るのは通常いちばん新しい1つ。
**分級**: B（ビルド環境を壊すので、必ず実地検証とセットで）

| 対象 | 実績 | 備考 |
|---|---|---|
| Windows SDK の旧世代 | 7.6GB | `UninstallString` が空で設定アプリから消せない。`Package Cache\{GUID}\winsdksetup.exe /uninstall /quiet /norestart` を直接叩く |
| VS Build Tools の過剰構成 | 11.5GB | 最小構成で入れ直すと減る。Rust には `Microsoft.VisualStudio.Component.VC.Tools.x86.x64` だけでよい |
| システムPythonのAI系パッケージ | 4.6GB | `torch` が大半。`requirements.txt` が `pip freeze` の丸写しだと使っていないものが列挙される。実際の `import` を見る |

## P5. 再取得可能なキャッシュ（再生成の速さで分ける）

**すぐ生えるものは消さない。** 最古と最新の日付の幅を見て、溜まるのに要した期間で判断する。

**A（当分生えない）**: 次にビルドやインストールを走らせるまで増えない。
`npm-cache` / `%LOCALAPPDATA%\uv\cache` / `.cargo\registry` / `.gradle\caches` / `ms-playwright` / `.cache\puppeteer` / `pip\cache` / ゲームクライアントのWebキャッシュ（1年で5GB規模）

**C（すぐ生える。対象外）**: ブラウザのキャッシュとService Worker、シェーダーキャッシュ、音楽アプリのオフラインデータ、エディタの起動キャッシュ。

## P6. ビルド成果物

**判定**: `target/debug` `target/release` `node_modules` `.venv` `build` `dist`
**分級**: B（開発中なら `debug` のみ。`release` はインストール済みアプリの供給元になっていることがあるので、ショートカットの参照先を先に確認する）

## P7. 移動で解決するもの

消せないが場所は選べる。他ドライブへ移してジャンクションを張る。

**判定**: 数GB以上、内容が実質的に静的、アプリが固定パスを見ている。AIモデル、ゲーム本体、素材ライブラリ。
**分級**: B
**除外**: Python の仮想環境（`.venv`）は内部に絶対パスを持つので移動すると壊れる。モデルやアセットだけを移す。

| 実績 | GB |
|---|---|
| 画像生成環境の `models`（`.venv` は残す） | 16.1 |

## P8. システムが予約している領域

**分級**: B（要admin、ユーザーの運用次第）

- `hiberfil.sys` — 休止状態。デスクトップPCなら使っていないことが多い。`powercfg /h off` で全量解放。起動不能にはならず `powercfg /h on` で戻る。副作用は高速スタートアップの無効化のみ
- `WinSxS` — `DISM /Online /Cleanup-Image /StartComponentCleanup`。数GB。実行後は該当更新をアンインストールできなくなる

## P9. Windows標準の掃除対象

一度も掃除していないPCでは数GBある。掃除済みのPCではほぼ0なので、まずサイズを測ってから提案する。
**分級**: A

`C:\Windows\SoftwareDistribution\Download`（Update の残骸）、`ProgramData\Microsoft\Windows\DeliveryOptimization`（他PCへの配信用キャッシュ。`Delete-DeliveryOptimizationCache -Force`）、ゴミ箱（`Clear-RecycleBin -Force`）、`%TEMP%`、`C:\Windows\Temp`。

`C:\Windows\Installer` は消さない。MSIのアンインストールと修復に要る。
