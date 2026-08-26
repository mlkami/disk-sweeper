# 除外リスト

**削除候補に入れる前に必ず突き合わせる。** どれも実際に壊した、または壊しかけたもの。

## X1. 名前が似ているだけで別物

| 触ってよい | 触ってはいけない | 見分け方 |
|---|---|---|
| `ProgramData\NVIDIA Corporation\NVIDIA App\UpdateFramework`（旧ドライバ） | `ProgramData\NVIDIA`（DLSS / DLAA のAIモデル本体） | 後者は `NGX\models\dlss\` の `.bin`。ゲームが実行時に使う |
| `Riot Games\League of Legends\Saved\webcache`（Chromiumキャッシュ） | `Documents\League of Legends\Replays`（`.rofl`）と `Highlights`（`.webm`） | 前者はハッシュ名で拡張子なし |

隣接している、名前が似ている、というだけで同類にしない。

## X2. 消すと他を消せなくなるもの

`C:\ProgramData\Package Cache` を残す。Windows SDK はレジストリの `UninstallString` が空で設定アプリから削除できず、`Package Cache\{GUID}\winsdksetup.exe /uninstall` が唯一の手段になる。先に消すとSDKが永久に消せなくなる。

## X3. VS Build Tools の登録が同居しているフォルダ

`C:\ProgramData\Microsoft\VisualStudio\Packages` を丸ごと消さない。再ダウンロード可能なペイロードに見えるが、`_Instances`（インストール状態の記録）が同じ階層にある。これが消えると `vswhere` が Build Tools を発見できず、`link.exe` が無傷でもRustのリンクが通らなくなる。

復旧には `InstallCleanup.exe -f` とブートストラッパでの再インストールが要る。**払う代償に対して得られる容量が見合わない。**

## X4. 稼働中のアプリが掴んでいるもの

Electron の旧バージョンフォルダ（`app-x.y.z`）は、現行版が中のモジュールを参照していることがある。削除して `The process cannot access the file` が出たら、そこで止めて対象から外す。**部分的に消えた状態が最も危険。**

## X5. ブラウザのユーザーデータ

`Chrome\User Data\Default` 配下の履歴・Cookie・パスワード・サイトデータには触れない。`Cache` と `Service Worker` も、すぐ再生成されるうえ体感が悪くなるだけなので対象外。

`OptGuideOnDeviceModel`（4GB規模のオンデバイスAIモデル）は消しても自動で再ダウンロードされる。新規プロファイルにも自動で入るので、容量は戻る。

## X6. ロールバック用の退避

`C:\Windows\LastGood` / `LastGood.Tmp` はドライバ更新前のシステムファイルの控え。消してもWindowsは動くが、更新で不具合が出たときに戻せない。1GB未満のことが多く割に合わない。

## X7. アクセス日時は当てにならない

Windows は既定で最終アクセス日時を記録しない（`fsutil behavior query DisableLastAccess` が `2` なら無効）。「最後に使った日」はファイルシステムから分からない。分かるのは作成日と更新日だけなので、「使っていない」と断定せず根拠を示してユーザーに判断を渡す。
