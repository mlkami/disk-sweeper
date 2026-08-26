# 検出パターン

追記で育てる。1パターン＝「判定」「分級」「実績」。**回収量の大きい順に並べる。**

## P1. ゴミ箱（全ドライブ分）

実測で最大の回収源。**起動ドライブだけ見ると外付けを丸ごと見落とす。** ドライブごとに独立したゴミ箱がある。

**判定**: Windows は各ドライブ直下の `$RECYCLE.BIN`。Linux は `~/.local/share/Trash` と各マウントの `.Trash-<uid>`。macOS は `~/.Trash` と `<volume>/.Trashes`
**分級**: A（ただし中身はユーザーが捨てたもの。件数と最大ファイル名を提示してから消す）
**実績**: 4ドライブ計 40.6GB

## P2. ビルド成果物

**リポジトリ横断で探す。** 1つ見つけて終わりにしない。worktree があると同じ `node_modules` が複製される。

**判定**: `target` `node_modules` `.venv` `build` `dist`。`find -name target -type d` して同階層に `Cargo.toml` があるか確認する
**分級**: B（`release` はインストール済みアプリの供給元になっていることがある。ショートカットや起動スクリプトの参照先を先に確認）
**実績**: 19件 28.1GB（Rust `target` ×10 で 20.5GB、worktree の `node_modules` ×9 で 7.6GB）／ Tauri 2プロジェクトの `target/debug` 16.2GB

## P3. 更新機構が溜め込む旧世代

tempでもキャッシュでもなく、正規のデータフォルダに過去のインストーラやビルド材料が世代ごと残る。自動では消えず、更新のたびに積もる。パス名からは判別できない。

**判定**: 同系統のファイルが複数世代あり、サイズが揃っていて、日付が更新ごとに1つずつ増えている
**分級**: A

| 対象 | 実績 |
|---|---|
| `~/.cache/paru/clone`（AURのビルド材料） | 15GB / 79リポジトリ |
| `C:\ProgramData\LGHUB\depots` | 11.3GB / 700MB × 17世代 |
| `ProgramData\NVIDIA Corporation\NVIDIA App\UpdateFramework` | 6.5GB / 旧ドライバ |
| `%LOCALAPPDATA%\*-updater` | 3.1GB / electron-updater 14本 |
| `ProgramData\Emurasoft\EmEditor\updates` | 0.6GB |

## P4. 再取得可能なキャッシュ（再生成の速さで分ける）

すぐ生えるものは消さない。最古と最新の日付の幅を見て判断する。

**A（当分生えない）**: パッケージマネージャのキャッシュ（npm / pnpm / pip / uv / cargo registry / gradle / pacman）、ブラウザ自動化のバイナリ（playwright / puppeteer）、ゲームクライアントのWebキャッシュ（1年で5GB規模）
**C（すぐ生える。対象外）**: ブラウザのキャッシュとService Worker、シェーダーキャッシュ、サムネイル、音楽アプリのオフラインデータ、エディタの起動キャッシュ

**ハードリンクに注意。** `~/.npm/_cacache` は各プロジェクトの `node_modules` と実体を共有する。消しても見かけほど空かない。

## P5. アンインストール済みアプリの残置データ

アンインストールは実行ファイルしか消さない。

**判定**: アプリのデータ置き場を、インストール済み一覧と突き合わせる。Windows はレジストリ3キー + **`Get-AppxPackage`（忘れるとストアアプリを孤児と誤判定する）**。Linux は `pacman -Qq` / `dpkg -l` 等 + `/opt` と `.desktop` の実在確認
**分級**: A（ログイン状態やセーブが入るものはB）
**実績**: 削除済みパッケージの残キャッシュ 929件 7.3GB（`paccache -dk0` で検出、削除には昇格が要る）／ 旧版のブラウザプロファイル 9.6GB ／ ローカルLLM 3チャンネル 6.1GB ／ Steamに無いゲームの `LocalLow` 1.5GB

## P6. 開発ツールの重複世代

**判定**: 同じツールのバージョン違いが複数。要るのは通常いちばん新しい1つ
**分級**: B（ビルド環境を壊すので必ず実地検証とセットで）

| 対象 | 実績 | 備考 |
|---|---|---|
| Windows SDK の旧世代 | 7.6GB | `UninstallString` が空で設定アプリから消せない。`Package Cache\{GUID}\winsdksetup.exe /uninstall /quiet /norestart` を直接叩く |
| VS Build Tools の過剰構成 | 11.5GB | 最小構成で入れ直すと減る。Rust には `Microsoft.VisualStudio.Component.VC.Tools.x86.x64` だけでよい |
| システムPythonのAI系パッケージ | 4.6GB | `requirements.txt` が `pip freeze` の丸写しだと使っていないものが並ぶ。実際の `import` を見る |

## P7. アプリの識別子変更で取り残されたデータ

Tauri / Electron は識別子ごとにデータ先を作る。

**判定**: 同じアプリの識別子違いが複数。現行は `tauri.conf.json` 等の `identifier`。中身がWebViewキャッシュだけなら保存データは入っていない
**分級**: A（保存領域が1MB未満であることを確認してから）

素の製品名フォルダは実行ファイルの置き場所であることがある。参照先を確認してから触る。

## P8. 移動で解決するもの

消せないが場所は選べる。空きのあるドライブへ移してリンクを張る（Windows は junction、Unix はシンボリックリンクか bind mount）。

**判定**: 数GB以上、内容が実質的に静的、アプリが固定パスを見ている。AIモデル、ゲーム、素材
**分級**: B
**除外**: Python の仮想環境は内部に絶対パスを持つので移動すると壊れる
**実績**: 画像生成環境の `models` 16.1GB

## P9. システム予約とOS標準の掃除対象

**分級**: B（昇格が要る）

- **休止ファイル** — Windows の `hiberfil.sys`。デスクトップなら未使用のことが多く、`powercfg /h off` で全量解放。起動不能にはならず `/h on` で戻る。副作用は高速スタートアップの無効化のみ。Unix は zram / swap で該当しないことが多い
- **Windows** — `WinSxS` の `DISM /Online /Cleanup-Image /StartComponentCleanup`（実行後は該当更新を戻せない）、`SoftwareDistribution\Download`、配信最適化キャッシュ。**`C:\Windows\Installer` は消さない**
- **Linux** — `journalctl --vacuum-size=`、`paccache -rk1`、prunable な git worktree（`git worktree prune`）
