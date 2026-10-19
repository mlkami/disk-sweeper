# 担当: dev（開発環境）

## 範囲

`digest-dev.md` の全部。主に次のもの。

- プロジェクトのフォルダと、その中のビルド成果物・環境（digest の「Build output and environments」の表。ここだけはほかの担当の範囲のパスも含む）
- パッケージマネージャのキャッシュ（npm、pnpm、yarn、pip、uv、cargo、gradle、maven、nuget、go など）
- ツールチェーン、SDK、ビルドツール（Rust、Python、Node、Windows SDK、Visual Studio、Android など）
- エディタの拡張機能（`.vscode`、`.cursor` など）
- AI モデルのキャッシュ（Hugging Face、Ollama、LM Studio など）、Docker、WSL

## 見るパターンと除外

- パターン: P2（ビルド成果物）、P4（キャッシュ）、P6（開発ツールの重複世代）、P8（移動で解決するもの）
- 除外: X2（`Package Cache`）、X3（Visual Studio の `Packages`）、X4（使用中のもの）

## 判定のこつ

- **ビルド成果物の表の `area` を見る。** インストールしたツールの中にあるもの（npm のグローバルの `node_modules\<パッケージ>\node_modules`、`Programs`、`Program Files` の下など）は、ツール本体の一部なので C。ユーザーのプロジェクトの下にあって、隣に manifest があるものだけを P2 として扱う。
- `.venv` などの環境は、作り直しに手間がかかるので B。プロジェクトの最終更新が古い、またはプロジェクトがアーカイブ済み（`_archive` など）なら、その旨を根拠に書く。
- `target` は、`release` の下の実行ファイルが普段使われていないか（ショートカットやスクリプトの参照先）を確かめる（P2）。
- キャッシュは、ファイルの日付の幅から再生成の頻度を読む（P4）。npm の `_npx` は、起動中の MCP サーバーが使っていることがある（X4、`processes.json` を見る）。
- 同じツールの複数の版（P6）は、今使っている版を確かめてから旧版だけを挙げる。
- AI モデルは、使うアプリが今も入っているかを `apps.json` で確かめる（フォルダがあるだけでは入っていると判断しない。`output.md` のルール 7）。アプリがなければ A、あれば P8（移動）か B。
- エディタの拡張機能のフォルダでは、`.` で始まる中途半端なフォルダは A。digest の「Uninstalled editor extensions still on disk」にあるもの（`.obsolete` に載っている、アンインストール済みの版）も A。今の版は C。
