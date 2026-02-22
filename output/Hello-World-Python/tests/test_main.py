import subprocess
import sys

def test_hello_world_output():
    """
    main.py を実行し、標準出力が「Hello World」と正確に一致し、改行が含まれ、
    終了コードが0であることを確認する。
    """
    # main.py を実行するコマンド
    # sys.executable を使用して、現在のPythonインタプリタでスクリプトを実行
    command = [sys.executable, "main.py"]

    # サブプロセスとして実行し、出力をキャプチャ
    # text=True で stdout/stderr を文字列として取得
    # check=False でエラーが発生しても例外を投げない（出力を確認するため）
    result = subprocess.run(command, capture_output=True, text=True, check=False)

    # 終了コードが0であることを確認（正常終了）
    assert result.returncode == 0, f"スクリプトがエラー終了しました。Stderr: {result.stderr}"

    # 標準出力の内容が「Hello World」と正確に一致することを確認
    # .strip() を使って前後の空白や改行を除去してから比較
    assert result.stdout.strip() == "Hello World", \
        f"期待される出力と異なります。期待: 'Hello World', 実際: '{result.stdout.strip()}'"

    # 出力の末尾に改行が含まれていることを確認
    assert result.stdout.endswith("\n"), "出力の末尾に改行が含まれていません。"

def test_hello_world_output_no_args():
    """
    引数なしで main.py を実行した場合も、標準出力が「Hello World」と正確に一致することを確認する。
    """
    command = ["python", "main.py"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert result.stdout.strip() == "Hello World"
    assert result.stdout.endswith("\n")

def test_hello_world_output_with_unexpected_args():
    """
    予期しない引数を付けて main.py を実行した場合でも、標準出力が「Hello World」と正確に一致することを確認する。
    """
    command = ["python", "main.py", "unexpected_arg"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert result.stdout.strip() == "Hello World"
    assert result.stdout.endswith("\n")