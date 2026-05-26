# Pomodoro Timer

Windows向けのポモドーロ・テクニック実装タイマーアプリです。
モダンなUIとシステムトレイ対応、タスクバープログレス表示が特徴です。

## 機能

- 集中（25分）・短休憩（5分）・長休憩（15分）の自動切り替え
- 4セッション後に長休憩へ自動遷移
- 画面上部に常時表示できるフローティングHUDバー
- システムトレイアイコン（プログレスリング付き）
- Windowsタスクバーへのプログレス表示
- グローバルキーボードショートカット

## キーボードショートカット

| キー | 動作 |
|------|------|
| Space | スタート / 一時停止 |
| R | リセット |
| ↑ / ↓ | 時間を1分調整 |

## 技術スタック

| 区分 | 技術 |
|------|------|
| 言語 | Python 3.8+ |
| GUI | customtkinter 5.2.0 |
| システムトレイ | pystray |
| 画像処理 | Pillow |
| タスクバー連携 | ctypes (Windows API) |
| パッケージング | PyInstaller |

## セットアップ

```bash
pip install -r requirements.txt
python pomodoro.py
```

または `run.bat` をダブルクリックしてください。

### EXEビルド（配布用）

```bash
pyinstaller Pomodoro.spec
```

`dist/Pomodoro.exe` が生成されます。
