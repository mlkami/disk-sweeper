# 除外リスト

**削除候補に含める前に必ず照合する。** 過去に不具合を招いた、または招きかけた対象のリスト。

## X1. 名前が似ているだけで別物

| 触ってよい | 触ってはいけない | 見分け方 |
|---|---|---|
| `ProgramData\NVIDIA Corporation\NVIDIA App\UpdateFramework`（旧ドライバ） | `ProgramData\NVIDIA`（DLSS / DLAA のAIモデル本体） | 後者は `NGX\models\dlss\` の `.bin`。ゲームが実行時に参照 |
| ゲームクライアントの `webcache`（Chromium構造） | `Replays` の `.rofl` や `Highlights` の `.webm` | 前者はハッシュ名で拡張子なし。後者はユーザー資産 |

隣接している、あるいは名前が似ているだけで同列に扱わないこと。

## X2. 消すと他を消せなくなるもの

`C:\ProgramData\Package Cache` は残す。Windows SDK はレジストリの `UninstallString` が空のため「設定」アプリから削除できず、`Package Cache\{GUID}\winsdksetup.exe /uninstall` が唯一の削除手段となる。先に消去すると SDK が二度とアンインストールできなくなる。

同様に、パッケージマネージャのデータベースやアンインストーラ本体は、一見キャッシュに見えても必ず残す。

## X3. インストール状態の記録が同居しているフォルダ

`C:\ProgramData\Microsoft\VisualStudio\Packages` を一括削除してはならない。再取得可能なインストーラ群に見えるが、同一階層にインストール状態を記録する `_Instances` が同居している。これを削除すると `vswhere` が Build Tools を検出できなくなり、`link.exe` 自体が残っていても Rust のリンク処理に失敗する。

復旧には再インストールが必要であり、**リスクに対して得られるディスク容量が見合わない。**

## X4. 稼働中のプロセスが掴んでいるもの

削除時に `Access denied` や `being used by another process` が発生した場合は、直ちに処理を中断して対象から除外する。**中途半端に削除された状態が最も危険である。**

ロック状態は事前に確認する。Electron の旧バージョンディレクトリは稼働中の現行版から参照されている場合があり、`~/.npm/_npx` 配下は常駐中の MCP サーバーが使用している可能性がある。ディレクトリ全体を削除対象とせず、ロックされていない下位階層のみに限定する。

## X5. ブラウザと資格情報

履歴、Cookie、パスワード、サイトデータは削除対象外とする。キャッシュや Service Worker も即座に再生成され、操作性を損なうため除外する。

Chrome の `OptGuideOnDeviceModel`（約4GBのオンデバイス AI モデル）は削除しても自動で再ダウンロードされ、新規プロファイル作成時にも取得されるため、削除しても効果がない。

## X6. ロールバック用の退避

`C:\Windows\LastGood` および `LastGood.Tmp` は、ドライバ更新前のバックアップファイルである。削除しても Windows 自体は動作するが、ドライバ更新時の障害からロールバックできなくなる。大半は1GB未満であり、リスクに対して容量削減効果が小さい。

## X7. 最終アクセス日時は環境によって当てにならない

Windows はデフォルトで最終アクセス日時を記録しない（`fsutil behavior query DisableLastAccess` の値が `2` なら無効）。Linux の `relatime` でも完全な保証はない。**取得可能な環境であっても判断の主根拠としないこと。** ファイル内容の実態を確認し、「未使用」と断定せずにユーザーの判断を仰ぐ。
