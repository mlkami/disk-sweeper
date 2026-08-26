# 除外リスト

**削除候補に入れる前に必ず突き合わせる。** どれも実際に壊した、または壊しかけたもの。

## X1. 名前が似ているだけで別物

| 触ってよい | 触ってはいけない | 見分け方 |
|---|---|---|
| `ProgramData\NVIDIA Corporation\NVIDIA App\UpdateFramework`（旧ドライバ） | `ProgramData\NVIDIA`（DLSS / DLAA のAIモデル本体） | 後者は `NGX\models\dlss\` の `.bin`。ゲームが実行時に使う |
| ゲームクライアントの `webcache`（Chromium構造） | `Replays` の `.rofl` や `Highlights` の `.webm` | 前者はハッシュ名で拡張子なし。後者はユーザーの資産 |

隣接している、名前が似ている、というだけで同類にしない。

## X2. 消すと他を消せなくなるもの

`C:\ProgramData\Package Cache` を残す。Windows SDK はレジストリの `UninstallString` が空で設定アプリから削除できず、`Package Cache\{GUID}\winsdksetup.exe /uninstall` が唯一の手段になる。先に消すとSDKが永久に消せなくなる。

同じ理屈で、パッケージマネージャのDBやアンインストーラ本体は、キャッシュに見えても残す。

## X3. インストール状態の記録が同居しているフォルダ

`C:\ProgramData\Microsoft\VisualStudio\Packages` を丸ごと消さない。再ダウンロード可能なペイロードに見えるが、`_Instances`（インストール状態の記録）が同じ階層にある。これが消えると `vswhere` が Build Tools を発見できず、`link.exe` が無傷でもRustのリンクが通らなくなる。

復旧には再インストールが要る。**払う代償に対して得られる容量が見合わない。**

## X4. 稼働中のプロセスが掴んでいるもの

削除して `Access denied` や `being used by another process` が出たら、そこで止めて対象から外す。**部分的に消えた状態が最も危険。**

事前に確認する。Electron の旧バージョンフォルダは現行版がモジュールを参照していることがある。`~/.npm/_npx` 配下は常駐している MCP サーバーが実行中のことがある。キャッシュ全体ではなく、掴まれていない下位ディレクトリだけに絞る。

## X5. ブラウザと資格情報

履歴・Cookie・パスワード・サイトデータには触れない。キャッシュとService Workerも、すぐ再生成されるうえ体感が悪くなるだけなので対象外。

Chrome の `OptGuideOnDeviceModel`（4GB規模のオンデバイスAIモデル）は消しても自動で再ダウンロードされる。新規プロファイルにも自動で入るので、容量は戻る。

## X6. ロールバック用の退避

`C:\Windows\LastGood` / `LastGood.Tmp` はドライバ更新前のシステムファイルの控え。消してもWindowsは動くが、更新で不具合が出たときに戻せない。1GB未満のことが多く割に合わない。

## X7. 最終アクセス日時は環境によって当てにならない

Windows は既定で記録しない（`fsutil behavior query DisableLastAccess` が `2` なら無効）。Linux の `relatime` は概ね取得できる。**取れる環境でも判断の主根拠にしない。** 中身の確認を根拠にして、「使っていない」と断定せずユーザーに判断を渡す。
