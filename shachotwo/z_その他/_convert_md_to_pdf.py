#!/usr/bin/env python3
"""
Markdown → PDF 変換ツール

使い方:
  # 単一ファイル
  python3 _convert_md_to_pdf.py shachotwo/g_ナレッジ/g_02_製造業ドメイン知識大全.md

  # ディレクトリ内の全MDファイル
  python3 _convert_md_to_pdf.py shachotwo/g_ナレッジ/

  # 引数なし → デフォルト（g_ナレッジ/ 全ファイル）
  python3 _convert_md_to_pdf.py

方式: Markdown → styled HTML → Chrome headless CDP → PDF
フォント: Hiragino Sans（macOS標準）
"""

import base64
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import markdown
import websocket  # pip install websocket-client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(BASE_DIR, "shachotwo", "g_ナレッジ")

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# 9230 は既存Chromeセッションと衝突しやすいため別ポート
CDP_PORT = 19230

# ── HTML テンプレート ──────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  @page {{
    size: A4;
    margin: 22mm 20mm 25mm 20mm;
  }}

  * {{ box-sizing: border-box; }}

  body {{
    font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", "Yu Gothic", sans-serif;
    font-size: 10pt;
    line-height: 1.75;
    color: #1a1a1a;
    max-width: 100%;
    padding: 0;
    margin: 0;
  }}

  /* ── 見出し ── */
  h1 {{
    font-size: 22pt;
    font-weight: 700;
    color: #0f172a;
    border-bottom: 3px solid #1e40af;
    padding-bottom: 8px;
    margin: 32px 0 16px 0;
    page-break-after: avoid;
  }}
  h2 {{
    font-size: 16pt;
    font-weight: 700;
    color: #1e3a5f;
    border-bottom: 2px solid #3b82f6;
    padding-bottom: 5px;
    margin: 28px 0 12px 0;
    page-break-after: avoid;
  }}
  h3 {{
    font-size: 13pt;
    font-weight: 700;
    color: #1e3a5f;
    margin: 22px 0 8px 0;
    page-break-after: avoid;
  }}
  h4 {{
    font-size: 11pt;
    font-weight: 700;
    color: #374151;
    margin: 16px 0 6px 0;
    page-break-after: avoid;
  }}
  h5 {{
    font-size: 10pt;
    font-weight: 700;
    color: #4b5563;
    margin: 12px 0 4px 0;
  }}

  /* ── 段落・テキスト ── */
  p {{
    margin: 6px 0 10px 0;
    text-align: justify;
    word-break: break-all;
  }}
  strong {{
    color: #1e3a5f;
    font-weight: 700;
  }}

  /* ── テーブル ── */
  table {{
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0 16px 0;
    font-size: 9pt;
    line-height: 1.5;
    page-break-inside: avoid;
  }}
  thead th {{
    background: #1e3a5f;
    color: #ffffff;
    font-weight: 600;
    padding: 7px 10px;
    text-align: left;
    border: 1px solid #1e3a5f;
    white-space: nowrap;
  }}
  tbody td {{
    padding: 6px 10px;
    border: 1px solid #d1d5db;
    vertical-align: top;
  }}
  tbody tr:nth-child(even) {{
    background: #f8fafc;
  }}

  /* ── 引用 ── */
  blockquote {{
    border-left: 4px solid #3b82f6;
    margin: 14px 0;
    padding: 10px 18px;
    background: #eff6ff;
    color: #1e40af;
    font-size: 9.5pt;
    border-radius: 0 6px 6px 0;
  }}
  blockquote p {{
    margin: 4px 0;
  }}

  /* ── コードブロック ── */
  pre {{
    background: #1e293b;
    color: #e2e8f0;
    padding: 14px 18px;
    border-radius: 8px;
    font-family: "SF Mono", "Menlo", "Consolas", monospace;
    font-size: 8.5pt;
    line-height: 1.6;
    overflow-x: auto;
    white-space: pre-wrap;
    word-wrap: break-word;
    margin: 10px 0 14px 0;
    page-break-inside: avoid;
  }}
  code {{
    background: #f1f5f9;
    color: #be185d;
    padding: 1px 5px;
    border-radius: 3px;
    font-family: "SF Mono", "Menlo", "Consolas", monospace;
    font-size: 9pt;
  }}
  pre code {{
    background: none;
    color: inherit;
    padding: 0;
    border-radius: 0;
    font-size: inherit;
  }}

  /* ── リスト ── */
  ul, ol {{
    margin: 6px 0 10px 0;
    padding-left: 22px;
  }}
  li {{
    margin: 3px 0;
    line-height: 1.65;
  }}
  li > ul, li > ol {{
    margin: 2px 0;
  }}

  /* ── 水平線 ── */
  hr {{
    border: none;
    border-top: 1px solid #d1d5db;
    margin: 24px 0;
  }}

  /* ── 印刷最適化 ── */
  h1, h2, h3, h4 {{
    page-break-after: avoid;
  }}
  table, pre, blockquote {{
    page-break-inside: avoid;
  }}
  p {{
    orphans: 3;
    widows: 3;
  }}
