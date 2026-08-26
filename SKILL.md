---
name: disk-sweeper
description: Windows PC の空き容量を1回の指示で最大化する。「Cドライブが一杯」「容量を空けたい」「ディスク掃除して」「不要なファイルを探して」「ジャンクファイル」といった依頼で使う。全体を一度に測って分類し、根拠を確認したうえで一括承認を取り、まとめて削除して検証まで行う。開発環境・ゲームデータ・ブラウザデータを壊さないための除外知識を含む。
---

# disk-sweeper

走査 → 分類 → 根拠確認 → 一括承認 → 実行 → 検証を1パスで通す。途中で小分けの質問をしない。

フェーズ2に入る前に、同じディレクトリの `patterns.md`（何を探すか）と `exclusions.md`（触ると壊れるもの）を読む。

## 原則

1. **中身を見ずに性質を断定しない。** パス名や隣のフォルダ名から用途を推測しない。削除候補に入れたものは中を開き、ファイル名・拡張子・日付を根拠として記録する。
2. **質問は1回にまとめる。** 走査の途中で聞かない。全部調べ終えてから1つの表で承認を取る。
3. **削除量の合計と実測の空き増加を突き合わせる。** ずれたら報告する。黙って通さない。
4. **ユーザーの資産は削除候補に入れない。** 写真・録画・セーブ・購入物は、サイズが大きくても別枠で判断を仰ぐ。「自動生成されるものだから価値が低い」と決めつけない。

## 1. 走査

**重いものは背景で回し、待っている間に軽いものを進める。** 直列にすると1回で終わらない。全ドライブの空き容量も取る（移動先の判断に要る）。

```powershell
# 背景で回す：巨大な単体ファイル。インストーラの溜め込みはここに出る
Get-ChildItem "$env:USERPROFILE",'C:\ProgramData' -Recurse -File -Force -EA SilentlyContinue |
  Where-Object { $_.Length -gt 150MB -and $_.Extension -match '^\.(exe|msi|msu|cab|zip|7z|nupkg|bin|iso)$' } |
  Sort-Object Length -Descending | Select-Object -First 30 Length, LastWriteTime, FullName

# ドライブ（移動先の空きも見る）
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Select-Object DeviceID,
  @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}}, @{n='FreeGB';e={[math]::Round($_.FreeSpace/1GB,1)}}

# 大きいフォルダ（深さ2、300MB超）
foreach ($r in @("$env:USERPROFILE",'C:\ProgramData','C:\Program Files','C:\Program Files (x86)')) {
  Get-ChildItem $r -Directory -Force -Recurse -Depth 1 -EA SilentlyContinue | ForEach-Object {
    $b = [long](Get-ChildItem $_.FullName -Recurse -File -Force -EA SilentlyContinue |
      Measure-Object -Property Length -Sum).Sum
    if ($b -gt 300MB) { [PSCustomObject]@{MB = ($b -shr 20); Path = $_.FullName} }
  }
} | Sort-Object MB -Descending

# インストール済みアプリ（孤児判定の照合用。Appx を取り忘れると稼働中のストアアプリを孤児と誤判定する）
Get-ItemProperty @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*') -EA SilentlyContinue |
  Where-Object DisplayName | Select-Object -Expand DisplayName
Get-AppxPackage | Select-Object -Expand Name

# システムが予約している領域
Get-ChildItem C:\ -Force -File | Where-Object Name -match 'hiberfil|pagefile'
```

`Get-ChildItem` は読めないファイルを黙って飛ばす。測定値は実占有量より小さく出る。

## 2. 分類

`patterns.md` と照合して4つに分ける。

| | 意味 |
|---|---|
| **A 即削除可** | アプリ本体が無い、または再取得可能で当分再生成されない |
| **B 要判断** | 消せるが失うものがある。または移動で解決できる |
| **C 除外** | `exclusions.md` に該当、またはすぐ再生成される |
| **D 未分類** | 500MB超なのにルールが無い。**レポートに必ず残す** |

**B群では削除の代わりに移動を検討する。** 消せないが場所は選べるもの（モデルデータ、ゲーム、素材）は、他ドライブへ移してジャンクションを張れば、アプリから見た位置は変わらない。今日の最大単発はこれで16GBだった。

Dは学習対象なので握り潰さない。中を開いて何であるかを1行で書く。

## 3. 根拠確認

AとBの全項目について、消す前に中を見る。ここを省くと誤判定する。

- 直下のファイル名と拡張子（`.exe` が世代ごと並ぶ＝インストーラの溜め込み）
- 最古と最新の日付（溜まるのに要した期間＝再生成の速さ）
- ユーザーの資産が混ざっていないか（`.rofl` `.webm` `.sav` `.psd` 等を検索して0件を確認）
- 掴んでいるプロセス（`Get-Process`。ロックされたら即座に手を引く）

## 4. 一括承認

**1つのメッセージで全部出す。** 分類ごとの表にし、各行にサイズと根拠を1行で添える。AskUserQuestion を使うなら、A群まとめて1問＋B群の大きいものを個別に、で最大4問に収める。

Dは「これが何か分かりますか」と添える。ユーザーしか知らないことがある。

## 5. 実行

- **管理者権限が要るものは1本のスクリプトにまとめ、UACを1回で済ませる。** `ProgramData` と `Program Files` の配下はほぼ要る。
- スクリプトに日本語を書かない。Windows PowerShell 5.1 はBOM無しUTF-8をANSIとして読む。ログも英数字にする。
- 削除は `-LiteralPath` で1件ずつ。空白を含むパスは短縮名が安定する（`(New-Object -ComObject Scripting.FileSystemObject).GetFolder($p).ShortPath`）。
- 各項目の削除前サイズを控えてから消す。事後の突き合わせに要る。
- 移動は `robocopy <src> <dst> /E /MOVE /R:1 /W:1 /NP`（終了コード8未満が成功）のあと `New-Item -ItemType Junction -Path <src> -Target <dst>`。robocopy は移動元フォルダごと消すので、ジャンクションは後から張る。

## 6. 検証

1. 削除量の合計と実測の空き増加を比べる。差の原因が分からなければ「分からない」と書く。
2. 触っていないフォルダのサイズが変わっていないことを確認する（予定外の消失が無いことの証明）。
3. **開発ツールに触れたら実地でビルドを通す。** 一時ディレクトリに最小プロジェクトを作り、コンパイルとリンクを確認する。着手前にも同じビルドを流してベースラインを取っておく。
4. 稼働中だったアプリを起動し直せるか確認する。

## 7. レポート

`%USERPROFILE%\disk-sweeper-report-<YYYYMMDD>.md` に書き出し、保存先を伝える。

```markdown
# disk-sweeper 実行結果 <日付>

環境: Windows <版> / C: <総容量> / 開始 <空き> → 終了 <空き>

## 未分類だったもの
| パス | MB | 中身から分かったこと |

## 判断が分かれたもの
| 対象 | 推奨 | ユーザーの判断 | 理由 |

## 起きた問題
（部分削除・ロック・検証失敗・想定との差。無ければ「なし」）

## 既知パターンの結果
| パターン名 | 件数 | MB |
```

上の3節が本体。既知パターンは集計だけでよい。
