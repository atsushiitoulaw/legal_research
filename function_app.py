"""
Azure Functions が、main.py の FastAPI アプリ（ASGIアプリ）を
呼び出すための橋渡し役ファイル。

Azure Functions は、この function_app.py を入り口として起動する。
ここで FastAPI アプリ（main.py の app）を読み込んで、
すべてのリクエストをそちらに渡す。
"""
import azure.functions as func
from main import app as fastapi_app

app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)