</style>
</head>
<body>
{content}
</body>
</html>"""


# ── Chrome CDP マネージャー ──────────────────────────────────

class ChromeCDP:
    """Chrome headless を CDP で制御するコンテキストマネージャー。"""

    def __init__(self, port=CDP_PORT):
        self.port = port
        self.proc = None
        self.ws = None
        self._msg_id = 1

    def __enter__(self):
        # 前回残骸を掃除
        subprocess.run(["pkill", "-f", f"remote-debugging-port={self.port}"],
                       capture_output=True)
        time.sleep(0.5)

        self.proc = subprocess.Popen(
            [
                CHROME_PATH,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                f"--remote-debugging-port={self.port}",
                "--remote-allow-origins=*",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # ページターゲットのWSに接続
        page_ws_url = None
        for _ in range(40):
            try:
                resp = urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json", timeout=2
                )
                targets = json.loads(resp.read())
                for t in targets:
                    if t["type"] == "page":
                        page_ws_url = t["webSocketDebuggerUrl"]
                        break
                if page_ws_url:
                    break
            except Exception:
                pass
            time.sleep(0.5)

        if not page_ws_url:
            raise RuntimeError("Chrome CDP 接続タイムアウト")

        self.ws = websocket.create_connection(page_ws_url, timeout=60)
        self._send("Page.enable")
        return self

    def __exit__(self, *args):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()

    def _send(self, method, params=None):
        msg = {"id": self._msg_id, "method": method, "params": params or {}}
        self.ws.send(json.dumps(msg))
        target_id = self._msg_id
        self._msg_id += 1
        for _ in range(500):
            r = json.loads(self.ws.recv())
            if r.get("id") == target_id:
                if "error" in r:
                    raise RuntimeError(f"CDP {method}: {r['error']}")
                return r.get("result", {})
        raise RuntimeError(f"CDP {method}: タイムアウト")

    def navigate(self, url):
        self._send("Page.navigate", {"url": url})

    def print_pdf(self) -> bytes:
        result = self._send("Page.printToPDF", {
            "landscape": False,
            "displayHeaderFooter": False,
            "printBackground": True,
            "preferCSSPageSize": True,
            "paperWidth": 8.27,
            "paperHeight": 11.69,
            "marginTop": 0,
            "marginBottom": 0,
            "marginLeft": 0,
            "marginRight": 0,
            "generateDocumentOutline": True,
        })
        return base64.b64decode(result["data"])


# ── MD → HTML 変換 ──────────────────────────────────────────

def md_to_html(md_path: str) -> str:
    """Markdownファイルを読み込み、スタイル付きHTMLを生成する。"""
    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    # リダイレクト注記を除去（> ⚠️ このファイルはリネームされました...）
    lines = md_text.split("\n")
    filtered = []
    skip = False
    for line in lines:
        if line.startswith("> **⚠️") and "リネーム" in line:
            skip = True
            continue
        if skip and line.startswith(">"):
            continue
        skip = False
        filtered.append(line)
    md_text = "\n".join(filtered)

    # タイトル抽出
    title = os.path.splitext(os.path.basename(md_path))[0]
    for line in filtered:
        if line.startswith("# ") and not line.startswith("##"):
            title = line.lstrip("# ").strip()
            break

    # Markdown → HTML 変換
    html_body = markdown.markdown(
        md_text,
        extensions=[
            "tables",
            "fenced_code",
            "codehilite",
            "toc",
            "nl2br",
            "sane_lists",
        ],
        extension_configs={
            "codehilite": {"css_class": "highlight", "guess_lang": False},
        },
    )

    return HTML_TEMPLATE.format(title=title, content=html_body)


# ── ファイル変換 ──────────────────────────────────────────────

def convert_file(cdp: ChromeCDP, md_path: str, output_dir: str | None = None) -> str:
    """MDファイルをPDFに変換し、出力パスを返す。
    output_dir: 指定時はそこに出力。未指定時は同階層のpdf/に出力。
    """
    md_path = os.path.abspath(md_path)
    if output_dir:
        pdf_dir = os.path.abspath(output_dir)
    else:
        pdf_dir = os.path.join(os.path.dirname(md_path), "pdf")
    os.makedirs(pdf_dir, exist_ok=True)
    pdf_name = os.path.splitext(os.path.basename(md_path))[0] + ".pdf"
    pdf_path = os.path.join(pdf_dir, pdf_name)

    print(f"  変換中: {os.path.basename(md_path)}")

    # MD → HTML
    html_content = md_to_html(md_path)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(html_content)
        html_path = tmp.name

    try:
        # HTML → PDF via CDP
        cdp.navigate(f"file://{html_path}")
        time.sleep(3)  # レンダリング待ち（大きいファイル対応）

        pdf_data = cdp.print_pdf()
        with open(pdf_path, "wb") as f:
            f.write(pdf_data)

        size_kb = len(pdf_data) / 1024
        print(f"    → {os.path.basename(pdf_path)} ({size_kb:.0f}KB)")
        return pdf_path
    except Exception as e:
        print(f"    ✗ 失敗: {e}")
        return ""
    finally:
        os.unlink(html_path)


def convert_directory(dir_path: str, output_dir: str | None = None) -> list[str]:
    """ディレクトリ内の全MDファイルをPDFに変換する。"""
    md_files = sorted(glob.glob(os.path.join(dir_path, "*.md")))
    if not md_files:
        print(f"MDファイルが見つかりません: {dir_path}")
        return []

    print(f"=== {len(md_files)}件のMDファイルをPDFに変換 ===\n")
    results = []

    with ChromeCDP() as cdp:
        for md_file in md_files:
            pdf_path = convert_file(cdp, md_file, output_dir)
            if pdf_path:
                results.append(pdf_path)

    print(f"\n=== 完了: {len(results)}/{len(md_files)}件 ===")
    return results


def convert_all_to_project_pdf():
    """shachotwo/ 配下の全MDファイルを pdf/ に用途別フォルダで一括変換する。"""
    doc_root = os.path.join(BASE_DIR, "shachotwo")
    pdf_root = os.path.join(BASE_DIR, "pdf")

    # ソースディレクトリ → 出力先フォルダのマッピング
    mapping = [
        ("c_事業計画",       "01_事業計画"),
        ("a_セキュリティ",   "02_セキュリティ"),
        ("b_詳細設計",       "03_詳細設計"),
        ("d_アーキテクチャ", "04_アーキテクチャ"),
        ("e_業界別BPO",      "05_業界別BPO"),
        ("f_BPOガイド",      "06_BPOガイド"),
        ("g_ナレッジ",       "07_ドメイン知識"),
        ("h_意思決定記録",   "08_意思決定記録"),
        ("i_リファクタリング", "09_リファクタリング"),
        ("z_その他",         "10_その他"),
    ]

    total = 0
    success = 0

    with ChromeCDP() as cdp:
        for src_dir_name, pdf_dir_name in mapping:
            src_dir = os.path.join(doc_root, src_dir_name)
            if not os.path.isdir(src_dir):
                continue

            md_files = sorted(glob.glob(os.path.join(src_dir, "*.md")))
            # テンプレートファイルを除外
            md_files = [f for f in md_files if "template" not in os.path.basename(f).lower()]
            if not md_files:
                continue

            out_dir = os.path.join(pdf_root, pdf_dir_name)
            os.makedirs(out_dir, exist_ok=True)

            print(f"\n── {pdf_dir_name} ({len(md_files)}件) ──")
            for md_file in md_files:
                total += 1
                pdf_path = convert_file(cdp, md_file, out_dir)
                if pdf_path:
                    success += 1

    print(f"\n{'='*50}")
    print(f"全体完了: {success}/{total}件")
    print(f"出力先: {pdf_root}/")


def main():
    if len(sys.argv) > 1:
        arg = sys.argv[1]

        # --all フラグ: 全MDファイルをプロジェクトpdf/に一括変換
        if arg == "--all":
            convert_all_to_project_pdf()
            return

        target = os.path.abspath(arg)
        output_dir = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else None

        if os.path.isdir(target):
            convert_directory(target, output_dir)
        elif os.path.isfile(target) and target.endswith(".md"):
            with ChromeCDP() as cdp:
                convert_file(cdp, target, output_dir)
        else:
            print(f"エラー: 有効なMDファイルまたはディレクトリを指定してください: {target}")
            sys.exit(1)
    else:
        # 引数なし → 全プロジェクト一括変換
        convert_all_to_project_pdf()


if __name__ == "__main__":
    main()
