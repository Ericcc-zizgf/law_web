"""專案啟動入口。

實際 Flask 應用程式位於 ``legal_tool.web_app``；保留這個檔案，
讓原本的 ``python app.py`` 啟動方式繼續可用。
"""

from legal_tool.config import get_server_port
from legal_tool.web_app import app


if __name__ == "__main__":
    port = get_server_port()
    print(f"訴願書判決工具平台已啟動：http://127.0.0.1:{port}/")
    app.run(host="127.0.0.1", debug=True, use_reloader=False, port=port)
