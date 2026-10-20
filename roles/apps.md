# 担当: apps（アプリのデータ）

## 範囲

`digest-apps.md` の全部。主に次のもの。

- Windows: `AppData\Roaming`、`AppData\Local`、`AppData\LocalLow`、`ProgramData`、`Program Files`、`Program Files (x86)`
- Linux / macOS: `~/.cache`、`~/.config`、`~/.local/share`、`~/Library`、`/opt`、`/Applications`

開発ツール、ゲーム、OS の一時ファイルはほかの担当が見る（digest にも入っていない）。

## 見るパターンと除外

- パターン: P3（更新機構が溜め込む旧世代）、P4（キャッシュ）、P5（アンインストール済みアプリの残り）、P7（識別子が変わって取り残されたデータ）
- 除外: X1（名前が似ているだけで別物）、X4（使用中のもの）、X5（ブラウザと資格情報）、X6（ロールバック用の退避）

## 判定のこつ

- **アンインストール済みの残り（P5）が主な獲物。** フォルダ名と `apps.json` を照合する。Windows では、レジストリだけでなく Appx（`source: appx`）も必ず見る。見落とすと、ストアアプリのデータを残骸と取り違える。
- **照合には digest の「Every folder directly under app data locations」を使う。** アプリのデータの置き場の直下のフォルダが、大きさにかかわらず全部載っている。上の大きさ順の表は行数の上限で切れることがあり、100 MB 未満のものも載らない。小さい残骸は、同じアプリのものをまとめて 1 行にしてよい。
- フォルダがあることを、アプリが入っている根拠にしない（`output.md` のルール 7）。
- フォルダ名は、会社名、製品名、識別子（`com.example.app` など）のどれのこともある。`apps.json` の名前と一致しないだけで「残骸」と決めない。中の実行ファイル名や設定ファイルの中身も手がかりにする。
- 残骸の中に、ログイン情報やセーブデータらしきもの（`.sav`、`saves`、`profiles`、`Local Storage` など）があれば B。
- 更新の旧世代（P3）は、同じ系統のファイルが日付順に複数あるかで見る。
- ブラウザのプロファイル、Cookie、Service Worker、`OptGuideOnDeviceModel` は C（X5）。
- ゲームの譜面、リプレイ、録画、セーブなど、ユーザーが集めたり作ったりしたものは削除候補にしない。D に入れて「ユーザー資産らしい」と書く。
- digest の「Links not followed」で、リンク先が存在しない（`target exists: no`）ものは報告する。リンクだけを消せば片付く。
- `processes.json` で起動中のアプリのフォルダは、消すと壊れるおそれがある（X4）。
- WSL のディストリビューションの置き場（`AppData\Local\Packages\CanonicalGroupLimited...\LocalState` など）が大きいのは、中の `.vhdx` のためである。`.vhdx` は dev 担当が見るので、挙げない。
